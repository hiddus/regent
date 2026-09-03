"""Novel C 端 HTTP 契约（Tech-Spec §8）。

要点：
- 所有 mutation 支持 ``Idempotency-Key``；同键同参数返回首个结果，同键异参数 409。
- 关键路径更新携带 ``expected_version``/ETag；409 返回 current_version 与 conflict_summary。
- 统一错误 envelope：code / message / request_id / retryable / available_actions。
- 429 返回 Retry-After；204 不含 JSON body。
- 服务端解析 principal，不接受客户端 actor（G-11）。
"""

from __future__ import annotations

import hashlib
import json
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from regent.config import get_settings
from regent.model import ModelProvider
from regent.model.factory import build_model_provider
from regent.novel.application import events as events_app
from regent.novel.application import ledger as ledger_app
from regent.novel.application import works as works_app
from regent.novel.application.principal import (
    CurrentPrincipal,
    hash_token,
    issue_token,
)
from regent.novel.domain import money
from regent.novel.domain.errors import (
    IdempotencyConflict,
    NovelError,
    QuotaExceeded,
    ValidationFailed,
)
from regent.novel.domain.models import (
    AcknowledgeExportNoticeRequest,
    AnswerClarifyRequest,
    ConfirmDirectionRequest,
    CreateShareRequest,
    CreateWorkRequest,
    CreateWorkResponse,
    CriticalPathOut,
    CriticalPathUpdate,
    DecisionView,
    EventPage,
    ExportNoticeOut,
    ExportOut,
    ExportRequest,
    ModerationCaseOut,
    OnboardingOut,
    PathChangeImpact,
    ReportFactRequest,
    ReportFactResponse,
    ResolveDecisionRequest,
    RunProgressOut,
    ShareOut,
    WorkDetail,
    WorkStateOut,
    WorkSummary,
)
from regent.novel.infrastructure.models import (
    IdempotencyRecordModel,
    NovelPrincipalModel,
    NovelSessionModel,
)

router = APIRouter(prefix="/v1/novel", tags=["novel"])

SESSION_TTL = timedelta(days=30)


def _content_disposition(filename: str) -> str:
    """ASCII 回退 + RFC 5987。中文标题与引号都必须安全。"""
    from urllib.parse import quote

    ascii_fallback = "".join(
        ch if ch.isascii() and (ch.isalnum() or ch in "._-") else "_" for ch in filename
    )
    return f'attachment; filename="{ascii_fallback}"; filename*=UTF-8\'\'{quote(filename)}'


# ---------------------------------------------------------------------------
# 依赖
# ---------------------------------------------------------------------------


async def _session(request: Request):
    async with request.app.state.sessions() as session, session.begin():
        yield session


DbSession = Annotated[AsyncSession, Depends(_session)]


def _model_provider() -> ModelProvider:
    return build_model_provider(get_settings())


NovelModel = Annotated[ModelProvider, Depends(_model_provider)]


# ---------------------------------------------------------------------------
# 身份（G-11：服务端签发，客户端无法伪造 principal）
# ---------------------------------------------------------------------------


@router.post("/auth/session", status_code=200)
async def create_session(
    request: Request,
    session: DbSession,
    subject: Annotated[str | None, Query(max_length=255)] = None,
    display_name: Annotated[str, Query(max_length=120)] = "",
) -> dict[str, Any]:
    """封闭测试用身份签发。

    正式版本替换为手机号/第三方登录；**签发与校验始终在服务端**，
    客户端拿到的只是不透明 token，无法构造 principal。
    """
    if not subject:
        subject = f"guest-{secrets.token_hex(8)}"
    row = await session.scalar(
        select(NovelPrincipalModel).where(NovelPrincipalModel.subject == subject)
    )
    if row is None:
        row = NovelPrincipalModel(
            id=uuid.uuid4(),
            subject=subject,
            display_name=display_name or subject[:16],
        )
        session.add(row)
        await session.flush()

    raw = issue_token()
    session.add(
        NovelSessionModel(
            id=uuid.uuid4(),
            token_hash=hash_token(raw),
            principal_id=row.id,
            expires_at=datetime.now(UTC) + SESSION_TTL,
            user_agent=request.headers.get("user-agent", "")[:512],
        )
    )
    await session.flush()
    return {
        "token": raw,
        "token_type": "Bearer",
        "expires_in": int(SESSION_TTL.total_seconds()),
        "principal_id": str(row.id),
    }


@router.get("/me")
async def me(principal: CurrentPrincipal) -> dict[str, Any]:
    return {"principal_id": str(principal.id), "subject": principal.subject}


# ---------------------------------------------------------------------------
# 幂等（Tech-Spec §5）
# ---------------------------------------------------------------------------


async def _guard_idempotency(
    session: AsyncSession,
    *,
    scope: str,
    key: str | None,
    payload: dict[str, Any],
) -> IdempotencyRecordModel | None:
    """同键同参数返回首个结果；同键异参数 409。"""
    if not key:
        return None
    fingerprint = hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
    ).hexdigest()
    existing = await session.scalar(
        select(IdempotencyRecordModel).where(
            IdempotencyRecordModel.scope == scope,
            IdempotencyRecordModel.idempotency_key == key,
        )
    )
    if existing is None:
        return None
    if existing.request_fingerprint != fingerprint:
        raise IdempotencyConflict(key)
    return existing


async def _store_idempotency(
    session: AsyncSession,
    *,
    scope: str,
    key: str | None,
    payload: dict[str, Any],
    body: dict[str, Any],
    response_ref: str = "",
) -> None:
    if not key:
        return
    fingerprint = hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
    ).hexdigest()
    session.add(
        IdempotencyRecordModel(
            id=uuid.uuid4(),
            scope=scope,
            idempotency_key=key,
            request_fingerprint=fingerprint,
            response_ref=response_ref,
            response_body=body,
            status_code=200,
        )
    )
    await session.flush()


# ---------------------------------------------------------------------------
# 作品
# ---------------------------------------------------------------------------


@router.post("/works", response_model=CreateWorkResponse, status_code=201)
async def create_work(
    request: Request,
    session: DbSession,
    payload: CreateWorkRequest,
    principal: CurrentPrincipal,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> Any:
    body = payload.model_dump(mode="json")
    cached = await _guard_idempotency(
        session,
        scope=f"works:create:{principal.id}",
        key=idempotency_key or payload.client_nonce or None,
        payload=body,
    )
    if cached is not None:
        return cached.response_body

    work, onboarding = await works_app.create_work(
        session,
        owner_id=principal.id,
        raw_intent=payload.raw_intent,
        title=payload.title,
        genre=payload.genre,
        client_nonce=payload.client_nonce,
    )

    out = CreateWorkResponse(
        work_id=str(work.id),
        state=WorkStateOut(work.state),
        onboarding=onboarding,
    ).model_dump(mode="json")
    await _store_idempotency(
        session,
        scope=f"works:create:{principal.id}",
        key=idempotency_key or payload.client_nonce or None,
        payload=body,
        body=out,
        response_ref=str(work.id),
    )

    return out


@router.get("/works", response_model=list[WorkSummary])
async def list_works(
    session: DbSession,
    principal: CurrentPrincipal,
) -> Any:
    return await works_app.list_works(session, owner_id=principal.id)


@router.get("/works/{work_id}", response_model=WorkDetail)
async def get_work(
    work_id: uuid.UUID,
    session: DbSession,
    principal: CurrentPrincipal,
) -> Any:
    return await works_app.get_work(session, owner_id=principal.id, work_id=work_id)


@router.delete("/works/{work_id}", status_code=204, response_model=None)
async def delete_work(
    work_id: uuid.UUID,
    session: DbSession,
    principal: CurrentPrincipal,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> Response:
    await _guard_idempotency(
        session,
        scope=f"works:delete:{principal.id}",
        key=idempotency_key,
        payload={"work_id": str(work_id)},
    )
    await works_app.soft_delete_work(session, owner_id=principal.id, work_id=work_id)

    return Response(status_code=204)


# ---------------------------------------------------------------------------
# Onboarding：澄清与方向
# ---------------------------------------------------------------------------


@router.post("/works/{work_id}/clarify", response_model=OnboardingOut)
async def answer_clarify(
    work_id: uuid.UUID,
    payload: AnswerClarifyRequest,
    session: DbSession,
    principal: CurrentPrincipal,
) -> Any:
    out = await works_app.answer_clarify(
        session,
        owner_id=principal.id,
        work_id=work_id,
        answers=payload.answers,
        accept_defaults=payload.accept_defaults,
    )

    return out


@router.post("/works/{work_id}/directions", response_model=CriticalPathOut)
async def confirm_direction(
    work_id: uuid.UUID,
    payload: ConfirmDirectionRequest,
    session: DbSession,
    principal: CurrentPrincipal,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> Any:
    body = payload.model_dump(mode="json")
    cached = await _guard_idempotency(
        session,
        scope=f"works:directions:{principal.id}:{work_id}",
        key=idempotency_key or payload.client_nonce or None,
        payload=body,
    )
    if cached is not None:
        return cached.response_body
    _, path = await works_app.confirm_direction(
        session, owner_id=principal.id, work_id=work_id, card_id=payload.card_id
    )

    out = path.model_dump(mode="json")
    await _store_idempotency(
        session,
        scope=f"works:directions:{principal.id}:{work_id}",
        key=idempotency_key or payload.client_nonce or None,
        payload=body,
        body=out,
    )

    return out


# ---------------------------------------------------------------------------
# 关键路径（FR-04 / FR-05）
# ---------------------------------------------------------------------------


@router.get("/works/{work_id}/critical-path", response_model=CriticalPathOut)
async def get_critical_path(
    work_id: uuid.UUID,
    session: DbSession,
    principal: CurrentPrincipal,
) -> Any:
    return await works_app.get_critical_path(
        session, owner_id=principal.id, work_id=work_id
    )


@router.put("/works/{work_id}/critical-path")
async def update_critical_path(
    work_id: uuid.UUID,
    payload: CriticalPathUpdate,
    session: DbSession,
    principal: CurrentPrincipal,
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
) -> Any:
    """expected_version 冲突保护。409 返回 current_version 与 conflict_summary。"""
    if if_match:
        raw = if_match.strip().strip('"')
        try:
            expected = int(raw)
        except ValueError as exc:
            raise ValidationFailed("If-Match must be an integer version") from exc
        if expected != payload.expected_version:
            raise ValidationFailed("If-Match does not match expected_version")
    path, impact = await works_app.update_critical_path(
        session, owner_id=principal.id, work_id=work_id, payload=payload
    )

    return {
        "critical_path": path.model_dump(mode="json"),
        "impact": impact.model_dump(mode="json"),
        "version": path.version,
    }


@router.post("/works/{work_id}/critical-path/preview", response_model=PathChangeImpact)
async def preview_critical_path(
    work_id: uuid.UUID,
    payload: CriticalPathUpdate,
    session: DbSession,
    principal: CurrentPrincipal,
) -> Any:
    """只算影响，不落库。用于「改之前先看到代价」（PRD §3.2）。"""
    from regent.novel.application.works import (
        MAX_PATH_NODES,
        MIN_PATH_NODES,
        _preview_impact,
        get_critical_path,
    )

    if not MIN_PATH_NODES <= len(payload.nodes) <= MAX_PATH_NODES:
        raise ValidationFailed(
            f"critical path must contain {MIN_PATH_NODES}-{MAX_PATH_NODES} nodes"
        )
    current = await get_critical_path(session, owner_id=principal.id, work_id=work_id)
    from regent.novel.infrastructure.models import StoryWorkModel

    work = await session.get(StoryWorkModel, work_id)
    return _preview_impact(
        current_nodes=current.nodes,
        next_nodes=payload.nodes,
        frozen_through_chapter=current.frozen_through_chapter,
        latest_chapter_no=int(work.latest_chapter_no) if work else 0,
    )


# ---------------------------------------------------------------------------
# 运行（FR-06 / FR-13 / FR-20）
# ---------------------------------------------------------------------------


@router.post("/works/{work_id}/runs", response_model=RunProgressOut, status_code=202)
async def start_run(
    work_id: uuid.UUID,
    session: DbSession,
    principal: CurrentPrincipal,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> Any:
    cached = await _guard_idempotency(
        session,
        scope=f"works:runs:{principal.id}:{work_id}",
        key=idempotency_key,
        payload={"work_id": str(work_id)},
    )
    if cached is not None:
        return cached.response_body
    out = await works_app.start_run(session, owner_id=principal.id, work_id=work_id)

    body = out.model_dump(mode="json")
    await _store_idempotency(
        session,
        scope=f"works:runs:{principal.id}:{work_id}",
        key=idempotency_key,
        payload={"work_id": str(work_id)},
        body=body,
    )

    return body


@router.get("/works/{work_id}/runs", response_model=RunProgressOut)
async def get_run(
    work_id: uuid.UUID,
    session: DbSession,
    principal: CurrentPrincipal,
) -> Any:
    return await works_app.get_run_progress(session, owner_id=principal.id, work_id=work_id)


@router.post("/works/{work_id}/runs/{chapter_no}/advance", response_model=RunProgressOut)
async def advance_step(
    work_id: uuid.UUID,
    chapter_no: int,
    session: DbSession,
    provider: NovelModel,
    principal: CurrentPrincipal,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> Any:
    """推进一个真实 Agent-loop 检查点，可安全重试。"""
    out = await works_app.advance_step(
        session,
        provider=provider,
        owner_id=principal.id,
        work_id=work_id,
        chapter_no=chapter_no,
    )

    return out


@router.post("/works/{work_id}/pause")
async def pause_work(
    work_id: uuid.UUID,
    session: DbSession,
    principal: CurrentPrincipal,
) -> Any:
    state = await works_app.pause_work(session, owner_id=principal.id, work_id=work_id)

    return {"state": state.value, "worker_released": True}


@router.post("/works/{work_id}/resume")
async def resume_work(
    work_id: uuid.UUID,
    session: DbSession,
    principal: CurrentPrincipal,
) -> Any:
    state = await works_app.resume_work(session, owner_id=principal.id, work_id=work_id)

    return {"state": state.value}


# ---------------------------------------------------------------------------
# 阅读（FR-15 / FR-22 / G-14）
# ---------------------------------------------------------------------------


@router.get("/works/{work_id}/chapters/{chapter_no}")
async def get_chapter(
    work_id: uuid.UUID,
    chapter_no: int,
    session: DbSession,
    principal: CurrentPrincipal,
) -> Any:
    """只读路径：本端点不持有任何生成能力引用。"""
    out = await works_app.get_chapter(
        session, owner_id=principal.id, work_id=work_id, chapter_no=chapter_no
    )
    return out


# ---------------------------------------------------------------------------
# 裁决（FR-10 / G-13）
# ---------------------------------------------------------------------------


@router.get("/works/{work_id}/decisions/{decision_id}", response_model=DecisionView)
async def get_decision(
    work_id: uuid.UUID,
    decision_id: uuid.UUID,
    session: DbSession,
    principal: CurrentPrincipal,
) -> Any:
    return await works_app.get_decision(
        session, owner_id=principal.id, work_id=work_id, decision_id=decision_id
    )


@router.post("/works/{work_id}/decisions/{decision_id}/resolve", response_model=DecisionView)
async def resolve_decision(
    work_id: uuid.UUID,
    decision_id: uuid.UUID,
    payload: ResolveDecisionRequest,
    session: DbSession,
    principal: CurrentPrincipal,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> Any:
    cached = await _guard_idempotency(
        session,
        scope=f"decisions:resolve:{principal.id}:{decision_id}",
        key=idempotency_key or payload.client_nonce or None,
        payload=payload.model_dump(mode="json"),
    )
    if cached is not None:
        return cached.response_body
    out = await works_app.resolve_decision(
        session,
        owner_id=principal.id,
        work_id=work_id,
        decision_id=decision_id,
        option_id=payload.option_id,
        accept_default=payload.accept_default,
        confirm_nonce=payload.confirm_nonce,
        resolved_by="user",
    )

    body = out.model_dump(mode="json")
    await _store_idempotency(
        session,
        scope=f"decisions:resolve:{principal.id}:{decision_id}",
        key=idempotency_key or payload.client_nonce or None,
        payload=payload.model_dump(mode="json"),
        body=body,
    )

    return body


# ---------------------------------------------------------------------------
# 事实报错（FR-11）
# ---------------------------------------------------------------------------


@router.post("/works/{work_id}/facts/report", response_model=ReportFactResponse)
async def report_fact(
    work_id: uuid.UUID,
    payload: ReportFactRequest,
    session: DbSession,
    principal: CurrentPrincipal,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> Any:
    cached = await _guard_idempotency(
        session,
        scope=f"facts:report:{principal.id}:{work_id}",
        key=idempotency_key or payload.client_nonce or None,
        payload=payload.model_dump(mode="json"),
    )
    if cached is not None:
        return cached.response_body
    out = await works_app.report_fact(
        session, owner_id=principal.id, work_id=work_id, payload=payload
    )

    return out


# ---------------------------------------------------------------------------
# 分享（FR-17）
# ---------------------------------------------------------------------------


@router.get("/public/shares/{token}")
async def read_public_share(token: str, session: DbSession) -> Response:
    if len(token) < 20 or len(token) > 64:
        raise ValidationFailed("invalid share token")
    body = await works_app.get_public_share(session, token=token)
    return JSONResponse(
        content=json.loads(json.dumps(body, ensure_ascii=False, default=str)),
        headers={"X-Robots-Tag": "noindex, nofollow", "Cache-Control": "private, no-store"},
    )


@router.post("/works/{work_id}/shares", response_model=ShareOut, status_code=201)
async def create_share(
    request: Request,
    work_id: uuid.UUID,
    payload: CreateShareRequest,
    session: DbSession,
    principal: CurrentPrincipal,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> Any:
    body = payload.model_dump(mode="json")
    cached = await _guard_idempotency(
        session,
        scope=f"shares:create:{principal.id}:{work_id}",
        key=idempotency_key,
        payload=body,
    )
    if cached is not None:
        return cached.response_body
    base = str(request.base_url).rstrip("/")
    out = await works_app.create_share(
        session,
        owner_id=principal.id,
        work_id=work_id,
        scope=payload.scope,
        from_chapter=payload.from_chapter,
        to_chapter=payload.to_chapter,
        expires_in_hours=payload.expires_in_hours,
        invitee_label=payload.invitee_label,
        base_url=base,
    )

    dumped = out.model_dump(mode="json")
    await _store_idempotency(
        session,
        scope=f"shares:create:{principal.id}:{work_id}",
        key=idempotency_key,
        payload=body,
        body=dumped,
        response_ref=out.share_id,
    )

    return dumped


@router.delete("/works/{work_id}/shares/{share_id}", status_code=204, response_model=None)
async def revoke_share(
    work_id: uuid.UUID,
    share_id: uuid.UUID,
    session: DbSession,
    principal: CurrentPrincipal,
) -> Response:
    await works_app.revoke_share(
        session, owner_id=principal.id, work_id=work_id, share_id=share_id
    )

    return Response(status_code=204)


# ---------------------------------------------------------------------------
# 导出（FR-23 / G-15 / G-22）
# ---------------------------------------------------------------------------


@router.get("/works/{work_id}/export-notice", response_model=ExportNoticeOut)
async def get_export_notice(
    work_id: uuid.UUID,
    session: DbSession,
    principal: CurrentPrincipal,
) -> Any:
    return await works_app.get_export_notice(
        session, owner_id=principal.id, work_id=work_id
    )


@router.post("/works/{work_id}/export-notice/acknowledge", response_model=ExportNoticeOut)
async def acknowledge_export_notice(
    work_id: uuid.UUID,
    payload: AcknowledgeExportNoticeRequest,
    session: DbSession,
    principal: CurrentPrincipal,
) -> Any:
    out = await works_app.acknowledge_export_notice(
        session,
        owner_id=principal.id,
        work_id=work_id,
        notice_version=payload.notice_version,
    )

    return out


@router.post("/works/{work_id}/exports", response_model=ExportOut, status_code=201)
async def export_work(
    request: Request,
    work_id: uuid.UUID,
    payload: ExportRequest,
    session: DbSession,
    principal: CurrentPrincipal,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> Any:
    body = payload.model_dump(mode="json")
    cached = await _guard_idempotency(
        session,
        scope=f"exports:{principal.id}:{work_id}",
        key=idempotency_key,
        payload=body,
    )
    if cached is not None:
        return cached.response_body
    base = str(request.base_url).rstrip("/")
    out = await works_app.export_work(
        session, owner_id=principal.id, work_id=work_id, payload=payload, base_url=base
    )

    dumped = out.model_dump(mode="json")
    await _store_idempotency(
        session,
        scope=f"exports:{principal.id}:{work_id}",
        key=idempotency_key,
        payload=body,
        body=dumped,
        response_ref=out.export_id,
    )

    return dumped


@router.get("/exports/{export_id}/content")
async def get_export_content(
    export_id: uuid.UUID,
    session: DbSession,
    principal: CurrentPrincipal,
) -> Any:
    filename, text = await works_app.get_export_payload(
        session, owner_id=principal.id, export_id=export_id
    )
    return Response(
        content=text.encode("utf-8"),
        media_type="text/plain; charset=utf-8",
        headers={
            "Content-Disposition": _content_disposition(filename),
            # G-15：导出物始终带 AI 标识
            "X-AI-Disclosure": works_app.AI_DISCLOSURE,
            "X-Content-Options": "nosniff",
        },
    )


# ---------------------------------------------------------------------------
# 审核与申诉（FR-25 / G-23）
# ---------------------------------------------------------------------------


@router.get(
    "/works/{work_id}/moderation", response_model=list[ModerationCaseOut]
)
async def list_moderation(
    work_id: uuid.UUID,
    session: DbSession,
    principal: CurrentPrincipal,
) -> Any:
    return await works_app.list_moderation_cases(
        session, owner_id=principal.id, work_id=work_id
    )


@router.post("/works/{work_id}/moderation/{case_id}/appeal", response_model=ModerationCaseOut)
async def appeal_moderation(
    work_id: uuid.UUID,
    case_id: uuid.UUID,
    session: DbSession,
    principal: CurrentPrincipal,
    reason: Annotated[str, Query(max_length=2000)] = "",
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> Any:
    out = await works_app.appeal_moderation(
        session, owner_id=principal.id, work_id=work_id, case_id=case_id, reason=reason
    )

    return out


# ---------------------------------------------------------------------------
# 成本（FR-19）
# ---------------------------------------------------------------------------


@router.get("/works/{work_id}/costs")
async def work_costs(
    work_id: uuid.UUID,
    session: DbSession,
    principal: CurrentPrincipal,
    currency: Annotated[str, Query(max_length=3)] = "CNY",
) -> Any:
    money.currency_exponent(currency)
    await works_app._get_owned_work(session, work_id=work_id, owner_id=principal.id)
    return await ledger_app.work_cost(session, work_id=work_id, currency=currency.upper())


# ---------------------------------------------------------------------------
# 事件与 SSE（Tech-Spec §9 / G-17）
# ---------------------------------------------------------------------------


@router.get("/works/{work_id}/events", response_model=EventPage)
async def get_events(
    work_id: uuid.UUID,
    session: DbSession,
    principal: CurrentPrincipal,
    after_seq: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
) -> Any:
    await works_app._get_owned_work(session, work_id=work_id, owner_id=principal.id)
    return await events_app.read_events(
        session, work_id=work_id, after_seq=after_seq, limit=limit
    )


@router.get("/works/{work_id}/events/stream")
async def stream_events(
    request: Request,
    work_id: uuid.UUID,
    principal: CurrentPrincipal,
    after_seq: Annotated[int, Query(ge=0)] = 0,
) -> Any:
    """SSE。``id:`` 为持久 sequence；客户端可用 Last-Event-ID 续接。

    heartbeat 只表示连接存活；数据查询失败会发送 stream.degraded，不吞异常。
    """
    last_event_id = request.headers.get("last-event-id")
    start = after_seq
    if last_event_id and last_event_id.isdigit():
        start = max(start, int(last_event_id))

    async def gen():
        nonlocal start
        factory = request.app.state.sessions
        idle = 0
        while True:
            if await request.is_disconnected():
                return
            try:
                async with factory() as session:
                    await works_app._get_owned_work(
                        session, work_id=work_id, owner_id=principal.id
                    )
                    page = await events_app.read_events(
                        session, work_id=work_id, after_seq=start, limit=200
                    )
            except NovelError:
                # 资源不存在/越权：直接结束流，不泄露存在性
                return
            except Exception:
                yield "event: stream.degraded\ndata: {}\n\n"
                await _sleep(2.0)
                continue

            if page.resync_required:
                yield "event: stream.resync_required\ndata: {}\n\n"
                return

            if page.events:
                for event in page.events:
                    yield events_app.sse_frame(event)
                    start = event.sequence
                idle = 0
            else:
                idle += 1
                yield ": heartbeat\n\n"
                if idle > 600:  # ~10 分钟无事件，收流由客户端重连
                    return
            await _sleep(1.0)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


async def _sleep(seconds: float) -> None:
    import asyncio

    await asyncio.sleep(seconds)


# ---------------------------------------------------------------------------
# 错误 envelope
# ---------------------------------------------------------------------------


def register_exception_handlers(app: Any) -> None:
    """统一错误 envelope：code / message / request_id / retryable / available_actions。"""

    from fastapi import FastAPI
    from fastapi.responses import JSONResponse

    if not isinstance(app, FastAPI):
        return

    @app.exception_handler(NovelError)
    async def _novel_error_handler(request: Request, exc: NovelError) -> JSONResponse:
        request_id = getattr(request.state, "request_id", None) or request.headers.get(
            "x-request-id", ""
        )
        headers: dict[str, str] = {}
        if isinstance(exc, QuotaExceeded):
            headers["Retry-After"] = str(getattr(exc, "retry_after", 60))
        return JSONResponse(
            status_code=exc.status_code,
            content=exc.to_payload(request_id),
            headers=headers,
        )

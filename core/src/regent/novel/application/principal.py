"""服务端身份解析（G-11 / FR-24）。

规则：
- 服务端从 session token 解析 principal，**绝不采信客户端传来的 actor 字段**。
- Novel 路由不接受任何形如 ``actor`` / ``user_id`` / ``owner_id`` 的请求体字段作为授权依据。
- 越权查询返回与"不存在"一致的结果，不泄露资源是否存在（G-12）。
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import Depends, Header, Request
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select

from regent.novel.domain.errors import Unauthenticated
from regent.novel.infrastructure.models import (
    NovelPrincipalModel,
    NovelSessionModel,
)

SESSION_TTL = timedelta(days=30)


class Principal(BaseModel):
    """服务端解析出的主体。客户端无法构造。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: uuid.UUID
    subject: str
    display_name: str = ""


def hash_token(raw: str) -> str:
    """存 hash 不存明文。使用 SHA-256（token 本身为 256bit 随机值，无需 KDF）。"""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def issue_token() -> str:
    return secrets.token_urlsafe(48)


def constant_time_eq(a: str, b: str) -> bool:
    return hmac.compare_digest(a, b)


async def require_principal(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
    x_novel_token: Annotated[str | None, Header(alias="X-Novel-Token")] = None,
) -> Principal:
    """FastAPI 依赖：解析并校验 session token。

    支持 ``Authorization: Bearer <token>`` 与 ``X-Novel-Token: <token>``（便于 SSE）。
    """
    raw = ""
    if authorization and authorization.lower().startswith("bearer "):
        raw = authorization[7:].strip()
    elif x_novel_token:
        raw = x_novel_token.strip()
    if not raw:
        raise Unauthenticated()

    now = datetime.now(UTC)
    factory = request.app.state.sessions
    async with factory() as session:
        row = await session.scalar(
            select(NovelSessionModel).where(
                NovelSessionModel.token_hash == hash_token(raw)
            )
        )
        if row is None or row.revoked_at is not None or row.expires_at < now:
            raise Unauthenticated()
        principal = await session.get(NovelPrincipalModel, row.principal_id)
        if principal is None or principal.deleted_at is not None:
            raise Unauthenticated()
        return Principal(
            id=principal.id,
            subject=principal.subject,
            display_name=principal.display_name,
        )


async def optional_principal(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
    x_novel_token: Annotated[str | None, Header(alias="X-Novel-Token")] = None,
) -> Principal | None:
    """允许匿名（落地页 / 分享阅读）。失败不抛错，返回 None。"""
    try:
        return await require_principal(request, authorization, x_novel_token)
    except Unauthenticated:
        return None


CurrentPrincipal = Annotated[Principal, Depends(require_principal)]
OptionalPrincipal = Annotated[Principal | None, Depends(optional_principal)]

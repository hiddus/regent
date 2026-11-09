"""PostgreSQL-only context-tail experiment adapter; never uses production recovery dispatch.

Dedicated database required. CallBroker owns per-call financial accounting; these
three experiment tables own stage/global admission and a permanent no-resend key.
Live transports remain disabled until R0 and PostgreSQL acceptance are complete.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from pydantic import ValidationError
from sqlalchemy import (BigInteger, Boolean, CheckConstraint, DateTime, Integer, String, Text,
                        func, or_, select, text)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from regent.model import OpenAICompatibleProvider
from regent.novel.application import production
from regent.novel.application import directing_chapter as chapter
from regent.novel.domain.price_book import estimate_minor
from regent.novel.infrastructure.models import (
    CostEntryModel, ModelCallModel, NovelBase, NovelPrincipalModel, QuotaReservationModel,
    StoryWorkModel,
)


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def now() -> datetime:
    return datetime.now(UTC)


def elapsed(since: datetime) -> int:
    return max(0, int((now() - since).total_seconds() * 1000))


class LocalDispatchRejected(ValueError):
    """Known local rejection before transport; not an uncertain supplier outcome."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class ExperimentBase(DeclarativeBase):
    pass


class Budget(ExperimentBase):
    __tablename__ = "context_tail_budget"
    __table_args__ = (
        CheckConstraint("claimed_requests >= 0 AND claimed_requests <= request_cap"),
        CheckConstraint("held_minor >= 0 AND charged_minor >= 0"),
    )
    experiment_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    scope: Mapped[str] = mapped_column(String(20), primary_key=True)
    config_hash: Mapped[str] = mapped_column(String(64))
    manifest_hash: Mapped[str] = mapped_column(String(64), default="")
    amount_cap: Mapped[int] = mapped_column(BigInteger)
    request_cap: Mapped[int] = mapped_column(Integer)
    claimed_requests: Mapped[int] = mapped_column(Integer, default=0)
    held_minor: Mapped[int] = mapped_column(BigInteger, default=0)
    charged_minor: Mapped[int] = mapped_column(BigInteger, default=0)
    active_delivery: Mapped[str | None] = mapped_column(String(64))
    paused: Mapped[bool] = mapped_column(Boolean, default=False)


class Delivery(ExperimentBase):
    __tablename__ = "context_tail_delivery"
    __table_args__ = (CheckConstraint("http_attempts >= 0 AND http_attempts <= 1"),)
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    experiment_id: Mapped[str] = mapped_column(String(100), index=True)
    stage: Mapped[str] = mapped_column(String(20))
    sample_id: Mapped[str] = mapped_column(String(160))
    arm: Mapped[str] = mapped_column(String(20))
    request_hash: Mapped[str] = mapped_column(String(64))
    work_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    logical_call_id: Mapped[str] = mapped_column(String(255), unique=True)
    command_id: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(20), default="CLAIMED")
    http_attempts: Mapped[int] = mapped_column(Integer, default=0)
    held_minor: Mapped[int] = mapped_column(BigInteger)
    actual_minor: Mapped[int | None] = mapped_column(BigInteger)
    usage_source: Mapped[str] = mapped_column(String(40), default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    lease_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int] = mapped_column(BigInteger, default=0)
    output_json: Mapped[dict | None] = mapped_column(JSONB)
    rules: Mapped[list] = mapped_column(JSONB, default=list)
    error: Mapped[str] = mapped_column(Text, default="")
    wire_json: Mapped[dict | None] = mapped_column(JSONB)


class Audit(ExperimentBase):
    # No FK to the locked account: WAIT_LOCK must remain independently writable.
    __tablename__ = "context_tail_audit"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    experiment_id: Mapped[str] = mapped_column(String(100), index=True)
    delivery_key: Mapped[str] = mapped_column(String(64))
    category: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(60))
    duration_ms: Mapped[int] = mapped_column(BigInteger)
    attempts: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


@dataclass(frozen=True)
class ModelConfig:
    model: str
    base_url: str = "https://offline.invalid/v1"
    api_key: str = field(default="offline-dummy", repr=False)
    max_output_tokens: int = 8192
    temperature: float = 0.8


@dataclass(frozen=True)
class WriterRequest:
    experiment_id: str
    stage: str
    sample_id: str
    arm: str
    context: dict
    cast: dict
    title: str
    system_prompt: str
    user_prompt: str
    request_hash: str
    rules_hash: str

    @property
    def key(self) -> str:
        # Stage, run id, process id and nonce cannot be changed to bypass A=1.
        return digest([self.experiment_id, self.sample_id, self.arm])

    @property
    def command_id(self) -> str:
        return "context-tail:" + self.key

    @property
    def logical_call_id(self) -> str:
        return production.logical_call_key(None, self.command_id, "", "context_tail_write")


def prepare_writer(experiment_id: str, stage: str, sample_id: str, arm: str,
                   context: dict, cast: dict, model: ModelConfig, rules_hash: str,
                   title: str = "") -> WriterRequest:
    if arm not in {"CONTROL", "TREATMENT"}:
        raise ValueError("unknown experiment arm")
    # Snapshot copies prevent later caller mutation from altering the checks.
    ctx = json.loads(json.dumps(context, ensure_ascii=False))
    actors = json.loads(json.dumps(cast, ensure_ascii=False))
    previous = chapter.previous_chapter_text(ctx)
    if len(previous) < 1600:
        raise ValueError("historical source must contain at least 1600 Python characters")
    payload = chapter.build_write_payload(ctx, actors)
    if arm == "TREATMENT":
        payload["previous_chapter"] = previous[-1600:]
    system = chapter._writer_system(ctx, actors)
    user = json.dumps(payload, ensure_ascii=False)
    fingerprint = digest({"system": system, "user": user, "context": ctx, "cast": actors,
        "title": title, "model": model.model, "base_url": model.base_url,
        "output_tokens": model.max_output_tokens, "temperature": model.temperature,
        "schema": chapter.ChapterProse.model_json_schema(), "rules_hash": rules_hash})
    return WriterRequest(experiment_id, stage, sample_id, arm, ctx, actors, title,
                         system, user, fingerprint, rules_hash)


RULE_PREFIXES = (
    ("正文不足", "chapter.length_min"), ("正文超过", "chapter.length_max"),
    ("与上一章连续重复", "chapter.cross_copy"),
    ("同一段原文在后文又出现了一遍", "chapter.internal_copy"),
    ("[pov:pronoun]", "pov.pronoun"), ("[pov:host_name]", "pov.host_name"),
    ("[front:banned_label]", "front.banned_label"),
    ("[front:first_person]", "front.first_person"),
    ("[front:title_cast]", "front.title_cast"),
    ("[front:power_surface]", "front.power_surface"),
    ("[front:redundant]", "front.redundant"),
)


def check_output(request: WriterRequest, output: dict) -> list[dict]:
    try:
        body = chapter.ChapterProse.model_validate(output).content.strip()
    except ValidationError as exc:
        return [{"rule_id": "schema.chapter_prose",
                 "message": "schema parse rejected: " + str(exc).splitlines()[0]}]
    failures = chapter.check_chapter(body, ctx=request.context, cast=request.cast,
                                    title=request.title, ignore_min_length=False,
                                    ignore_max_length=False)
    result = []
    for message in failures:
        rule_id = next((rid for prefix, rid in RULE_PREFIXES if message.startswith(prefix)), None)
        if rule_id is None:
            raise ValueError("unmapped production rule; experiment must pause: " + message)
        result.append({"rule_id": rule_id, "message": message})
    return result


class ExperimentStore:
    def __init__(self, factory: async_sessionmaker[AsyncSession], *, database: str,
                 production_database: str, experiment_id: str) -> None:
        self.factory = factory
        self.database = database
        self.production_database = production_database
        self.experiment_id = experiment_id

    async def assert_isolated(self) -> None:
        async with self.factory() as session:
            if session.bind is None or session.bind.dialect.name != "postgresql":
                raise ValueError("real PostgreSQL is mandatory; SQLite cannot certify this adapter")
            actual = await session.scalar(text("select current_database()"))
            if (not self.database.startswith("regent_exp_") or actual != self.database
                    or actual == self.production_database or not self.production_database):
                raise ValueError("refusing non-dedicated or mismatched experiment database")

    async def initialize(self, stages: dict[str, tuple[int, int]], config_hash: str) -> None:
        await self.assert_isolated()
        config_hash = digest({"identity": config_hash, "stages": stages})
        if not stages or "ALL" in stages:
            raise ValueError("explicit nonempty stage limits required")
        for requests, amount in stages.values():
            if type(requests) is not int or type(amount) is not int or requests < 1 or amount < 300:
                raise ValueError("invalid stage limits")
        async with self.factory() as s:
            async with s.begin():
                await s.execute(text("select pg_advisory_xact_lock(hashtext(:key))"),
                                {"key": "context-tail-init:" + self.experiment_id})
                configs = {**stages, "ALL": (sum(x[0] for x in stages.values()),
                                             sum(x[1] for x in stages.values()))}
                for scope, (requests, amount) in configs.items():
                    row = await s.get(Budget, (self.experiment_id, scope))
                    if row is not None:
                        if (row.config_hash, row.request_cap, row.amount_cap) != (
                                config_hash, requests, amount):
                            raise ValueError("frozen budget configuration conflict")
                    else:
                        s.add(Budget(experiment_id=self.experiment_id, scope=scope,
                            config_hash=config_hash, request_cap=requests, amount_cap=amount))

    async def freeze_requests(self, requests: list[WriterRequest]) -> None:
        """Persist the task set before dispatch; a fresh run id/nonce cannot add tasks."""
        await self.assert_isolated()
        if not requests or len({r.key for r in requests}) != len(requests):
            raise ValueError("empty or duplicated frozen task list")
        manifest_hash = digest(sorted((r.key, r.stage, r.request_hash) for r in requests))
        async with self.factory() as s, s.begin():
            root = await s.scalar(select(Budget).where(
                Budget.experiment_id == self.experiment_id, Budget.scope == "ALL"
            ).with_for_update())
            if root is None:
                raise ValueError("initialize budget before freezing tasks")
            if root.manifest_hash:
                if root.manifest_hash != manifest_hash:
                    raise ValueError("frozen task manifest conflict")
                return
            if root.claimed_requests:
                raise ValueError("cannot freeze new tasks after dispatch")
            for r in requests:
                if r.experiment_id != self.experiment_id or len(r.sample_id) > 160:
                    raise ValueError("invalid frozen task identity")
                if await s.get(Budget, (self.experiment_id, r.stage)) is None:
                    raise ValueError("unregistered task stage")
                s.add(Delivery(key=r.key, experiment_id=self.experiment_id,
                    stage=r.stage, sample_id=r.sample_id, arm=r.arm,
                    request_hash=r.request_hash,
                    work_id=uuid.uuid5(uuid.NAMESPACE_URL, "context-tail-work:" + r.key),
                    logical_call_id=r.logical_call_id, command_id=r.command_id,
                    status="PLANNED", held_minor=0, lease_expires_at=now()))
            root.manifest_hash = manifest_hash

    async def audit(self, key: str, category: str, status: str, since: datetime,
                    attempts: int | None = None) -> None:
        async with self.factory() as s, s.begin():
            if attempts is None:
                delivery = await s.get(Delivery, key)
                attempts = (delivery.http_attempts if delivery is not None and
                            delivery.experiment_id == self.experiment_id else 0)
            s.add(Audit(experiment_id=self.experiment_id, delivery_key=key, category=category,
                        status=status, duration_ms=elapsed(since), attempts=attempts))

    async def claim(self, request: WriterRequest) -> str:
        await self.assert_isolated()
        if request.experiment_id != self.experiment_id:
            raise ValueError("cross-experiment request")
        started = now()
        try:
            async with self.factory() as s, s.begin():
                accounts = []
                for scope in ("ALL", request.stage):
                    account = await s.scalar(select(Budget).where(
                        Budget.experiment_id == self.experiment_id, Budget.scope == scope
                    ).with_for_update(nowait=True))
                    if account is None:
                        raise ValueError("unregistered experiment stage")
                    accounts.append(account)
                existing = await s.get(Delivery, request.key)
                if not accounts[0].manifest_hash or existing is None:
                    raise ValueError("request absent from the frozen task manifest")
                if (existing.experiment_id, existing.request_hash, existing.stage) != (
                        self.experiment_id, request.request_hash, request.stage):
                    raise ValueError("delivery identity/input conflict; cannot bypass permanent key")
                if existing.status != "PLANNED":
                    status = "REUSED" if existing.status == "SUCCEEDED" else (
                        "TERMINAL_FAILED" if existing.status == "FAILED" else "WAIT_EXISTING")
                elif accounts[0].paused:
                    status = "WAIT_PAUSED"
                elif accounts[0].active_delivery:
                    status = "WAIT_ACTIVE"
                elif any(a.claimed_requests >= a.request_cap or
                         a.charged_minor + a.held_minor + 300 > a.amount_cap for a in accounts):
                    status = "SKIP_LIMIT"
                else:
                    status = "DISPATCH"
                    for a in accounts:
                        a.claimed_requests += 1
                        a.held_minor += 300
                    accounts[0].active_delivery = request.key
                    work_id = existing.work_id
                    owner_id = uuid.uuid5(uuid.NAMESPACE_URL, "context-tail-owner:" + self.experiment_id)
                    if await s.get(NovelPrincipalModel, owner_id) is None:
                        s.add(NovelPrincipalModel(id=owner_id, subject="context-tail:" + self.experiment_id))
                        await s.flush()
                    if await s.get(StoryWorkModel, work_id) is None:
                        s.add(StoryWorkModel(id=work_id, owner_id=owner_id, state="RUNNING"))
                        await s.flush()
                    existing.status = "CLAIMED"
                    existing.held_minor = 300
                    existing.created_at = now()
                    existing.lease_expires_at = now() + production.DEFAULT_LEASE_TTL
                category = "CALL" if status == "DISPATCH" else (
                    "WAIT" if status.startswith("WAIT") else "SKIP")
                s.add(Audit(experiment_id=self.experiment_id, delivery_key=request.key,
                            category=category, status=status, duration_ms=elapsed(started),
                            attempts=existing.http_attempts))
                return status
        except DBAPIError as exc:
            state = getattr(exc.orig, "sqlstate", None)
            if state != "55P03":
                raise
            await self.audit(request.key, "WAIT", "WAIT_LOCK", started)
            return "WAIT_LOCK"

    async def record_post(self, request: WriterRequest, wire: dict) -> None:
        async with self.factory() as s, s.begin():
            row = await s.scalar(select(Delivery).where(Delivery.key == request.key).with_for_update())
            if (row is None or row.experiment_id != self.experiment_id or
                    row.request_hash != request.request_hash or row.status != "CLAIMED" or
                    row.http_attempts != 0):
                raise LocalDispatchRejected("NO_RESEND_BARRIER")
            row.http_attempts = 1  # committed before transport; a crash here sacrifices the permit
            row.wire_json = wire  # messages/body only; never authorization headers
            s.add(Audit(experiment_id=self.experiment_id, delivery_key=request.key,
                        category="CALL", status="POST_CLAIMED", duration_ms=elapsed(row.created_at),
                        attempts=1))

    async def local_rejection(self, request: WriterRequest, code: str) -> None:
        # Persist zero-POST proof before the broker wraps the exception as CallUnknown.
        # Existing POST evidence must never be overwritten or settled as zero cost.
        async with self.factory() as session, session.begin():
            row = await session.scalar(select(Delivery).where(
                Delivery.key == request.key).with_for_update())
            if (row is None or row.experiment_id != self.experiment_id or
                    row.request_hash != request.request_hash):
                raise ValueError("local rejection task mismatch")
            if row.status == "CLAIMED" and row.http_attempts == 0:
                row.error = "LOCAL_PRE_DISPATCH:" + code
            session.add(Audit(experiment_id=self.experiment_id, delivery_key=request.key,
                category="SKIP", status=code, duration_ms=elapsed(row.created_at),
                attempts=row.http_attempts))

    async def unknown(self, key: str, reason: str) -> None:
        async with self.factory() as s, s.begin():
            account = await s.scalar(select(Budget).where(
                Budget.experiment_id == self.experiment_id, Budget.scope == "ALL"
            ).with_for_update())
            row = await s.scalar(select(Delivery).where(Delivery.key == key).with_for_update())
            if row is None or row.experiment_id != self.experiment_id:
                raise ValueError("cross-experiment settlement")
            if row.status in {"SUCCEEDED", "FAILED"}:
                return
            row.status, row.error = "UNKNOWN", reason
            row.duration_ms = elapsed(row.created_at)
            account.paused = True
            s.add(Audit(experiment_id=self.experiment_id, delivery_key=key, category="WAIT",
                        status="UNKNOWN", duration_ms=row.duration_ms, attempts=row.http_attempts))

    async def finish(self, key: str, *, status: str, actual: int, usage_source: str,
                     output: dict | None, rules: list[dict], error: str = "",
                     pause: bool = False) -> dict:
        if status not in {"SUCCEEDED", "FAILED"} or type(actual) is not int or actual < 0:
            raise ValueError("invalid terminal settlement")
        async with self.factory() as s, s.begin():
            first = await s.get(Delivery, key)
            if first is None or first.experiment_id != self.experiment_id:
                raise ValueError("delivery missing or cross-experiment key")
            accounts = []
            for scope in ("ALL", first.stage):
                accounts.append(await s.scalar(select(Budget).where(
                    Budget.experiment_id == self.experiment_id, Budget.scope == scope
                ).with_for_update()))
            row = await s.scalar(select(Delivery).where(Delivery.key == key).with_for_update())
            if row.status in {"SUCCEEDED", "FAILED"}:
                return self.view(row)
            if row.status == "PLANNED":
                raise ValueError("cannot settle an unclaimed task")
            for account in accounts:
                account.held_minor -= row.held_minor
                account.charged_minor += actual
            row.held_minor = 0
            row.actual_minor, row.usage_source = actual, usage_source
            row.status, row.output_json, row.rules, row.error = status, output, rules, error
            row.finished_at, row.duration_ms = now(), elapsed(row.created_at)
            accounts[0].active_delivery = None
            # Truthful overrun accounting; never hide real fees to make the cap pass.
            accounts[0].paused = pause or any(
                a.charged_minor + a.held_minor > a.amount_cap for a in accounts)
            s.add(Audit(experiment_id=self.experiment_id, delivery_key=key,
                category="SUCCESS" if status == "SUCCEEDED" else "FAILED", status=status,
                duration_ms=row.duration_ms, attempts=row.http_attempts))
            return self.view(row)

    @staticmethod
    def view(row: Delivery) -> dict:
        return {"key": row.key, "status": row.status, "attempts": row.http_attempts,
                "duration_ms": row.duration_ms, "actual_minor": row.actual_minor,
                "usage_source": row.usage_source, "output": row.output_json,
                "rules": row.rules, "error": row.error}

    async def read(self, key: str) -> dict:
        async with self.factory() as s:
            row = await s.get(Delivery, key)
            if row is None or row.experiment_id != self.experiment_id:
                raise ValueError("delivery missing or cross-experiment key")
            return self.view(row)

    async def summary(self) -> dict:
        async with self.factory() as s:
            counts = dict((await s.execute(select(Delivery.status, func.count()).where(
                Delivery.experiment_id == self.experiment_id).group_by(Delivery.status))).all())
            account = await s.get(Budget, (self.experiment_id, "ALL"))
            calls = (await s.scalars(select(ModelCallModel).join(
                Delivery, Delivery.logical_call_id == ModelCallModel.logical_call_id
            ).where(Delivery.experiment_id == self.experiment_id))).all()
            failed_keys = (await s.scalars(select(Delivery.key).outerjoin(
                ModelCallModel, ModelCallModel.logical_call_id == Delivery.logical_call_id
            ).where(Delivery.experiment_id == self.experiment_id, or_(
                Delivery.status == "FAILED", ModelCallModel.status == "FAILED")))).all()
            return {"delivery_states": counts, "failed": len(set(failed_keys)),
                    "delivery_failed": counts.get("FAILED", 0),
                    "model_call_failed": sum(c.status == "FAILED" for c in calls),
                    "broker_recorded_minor": sum(int(c.actual_amount_minor or 0) for c in calls),
                    "claimed_request_permits": account.claimed_requests,
                    "held_minor": account.held_minor, "charged_minor": account.charged_minor,
                    "paused": account.paused}


async def initialize_isolated_database(store: ExperimentStore) -> None:
    """Create tables only inside an explicitly named, dedicated experiment database."""
    await store.assert_isolated()
    async with store.factory() as s:
        connection = await s.connection()
        await connection.execute(text(
            "select pg_advisory_xact_lock(hashtext('context-tail-schema-v1'))"
        ))
        await connection.run_sync(lambda conn: NovelBase.metadata.create_all(conn, tables=[
            NovelPrincipalModel.__table__, StoryWorkModel.__table__, ModelCallModel.__table__,
            CostEntryModel.__table__,
            QuotaReservationModel.__table__,
        ]))
        await connection.run_sync(ExperimentBase.metadata.create_all)
        await s.commit()


class ContextTailAdapter:
    def __init__(self, store: ExperimentStore, model: ModelConfig,
                 transport: httpx.MockTransport) -> None:
        if not isinstance(transport, httpx.MockTransport):
            raise ValueError("live transports remain blocked pending experiment certification")
        self.store, self.model, self.transport = store, model, transport
        self.broker = production.CallBroker(session_factory=store.factory,
            lease_owner="context-tail:" + uuid.uuid4().hex, budget_limit_minor=300)

    async def run_writer(self, request: WriterRequest) -> dict:
        rebuilt = prepare_writer(request.experiment_id, request.stage, request.sample_id,
            request.arm, request.context, request.cast, self.model, request.rules_hash, request.title)
        if rebuilt != request:
            await self.store.audit(request.key, "FAILED", "CONFIG_CONFLICT", now())
            raise ValueError("prepared request/model/source was mutated")
        reserved = estimate_minor(self.model.model, prompt_chars=len(request.user_prompt),
                                  system_chars=len(request.system_prompt))
        if reserved > 300:
            await self.store.audit(request.key, "SKIP", "PRICE_PRECHECK_BLOCKED", now())
            raise ValueError("frozen request cannot fit broker reservation cap")
        status = await self.store.claim(request)
        if status != "DISPATCH":
            if status in {"REUSED", "TERMINAL_FAILED", "WAIT_EXISTING"}:
                return await self.store.read(request.key)
            return {"status": status, "attempts": 0, "duration_ms": 0}

        local_rejection: LocalDispatchRejected | None = None

        async def before_request(http_request: httpx.Request) -> None:
            nonlocal local_rejection
            try:
                try:
                    wire = json.loads(http_request.content)
                    schema = json.dumps(chapter.ChapterProse.model_json_schema(), ensure_ascii=False)
                    if (wire.get("model") != self.model.model or
                            wire.get("max_tokens") != self.model.max_output_tokens or
                            wire.get("temperature") != self.model.temperature or
                            wire["messages"][1]["content"] != request.user_prompt or
                            not wire["messages"][0]["content"].startswith(request.system_prompt) or
                            schema not in wire["messages"][0]["content"]):
                        raise LocalDispatchRejected("WIRE_CONFIG_REJECTED")
                except (ValueError, KeyError, IndexError, TypeError, AttributeError) as exc:
                    if isinstance(exc, LocalDispatchRejected):
                        raise
                    raise LocalDispatchRejected("WIRE_SERIALIZATION_REJECTED") from exc
                await self.store.record_post(request, wire)
            except LocalDispatchRejected as exc:
                local_rejection = exc
                await self.store.local_rejection(request, exc.code)
                raise

        async with httpx.AsyncClient(transport=self.transport,
                                    event_hooks={"request": [before_request]}) as client:
            provider = OpenAICompatibleProvider(base_url=self.model.base_url,
                api_key=self.model.api_key, model=self.model.model,
                max_output_tokens=self.model.max_output_tokens, max_structured_attempts=1,
                max_http_retries=0, stream=False, client=client)
            try:
                async with self.store.factory() as s:
                    await self.broker.run(s, provider=provider, schema=chapter.ChapterProse,
                        work_id=uuid.uuid5(uuid.NAMESPACE_URL, "context-tail-work:" + request.key),
                        run_id=None, chapter_no=int(request.context.get("chapter_no") or 1),
                        step="CONTEXT_TAIL", purpose="context_tail_write",
                        command_id=request.command_id, system_prompt=request.system_prompt,
                        user_prompt=request.user_prompt, temperature=self.model.temperature,
                        model_hint=self.model.model)
            except Exception as exc:
                if local_rejection is not None:
                    return await self._finish_local_rejection(request)
                await self.store.unknown(request.key, type(exc).__name__ + ": " + str(exc))
                return await self.store.read(request.key)
        return await self._finish_from_call(request)

    async def _finish_local_rejection(self, request: WriterRequest) -> dict:
        evidence = await self.store.read(request.key)
        if (evidence["attempts"] != 0 or
                not evidence["error"].startswith("LOCAL_PRE_DISPATCH:")):
            # A prior POST remains authoritative. Reject the duplicate locally;
            # do not invent UNKNOWN, erase its fees, or start reconciliation here.
            return evidence
        async with self.store.factory() as session:
            async with self.broker._tx(session) as acc:
                call = await acc.scalar(select(ModelCallModel).where(
                    ModelCallModel.logical_call_id == request.logical_call_id).with_for_update())
                if call is not None:
                    if call.attempt != 1 or call.status not in {"RESERVED", "UNKNOWN", "FAILED"}:
                        raise ValueError("local rejection ledger state conflict")
                    await self.broker._finalize(acc, call, status="FAILED", actual=0,
                        usage_source="local_no_post", error_code=evidence["error"])
        return await self.store.finish(request.key, status="FAILED", actual=0,
            usage_source="local_no_post", output=None, rules=[], error=evidence["error"],
            pause=True)  # configuration/barrier anomaly, not an ordinary rejected manuscript

    async def _finish_from_call(self, request: WriterRequest) -> dict:
        async with self.store.factory() as s:
            call = await s.scalar(select(ModelCallModel).where(
                ModelCallModel.logical_call_id == request.logical_call_id))
            if call is None:
                return await self.store.finish(request.key, status="FAILED", actual=0,
                    usage_source="no_broker_prepare", output=None, rules=[], error="NO_CALL_PREPARED")
            if call.attempt != 1:
                raise ValueError("experiment broker attempt invariant violated")
            if call.status not in {"SUCCEEDED", "FAILED"}:
                return await self.store.read(request.key)
            rules = []
            failure = str(call.error_code or "")
            status = "FAILED"
            pause = False
            if call.status == "SUCCEEDED":
                try:
                    rules = check_output(request, call.output_json or {})
                    status = "FAILED" if rules else "SUCCEEDED"
                except Exception as exc:
                    # Unknown rule/configuration is not silently accepted.
                    failure = "CHECK_UNVERIFIED:" + str(exc)
                    pause = True
            return await self.store.finish(request.key, status=status,
                actual=int(call.actual_amount_minor or 0), usage_source=call.usage_source,
                output=call.output_json, rules=rules, error=failure, pause=pause)

    async def recover(self, request: WriterRequest, lookup: Any = None) -> dict:
        """Serialize query-only recovery; never run production generation recovery dispatch."""
        await self.store.assert_isolated()
        async with self.store.factory() as lock_session:
            locked = await lock_session.scalar(text(
                "select pg_try_advisory_xact_lock(hashtext(:key))"
            ), {"key": "context-tail-recovery:" + request.key})
            if not locked:
                started = now()
                await self.store.audit(request.key, "WAIT", "WAIT_RECOVERY_LOCK", started)
                return {"status": "WAIT_RECOVERY_LOCK", "attempts": 0, "duration_ms": 0}
            return await self._recover_locked(request, lookup)

    async def _recover_locked(self, request: WriterRequest, lookup: Any = None) -> dict:
        """Read-only supplier lookup + broker settlement; never broker.run/generate again."""
        await self.store.assert_isolated()
        started = now()
        async with self.store.factory() as s:
            row = await s.get(Delivery, request.key)
            if (row is None or row.experiment_id != self.store.experiment_id or
                    request.experiment_id != self.store.experiment_id or
                    row.request_hash != request.request_hash):
                raise ValueError("unregistered recovery or input mismatch")
            if row.status == "PLANNED":
                await self.store.audit(request.key, "SKIP", "RECOVERY_NOT_DISPATCHED", started)
                return self.store.view(row)
            if row.status in {"SUCCEEDED", "FAILED"}:
                await self.store.audit(request.key, "SKIP", "RECOVERY_TERMINAL", started)
                return self.store.view(row)
            if row.error.startswith("LOCAL_PRE_DISPATCH:") and row.http_attempts == 0:
                await s.rollback()
                return await self._finish_local_rejection(request)
            call = await s.scalar(select(ModelCallModel).where(
                ModelCallModel.logical_call_id == row.logical_call_id).with_for_update())
            if call is not None and call.status == "RESERVED":
                if call.lease_expires_at and call.lease_expires_at > now():
                    await s.rollback()
                    await self.store.audit(request.key, "WAIT", "WAIT_LEASE", started)
                    return {"status": "WAIT_LEASE", "attempts": 0, "duration_ms": elapsed(started)}
                await production._reap(s, call)
                await s.commit()  # E5: a separate reconciliation connection must see UNKNOWN.
            elif call is None and row.lease_expires_at > now():
                await s.rollback()
                await self.store.audit(request.key, "WAIT", "WAIT_PREPARE", started)
                return {"status": "WAIT_PREPARE", "attempts": 0, "duration_ms": elapsed(started)}
        await self.store.unknown(request.key, "RECOVERY_ONLY_NO_RESEND")
        # Query-only object: no generation/chat methods exposed to recovery.
        class ReadOnlyLookup:
            async def lookup_call(self, request_id: str) -> Any:
                return await lookup(request_id) if lookup is not None else None
        try:
            async with self.store.factory() as s:
                await self.broker.reconcile(s, logical_call_id=request.logical_call_id,
                                            provider=ReadOnlyLookup())
        except Exception as exc:
            await self.store.unknown(request.key, "READONLY_LOOKUP_FAILED:" + type(exc).__name__)
            await self.store.audit(request.key, "WAIT", "READONLY_LOOKUP_FAILED", started)
            # Query failure spends no new model request. Still advance bounded settlement.
            async with self.store.factory() as s:
                await self.broker.reconcile(s, logical_call_id=request.logical_call_id, provider=None)
        return await self._finish_from_call(request)

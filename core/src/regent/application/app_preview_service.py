import uuid
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from regent.application.p1_contracts import canonical_hash
from regent.domain.errors import DomainError, ErrorCode
from regent.infrastructure.models import (
    AppPreviewReleaseModel,
    AppProjectModel,
    ConversationMessageModel,
    ConversationModel,
    GateEvaluationModel,
    GoalModel,
    GoalSpecModel,
    MetricDefinitionBindingModel,
    ObservationModel,
)
from regent.infrastructure.static_app_publisher import StaticAppPublisher
from regent.model import ModelProvider


class GeneratedStaticApp(BaseModel):
    app_name: str = Field(min_length=1, max_length=120)
    index_html: str = Field(min_length=100)
    styles_css: str = Field(min_length=50)
    app_js: str = Field(min_length=20)


@dataclass(frozen=True, slots=True)
class PreviewReceipt:
    id: uuid.UUID
    app_project_id: uuid.UUID
    goal_id: uuid.UUID
    status: str
    source_hash: str | None
    preview_endpoint: str | None
    verification_checks: list[dict[str, object]]


class AppPreviewService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        provider: ModelProvider,
        preview_root: Path,
    ) -> None:
        self._sessions = sessions
        self._provider = provider
        self._publisher = StaticAppPublisher(preview_root)

    async def generate(
        self, project_id: uuid.UUID, goal_id: uuid.UUID, *, actor: str
    ) -> PreviewReceipt:
        release_id = uuid.uuid4()
        async with self._sessions() as session, session.begin():
            existing = await session.scalar(
                select(AppPreviewReleaseModel).where(AppPreviewReleaseModel.goal_id == goal_id)
            )
            if existing is not None and existing.status == "PREVIEW_READY":
                return self._receipt(existing)
            project = await session.get(AppProjectModel, project_id)
            goal = await session.get(GoalModel, goal_id)
            if project is None or goal is None or goal.app_project_id != project_id:
                raise DomainError(ErrorCode.NOT_FOUND, "app project goal not found")
            spec = await session.scalar(
                select(GoalSpecModel)
                .where(GoalSpecModel.goal_id == goal_id)
                .order_by(GoalSpecModel.version.desc())
            )
            if spec is None or spec.status != "FROZEN" or goal.status not in {"READY", "ACTIVE"}:
                raise DomainError(ErrorCode.INVALID_STATE, "confirmed goal is required")
            if existing is None:
                release = AppPreviewReleaseModel(
                    id=release_id,
                    app_project_id=project_id,
                    goal_id=goal_id,
                    status="GENERATING",
                    manifest_json={},
                    verification_checks=[],
                    created_by=actor,
                )
                session.add(release)
            else:
                release_id = existing.id
                existing.status = "GENERATING"
                existing.failure_code = None
                existing.failure_summary = None
            prompt = {
                "app_name": project.name,
                "product_intent": project.product_intent,
                "goal": goal.original_input,
                "target_users": goal.metadata_json.get("target_users"),
                "problem": goal.metadata_json.get("problem"),
                "first_deliverable": goal.metadata_json.get("first_deliverable"),
                "constraints": spec.explicit_constraints,
                "success_criteria": spec.success_criteria,
            }
        try:
            generated = await self._provider.generate_structured(
                system_prompt=(
                    "Generate a polished, responsive, accessible static web App for real preview "
                    "testing. Return complete index_html, styles_css, and app_js. Use no external "
                    "resources, network calls, frameworks, or placeholder TODOs. index_html must "
                    "reference ./styles.css and ./app.js, contain a semantic <main>, and put "
                    "data-regent-event on the primary activation action. JavaScript may only "
                    "provide local interaction. Make the product immediately understandable."
                ),
                user_prompt=str(prompt),
                response_model=GeneratedStaticApp,
            )
            output = generated.output
            published = self._publisher.publish(
                project_id,
                release_id,
                {
                    "index.html": output.index_html,
                    "styles.css": output.styles_css,
                    "app.js": output.app_js
                    + (
                        "\n;document.querySelectorAll('[data-regent-event]').forEach((el)=>{"
                        "el.addEventListener('click',()=>{const event_id=(globalThis.crypto&&"
                        "typeof globalThis.crypto.randomUUID==='function')?"
                        "globalThis.crypto.randomUUID():String(Date.now())+'-'+Math.random();"
                        f"fetch('/v1/preview-releases/{release_id}/events',{{method:'POST',"
                        "headers:{'Content-Type':'application/json'},"
                        "body:JSON.stringify({event_id,event_name:'activation'})})"
                        ".catch(()=>{});});});"
                    ),
                },
            )
            endpoint = f"/preview/{project_id}/{release_id}/"
            async with self._sessions() as session, session.begin():
                locked = await session.get(AppPreviewReleaseModel, release_id, with_for_update=True)
                if locked is None or locked.status != "GENERATING":
                    raise DomainError(ErrorCode.INVALID_STATE, "preview release cannot complete")
                locked.status = "PREVIEW_READY"
                locked.source_hash = published.source_hash
                locked.manifest_json = published.manifest
                locked.workspace_locator = str(published.root)
                locked.preview_endpoint = endpoint
                locked.verification_checks = published.checks
                locked.model_ref = generated.model
                await self._append_event(
                    session,
                    project_id,
                    "PREVIEW_READY",
                    "可访问的 App 预览已经生成并通过离线静态验证。",
                    {"preview_release_id": str(release_id), "endpoint": endpoint},
                )
                await session.flush()
                return self._receipt(locked)
        except Exception as exc:
            async with self._sessions() as session, session.begin():
                failed = await session.get(AppPreviewReleaseModel, release_id)
                if failed is not None:
                    failed.status = "FAILED"
                    failed.failure_code = "STATIC_PREVIEW_GENERATION_FAILED"
                    failed.failure_summary = f"{type(exc).__name__}: {exc}"[:4000]
            raise

    async def evaluate(
        self,
        release_id: uuid.UUID,
        *,
        minimum_samples: int,
        activation_threshold: int,
        actor: str,
    ) -> GateEvaluationModel:
        async with self._sessions() as session, session.begin():
            release = await session.get(AppPreviewReleaseModel, release_id)
            if release is None or release.status != "PREVIEW_READY":
                raise DomainError(ErrorCode.INVALID_STATE, "ready preview release is required")
            definition = {
                "metric_key": "activation",
                "definition_version": "preview-activation-v1",
                "observation_source": f"preview:{release_id}",
                "aggregation": "COUNT",
                "comparison": "GTE",
                "threshold": float(activation_threshold),
                "minimum_samples": minimum_samples,
                "exclude_bots": True,
                "exclude_internal": True,
                "value_field": "value",
            }
            definition_hash = canonical_hash(definition)
            binding = await session.scalar(
                select(MetricDefinitionBindingModel).where(
                    MetricDefinitionBindingModel.goal_id == release.goal_id,
                    MetricDefinitionBindingModel.metric_key == "activation",
                    MetricDefinitionBindingModel.definition_version == "preview-activation-v1",
                )
            )
            if binding is None:
                session.add(
                    MetricDefinitionBindingModel(
                        id=uuid.uuid4(),
                        goal_id=release.goal_id,
                        deployment_id=None,
                        preview_release_id=release.id,
                        metric_key="activation",
                        definition_version="preview-activation-v1",
                        definition_json=definition,
                        definition_hash=definition_hash,
                        created_by=actor,
                    )
                )
            observations = list(
                await session.scalars(
                    select(ObservationModel).where(
                        ObservationModel.goal_id == release.goal_id,
                        ObservationModel.metric_name == "activation",
                        ObservationModel.definition_version == "preview-activation-v1",
                        ObservationModel.source == f"preview:{release_id}",
                        ObservationModel.is_bot.is_(False),
                        ObservationModel.is_internal.is_(False),
                    )
                )
            )
            count = len(observations)
            enough = count >= minimum_samples
            passed = enough and count >= activation_threshold
            status = "PASSED" if passed else ("FAILED" if enough else "INSUFFICIENT_EVIDENCE")
            result = {
                "metrics": [
                    {
                        "metric_key": "activation",
                        "sample_count": count,
                        "minimum_samples": minimum_samples,
                        "aggregation": "COUNT",
                        "aggregate": float(count),
                        "comparison": "GTE",
                        "threshold": float(activation_threshold),
                        "enough_evidence": enough,
                        "passed": passed,
                    }
                ]
            }
            evidence = {
                "definition_hash": definition_hash,
                "observations": sorted(str(item.id) for item in observations),
                "result": result,
            }
            digest = canonical_hash({"preview_release_id": str(release_id), **evidence})
            existing = await session.scalar(
                select(GateEvaluationModel).where(GateEvaluationModel.input_digest == digest)
            )
            if existing is not None:
                return existing
            gate = GateEvaluationModel(
                id=uuid.uuid4(),
                goal_id=release.goal_id,
                deployment_id=None,
                preview_release_id=release.id,
                status=status,
                input_digest=digest,
                policy_version="preview-activation-gate-v1",
                result_json=result,
                observation_ids=[str(item.id) for item in observations],
                evidence_digest=canonical_hash(evidence),
                created_by=actor,
            )
            session.add(gate)
            await session.flush()
            return gate

    async def get(self, release_id: uuid.UUID) -> PreviewReceipt:
        async with self._sessions() as session:
            release = await session.get(AppPreviewReleaseModel, release_id)
            if release is None:
                raise DomainError(ErrorCode.NOT_FOUND, "preview release not found")
            return self._receipt(release)

    async def _append_event(
        self,
        session: AsyncSession,
        project_id: uuid.UUID,
        message_type: str,
        content: str,
        metadata: dict[str, str],
    ) -> None:
        conversation = await session.scalar(
            select(ConversationModel).where(ConversationModel.app_project_id == project_id)
        )
        if conversation is None:
            return
        last = await session.scalar(
            select(ConversationMessageModel.ordinal)
            .where(ConversationMessageModel.conversation_id == conversation.id)
            .order_by(ConversationMessageModel.ordinal.desc())
            .limit(1)
        )
        session.add(
            ConversationMessageModel(
                id=uuid.uuid4(),
                conversation_id=conversation.id,
                ordinal=(last or 0) + 1,
                role="EVENT",
                message_type=message_type,
                content=content,
                metadata_json=metadata,
                created_by="regent-core",
            )
        )

    @staticmethod
    def _receipt(model: AppPreviewReleaseModel) -> PreviewReceipt:
        return PreviewReceipt(
            model.id,
            model.app_project_id,
            model.goal_id,
            model.status,
            model.source_hash,
            model.preview_endpoint,
            model.verification_checks,
        )

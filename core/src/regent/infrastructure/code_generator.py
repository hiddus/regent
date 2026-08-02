import hashlib
import json
import tempfile
import uuid
from enum import StrEnum
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from pydantic import BaseModel, Field, model_validator

from regent.agent.tools import WorkspaceToolkit
from regent.agent.verification import VerificationAgent
from regent.application.delivery_rejection import DeliveryRejection
from regent.application.p1_contracts import (
    FileChange,
    FileChangeSet,
    FileMode,
    FileOperation,
)
from regent.application.p1_ports import GeneratedFileChangeSet
from regent.domain.errors import DomainError, ErrorCode
from regent.infrastructure.artifact_store import FileArtifactStore
from regent.infrastructure.sandbox import build_agent_sandbox
from regent.application.goal_anchor_service import build_goal_anchored_prompt
from regent.infrastructure.html_evidence import (
    ensure_semantic_main,
    inject_observed_entries,
)
from regent.model import ModelProvider


class SourceFileOperation(StrEnum):
    CREATE = "CREATE"
    REPLACE = "REPLACE"
    DELETE = "DELETE"


class GeneratedSourceFile(BaseModel):
    relative_path: str = Field(min_length=1, max_length=512)
    operation: SourceFileOperation
    content: str | None = None
    expected_previous_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    executable: bool = False
    media_type: str = "text/plain"
    rationale: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_content(self) -> "GeneratedSourceFile":
        if self.operation is SourceFileOperation.DELETE and self.content is not None:
            raise ValueError("DELETE cannot contain content")
        if self.operation is not SourceFileOperation.DELETE and self.content is None:
            raise ValueError("CREATE and REPLACE require complete content")
        return self


class GeneratedSourceBundle(BaseModel):
    files: list[GeneratedSourceFile] = Field(min_length=1)


def _build_system_prompt(
    *,
    goal_text: str = "",
    first_deliverable: str = "",
    success_criteria: dict | None = None,
) -> str:
    """Build a goal-aware system prompt.  The original user goal is placed at
    the very top so the LLM understands *what* it is building before reading
    structural constraints.
    """
    goal_block = ""
    if goal_text:
        goal_block = (
            "══════ PRIMARY OBJECTIVE ══════\n"
            f"The user's original goal: {goal_text}\n"
        )
        if first_deliverable:
            goal_block += f"First deliverable: {first_deliverable}\n"
        if success_criteria:
            criteria_lines = "\n".join(
                f"  - {k}: {v}" for k, v in success_criteria.items()
            )
            goal_block += f"Success criteria:\n{criteria_lines}\n"
        goal_block += (
            "Every file you generate MUST directly serve this goal. "
            "If the goal says 'timestamp', the page MUST show a timestamp. "
            "If the goal says 'news digest', the page MUST show news items. "
            "Do NOT generate unrelated templates, forms, or demo stubs.\n"
            "══════════════════════════════════\n\n"
        )
    return goal_block + _BASE_PROMPT


_BASE_PROMPT = """You are Regent Code Generation Adapter v2. Generate complete UTF-8 source files for
the frozen python-web-v1 plan. Return only the requested structured object. Do not emit patches,
shell commands, artifact URIs, secrets, or files outside planned_paths. DELETE has no content.
REPLACE must include expected_previous_hash. Generated code must not import Regent Core.

Project structure is mandatory:
- Every project MUST include requirements.txt (even if empty or just comments).
- Every project MUST include README.md with project description and run instructions.
- If the plan includes test paths, generate tests/ directory with at least one test file.
- src/app.py MUST be a runnable WSGI/ASGI application (Flask, FastAPI, Starlette, etc.),
  NOT a http.server.SimpleHTTPRequestHandler or trivial static file server.
- The application must have real business logic matching the plan's product outcome.

Product fidelity is mandatory:
- Implement the frozen requirement deliverable and success criteria from the plan (titles,
  sections, lists, copy, and primary user journey). A bare "Welcome" / "Activation Page"
  stub is forbidden when the plan describes a richer product (for example a news digest
  must render a visible list of at least the required headline items with source labels
  and outbound links).
- When the plan includes observed_evidence_entries or similar evidence payloads, render
  those headlines/links/summaries in the UI. Do not invent substitute news when observed
  entries exist.
- Placeholder / fake user profiles / hard-coded demo cards are FORBIDDEN unless the Goal
  explicitly requests a demo mockup (acceptance_contract.delivery_policy == "demo").
  If live data is unavailable, implement real persistence + API endpoints that return
  empty collections with honest empty states — never invent fake records.
- Target is delivery of the Goal, not a demo: pass means GoalSpec success criteria /
  first_deliverable are met for a real user. Capability delivery-review-v1 will reject
  Goal-not-attained surfaces and unstyled browser-default dumps before preview publish.
- Every HTML page MUST wrap primary content in a single <main> landmark (not only
  <body>/<div>).
- Visible copy MUST be substantial: at least 120 Chinese characters (or 80 Latin) of
  real product text inside <main> — title, short help, timezone/context, and how to use.
  Sparse clock-only / one-liner pages fail min-visible-text.
- Include product structure signals: use semantic tags such as <section>, <article>,
  <ul>/<ol>, or <nav> inside <main> (product-structure check).
- Prefer Flask (or FastAPI) with at least one domain JSON route (for example
  GET /api/now returning {"beijing_time": "..."} or todo CRUD). Pure
  send_from_directory / StaticFiles-only backends fail forbid-pure-static-backend.
  Never use http.server / SimpleHTTPRequestHandler.
- Visual product quality is mandatory: include a real stylesheet (<style> and/or
  styles.css) with intentional layout (max-width, spacing, typography, color, list/card
  treatment). Bare default-browser black text + blue underlined links is a failed
  delivery.
- If acceptance_contract.delivery_policy is goal_attainment_retry, prior attempt failed
  review; fix the listed delivery_gap_reasons and do not emit another unreliable surface.

Forbidden patterns (will be rejected by delivery review):
- http.server.SimpleHTTPRequestHandler or socketserver.TCPServer as the application
- Pure static file serving without business logic (Flask/FastAPI send_from_directory /
  StaticFiles-only apps with no domain routes/models are rejected)
- Single-file projects without requirements.txt
- Placeholder-only content (lorem ipsum, fake users, hard-coded demo cards, "sample")
- Unrendered template markers in any HTML/static file: literal "{{", "{%", or "{#".
  Emit fully rendered HTML only. If the server uses Jinja/Mustache, templates must be
  rendered to concrete HTML before write, or use plain HTML files with no template syntax.
  Leaving raw Jinja in templates/index.html (or any .html) is an automatic fail
  (forbid-unrendered-templates / SMOKE_FAILED).

Activation instrumentation (additional, not a substitute for the product):
- If an HTML page is generated, also include a user-visible control with data-regent-event
  marking the core user task (for example data-regent-event="activation"). Preview
  deployment rejects pages without this attribute, but the attribute alone is not a
  valid product."""


class ArtifactBackedCodeGenerator:
    generator_type = "artifact-backed"
    generator_ref = "artifact-backed-code-generator-v1"
    prompt_version = "code-generation-v1"

    def __init__(
        self,
        provider: ModelProvider,
        artifacts: FileArtifactStore,
        *,
        semantic_alignment_enabled: bool = False,
        workspace_root: Path | None = None,
        enforce_delivery_verification: bool = True,
    ) -> None:
        self._provider = provider
        self._artifacts = artifacts
        # Opt-in only. Default False: not quality verification, not fail-closed.
        # See validate_goal_alignment_semantic / REGENT_GOAL_SEMANTIC_ALIGNMENT_ENABLED.
        self._semantic_alignment_enabled = semantic_alignment_enabled
        self._workspace_root = (
            workspace_root or Path(tempfile.gettempdir()) / "regent-artifact-backed"
        ).resolve()
        # CD-1.4 / CD-2.2: VerificationAgent becomes the unified delivery gate for
        # BOTH generators (not an agentic-only capability). Callers that only need
        # raw artifact materialization (e.g. narrow unit tests) may opt out.
        self._enforce_delivery_verification = enforce_delivery_verification

    async def generate(
        self,
        plan: dict[str, Any],
        *,
        on_progress: Any = None,
    ) -> GeneratedFileChangeSet:
        planned_paths = set(plan.get("planned_paths", []))
        if not planned_paths:
            raise DomainError(
                ErrorCode.POLICY_DENIED, "generation plan must freeze planned paths"
            )
        if on_progress is not None:
            await on_progress("正在请求模型生成应用代码…")
        # GAC-GA: GoalAnchor — inject original goal text into user prompt
        # so the LLM cannot ignore what the user actually asked for.
        acceptance = plan.get("acceptance_contract") or {}
        goal_text = plan.get("goal_anchor_text") or ""
        first_deliverable = str(
            acceptance.get("first_deliverable") or ""
        )
        success_criteria = acceptance.get("success_criteria")
        # Build a goal-aware system prompt: original goal at the top,
        # then structural constraints.
        system_prompt = _build_system_prompt(
            goal_text=goal_text,
            first_deliverable=first_deliverable,
            success_criteria=success_criteria,
        )
        user_prompt = json.dumps(plan, ensure_ascii=False)
        if goal_text:
            user_prompt = build_goal_anchored_prompt(
                user_prompt,
                goal_text=goal_text,
                success_criteria=success_criteria,
                first_deliverable=first_deliverable,
                retry_context=self._build_retry_context(acceptance),
            )
        response = await self._provider.generate_structured(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            response_model=GeneratedSourceBundle,
        )
        scope = uuid.UUID(str(plan["hypothesis_decision_id"]))
        changes: list[FileChange] = []
        seen: set[str] = set()
        # Collect generated HTML for semantic alignment check
        generated_htmls: list[str] = []
        # CD-1.4/CD-2.2: mirror final (post-enhancement) text so the unified
        # verification gate reviews exactly what will be shipped.
        materialized: dict[str, str] = {}
        for generated in response.output.files:
            normalized = generated.relative_path.replace("\\", "/")
            from regent.application.planned_path_policy import is_path_within_frozen_plan

            if not is_path_within_frozen_plan(normalized, planned_paths):
                # Soft-skip unsafe / unexpected paths (aligned with agentic).
                continue
            if normalized in seen:
                raise DomainError(
                    ErrorCode.POLICY_DENIED, f"duplicate generated path: {normalized}"
                )
            seen.add(normalized)
            if generated.operation is SourceFileOperation.DELETE:
                changes.append(
                    FileChange(
                        relative_path=normalized,
                        operation=FileOperation.DELETE,
                        expected_previous_hash=generated.expected_previous_hash,
                        rationale=generated.rationale,
                    )
                )
                continue
            # Enhance HTML with observed entries and semantic landmarks.
            # This is non-blocking enhancement; the downstream delivery-review-v1
            # capability performs the actual validation and gap recovery.
            content_bytes = generated.content.encode("utf-8") if generated.content is not None else b""
            if normalized.endswith((".html", ".htm")) and generated.content is not None:
                text = generated.content
                acceptance = plan.get("acceptance_contract") or {}
                entries = acceptance.get("observed_evidence_entries") or []
                if entries:
                    text = inject_observed_entries(text, list(entries))
                text = ensure_semantic_main(text)
                # Fail closed early: unrendered Jinja/Mustache never reach smoke/deploy.
                if "{{" in text or "{%" in text or "{#" in text:
                    raise DeliveryRejection(
                        reasons=[
                            "forbid-unrendered-templates: unrendered template markers "
                            "({{, {%, or {#) in HTML — emit fully rendered HTML only"
                        ],
                        producer_ref=self.generator_ref,
                    )
                content_bytes = text.encode("utf-8")
                generated_htmls.append(text)
            content = content_bytes
            materialized[normalized] = content.decode("utf-8", errors="replace")
            digest = hashlib.sha256(content).hexdigest()
            artifact = self._artifacts.put(scope, f"generated/{digest[:2]}/{digest}", content)
            changes.append(
                FileChange(
                    relative_path=normalized,
                    operation=FileOperation(generated.operation.value),
                    content_artifact_uri=artifact.uri,
                    content_hash=artifact.content_hash,
                    expected_previous_hash=generated.expected_previous_hash,
                    mode=FileMode.EXECUTABLE if generated.executable else FileMode.REGULAR,
                    media_type=generated.media_type,
                    rationale=generated.rationale,
                )
            )
        # Optional LLM semantic alignment is NOT quality verification and is
        # NOT fail-closed real verification. Default off (hot path must not
        # pay an extra LLM call after artifact-backed write). Opt-in via
        # REGENT_GOAL_SEMANTIC_ALIGNMENT_ENABLED / semantic_alignment_enabled.
        if self._semantic_alignment_enabled and goal_text and generated_htmls:
            from regent.application.goal_anchor_service import (
                validate_goal_alignment_semantic,
            )

            combined_html = "\n".join(generated_htmls)
            semantic_result = await validate_goal_alignment_semantic(
                combined_html,
                goal_text,
                provider=self._provider,
                first_deliverable=first_deliverable,
            )
            if not semantic_result.aligned:
                reason = "; ".join(semantic_result.details[:2])
                raise DeliveryRejection(
                    reasons=[
                        f"goal-semantic-alignment: score={semantic_result.score:.0%} — {reason}"
                    ],
                    producer_ref=self.generator_ref,
                )
        if self._enforce_delivery_verification:
            await self._verify_or_reject(
                acceptance=acceptance,
                success_criteria=success_criteria,
                materialized=materialized,
                scope=scope,
            )
        if not changes:
            raise DeliveryRejection(
                reasons=["empty-changeset: no materializable files after planned-path filter"],
                gap_kind="empty-changeset",
                producer_ref=self.generator_ref,
            )
        change_set = FileChangeSet(
            changes=changes,
            generator_ref=self.generator_ref,
            prompt_version=self.prompt_version,
        )
        return GeneratedFileChangeSet(
            output=change_set,
            model_ref=response.model,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
        )

    async def _verify_or_reject(
        self,
        *,
        acceptance: dict[str, Any],
        success_criteria: Any,
        materialized: dict[str, str],
        scope: uuid.UUID,
    ) -> None:
        """CD-1.4/CD-2.2: unified delivery gate — verify before returning changes.

        Materializes the exact shipped content into a draft workspace and runs
        the same ``VerificationAgent`` the agentic path uses. On failure the
        draft directory is retained (never discarded, AC4) and a typed
        ``DeliveryRejection`` is raised instead of a bare ``ValueError``.
        """
        draft_root = (self._workspace_root / "drafts" / str(scope)).resolve()
        toolkit = WorkspaceToolkit(draft_root, command_sandbox=build_agent_sandbox())
        for relative, text in materialized.items():
            toolkit.write_text(relative, text)
        run_smoke = bool(acceptance.get("batch_run_smoke", True))
        verdict = await VerificationAgent(toolkit).verify(
            acceptance_contract=acceptance,
            success_criteria=success_criteria,
            run_smoke=run_smoke,
        )
        if verdict.passed:
            return
        reasons = [f"{g.code}: {g.detail}" for g in verdict.gaps] or [verdict.summary]
        raise DeliveryRejection(
            reasons=reasons,
            draft_uri=draft_root.as_uri(),
            producer_ref=self.generator_ref,
        )

    @staticmethod
    def _build_retry_context(acceptance: dict[str, Any]) -> str:
        """Build retry context from delivery gaps, FailureEnvelope, and constraints."""
        gap_reasons = acceptance.get("delivery_gap_reasons") or []
        attempt = acceptance.get("delivery_gap_recovery_attempt") or 1
        constraints = list(acceptance.get("learned_constraints") or [])
        lessons = list(acceptance.get("failure_lessons") or [])
        envelopes = list(acceptance.get("failure_envelopes") or [])
        replan_nonce = str(acceptance.get("replan_nonce") or "").strip()
        if not gap_reasons and not constraints and not lessons and not envelopes:
            return ""
        lines: list[str] = []
        if int(attempt) > 1 or gap_reasons:
            lines.append(f"Previous attempt #{attempt} failed delivery review:")
            for reason in gap_reasons[:5]:
                lines.append(f"  - {reason}")
        if envelopes:
            lines.append("Real build/test/smoke failures (prefer these over gap reasons):")
            for env in envelopes[:5]:
                if not isinstance(env, dict):
                    continue
                stage = env.get("stage") or "unknown"
                summary = env.get("error_summary") or env.get("summary") or ""
                code = env.get("error_code") or ""
                prefix = f"[{stage}]"
                if code:
                    prefix = f"[{stage}/{code}]"
                lines.append(f"  - {prefix} {str(summary)[:800]}")
        if replan_nonce:
            lines.append(f"Replan nonce: {replan_nonce}")
        if constraints:
            lines.append("Learned constraints (must satisfy):")
            for item in constraints[:8]:
                lines.append(f"  - {item}")
        if lessons:
            latest = lessons[-1] if isinstance(lessons[-1], dict) else {}
            digest = latest.get("lesson_digest") if isinstance(latest, dict) else None
            if digest:
                lines.append(f"Absorb failure lesson {digest} before regenerating.")
        lines.append(
            "You MUST fix these issues and ensure the output matches the goal."
        )
        return "\n".join(lines)


class ArtifactUriResolver:
    def __init__(self, artifact_root: Path) -> None:
        self._root = artifact_root.resolve()

    def __call__(self, uri: str) -> bytes:
        parsed = urlparse(uri)
        if parsed.scheme != "file" or parsed.netloc not in {"", "localhost"}:
            raise ValueError("only local artifact URIs are supported")
        raw_path = unquote(parsed.path)
        if len(raw_path) >= 3 and raw_path[0] == "/" and raw_path[2] == ":":
            raw_path = raw_path[1:]
        path = Path(raw_path).resolve()
        if self._root not in path.parents or not path.is_file() or path.is_symlink():
            raise ValueError("artifact URI escapes immutable artifact root")
        return path.read_bytes()

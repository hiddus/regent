import hashlib
import json
import uuid
from enum import StrEnum
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from pydantic import BaseModel, Field, model_validator

from regent.application.p1_contracts import (
    FileChange,
    FileChangeSet,
    FileMode,
    FileOperation,
)
from regent.application.p1_ports import GeneratedFileChangeSet
from regent.infrastructure.artifact_store import FileArtifactStore
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


_PROMPT = """You are Regent Code Generation Adapter v2. Generate complete UTF-8 source files for
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
- If live external data is unavailable and no observed entries are supplied, embed
  realistic placeholder content that still matches the product shape; do not collapse
  the UI to a single heading.
- Target is delivery of the Goal, not a demo: pass means GoalSpec success criteria /
  first_deliverable are met for a real user. Capability delivery-review-v1 will reject
  Goal-not-attained surfaces and unstyled browser-default dumps before preview publish.
- Every HTML page MUST wrap primary content in a single <main> landmark (not only
  <body>/<div>).
- Visual product quality is mandatory: include a real stylesheet (<style> and/or
  styles.css) with intentional layout (max-width, spacing, typography, color, list/card
  treatment). Bare default-browser black text + blue underlined links is a failed
  delivery.
- If acceptance_contract.delivery_policy is goal_attainment_retry, prior attempt failed
  review; fix the listed delivery_gap_reasons and do not emit another unreliable surface.

Forbidden patterns (will be rejected by delivery review):
- http.server.SimpleHTTPRequestHandler or socketserver.TCPServer as the application
- Pure static file serving without business logic
- Single-file projects without requirements.txt
- Placeholder-only content (lorem ipsum, "demo", "sample")

Activation instrumentation (additional, not a substitute for the product):
- If an HTML page is generated, also include a user-visible control with data-regent-event
  marking the core user task (for example data-regent-event="activation"). Preview
  deployment rejects pages without this attribute, but the attribute alone is not a
  valid product."""


class ArtifactBackedCodeGenerator:
    def __init__(self, provider: ModelProvider, artifacts: FileArtifactStore) -> None:
        self._provider = provider
        self._artifacts = artifacts

    async def generate(self, plan: dict[str, Any]) -> GeneratedFileChangeSet:
        planned_paths = set(plan.get("planned_paths", []))
        if not planned_paths:
            raise ValueError("generation plan must freeze planned paths")
        # GAC-GA: GoalAnchor — inject original goal text into user prompt
        # so the LLM cannot ignore what the user actually asked for.
        user_prompt = json.dumps(plan, ensure_ascii=False)
        goal_text = plan.get("goal_anchor_text") or ""
        if goal_text:
            acceptance = plan.get("acceptance_contract") or {}
            user_prompt = build_goal_anchored_prompt(
                user_prompt,
                goal_text=goal_text,
                success_criteria=acceptance.get("success_criteria"),
                first_deliverable=str(
                    acceptance.get("first_deliverable") or ""
                ),
                retry_context=self._build_retry_context(acceptance),
            )
        response = await self._provider.generate_structured(
            system_prompt=_PROMPT,
            user_prompt=user_prompt,
            response_model=GeneratedSourceBundle,
        )
        scope = uuid.UUID(str(plan["hypothesis_decision_id"]))
        changes: list[FileChange] = []
        seen: set[str] = set()
        for generated in response.output.files:
            normalized = generated.relative_path.replace("\\", "/")
            if normalized not in planned_paths:
                raise ValueError(f"generated path is outside frozen plan: {normalized}")
            if normalized in seen:
                raise ValueError(f"duplicate generated path: {normalized}")
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
                content_bytes = text.encode("utf-8")
            content = content_bytes
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
        change_set = FileChangeSet(
            changes=changes,
            generator_ref="artifact-backed-code-generator-v1",
            prompt_version="code-generation-v1",
        )
        return GeneratedFileChangeSet(
            output=change_set,
            model_ref=response.model,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
        )

    @staticmethod
    def _build_retry_context(acceptance: dict[str, Any]) -> str:
        """Build retry context from delivery gap reasons."""
        gap_reasons = acceptance.get("delivery_gap_reasons") or []
        attempt = acceptance.get("delivery_gap_recovery_attempt") or 1
        if not gap_reasons or int(attempt) <= 1:
            return ""
        lines = [f"Previous attempt #{attempt} failed delivery review:"]
        for reason in gap_reasons[:5]:
            lines.append(f"  - {reason}")
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

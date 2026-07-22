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


_PROMPT = """You are Regent Code Generation Adapter v1. Generate complete UTF-8 source files for
the frozen python-web-v1 plan. Return only the requested structured object. Do not emit patches,
shell commands, artifact URIs, secrets, or files outside planned_paths. DELETE has no content.
REPLACE must include expected_previous_hash. Generated code must not import Regent Core.
If an HTML page is generated, it MUST include a user-visible control with data-regent-event
attribute marking the core user task (for example data-regent-event=\"activation\"). Do not omit
this hook; Preview deployment will reject pages without it."""


class ArtifactBackedCodeGenerator:
    def __init__(self, provider: ModelProvider, artifacts: FileArtifactStore) -> None:
        self._provider = provider
        self._artifacts = artifacts

    async def generate(self, plan: dict[str, Any]) -> GeneratedFileChangeSet:
        planned_paths = set(plan.get("planned_paths", []))
        if not planned_paths:
            raise ValueError("generation plan must freeze planned paths")
        response = await self._provider.generate_structured(
            system_prompt=_PROMPT,
            user_prompt=json.dumps(plan, ensure_ascii=False),
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
            content = generated.content.encode("utf-8") if generated.content is not None else b""
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

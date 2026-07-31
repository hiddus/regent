import uuid
from pathlib import Path
from typing import Any

import pytest
from regent.infrastructure.artifact_store import FileArtifactStore
from regent.infrastructure.code_generator import (
    ArtifactBackedCodeGenerator,
    ArtifactUriResolver,
    GeneratedSourceBundle,
    GeneratedSourceFile,
    SourceFileOperation,
)
from regent.model import ModelUsage, StructuredModelResponse


class FakeProvider:
    def __init__(self, output: GeneratedSourceBundle) -> None:
        self.output = output
        self.calls = 0

    async def generate_structured(self, **_: Any) -> Any:
        self.calls += 1
        return StructuredModelResponse(
            output=self.output,
            usage=ModelUsage(input_tokens=10, output_tokens=20),
            model="fake-model",
        )


@pytest.mark.asyncio
async def test_generator_materializes_content_as_immutable_artifact(tmp_path: Path) -> None:
    bundle = GeneratedSourceBundle(
        files=[
            GeneratedSourceFile(
                relative_path="src/app.py",
                operation=SourceFileOperation.CREATE,
                content="print('hello')\n",
                rationale="entrypoint",
            )
        ]
    )
    store = FileArtifactStore(tmp_path / "artifacts")
    provider = FakeProvider(bundle)
    # Narrow unit test for artifact materialization only — opt out of the
    # full delivery-review gate (covered separately by delivery_review tests).
    generator = ArtifactBackedCodeGenerator(
        provider, store, enforce_delivery_verification=False
    )  # type: ignore[arg-type]
    result = await generator.generate(
        {
            "planned_paths": ["src/app.py"],
            "hypothesis_decision_id": str(uuid.uuid4()),
        }
    )
    change = result.output.changes[0]
    assert change.content_artifact_uri is not None
    assert ArtifactUriResolver(store.root)(change.content_artifact_uri) == b"print('hello')\n"
    assert result.model_ref == "fake-model"
    # Default path: only the generation call — no extra semantic-alignment LLM.
    assert provider.calls == 1


@pytest.mark.asyncio
async def test_generator_default_skips_semantic_alignment_llm(tmp_path: Path) -> None:
    """Hot path must not call LLM again for semantic alignment (default off)."""
    html = (
        "<html><body><main><h1>Clock</h1>"
        '<button data-regent-event="activation">Go</button></main></body></html>'
    )
    bundle = GeneratedSourceBundle(
        files=[
            GeneratedSourceFile(
                relative_path="index.html",
                operation=SourceFileOperation.CREATE,
                content=html,
                rationale="page",
                media_type="text/html",
            )
        ]
    )
    provider = FakeProvider(bundle)
    generator = ArtifactBackedCodeGenerator(
        provider,
        FileArtifactStore(tmp_path / "artifacts"),  # type: ignore[arg-type]
        enforce_delivery_verification=False,
    )
    await generator.generate(
        {
            "planned_paths": ["index.html"],
            "hypothesis_decision_id": str(uuid.uuid4()),
            "goal_text": "Show current timestamp",
            "first_deliverable": "A live clock page",
        }
    )
    assert provider.calls == 1
    assert generator._semantic_alignment_enabled is False


@pytest.mark.asyncio
async def test_generator_rejects_unplanned_or_duplicate_paths(tmp_path: Path) -> None:
    bundle = GeneratedSourceBundle(
        files=[
            GeneratedSourceFile(
                relative_path="outside.py",
                operation=SourceFileOperation.CREATE,
                content="bad",
                rationale="bad",
            )
        ]
    )
    generator = ArtifactBackedCodeGenerator(
        FakeProvider(bundle),
        FileArtifactStore(tmp_path / "artifacts"),  # type: ignore[arg-type]
    )
    with pytest.raises(ValueError):
        await generator.generate(
            {"planned_paths": ["inside.py"], "hypothesis_decision_id": str(uuid.uuid4())}
        )


def test_artifact_resolver_rejects_outside_root(tmp_path: Path) -> None:
    outside = tmp_path / "outside.txt"
    outside.write_text("secret")
    resolver = ArtifactUriResolver(tmp_path / "artifacts")
    with pytest.raises(ValueError):
        resolver(outside.as_uri())

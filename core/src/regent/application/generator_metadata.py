"""GQ-0/GQ-1 generator metadata protocol and fail-closed consistency (Tech-Spec §13.4)."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any, Literal, Mapping

from regent.domain.errors import DomainError, ErrorCode

GenerationStrategy = Literal["artifact-backed", "agentic"]

ARTIFACT_BACKED_REF = "artifact-backed-code-generator-v1"
ARTIFACT_BACKED_PROMPT = "code-generation-v1"
AGENTIC_REF = "agentic-generation-v1"
AGENTIC_PROMPT = "agentic-generation-v1"

STRATEGY_METADATA: Mapping[GenerationStrategy, dict[str, str]] = {
    "artifact-backed": {
        "generator_type": "artifact-backed",
        "generator_ref": ARTIFACT_BACKED_REF,
        "prompt_version": ARTIFACT_BACKED_PROMPT,
    },
    "agentic": {
        "generator_type": "agentic",
        "generator_ref": AGENTIC_REF,
        "prompt_version": AGENTIC_PROMPT,
    },
}


@dataclass(frozen=True, slots=True)
class GeneratorMismatchEvidence:
    """Evidence payload written when strategy / label / object type disagree."""

    expected_strategy: str
    expected_generator_type: str
    expected_generator_ref: str
    expected_prompt_version: str
    actual_generator_type: str | None
    actual_generator_ref: str | None
    actual_prompt_version: str | None
    actual_class: str
    plan_id: str | None
    run_id: str | None
    timestamp: str
    mismatch_fields: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def metadata_for_strategy(strategy: GenerationStrategy) -> dict[str, str]:
    return dict(STRATEGY_METADATA[strategy])


def read_generator_metadata(generator: Any) -> dict[str, str | None]:
    """Read frozen metadata from a generator instance (Protocol attributes)."""
    return {
        "generator_type": getattr(generator, "generator_type", None),
        "generator_ref": getattr(generator, "generator_ref", None),
        "prompt_version": getattr(generator, "prompt_version", None),
    }


def assert_generator_consistency(
    *,
    strategy: GenerationStrategy,
    generator: Any,
    plan_id: str | None = None,
    run_id: str | None = None,
    contract_generator_ref: str | None = None,
    contract_prompt_version: str | None = None,
) -> None:
    """Fail closed when label, object type, and strategy disagree.

    Never silently falls back to another generator.
    """
    expected = metadata_for_strategy(strategy)
    actual = read_generator_metadata(generator)
    mismatches: list[str] = []

    if actual["generator_type"] != expected["generator_type"]:
        mismatches.append("generator_type")
    if actual["generator_ref"] != expected["generator_ref"]:
        mismatches.append("generator_ref")
    if actual["prompt_version"] != expected["prompt_version"]:
        mismatches.append("prompt_version")
    if (
        contract_generator_ref is not None
        and contract_generator_ref != expected["generator_ref"]
    ):
        mismatches.append("contract.generator_ref")
    if (
        contract_prompt_version is not None
        and contract_prompt_version != expected["prompt_version"]
    ):
        mismatches.append("contract.prompt_version")
    if (
        contract_generator_ref is not None
        and actual["generator_ref"] is not None
        and contract_generator_ref != actual["generator_ref"]
    ):
        mismatches.append("contract_vs_object.generator_ref")

    if not mismatches:
        return

    evidence = GeneratorMismatchEvidence(
        expected_strategy=strategy,
        expected_generator_type=expected["generator_type"],
        expected_generator_ref=expected["generator_ref"],
        expected_prompt_version=expected["prompt_version"],
        actual_generator_type=actual["generator_type"],
        actual_generator_ref=actual["generator_ref"],
        actual_prompt_version=actual["prompt_version"],
        actual_class=type(generator).__name__,
        plan_id=plan_id,
        run_id=run_id,
        timestamp=datetime.now(UTC).isoformat(),
        mismatch_fields=tuple(mismatches),
    )
    raise DomainError(
        ErrorCode.GENERATOR_METADATA_MISMATCH,
        f"generator metadata mismatch ({', '.join(mismatches)}); "
        f"evidence={evidence.as_dict()}",
    )

import uuid
from pathlib import Path

import pytest
from regent.domain.errors import DomainError
from regent.infrastructure.self_improvement_sandbox import SelfImprovementSandbox


def test_self_improvement_candidate_is_isolated_and_compiles(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "feature.py").write_text("VALUE = 1\n", encoding="utf-8")
    sandbox = SelfImprovementSandbox(source, tmp_path / "candidates")
    result = sandbox.materialize(uuid.uuid4(), "feature.py", "VALUE = 2\n")
    assert (source / "feature.py").read_text(encoding="utf-8") == "VALUE = 1\n"
    assert (result.workspace / "feature.py").read_text(encoding="utf-8") == "VALUE = 2\n"
    assert result.baseline_hash != result.candidate_hash


def test_self_improvement_protects_evaluator_and_governance(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "permit_service.py").write_text("VALUE = 1\n", encoding="utf-8")
    sandbox = SelfImprovementSandbox(source, tmp_path / "candidates")
    with pytest.raises(DomainError):
        sandbox.materialize(uuid.uuid4(), "permit_service.py", "VALUE = 2\n")
    with pytest.raises(DomainError):
        sandbox.materialize(uuid.uuid4(), "../escape.py", "VALUE = 2\n")

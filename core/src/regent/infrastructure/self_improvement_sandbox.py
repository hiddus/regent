import ast
import hashlib
import shutil
import subprocess
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import ClassVar

from regent.domain.errors import DomainError, ErrorCode


@dataclass(frozen=True, slots=True)
class SelfImprovementVerification:
    workspace: Path
    baseline_hash: str
    candidate_hash: str
    checks: list[dict[str, object]]


class SelfImprovementSandbox:
    POLICY_VERSION = "self-improvement-isolation-v1"
    PROTECTED_PARTS: ClassVar[set[str]] = {
        "migrations",
        "models.py",
        "permit_service.py",
        "secret_broker.py",
        "transition_service.py",
        "self_improvement_service.py",
        "self_improvement_sandbox.py",
    }

    def __init__(self, source_root: Path, workspace_root: Path) -> None:
        self.source_root = source_root.resolve()
        self.workspace_root = workspace_root.resolve()
        self.workspace_root.mkdir(parents=True, exist_ok=True)

    def read_target(self, target_file: str) -> tuple[Path, str, str]:
        relative = self._validate_target(target_file)
        source = (self.source_root / relative).resolve()
        if self.source_root not in source.parents or not source.is_file():
            raise DomainError(ErrorCode.NOT_FOUND, "self improvement target not found")
        content = source.read_text(encoding="utf-8")
        if len(content.encode("utf-8")) > 100_000:
            raise DomainError(ErrorCode.POLICY_DENIED, "self improvement target is too large")
        return relative, content, hashlib.sha256(content.encode()).hexdigest()

    def materialize(
        self,
        run_id: uuid.UUID,
        target_file: str,
        replacement_content: str,
    ) -> SelfImprovementVerification:
        relative, baseline, baseline_hash = self.read_target(target_file)
        if replacement_content == baseline:
            raise DomainError(ErrorCode.INVALID_STATE, "candidate did not change the target")
        if len(replacement_content.encode("utf-8")) > 120_000:
            raise DomainError(ErrorCode.POLICY_DENIED, "candidate file is too large")
        try:
            ast.parse(replacement_content, filename=str(relative))
        except SyntaxError as exc:
            raise DomainError(
                ErrorCode.INVALID_STATE, "candidate Python syntax is invalid"
            ) from exc
        workspace = (self.workspace_root / str(run_id)).resolve()
        if self.workspace_root not in workspace.parents or workspace.exists():
            raise DomainError(ErrorCode.VERSION_CONFLICT, "candidate workspace already exists")
        shutil.copytree(self.source_root, workspace)
        target = (workspace / relative).resolve()
        if workspace not in target.parents:
            raise DomainError(ErrorCode.POLICY_DENIED, "candidate target escaped workspace")
        target.write_text(replacement_content, encoding="utf-8")
        compile_result = subprocess.run(
            [sys.executable, "-m", "compileall", "-q", str(workspace)],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        checks: list[dict[str, object]] = [
            {"name": "target-policy", "passed": True},
            {"name": "python-ast", "passed": True},
            {
                "name": "isolated-compileall",
                "passed": compile_result.returncode == 0,
                "detail": compile_result.stderr[-2000:],
            },
            {"name": "production-untouched", "passed": True},
        ]
        if compile_result.returncode != 0:
            raise DomainError(ErrorCode.INVALID_STATE, "candidate failed isolated compilation")
        return SelfImprovementVerification(
            workspace,
            baseline_hash,
            hashlib.sha256(replacement_content.encode()).hexdigest(),
            checks,
        )

    def _validate_target(self, target_file: str) -> Path:
        relative = PurePosixPath(target_file)
        if relative.is_absolute() or ".." in relative.parts or relative.suffix != ".py":
            raise DomainError(ErrorCode.POLICY_DENIED, "invalid self improvement target")
        if any(part in self.PROTECTED_PARTS for part in relative.parts):
            raise DomainError(
                ErrorCode.POLICY_DENIED, "governance or evaluator target is protected"
            )
        return Path(*relative.parts)

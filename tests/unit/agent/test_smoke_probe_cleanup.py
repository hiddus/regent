"""T5: smoke probe is cleaned up and does not linger in the workspace."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from regent.agent.verification import VerificationAgent


class _FakeToolkit:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.commands: list[str] = []

    async def run_command(self, command: str, *, timeout_seconds: int = 60) -> str:
        self.commands.append(command)
        if "compileall" in command:
            return "exit=0\nok"
        if ".regent_smoke_probe.py" in command:
            probe = self.root / ".regent_smoke_probe.py"
            assert probe.is_file(), "probe must exist before run"
            return "exit=0\nSMOKE_OK"
        return "exit=0\nok"


@pytest.mark.asyncio
async def test_t5_smoke_probe_cleaned_up(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("app = object()\n", encoding="utf-8")
    toolkit = _FakeToolkit(tmp_path)
    agent = VerificationAgent(toolkit)  # type: ignore[arg-type]
    result = await agent._smoke_http(
        {"app.py": "app = object()\n"},
        {"routes": ["/"]},
    )
    assert result["passed"] is True
    assert not (tmp_path / ".regent_smoke_probe.py").exists()
    assert any(".regent_smoke_probe.py" in c for c in toolkit.commands)

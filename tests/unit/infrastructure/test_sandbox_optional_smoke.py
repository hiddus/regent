"""Optional real Docker sandbox smoke — skipped when Docker is unavailable."""

from __future__ import annotations

import shutil
import subprocess

import pytest


def _docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        result = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            timeout=10,
            check=False,
        )
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


@pytest.mark.sandbox
@pytest.mark.skipif(not _docker_available(), reason="Docker not available")
def test_sandbox_docker_info_smoke() -> None:
    result = subprocess.run(
        ["docker", "run", "--rm", "--network", "none", "hello-world"],
        capture_output=True,
        timeout=60,
        check=False,
        text=True,
    )
    assert result.returncode == 0, result.stderr

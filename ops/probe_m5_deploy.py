"""One-shot S0 post-deploy check for M0–M5 imports + funnel."""
from __future__ import annotations

from pathlib import Path

import paramiko
from dotenv import dotenv_values

CFG = dotenv_values(Path(__file__).resolve().parents[1] / ".env")
HOST = CFG.get("SERVER_IP") or "118.31.171.159"
PASSWORD = CFG["LOGIN_PASSWORD"]


def main() -> None:
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(HOST, username="root", password=PASSWORD, timeout=30)

    def run(cmd: str, timeout: int = 90) -> tuple[str, int]:
        _, o, e = ssh.exec_command(cmd, timeout=timeout)
        out = (o.read() + e.read()).decode("utf-8", "replace")
        return out, o.channel.recv_exit_status()

    py = (
        "from regent.agent.primary_failure import PrimaryFailureCode; "
        "from regent.agent.run_ledger import AgentRunLedger; "
        "from regent.agent.runtime_profile_v1 import CERTIFIED_RUNTIME_PROFILES_V1; "
        "from regent.agent.skills import list_builtin_skill_ids; "
        "from regent.agent.file_manifest import MANIFEST_POLICY_VERSION; "
        "from regent.model import ModelTruncatedError, ToolCallInvalidError; "
        "from regent.config import get_settings; "
        "s=get_settings(); "
        "print('strategy', s.generation_strategy); "
        "print('canary', s.generation_strategy_canary_percent, s.generation_strategy_canary_gate); "
        "print('skills', list_builtin_skill_ids()); "
        "print('profiles', [p.name for p in CERTIFIED_RUNTIME_PROFILES_V1]); "
        "print('manifest', MANIFEST_POLICY_VERSION); "
        "print('M5_IMPORT_OK')"
    )
    out, code = run(f"docker exec regent-api python -c \"{py}\"")
    print("imports:", out.strip(), "exit=", code)

    out, code = run(
        "docker exec -w /app regent-api python ops/probe_funnel_health.py 2>&1 | tail -50",
        timeout=120,
    )
    print("funnel:\n", out.strip(), "\nexit=", code)

    out, _ = run(
        'docker ps --format "{{.Names}} {{.Status}}" | grep -E "regent-(api|worker)"'
    )
    print("containers:\n", out.strip())
    ssh.close()


if __name__ == "__main__":
    main()

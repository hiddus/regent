"""Sync CONDITIONAL v2 docs to server release tree (docs only, no service restart)."""
from __future__ import annotations

import os
from pathlib import Path

import paramiko

ROOT = Path(__file__).resolve().parent
FILES = [
    "Regent-PRD-v2.md",
    "Regent-Technical-Spec-v2.md",
    "Regent-Measurement-Decision-Framework.md",
    "docs/p2-platform-plan.md",
    "docs/p1-remaining-coding-plan.md",
    "docs/README.md",
    "docs/definitions/REGENT-DEFINITION-1.0.txt",
    "docs/definitions/REGENT-DEFINITION-1.0.sha256",
    "docs/definitions/README.md",
    "docs/appendices/State-Machines-and-Invariants.md",
    "docs/appendices/Durable-Execution-and-External-Effects.md",
    "docs/appendices/Security-Tenancy-and-Recovery.md",
]


def load_env() -> tuple[str, str, str]:
    server = os.environ.get("SERVER_IP", "")
    user = os.environ.get("LOGIN_USER", "")
    password = os.environ.get("LOGIN_PASSWORD", "")
    env_path = ROOT / ".env"
    if env_path.is_file():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            if k == "SERVER_IP" and not server:
                server = v
            elif k == "LOGIN_USER" and not user:
                user = v
            elif k == "LOGIN_PASSWORD" and not password:
                password = v
    if not all([server, user, password]):
        raise RuntimeError("missing SERVER_IP/LOGIN_USER/LOGIN_PASSWORD")
    return server, user, password


def main() -> None:
    server, user, password = load_env()
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(server, username=user, password=password, timeout=20)
    sftp = client.open_sftp()

    # Resolve current release dir
    _, stdout, _ = client.exec_command("readlink -f /opt/regent/current")
    current = stdout.read().decode().strip() or "/opt/regent/current"
    print(f"sync target: {current}")

    for rel in FILES:
        local = ROOT / rel
        if not local.is_file():
            raise FileNotFoundError(local)
        remote = f"{current}/{rel.replace(chr(92), '/')}"
        remote_dir = remote.rsplit("/", 1)[0]
        client.exec_command(f"mkdir -p {remote_dir}")
        sftp.put(str(local), remote)
        print(f"  put {rel}")

    # Also keep a dated docs snapshot under /opt/regent/docs-sync
    stamp = "20260722-conditional-r2"
    snap = f"/opt/regent/docs-sync/{stamp}"
    client.exec_command(f"mkdir -p {snap}/docs/appendices")
    for rel in FILES:
        local = ROOT / rel
        remote = f"{snap}/{rel.replace(chr(92), '/')}"
        remote_dir = remote.rsplit("/", 1)[0]
        client.exec_command(f"mkdir -p {remote_dir}")
        sftp.put(str(local), remote)
    client.exec_command(f"ln -sfn {snap} /opt/regent/docs-sync/current")
    print(f"snapshot: {snap}")

    sftp.close()
    client.close()
    print("docs sync done (no container restart)")


if __name__ == "__main__":
    main()

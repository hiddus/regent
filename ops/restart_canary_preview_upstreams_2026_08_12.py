"""Restart dead preview upstream processes for the two canary deployments."""
from __future__ import annotations

from pathlib import Path

import paramiko
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
CFG = {
    (k.lstrip("\ufeff") if isinstance(k, str) else k): v
    for k, v in dotenv_values(ROOT / ".env").items()
}
HOST = CFG.get("SERVER_IP") or "118.31.171.159"
PASSWORD = CFG.get("LOGIN_PASSWORD") or ""

REMOTE = r'''
import json, os, signal, subprocess, time
from pathlib import Path

deploys = [
  "77c87a14-f412-469a-9a37-b2eb13a4433d",
  "da3cf65e-337f-43b9-ba77-b3e071f18be0",
]
base = Path("/var/lib/regent/workspaces/previews/runtime")
out = []
for did in deploys:
    root = base / did
    port_f = root / ".regent-preview-port"
    pid_f = root / ".regent-preview.pid"
    log_f = root / ".regent-preview.log"
    entry = (root / "entry.txt").read_text(encoding="utf-8").strip() if (root / "entry.txt").exists() else ""
    port = int(port_f.read_text().strip()) if port_f.exists() else 0
    old_pid = pid_f.read_text().strip() if pid_f.exists() else ""
    # kill stale
    if old_pid.isdigit():
        try:
            os.kill(int(old_pid), 0)
            alive = True
        except OSError:
            alive = False
        if alive:
            try:
                os.kill(int(old_pid), signal.SIGTERM)
                time.sleep(0.5)
            except OSError:
                pass
    # prefer static http.server if index.html at root (canary is static)
    index = root / "index.html"
    src_app = root / "src" / "app.py"
    venv_py = root / ".preview-venv" / "bin" / "python"
    py = str(venv_py) if venv_py.exists() else "python3"
    env = os.environ.copy()
    env["PORT"] = str(port)
    # Serve static root with http.server — enough for canary HTML/CSS
    # Bind 0.0.0.0 so api container can reach via docker network hostnames if needed;
    # but preview proxy uses worker hostnames / 127.0.0.1 — start on worker via docker exec.
    cmd = [py, "-m", "http.server", str(port), "--bind", "0.0.0.0"]
    log = open(log_f, "a", encoding="utf-8")
    log.write(f"\n--- restart static http.server port={port} at {time.time()} ---\n")
    log.flush()
    proc = subprocess.Popen(
        cmd,
        cwd=str(root),
        env=env,
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    pid_f.write_text(str(proc.pid), encoding="utf-8")
    time.sleep(0.8)
    # local probe
    import urllib.request
    status = "unknown"
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=3) as r:
            body = r.read(200)
            status = f"{r.status}:{len(body)}"
    except Exception as exc:
        status = f"err:{type(exc).__name__}:{exc}"
    out.append({
        "deploy": did,
        "port": port,
        "pid": proc.pid,
        "entry": entry,
        "has_index": index.exists(),
        "has_src_app": src_app.exists(),
        "probe": status,
        "log_tail": log_f.read_text(encoding="utf-8", errors="replace")[-300:],
    })
print(json.dumps(out, ensure_ascii=False))
'''


def main() -> int:
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(
        HOST,
        username=CFG.get("LOGIN_USER") or "root",
        password=PASSWORD,
        timeout=40,
        banner_timeout=120,
        auth_timeout=60,
        allow_agent=False,
        look_for_keys=False,
    )
    try:
        # Preview upstream must run where proxy expects it. Proxy tries
        # regent-worker{,-2,-3} then 127.0.0.1. Start on host AND workers.
        for where, prefix in [
            ("host", ""),
            ("worker", "docker exec -i regent-worker "),
        ]:
            cmd = (
                (prefix + "python - <<'PYEOF'\n" + REMOTE + "\nPYEOF")
                if where == "worker"
                else ("python3 - <<'PYEOF'\n" + REMOTE + "\nPYEOF")
            )
            _, out, err = ssh.exec_command(cmd, timeout=120)
            body = out.read().decode("utf-8", "replace")
            e = err.read().decode("utf-8", "replace").strip()
            print(f"=== start_on_{where} ===")
            print(body[-2000:])
            if e:
                print("[stderr]", e[:800])
            print()

        # verify via api public path
        _, out, err = ssh.exec_command(
            "for d in 77c87a14-f412-469a-9a37-b2eb13a4433d da3cf65e-337f-43b9-ba77-b3e071f18be0; do "
            "echo === $d ===; curl -sS -m 10 -o /tmp/p.html -w '%{http_code} %{size_download}\\n' "
            "http://127.0.0.1:8000/preview/runtime/$d/; python3 - <<'PY'\n"
            "import re,pathlib\n"
            "html=pathlib.Path('/tmp/p.html').read_text(encoding='utf-8',errors='replace')\n"
            "vis=re.sub(r'<[^>]+>',' ',html); vis=re.sub(r'\\s+',' ',vis).strip()\n"
            "print('visible', len(vis)); print(vis[:120])\n"
            "PY; done",
            timeout=60,
        )
        print("=== api_proxy ===")
        print(out.read().decode("utf-8", "replace")[:2500])
        e = err.read().decode("utf-8", "replace").strip()
        if e:
            print("[stderr]", e[:500])
    finally:
        ssh.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

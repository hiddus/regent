#!/usr/bin/env python3
"""监控已在跑的协议 X 连载，完成后拉回产物（不重启）。"""

from __future__ import annotations

import sys
import tarfile
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _ssh import Remote  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
LOCAL_OUT = ROOT / "deploy" / "novel" / "artifacts" / "serial_ent_gender_100k_x"
CONTAINER = "regent-api"
REMOTE_OUT = "/tmp/serial_x_host"
CONTAINER_OUT = "/tmp/serial_x_out"


def _running(r: Remote) -> bool:
    # 镜像无 pgrep；用 /proc cmdline 探测，避免嵌套引号踩坑
    helper = (
        "import pathlib\n"
        "print(any(\n"
        "  b'run_serial_scene_live' in pathlib.Path(f'/proc/{p.name}/cmdline').read_bytes()\n"
        "  for p in pathlib.Path('/proc').iterdir() if p.name.isdigit()\n"
        "))\n"
    )
    r.run(
        f"docker exec {CONTAINER} sh -c \"cat > /tmp/_x_running.py <<'PY'\n{helper}PY\"",
        timeout=30,
    )
    res = r.run(f"docker exec {CONTAINER} python /tmp/_x_running.py", timeout=30)
    return "True" in (res.out or "")


def main() -> int:
    argv = list(sys.argv[1:])
    local_out = LOCAL_OUT
    i = 0
    while i < len(argv):
        if argv[i] == "--local-out" and i + 1 < len(argv):
            local_out = Path(argv[i + 1])
            i += 2
            continue
        i += 1

    r = Remote()
    t0 = time.time()
    deadline = t0 + 60 * 240
    if not _running(r):
        # 可能已结束
        chk = r.run(
            f"docker exec {CONTAINER} test -f {CONTAINER_OUT}/summary.json && echo HAS || echo NO",
            timeout=30,
        )
        if "HAS" not in (chk.out or ""):
            print("no running serial and no summary", file=sys.stderr)
            log = r.run(f"docker exec {CONTAINER} tail -n 100 /tmp/serial_x_run.log", timeout=30)
            print(log.out)
            return 1

    while time.time() < deadline:
        status = r.run(
            f"docker exec {CONTAINER} sh -c '"
            f"wc -c {CONTAINER_OUT}/novel.txt 2>/dev/null || echo 0; "
            f"cat {CONTAINER_OUT}/progress.json 2>/dev/null || echo no_progress; "
            f"test -f {CONTAINER_OUT}/summary.json && echo HAS_SUMMARY || echo NO_SUMMARY; "
            f"tail -n 8 /tmp/serial_x_run.log"
            f"'",
            timeout=60,
        )
        out = status.out or ""
        print(out[-2000:], flush=True)
        running = _running(r)
        print(f"watch elapsed_s={time.time()-t0:.0f} running={running}", flush=True)
        if "HAS_SUMMARY" in out and not running:
            break
        if not running and "HAS_SUMMARY" not in out:
            time.sleep(20)
            again = r.run(
                f"docker exec {CONTAINER} sh -c '"
                f"test -f {CONTAINER_OUT}/summary.json && echo HAS_SUMMARY || echo NO_SUMMARY; "
                f"tail -n 100 /tmp/serial_x_run.log"
                f"'",
                timeout=45,
            )
            print(again.out[-3000:] if again.out else "", flush=True)
            if "HAS_SUMMARY" in (again.out or ""):
                break
            print("serial stopped without summary", file=sys.stderr)
            return 1
        time.sleep(120)

    pull = f"""
set -e
rm -rf {REMOTE_OUT}
mkdir -p {REMOTE_OUT}
docker cp {CONTAINER}:{CONTAINER_OUT}/. {REMOTE_OUT}/
docker cp {CONTAINER}:/tmp/serial_x_run.log {REMOTE_OUT}/serial_x_run.log || true
tar -czf /tmp/serial_x_out.tgz -C {REMOTE_OUT} .
"""
    res = r.run(pull, timeout=180)
    print(res.out, flush=True)
    if not res.ok:
        print(res.err, file=sys.stderr)
        return res.code or 1

    local_out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        local_tgz = Path(tmp) / "out.tgz"
        sftp = r._client.open_sftp()
        try:
            sftp.get("/tmp/serial_x_out.tgz", str(local_tgz))
        finally:
            sftp.close()
        with tarfile.open(local_tgz, mode="r:gz") as tar:
            tar.extractall(local_out)

    print(f"pulled -> {local_out}", flush=True)
    print(f"elapsed_s={time.time() - t0:.1f}", flush=True)
    summary = local_out / "summary.json"
    if summary.exists():
        print(summary.read_text(encoding="utf-8"), flush=True)
    novel = local_out / "novel.txt"
    if novel.exists():
        print(f"novel_chars={len(novel.read_text(encoding='utf-8'))}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

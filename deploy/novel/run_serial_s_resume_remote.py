#!/usr/bin/env python3
"""续跑：把本地已有 novel 推到远端，从断点继续冲 10 万字。"""

from __future__ import annotations

import sys
import tarfile
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _ssh import Remote  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
LOCAL_OUT = ROOT / "deploy" / "novel" / "artifacts" / "serial_ent_gender_100k"
REMOTE_STAGE = "/tmp/serial_s_stage"
CONTAINER = "regent-api"
REMOTE_OUT = "/tmp/serial_s_out"
CONTAINER_OUT = "/tmp/serial_s_out"

FILES = (
    "core/src/regent/novel/domain/script_protocol.py",
    "core/src/regent/novel/experiments/__init__.py",
    "core/src/regent/novel/experiments/quality_ab.py",
    "deploy/novel/run_serial_s_live.py",
)


def main() -> int:
    r = Remote()
    with tempfile.TemporaryDirectory() as tmp:
        pack = Path(tmp) / "serial.tgz"
        with tarfile.open(pack, mode="w:gz") as tar:
            for rel in FILES:
                tar.add(ROOT / rel, arcname=rel.replace("\\", "/"))
            # 已有正文与章节账
            for name in ("novel.txt", "chapters.json", "summary.json"):
                p = LOCAL_OUT / name
                if p.exists():
                    tar.add(p, arcname=f"resume/{name}")
        print(f"upload pack bytes={pack.stat().st_size}")
        r.put(str(pack), f"{REMOTE_STAGE}.tgz")

    prep = f"""
set -e
rm -rf {REMOTE_STAGE}
mkdir -p {REMOTE_STAGE} {REMOTE_OUT}
tar -xzf {REMOTE_STAGE}.tgz -C {REMOTE_STAGE}
docker exec {CONTAINER} mkdir -p /app/core/src/regent/novel/experiments /app/core/src/regent/novel/domain {CONTAINER_OUT}
docker cp {REMOTE_STAGE}/core/src/regent/novel/experiments/. {CONTAINER}:/app/core/src/regent/novel/experiments/
docker cp {REMOTE_STAGE}/core/src/regent/novel/domain/script_protocol.py {CONTAINER}:/app/core/src/regent/novel/domain/script_protocol.py
docker cp {REMOTE_STAGE}/deploy/novel/run_serial_s_live.py {CONTAINER}:/tmp/run_serial_s_live.py
if [ -d {REMOTE_STAGE}/resume ]; then
  docker cp {REMOTE_STAGE}/resume/. {CONTAINER}:{CONTAINER_OUT}/
fi
"""
    res = r.run(prep, timeout=120)
    print(res.out)
    if not res.ok:
        print(res.err, file=sys.stderr)
        return 1

    run_cmd = f"""
set -e
docker exec -e NOVEL_QUALITY_AB_LIVE=1 {CONTAINER} python /tmp/run_serial_s_live.py --fragment f4_ent_gender --target-chars 105000 --max-chapters 65 --out {CONTAINER_OUT} --resume
docker cp {CONTAINER}:{CONTAINER_OUT}/. {REMOTE_OUT}/
tar -czf /tmp/serial_s_out.tgz -C {REMOTE_OUT} .
"""
    print("resuming serial live run...", flush=True)
    t0 = time.time()
    res = r.run(run_cmd, timeout=60 * 180)
    print((res.out or "")[-10000:])
    if res.err:
        print("stderr:", res.err[-4000:], file=sys.stderr)
    print(f"elapsed_s={time.time() - t0:.1f} code={res.code}")
    if not res.ok:
        return res.code or 1

    LOCAL_OUT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        local_tgz = Path(tmp) / "out.tgz"
        sftp = r._client.open_sftp()
        try:
            sftp.get("/tmp/serial_s_out.tgz", str(local_tgz))
        finally:
            sftp.close()
        with tarfile.open(local_tgz, mode="r:gz") as tar:
            tar.extractall(LOCAL_OUT)
    print(f"pulled -> {LOCAL_OUT}")
    summary = LOCAL_OUT / "summary.json"
    if summary.exists():
        print(summary.read_text(encoding="utf-8"))
    novel = LOCAL_OUT / "novel.txt"
    if novel.exists():
        print(f"novel_chars={len(novel.read_text(encoding='utf-8'))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""远端连跑协议 S 多章（默认冲 10 万字），拉回 novel.txt。"""

from __future__ import annotations

import json
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


def _pack_to(path: Path) -> None:
    with tarfile.open(path, mode="w:gz") as tar:
        for rel in FILES:
            tar.add(ROOT / rel, arcname=rel.replace("\\", "/"))


def main() -> int:
    argv = list(sys.argv[1:])
    local_out = LOCAL_OUT
    filtered: list[str] = []
    i = 0
    while i < len(argv):
        if argv[i] == "--local-out" and i + 1 < len(argv):
            local_out = Path(argv[i + 1])
            i += 2
            continue
        filtered.append(argv[i])
        i += 1
    extra = " ".join(filtered) or (
        "--fragment f4_ent_gender --target-chars 100000 --max-chapters 50 "
        f"--out {CONTAINER_OUT}"
    )
    r = Remote()
    with tempfile.TemporaryDirectory() as tmp:
        pack = Path(tmp) / "serial.tgz"
        _pack_to(pack)
        print(f"upload pack bytes={pack.stat().st_size}")
        r.put(str(pack), f"{REMOTE_STAGE}.tgz")

    prep = f"""
set -e
rm -rf {REMOTE_STAGE} {REMOTE_OUT}
mkdir -p {REMOTE_STAGE} {REMOTE_OUT}
tar -xzf {REMOTE_STAGE}.tgz -C {REMOTE_STAGE}
docker exec {CONTAINER} mkdir -p /app/core/src/regent/novel/experiments
docker exec {CONTAINER} mkdir -p /app/core/src/regent/novel/domain
docker cp {REMOTE_STAGE}/core/src/regent/novel/experiments/. {CONTAINER}:/app/core/src/regent/novel/experiments/
docker cp {REMOTE_STAGE}/core/src/regent/novel/domain/script_protocol.py {CONTAINER}:/app/core/src/regent/novel/domain/script_protocol.py
docker cp {REMOTE_STAGE}/deploy/novel/run_serial_s_live.py {CONTAINER}:/tmp/run_serial_s_live.py
docker exec {CONTAINER} rm -rf {CONTAINER_OUT}
docker exec {CONTAINER} mkdir -p {CONTAINER_OUT}
"""
    res = r.run(prep, timeout=120)
    print(res.out)
    if not res.ok:
        print(res.err, file=sys.stderr)
        return 1

    run_cmd = f"""
set -e
docker exec -e NOVEL_QUALITY_AB_LIVE=1 {CONTAINER} \\
  python /tmp/run_serial_s_live.py {extra}
docker cp {CONTAINER}:{CONTAINER_OUT}/. {REMOTE_OUT}/
tar -czf /tmp/serial_s_out.tgz -C {REMOTE_OUT} .
"""
    print("starting serial live run...", flush=True)
    print("extra:", extra)
    print("local_out:", local_out)
    t0 = time.time()
    # 约 40–50 章 × 每章数分钟，预留 3 小时
    res = r.run(run_cmd, timeout=60 * 180)
    print(res.out[-8000:] if res.out else "")
    if res.err:
        print("stderr:", res.err[-4000:], file=sys.stderr)
    print(f"elapsed_s={time.time() - t0:.1f} code={res.code}")
    if not res.ok:
        return res.code or 1

    local_out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        local_tgz = Path(tmp) / "out.tgz"
        sftp = r._client.open_sftp()
        try:
            sftp.get("/tmp/serial_s_out.tgz", str(local_tgz))
        finally:
            sftp.close()
        with tarfile.open(local_tgz, mode="r:gz") as tar:
            tar.extractall(local_out)

    print(f"pulled -> {local_out}")
    summary = local_out / "summary.json"
    if summary.exists():
        print(summary.read_text(encoding="utf-8"))
    novel = local_out / "novel.txt"
    if novel.exists():
        print(f"novel_chars={len(novel.read_text(encoding='utf-8'))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

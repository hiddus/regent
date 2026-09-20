#!/usr/bin/env python3
"""在远端 regent-api 容器内实跑 A/B/C，再拉回产物。不重启服务、不 sync_tree。"""

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
LOCAL_OUT = ROOT / "deploy" / "novel" / "artifacts" / "quality_ab_live"
REMOTE_STAGE = "/tmp/quality_ab_stage"
CONTAINER = "regent-api"
REMOTE_OUT = "/tmp/quality_ab_out"
CONTAINER_OUT = "/tmp/quality_ab_out"

FILES = (
    "core/src/regent/novel/domain/script_protocol.py",
    "core/src/regent/novel/experiments/__init__.py",
    "core/src/regent/novel/experiments/quality_ab.py",
    "deploy/novel/quality_ab_harness.py",
)


def _pack_to(path: Path) -> None:
    with tarfile.open(path, mode="w:gz") as tar:
        for rel in FILES:
            local = ROOT / rel
            tar.add(local, arcname=rel.replace("\\", "/"))


def main() -> int:
    # 本地专用：--local-out DIR（不转发远端）；其余转给 harness
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
    extra = " ".join(filtered)
    r = Remote()
    with tempfile.TemporaryDirectory() as tmp:
        pack = Path(tmp) / "qa.tgz"
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
docker cp {REMOTE_STAGE}/deploy/novel/quality_ab_harness.py {CONTAINER}:/tmp/quality_ab_harness.py
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
  python /tmp/quality_ab_harness.py --live --out {CONTAINER_OUT} {extra}
docker cp {CONTAINER}:{CONTAINER_OUT}/. {REMOTE_OUT}/
tar -czf /tmp/quality_ab_out.tgz -C {REMOTE_OUT} .
"""
    print("starting live run...", flush=True)
    print("extra:", extra or "(all 9 samples)")
    print("local_out:", local_out)
    t0 = time.time()
    res = r.run(run_cmd, timeout=60 * 45)
    print(res.out)
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
            sftp.get("/tmp/quality_ab_out.tgz", str(local_tgz))
        finally:
            sftp.close()
        with tarfile.open(local_tgz, mode="r:gz") as tar:
            tar.extractall(local_out)

    print(f"pulled -> {local_out}")
    summary = local_out / "summary.json"
    if summary.exists():
        print(summary.read_text(encoding="utf-8"))
    results = local_out / "results.json"
    if results.exists():
        rows = json.loads(results.read_text(encoding="utf-8"))
        for row in rows:
            print(
                f"{row['fragment_id']}:{row['protocol']} "
                f"completed={row['completed']} calls={row['calls_used']} "
                f"cost={row['cost_minor']} stop={row['stop_reason']} "
                f"prose_chars={len(row.get('prose') or '')}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

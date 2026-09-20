#!/usr/bin/env python3
"""远端只跑协议 X 第 1 章短样本并拉回。"""

from __future__ import annotations

import sys
import tarfile
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _ssh import Remote  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
LOCAL_OUT = ROOT / "deploy" / "novel" / "artifacts" / "x_ch1_probe"
REMOTE_STAGE = "/tmp/x_ch1_stage"
CONTAINER = "regent-api"
REMOTE_OUT = "/tmp/x_ch1_host"
CONTAINER_OUT = "/tmp/x_ch1_probe"

FILES = (
    "core/src/regent/novel/domain/script_protocol.py",
    "core/src/regent/novel/domain/scene_card.py",
    "core/src/regent/novel/domain/story_ledger.py",
    "core/src/regent/novel/domain/price_book.py",
    "core/src/regent/novel/experiments/__init__.py",
    "core/src/regent/novel/experiments/quality_ab.py",
    "core/src/regent/novel/experiments/scene_exec.py",
    "deploy/novel/run_x_chapter1_probe.py",
)


def main() -> int:
    r = Remote()
    with tempfile.TemporaryDirectory() as tmp:
        pack = Path(tmp) / "xch1.tgz"
        with tarfile.open(pack, mode="w:gz") as tar:
            for rel in FILES:
                tar.add(ROOT / rel, arcname=rel.replace("\\", "/"))
        print(f"upload {pack.stat().st_size}", flush=True)
        r.put(str(pack), f"{REMOTE_STAGE}.tgz")

    prep = f"""
set -e
rm -rf {REMOTE_STAGE} {REMOTE_OUT}
mkdir -p {REMOTE_STAGE} {REMOTE_OUT}
tar -xzf {REMOTE_STAGE}.tgz -C {REMOTE_STAGE}
docker exec {CONTAINER} mkdir -p /app/core/src/regent/novel/experiments /app/core/src/regent/novel/domain
docker cp {REMOTE_STAGE}/core/src/regent/novel/experiments/. {CONTAINER}:/app/core/src/regent/novel/experiments/
docker cp {REMOTE_STAGE}/core/src/regent/novel/domain/script_protocol.py {CONTAINER}:/app/core/src/regent/novel/domain/script_protocol.py
docker cp {REMOTE_STAGE}/core/src/regent/novel/domain/scene_card.py {CONTAINER}:/app/core/src/regent/novel/domain/scene_card.py
docker cp {REMOTE_STAGE}/core/src/regent/novel/domain/story_ledger.py {CONTAINER}:/app/core/src/regent/novel/domain/story_ledger.py
docker cp {REMOTE_STAGE}/core/src/regent/novel/domain/price_book.py {CONTAINER}:/app/core/src/regent/novel/domain/price_book.py
docker cp {REMOTE_STAGE}/deploy/novel/run_x_chapter1_probe.py {CONTAINER}:/tmp/run_x_chapter1_probe.py
docker exec {CONTAINER} rm -rf {CONTAINER_OUT}
"""
    res = r.run(prep, timeout=180)
    print(res.out, flush=True)
    if not res.ok:
        print(res.err, file=sys.stderr)
        return 1

    t0 = time.time()
    run = f"""
set -e
docker exec -e NOVEL_QUALITY_AB_LIVE=1 {CONTAINER} \\
  python /tmp/run_x_chapter1_probe.py --fragment f4_ent_gender --out {CONTAINER_OUT}
docker cp {CONTAINER}:{CONTAINER_OUT}/. {REMOTE_OUT}/
tar -czf /tmp/x_ch1_out.tgz -C {REMOTE_OUT} .
"""
    print("running X chapter1 probe...", flush=True)
    res = r.run(run, timeout=60 * 25)
    print(res.out[-6000:] if res.out else "", flush=True)
    if res.err:
        print(res.err[-2000:], file=sys.stderr)
    print(f"elapsed_s={time.time()-t0:.1f} code={res.code}", flush=True)
    if not res.ok:
        return res.code or 1

    LOCAL_OUT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        tgz = Path(tmp) / "out.tgz"
        sftp = r._client.open_sftp()
        try:
            sftp.get("/tmp/x_ch1_out.tgz", str(tgz))
        finally:
            sftp.close()
        with tarfile.open(tgz, mode="r:gz") as tar:
            tar.extractall(LOCAL_OUT)
    print(f"pulled -> {LOCAL_OUT}", flush=True)
    report = LOCAL_OUT / "report.json"
    if report.exists():
        print(report.read_text(encoding="utf-8"), flush=True)
    ch = LOCAL_OUT / "chapter1.txt"
    if ch.exists():
        text = ch.read_text(encoding="utf-8")
        print(f"chapter1_chars={len(text)}", flush=True)
        print(text[:600], flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

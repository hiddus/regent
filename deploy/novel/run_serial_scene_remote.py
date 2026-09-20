#!/usr/bin/env python3
"""远端连跑协议 X（分场+账本）多章，默认女频文娱 ~10 万字，拉回产物。

与旧版协议 S 产物目录分开：artifacts/serial_ent_gender_100k_x
"""

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
REMOTE_STAGE = "/tmp/serial_x_stage"
CONTAINER = "regent-api"
REMOTE_OUT = "/tmp/serial_x_host"
CONTAINER_OUT = "/tmp/serial_x_out"

FILES = (
    "core/src/regent/novel/domain/script_protocol.py",
    "core/src/regent/novel/domain/scene_card.py",
    "core/src/regent/novel/domain/story_ledger.py",
    "core/src/regent/novel/domain/price_book.py",
    "core/src/regent/novel/experiments/__init__.py",
    "core/src/regent/novel/experiments/quality_ab.py",
    "core/src/regent/novel/experiments/scene_exec.py",
    "deploy/novel/run_serial_scene_live.py",
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
        "--fragment f4_ent_gender --target-chars 105000 --max-chapters 65 "
        f"--out {CONTAINER_OUT}"
    )
    r = Remote()
    with tempfile.TemporaryDirectory() as tmp:
        pack = Path(tmp) / "serial_x.tgz"
        _pack_to(pack)
        print(f"upload pack bytes={pack.stat().st_size}", flush=True)
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
docker cp {REMOTE_STAGE}/core/src/regent/novel/domain/scene_card.py {CONTAINER}:/app/core/src/regent/novel/domain/scene_card.py
docker cp {REMOTE_STAGE}/core/src/regent/novel/domain/story_ledger.py {CONTAINER}:/app/core/src/regent/novel/domain/story_ledger.py
docker cp {REMOTE_STAGE}/core/src/regent/novel/domain/price_book.py {CONTAINER}:/app/core/src/regent/novel/domain/price_book.py
docker cp {REMOTE_STAGE}/deploy/novel/run_serial_scene_live.py {CONTAINER}:/tmp/run_serial_scene_live.py
docker exec {CONTAINER} rm -rf {CONTAINER_OUT}
docker exec {CONTAINER} mkdir -p {CONTAINER_OUT}
docker exec {CONTAINER} rm -f /tmp/serial_x_run.log
"""
    res = r.run(prep, timeout=180)
    print(res.out, flush=True)
    if not res.ok:
        print(res.err, file=sys.stderr)
        return 1

    # docker exec -d：容器内后台跑，SSH 可轮询（镜像无 pgrep/pkill）
    start = f"""
set -e
docker exec -d -e NOVEL_QUALITY_AB_LIVE=1 {CONTAINER} \\
  sh -c 'python /tmp/run_serial_scene_live.py {extra} > /tmp/serial_x_run.log 2>&1'
sleep 4
docker exec {CONTAINER} tail -n 40 /tmp/serial_x_run.log || true
docker exec {CONTAINER} python -c "import pathlib; print('RUNNING', any(b'run_serial_scene_live' in pathlib.Path(f'/proc/'+p+'/cmdline').read_bytes() for p in __import__('os').listdir('/proc') if p.isdigit()))"
"""
    print("starting serial X live run...", flush=True)
    print("extra:", extra, flush=True)
    print("local_out:", local_out, flush=True)
    t0 = time.time()
    res = r.run(start, timeout=60)
    print(res.out, flush=True)
    if not res.ok:
        print(res.err, file=sys.stderr)
        return res.code or 1
    if "RUNNING True" not in (res.out or ""):
        print("failed to start serial process", file=sys.stderr)
        return 1

    # 交给 watch 逻辑同款轮询
    from run_serial_scene_watch import _running  # type: ignore

    deadline = t0 + 60 * 240
    while time.time() < deadline:
        time.sleep(120)
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

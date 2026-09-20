"""整树部署 + 部署期防错位（替代「手工维护文件清单」的热补丁）。

为什么必须整树：M2 旅程在真机上抓到的 500 就是错位造成的——
`works.py` 调到 `executor.executor_version()`，而容器里那份 `executor.py`
是旧的、没有这个符号。手工清单每次只带一两个文件，就一定会再犯。

本脚本做四件事：
1. 打包并整体替换 `/app/core/src`（+ migrations、alembic.ini）；
2. **逐文件 sha256 核对**本地树与容器内落地结果，不一致就失败退出；
3. **跨模块符号体检**：把已知的耦合点显式断言，让「少拷了一个文件」在部署时炸，
   而不是等到用户点下去才 500；
4. 同步前端 dist，重启 regent-api，健康检查。

用法：python deploy/novel/sync_tree.py [--skip-frontend]
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tarfile
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _ssh import Remote  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
REMOTE_ROOT = "/opt/regent/sync"
LOCAL_TAR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_sync_tree.tar.gz")
STAGING = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_sync_manifest.json")
TREE = ("core/src", "core/migrations", "alembic.ini")
EXCLUDES = ("__pycache__",)
DIST = os.path.join(ROOT, "apps", "novel-web", "dist")
# 跑同一份源码的**所有**容器：只更 regent-api 是第二个错位面——
# worker 会带着 18 小时的旧代码继续推进生产任务，而部署记录显示「全一致」。
CONTAINERS = ("regent-api", "regent-worker", "regent-worker-2", "regent-worker-3")

# 跨模块耦合点：新增耦合时必须在这里登记，否则下次错位又会漏到线上。
# 形如 (被测模块, 必须有这个属性, 为什么会被别处调到)
SYMBOL_CHECKS = [
    ("regent.novel.application.executor", "executor_version", "works.start_run 钉执行器版本"),
    ("regent.novel.application.executor", "choose_executor", "start_run 按作品分桶选执行器"),
    ("regent.novel.application.works", "get_onboarding", "GET /works/{id}/onboarding"),
    ("regent.novel.application.works", "get_active_run_progress", "GET /works/{id}/runs"),
    ("regent.novel.application.works", "resume_after_correction", "POST /works/{id}/resume-correction"),
    ("regent.novel.application.works", "resolve_ending", "POST /works/{id}/ending/resolve"),
    ("regent.novel.application.memory", "record_chapter_memory", "CANON 后写长期记忆"),
    ("regent.novel.application.evaluation", "decide", "R4 盲评晋级判定"),
    ("regent.novel.application.production", "CallBroker", "R0 调用账本"),
    ("regent.novel.domain.prose_patch", "apply_patch", "R21 局部补丁合并"),
    ("regent.novel.domain.scene_requirements", "freeze_scene_requirements", "R21 要求清单冻结"),
]


def _exclude(info: tarfile.TarInfo) -> tarfile.TarInfo | None:
    if os.path.basename(info.name) in EXCLUDES:
        return None
    return info


def build_tarball() -> str:
    with tarfile.open(LOCAL_TAR, mode="w:gz") as tar:
        for path in TREE:
            tar.add(path, arcname=path, filter=_exclude)
    return LOCAL_TAR


def build_manifest() -> dict[str, str]:
    """本地树逐文件 sha256——部署完拿它核对容器，别再靠「应该拷过去了」。"""
    out: dict[str, str] = {}
    for rel in TREE:
        full = os.path.join(ROOT, rel)
        if os.path.isfile(full):
            out[rel.replace("\\", "/")] = _sha(full)
            continue
        for root, dirs, files in os.walk(full):
            dirs[:] = [d for d in dirs if d not in EXCLUDES]
            for name in files:
                p = os.path.join(root, name)
                key = os.path.relpath(p, ROOT).replace("\\", "/")
                out[key] = _sha(p)
    return out


def _sha(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


REMOTE_VERIFY = r"""
import hashlib, json, os
man = json.load(open('/tmp/sync_manifest.json'))
trees = ('core/src', 'core/migrations')
bad, missing = [], []
for rel, want in man.items():
    p = '/app/' + rel
    try:
        h = hashlib.sha256(open(p, 'rb').read()).hexdigest()
    except OSError:
        missing.append(rel); continue
    if h != want:
        bad.append(rel)

# 存量多余文件同样致命：改名/删掉的文件留在容器里会继续被 import。
extra = []
for t in trees:
    for root, dirs, files in os.walk('/app/' + t):
        dirs[:] = [d for d in dirs if d != '__pycache__']
        for name in files:
            if name.endswith('.pyc'):
                continue
            key = os.path.relpath(os.path.join(root, name), '/app').replace(os.sep, '/')
            if key not in man:
                extra.append(key)
print(json.dumps({'checked': len(man), 'bad': bad[:20], 'bad_n': len(bad),
                  'missing': missing[:20], 'missing_n': len(missing),
                  'extra': extra[:20], 'extra_n': len(extra)}))
"""

REMOTE_SYMBOLS = r"""
import importlib, json
checks = json.load(open('/tmp/sync_symbols.json'))
missing = []
for mod_name, attr, why in checks:
    try:
        mod = importlib.import_module(mod_name)
    except Exception as exc:
        missing.append(f'{mod_name}: import failed: {type(exc).__name__}: {exc}'); continue
    if not hasattr(mod, attr):
        missing.append(f'{mod_name}.{attr} 缺失（{why}）')
print(json.dumps({'checked': len(checks), 'missing': missing}))
"""


def sync_frontend(r: Remote) -> None:
    r.run("mkdir -p /opt/regent/apps/novel-web/dist/assets", check=True)
    r.put(os.path.join(DIST, "index.html"), "/opt/regent/apps/novel-web/dist/index.html")
    assets = sorted(os.listdir(os.path.join(DIST, "assets")))
    for fname in assets:
        r.put(os.path.join(DIST, "assets", fname),
              f"/opt/regent/apps/novel-web/dist/assets/{fname}")
    r.run("docker exec regent-api mkdir -p /app/static/assets", check=True)
    r.run("docker cp /opt/regent/apps/novel-web/dist/index.html "
          "regent-api:/app/static/index.html", check=True)
    for fname in assets:
        r.run(f"docker cp /opt/regent/apps/novel-web/dist/assets/{fname} "
              f"regent-api:/app/static/assets/{fname}", check=True)
    print(f"[frontend] {len(assets)} assets 已同步")


def push_checkers(r: Remote, manifest: dict[str, str], containers=CONTAINERS) -> int:
    """校验脚本要「进容器」执行：写主机 /tmp 与容器 /tmp 是两回事。"""
    r.write_text(f"{REMOTE_ROOT}/sync_verify.py", REMOTE_VERIFY)
    r.write_text(f"{REMOTE_ROOT}/sync_manifest.json", json.dumps(manifest))
    r.write_text(f"{REMOTE_ROOT}/sync_symbols.json", json.dumps(SYMBOL_CHECKS, ensure_ascii=False))
    r.write_text(f"{REMOTE_ROOT}/sync_symbol_check.py", REMOTE_SYMBOLS)
    for c in containers:
        for name in ("sync_verify.py", "sync_manifest.json",
                     "sync_symbols.json", "sync_symbol_check.py"):
            out = r.run(f"docker cp {REMOTE_ROOT}/{name} {c}:/tmp/{name}")
            if not out.ok:
                print(f"  [FAIL] 无法把 {name} 送进 {c}:", out.err[:200] or out.out[:200])
                return 1
    return 0


def verify_tree(r: Remote, container: str) -> int:
    """逐文件 sha256 核对 + 多余文件检查。返回 0 表示一致。"""
    verify = r.run(f"docker exec {container} python /tmp/sync_verify.py", timeout=300)
    try:
        res = json.loads(verify.text.strip().splitlines()[-1])
    except Exception:
        print("  [FAIL] 核对输出无法解析:", verify.text[:400])
        return 1
    print(f"   [{container}] 已核对 {res['checked']} 个文件；不一致 {res['bad_n']}；"
          f"缺失 {res['missing_n']}；多余 {res['extra_n']}")
    if res["bad_n"] or res["missing_n"] or res["extra_n"]:
        for key in ("bad", "missing", "extra"):
            if res[key]:
                print(f"   {key}:", res[key])
        print(f"[FAIL] {container} 部署树与本地树不一致——拒绝重启，避免带病上线")
        return 1
    return 0


def verify_symbols(r: Remote, container: str) -> int:
    sym = r.run(f"docker exec {container} python /tmp/sync_symbol_check.py", timeout=300)
    try:
        sres = json.loads(sym.text.strip().splitlines()[-1])
    except Exception:
        print("  [FAIL] 符号体检输出无法解析:", sym.text[:400])
        return 1
    if sres["missing"]:
        print(f"[FAIL] {container} 符号错位（少拷文件/接口不匹配）：")
        for m in sres["missing"]:
            print("   -", m)
        return 1
    print(f"   [{container}] {sres['checked']} 项耦合点全部就位")
    return 0


def main() -> int:
    skip_frontend = "--skip-frontend" in sys.argv
    verify_only = "--verify-only" in sys.argv
    manifest = build_manifest()
    with open(STAGING, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh)
    print(f"[1/6] 本地清单 {len(manifest)} 个文件；打包…")
    build_tarball()

    with Remote() as r:
        r.run(f"mkdir -p {REMOTE_ROOT}", check=True)
        if not verify_only:
            r.put(LOCAL_TAR, f"{REMOTE_ROOT}/tree.tar.gz")
        if push_checkers(r, manifest):
            return 1
        if verify_only:
            # 只核对、不覆盖：这样「错位能不能被拦下」本身是可测的。
            print("[verify-only] 跳过落盘，直接核对现有容器树…")
            bad = 0
            for c in CONTAINERS:
                bad += verify_tree(r, c) or verify_symbols(r, c)
            if bad:
                return 1
            print("[OK] verify-only：全部容器树与本地树一致")
            return 0

        print("[2/6] 主机侧解包，再用 docker cp 落盘（所有跑代码的容器）…")
        # 容器内 `tar -x` 在 overlayfs 上会报 "Cannot open: File exists" 且
        # "Cannot utime: Operation not permitted"，只有 daemon 侧的 docker cp 可靠。
        r.run(f"rm -rf {REMOTE_ROOT}/stage && mkdir -p {REMOTE_ROOT}/stage", check=True)
        r.run(f"tar -xzf {REMOTE_ROOT}/tree.tar.gz -C {REMOTE_ROOT}/stage", check=True, timeout=600)
        for c in CONTAINERS:
            for src, dst in (
                (f"{REMOTE_ROOT}/stage/core/src/.", "/app/core/src/"),
                (f"{REMOTE_ROOT}/stage/core/migrations/.", "/app/core/migrations/"),
                (f"{REMOTE_ROOT}/stage/alembic.ini", "/app/alembic.ini"),
            ):
                out = r.run(f"docker cp {src} {c}:{dst}", timeout=600)
                if not out.ok:
                    print(f"  [FAIL] docker cp 失败（{c}）:", out.err[:300] or out.out[:300])
                    return 1
            print(f"   {c} 已落盘")

        print("[3/6] 逐文件 sha256 核对（防少拷/半拷）…")
        for c in CONTAINERS:
            if verify_tree(r, c):
                return 1

        print("[4/6] 跨模块符号体检…")
        for c in CONTAINERS:
            if verify_symbols(r, c):
                return 1

        if skip_frontend:
            print("[5/6] 跳过前端（--skip-frontend）")
        else:
            print("[5/6] 同步前端 dist…")
            sync_frontend(r)

        print("[6/6] 重启所有容器并健康检查…")
        for c in CONTAINERS:
            r.run(f"docker restart {c}", check=True)
        for _ in range(20):
            time.sleep(3)
            code = r.run("curl -s -o /dev/null -w '%{http_code}' "
                         "http://127.0.0.1:8000/health/live").text.strip()
            if code == "200":
                break
        print("   health:", code)
        if code != "200":
            print("[FAIL] 重启后健康检查未通过")
            return 1

    print(f"[OK] 整树部署完成（{datetime.now().strftime('%H:%M:%S')}），清单 {len(manifest)} 文件全一致")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

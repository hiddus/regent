"""用本地代码重建服务器镜像 regent-core:novel-mvp，并按原配置重建跑代码的四个容器。

sync_tree 只把代码 docker cp 进容器的可写层：容器一旦被删重建，就退回镜像里的旧代码。
本脚本把同一棵树（core/src、core/migrations、alembic.ini）和前端 dist 打进镜像；
旧镜像留一个回退标签；容器的环境变量、挂载、端口、网络、重启策略照抄 docker inspect。
健康检查不过就用回退标签把容器换回去；成功后删掉服务器上的构建上下文和含密钥的 env 文件。

依赖装在基础镜像 regent-core:latest 里，本脚本不重装依赖：pyproject.toml 变了要先重建基础镜像。

用法：python deploy/novel/rebuild_image.py
之后跑 python deploy/novel/sync_tree.py --verify-only 核对四个容器的树与耦合点。
"""

from __future__ import annotations

import json
import os
import shlex
import sys
import tarfile
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _ssh import Remote  # noqa: E402
from sync_tree import CONTAINERS, DIST, EXCLUDES, ROOT, TREE  # noqa: E402

IMAGE = "regent-core:novel-mvp"
BASE = "regent-core:latest"
REMOTE_DIR = "/opt/regent/sync/image"
LOCAL_TAR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_image_ctx.tar.gz")

DOCKERFILE = f"""FROM {BASE}
USER root
RUN rm -rf /app/core/src /app/core/migrations /app/static
COPY core/src /app/core/src
COPY core/migrations /app/core/migrations
COPY alembic.ini /app/alembic.ini
COPY static /app/static
ENV PYTHONPATH=/app/core/src
USER nobody
"""


def _exclude(info: tarfile.TarInfo) -> tarfile.TarInfo | None:
    return None if any(part in EXCLUDES for part in info.name.split("/")) else info


def build_context() -> str:
    if not os.path.isfile(os.path.join(DIST, "index.html")):
        raise SystemExit(f"[FAIL] 没有前端产物 {DIST}，先构建 apps/novel-web")
    with tarfile.open(LOCAL_TAR, mode="w:gz") as tar:
        for path in TREE:
            tar.add(os.path.join(ROOT, path), arcname=path, filter=_exclude)
        tar.add(DIST, arcname="static", filter=_exclude)
    return LOCAL_TAR


def run_args(spec: dict, env_file: str) -> str:
    """把 docker inspect 的结果还原成 docker run 参数（不含镜像与命令）。"""
    host = spec["HostConfig"]
    args = ["-d", "--name", spec["Name"].lstrip("/"), "--env-file", env_file]
    for network in (spec["NetworkSettings"].get("Networks") or {}):
        args += ["--network", network]
    policy = (host.get("RestartPolicy") or {}).get("Name") or ""
    if policy and policy != "no":
        args += ["--restart", policy]
    for bind in host.get("Binds") or []:
        args += ["-v", bind]
    for port, bindings in (host.get("PortBindings") or {}).items():
        for binding in bindings or []:
            ip = binding.get("HostIp") or ""
            args += ["-p", f"{ip + ':' if ip else ''}{binding['HostPort']}:{port.split('/')[0]}"]
    return " ".join(shlex.quote(a) for a in args)


def env_text(spec: dict) -> str:
    env = spec["Config"].get("Env") or []
    if any("\n" in item for item in env):
        raise SystemExit(f"[FAIL] {spec['Name']} 有含换行的环境变量，env-file 表达不了")
    return "\n".join(env) + "\n"


def recreate(r: Remote, specs: dict[str, dict], image: str) -> None:
    for name, spec in specs.items():
        env_file = f"{REMOTE_DIR}/{name}.env"
        r.run(f"docker rm -f {name}", check=True)
        command = " ".join(shlex.quote(a) for a in spec["Config"].get("Cmd") or [])
        r.run(f"docker run {run_args(spec, env_file)} {image} {command}", check=True)
        print(f"   {name} 已用 {image} 重建")


def healthy(r: Remote) -> bool:
    for _ in range(30):
        time.sleep(3)
        code = r.run(
            "curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8000/health/live"
        ).text.strip()
        if code == "200":
            return True
    return False


def main() -> int:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    rollback = f"regent-core:novel-mvp-prev-{stamp}"
    print("[1/6] 打包构建上下文（core/src、core/migrations、alembic.ini、前端 dist）…")
    build_context()
    with Remote() as r:
        r.run(f"rm -rf {REMOTE_DIR} && mkdir -p {REMOTE_DIR}/ctx", check=True)
        r.put(LOCAL_TAR, f"{REMOTE_DIR}/ctx.tar.gz")
        r.run(f"tar -xzf {REMOTE_DIR}/ctx.tar.gz -C {REMOTE_DIR}/ctx", check=True, timeout=600)
        r.write_text(f"{REMOTE_DIR}/ctx/Dockerfile", DOCKERFILE)

        print("[2/6] 记下四个容器的现有配置…")
        specs: dict[str, dict] = {}
        for name in CONTAINERS:
            spec = json.loads(r.run(f"docker inspect {name}", check=True).text)[0]
            specs[name] = spec
            r.write_text(f"{REMOTE_DIR}/{name}.inspect.json", json.dumps(spec, ensure_ascii=False, indent=1))
            r.write_text(f"{REMOTE_DIR}/{name}.env", env_text(spec))

        print(f"[3/6] 旧镜像留回退标签 {rollback}，构建新镜像…")
        r.run(f"docker tag {IMAGE} {rollback}", check=True)
        built = r.run(f"cd {REMOTE_DIR}/ctx && docker build -t {IMAGE} .", timeout=1800)
        if not built.ok:
            print("[FAIL] 构建失败，容器未动：", built.text[-800:])
            return 1
        print("  ", r.run(f"docker image inspect {IMAGE} --format '{{{{.Id}}}} {{{{.Created}}}}'").text.strip())

        print("[4/6] 按原配置重建容器…")
        recreate(r, specs, IMAGE)

        print("[5/6] 健康检查…")
        if not healthy(r):
            print(f"[FAIL] 新镜像健康检查不过，换回 {rollback}")
            recreate(r, specs, rollback)
            print("   回退后健康：", healthy(r))
            return 1

        print("[6/6] 容器状态：")
        print(r.run("docker ps --format '{{.Names}}\t{{.Image}}\t{{.Status}}'").text)
        # env 文件与 inspect 里是明文密钥；容器已建好，回退靠回退标签与 docker inspect
        r.run(f"rm -rf {REMOTE_DIR}", check=True)
    print(f"[OK] 镜像重建完成（{datetime.now().strftime('%H:%M:%S')}），回退标签 {rollback}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

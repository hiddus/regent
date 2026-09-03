"""Upload the bounded Novel MVP surface and deploy it on the configured server."""

from __future__ import annotations

from pathlib import Path

from _ssh import Remote


ROOT = Path(__file__).resolve().parents[2]
REMOTE = "/opt/regent"


def main() -> None:
    with Remote() as remote:
        remote.put_tree(
            str(ROOT / "core" / "src" / "regent" / "novel"),
            f"{REMOTE}/core/src/regent/novel",
            exclude=("__pycache__",),
        )
        remote.put(
            str(ROOT / "core" / "src" / "regent" / "api" / "main.py"),
            f"{REMOTE}/core/src/regent/api/main.py",
        )
        remote.put(
            str(ROOT / "core" / "src" / "regent" / "worker" / "main.py"),
            f"{REMOTE}/core/src/regent/worker/main.py",
        )
        remote.put(
            str(ROOT / "core" / "migrations" / "versions" / "20260903_0048_novel_domain.py"),
            f"{REMOTE}/core/migrations/versions/20260903_0048_novel_domain.py",
        )
        remote.put_tree(
            str(ROOT / "apps" / "novel-web" / "dist"),
            f"{REMOTE}/apps/novel-web/dist",
        )

        env_file = f"{REMOTE}/.env"
        commands = [
            # 1. 用增量 Dockerfile 重建镜像（基于 regent-core:latest）
            f"cd {REMOTE} && docker build -t regent-core:novel-mvp -f core/Dockerfile.novel-mvp .",
            # 2. 执行 Novel Domain 迁移（0048）
            (
                f"docker run --rm --network regent-net --env-file {env_file} "
                "regent-core:novel-mvp alembic upgrade head"
            ),
            # 3. 替换 API 容器
            "docker stop regent-api >/dev/null 2>&1 || true",
            "docker rm regent-api >/dev/null 2>&1 || true",
            (
                "docker run -d --name regent-api --network regent-net --restart unless-stopped "
                f"--env-file {env_file} "
                "-p 8000:8000 -v /var/run/docker.sock:/var/run/docker.sock "
                "-v /usr/bin/docker:/usr/bin/docker "
                f"-v {REMOTE}/artifacts:/var/lib/regent/artifacts "
                f"-v {REMOTE}/workspaces:/var/lib/regent/workspaces "
                f"-v {REMOTE}/builds:/var/lib/regent/builds "
                "regent-core:novel-mvp regent-api"
            ),
            # 4. 启动 3 个 Worker 容器
            "docker rm -f regent-worker regent-worker-2 regent-worker-3 >/dev/null 2>&1 || true",
            (
                "docker run -d --name regent-worker --network regent-net --restart unless-stopped "
                f"--env-file {env_file} "
                f"-v {REMOTE}/artifacts:/var/lib/regent/artifacts "
                f"-v {REMOTE}/workspaces:/var/lib/regent/workspaces "
                f"-v {REMOTE}/builds:/var/lib/regent/builds "
                "regent-core:novel-mvp regent-worker"
            ),
            (
                "docker run -d --name regent-worker-2 --network regent-net --restart unless-stopped "
                f"--env-file {env_file} "
                f"-v {REMOTE}/artifacts:/var/lib/regent/artifacts "
                f"-v {REMOTE}/workspaces:/var/lib/regent/workspaces "
                f"-v {REMOTE}/builds:/var/lib/regent/builds "
                "regent-core:novel-mvp regent-worker"
            ),
            (
                "docker run -d --name regent-worker-3 --network regent-net --restart unless-stopped "
                f"--env-file {env_file} "
                f"-v {REMOTE}/artifacts:/var/lib/regent/artifacts "
                f"-v {REMOTE}/workspaces:/var/lib/regent/workspaces "
                f"-v {REMOTE}/builds:/var/lib/regent/builds "
                "regent-core:novel-mvp regent-worker"
            ),
            # 5. novel-web 前端已合并到 API 镜像（/app/static），无需单独容器
        ]
        for command in commands:
            result = remote.run(command, timeout=1200)
            print(result)
            if not result.ok:
                raise SystemExit(result.code)


if __name__ == "__main__":
    main()

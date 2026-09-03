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
            str(ROOT / "apps" / "novel-web"),
            f"{REMOTE}/apps/novel-web",
            exclude=("node_modules", "dist"),
        )

        commands = [
            f"cd {REMOTE} && docker build -t regent-core:novel-mvp -f core/Dockerfile .",
            (
                "docker run --rm --network regent-net --env-file "
                f"{REMOTE}/.runtime.env --env-file {REMOTE}/.secrets.env "
                "regent-core:novel-mvp alembic upgrade head"
            ),
            "docker stop regent-api >/dev/null 2>&1 || true",
            "docker rename regent-api regent-api-before-novel-mvp",
            (
                "docker run -d --name regent-api --network regent-net --restart unless-stopped "
                f"--env-file {REMOTE}/.runtime.env --env-file {REMOTE}/.secrets.env "
                "-p 8000:8000 -v /var/run/docker.sock:/var/run/docker.sock "
                "-v /usr/bin/docker:/usr/bin/docker "
                f"-v {REMOTE}/artifacts:/var/lib/regent/artifacts "
                f"-v {REMOTE}/workspaces:/var/lib/regent/workspaces "
                f"-v {REMOTE}/builds:/var/lib/regent/builds "
                "regent-core:novel-mvp regent-api"
            ),
            "docker rm -f regent-worker >/dev/null 2>&1 || true",
            (
                "docker run -d --name regent-worker --network regent-net --restart unless-stopped "
                f"--env-file {REMOTE}/.runtime.env --env-file {REMOTE}/.secrets.env "
                f"-v {REMOTE}/artifacts:/var/lib/regent/artifacts "
                f"-v {REMOTE}/workspaces:/var/lib/regent/workspaces "
                f"-v {REMOTE}/builds:/var/lib/regent/builds "
                "regent-core:novel-mvp regent-worker"
            ),
            f"cd {REMOTE} && docker build -t novel-web:mvp -f apps/novel-web/Dockerfile .",
            "docker rm -f novel-web >/dev/null 2>&1 || true",
            (
                "docker run -d --name novel-web --network regent-net --restart unless-stopped "
                "-p 8088:80 novel-web:mvp"
            ),
        ]
        for command in commands:
            result = remote.run(command, timeout=1200)
            print(result)
            if not result.ok:
                raise SystemExit(result.code)


if __name__ == "__main__":
    main()

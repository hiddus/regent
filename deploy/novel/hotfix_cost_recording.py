"""Hot-fix: add cost recording to generation.py agent loop."""

from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.dirname(__file__))
from _ssh import Remote

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
REMOTE = "/opt/regent"


def main() -> None:
    local_file = os.path.join(
        ROOT, "core", "src", "regent", "novel", "application", "generation.py"
    )
    remote_file = f"{REMOTE}/core/src/regent/novel/application/generation.py"
    container_path = "/app/core/src/regent/novel/application/generation.py"

    with Remote() as r:
        print("Uploading fixed generation.py...")
        r.put(local_file, remote_file)

        # Copy into all containers
        for name in ["regent-api", "regent-worker", "regent-worker-2", "regent-worker-3"]:
            r.run(f"docker cp {remote_file} {name}:{container_path}")
            print(f"  Copied to {name}")

        # Restart workers to pick up the change
        for name in ["regent-worker", "regent-worker-2", "regent-worker-3"]:
            r.run(f"docker restart {name}")
        print("Workers restarted. Waiting...")
        import time
        time.sleep(5)
        print("Done.")


if __name__ == "__main__":
    main()

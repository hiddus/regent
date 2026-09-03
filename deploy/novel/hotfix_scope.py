"""Hot-fix idempotency scope column length."""

from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.dirname(__file__))
from _ssh import Remote

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
REMOTE = "/opt/regent"


def main() -> None:
    local_file = os.path.join(
        ROOT, "core", "src", "regent", "novel", "infrastructure", "models.py"
    )
    remote_file = f"{REMOTE}/core/src/regent/novel/infrastructure/models.py"
    container_path = "/app/core/src/regent/novel/infrastructure/models.py"

    with Remote() as r:
        # 1. ALTER COLUMN on database
        print("Altering scope column to VARCHAR(512)...")
        result = r.run(
            "docker exec regent-postgres psql -U regent -c "
            "\"ALTER TABLE novel_idempotency_records ALTER COLUMN scope TYPE VARCHAR(512);\""
        )
        print(result)

        # 2. Upload fixed models.py
        print("Uploading fixed models.py...")
        r.put(local_file, remote_file)

        # 3. Copy into API container
        print("Copying into API container...")
        r.run(f"docker cp {remote_file} regent-api:{container_path}")

        # 4. Copy into worker containers too
        for worker in ["regent-worker", "regent-worker-2", "regent-worker-3"]:
            r.run(f"docker cp {remote_file} {worker}:{container_path}")
        print("Copied to all containers.")

        # 5. Restart API
        print("Restarting API container...")
        r.run("docker restart regent-api")
        import time
        time.sleep(5)
        print("Done. API restarted.")


if __name__ == "__main__":
    main()

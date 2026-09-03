"""Hot-deploy updated frontend into the running API container."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from _ssh import Remote

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DIST = os.path.join(ROOT, "apps", "novel-web", "dist")
REMOTE_STATIC = "/opt/regent/apps/novel-web/dist"


def main() -> None:
    with Remote() as r:
        # 1. Upload dist/ to server
        print("Uploading dist/ to server...")
        r.run(f"mkdir -p {REMOTE_STATIC}/assets")
        r.put(os.path.join(DIST, "index.html"), f"{REMOTE_STATIC}/index.html")
        assets_dir = os.path.join(DIST, "assets")
        for fname in os.listdir(assets_dir):
            local = os.path.join(assets_dir, fname)
            if os.path.isfile(local):
                r.put(local, f"{REMOTE_STATIC}/assets/{fname}")
                print(f"  Uploaded {fname}")

        # 2. Copy into API container as /app/static
        print("Copying into regent-api container...")
        r.run(f"docker exec regent-api mkdir -p /app/static/assets")
        r.run(f"docker cp {REMOTE_STATIC}/index.html regent-api:/app/static/index.html")
        for fname in os.listdir(os.path.join(DIST, "assets")):
            r.run(f"docker cp {REMOTE_STATIC}/assets/{fname} regent-api:/app/static/assets/{fname}")
            print(f"  Copied {fname}")

        # 3. Restart API to pick up the new static dir
        print("Restarting API container...")
        r.run("docker restart regent-api")
        import time; time.sleep(5)
        print("Done. Frontend served at http://<server>:8000/")


if __name__ == "__main__":
    main()

"""Deploy updated novel-web frontend to server."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from _ssh import Remote

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DIST = os.path.join(ROOT, "apps", "novel-web", "dist")
NGINX_CONF = os.path.join(ROOT, "apps", "novel-web", "nginx.conf")
REMOTE_DIST = "/opt/regent/apps/novel-web/dist"


def main() -> None:
    with Remote() as r:
        # 1. Upload dist/ to server
        print("Uploading dist/ to server...")
        r.run(f"mkdir -p {REMOTE_DIST}/assets")

        # Upload index.html
        r.put(os.path.join(DIST, "index.html"), f"{REMOTE_DIST}/index.html")

        # Upload assets
        assets_dir = os.path.join(DIST, "assets")
        for fname in os.listdir(assets_dir):
            local = os.path.join(assets_dir, fname)
            if os.path.isfile(local):
                r.put(local, f"{REMOTE_DIST}/assets/{fname}")
                print(f"  Uploaded {fname}")

        # 2. Copy into container
        print("Copying into novel-web container...")
        r.run(f"docker cp {REMOTE_DIST}/index.html novel-web:/usr/share/nginx/html/index.html")
        for fname in os.listdir(os.path.join(DIST, "assets")):
            r.run(f"docker cp {REMOTE_DIST}/assets/{fname} novel-web:/usr/share/nginx/html/assets/{fname}")
            print(f"  Copied {fname}")

        # 3. Upload nginx.conf
        print("Uploading nginx.conf...")
        r.put(NGINX_CONF, "/opt/regent/apps/novel-web/nginx.conf")
        r.run("docker cp /opt/regent/apps/novel-web/nginx.conf novel-web:/etc/nginx/conf.d/default.conf")

        # 4. Reload nginx
        print("Reloading nginx...")
        r.run("docker exec novel-web nginx -s reload")
        print("Done. Frontend + nginx updated.")


if __name__ == "__main__":
    main()

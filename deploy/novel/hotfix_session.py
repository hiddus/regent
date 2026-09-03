"""Hot-fix the session transaction bug and restart the API container."""

from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.dirname(__file__))
from _ssh import Remote

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
REMOTE = "/opt/regent"


def main() -> None:
    local_file = os.path.join(
        ROOT, "core", "src", "regent", "novel", "api", "novel.py"
    )
    remote_file = f"{REMOTE}/core/src/regent/novel/api/novel.py"

    with Remote() as r:
        # 1. Upload fixed file to host
        print("Uploading fixed novel.py to host...")
        r.put(local_file, remote_file)

        # 2. Copy into the running API container (host path → container path)
        print("Copying into API container...")
        container_path = "/app/core/src/regent/novel/api/novel.py"
        result = r.run("docker cp {} regent-api:{}".format(remote_file, container_path))
        print(result)

        # 3. Restart API container to pick up the fix
        print("Restarting API container...")
        r.run("docker restart regent-api")
        print("Waiting for startup...")
        import time
        time.sleep(5)

        # 4. Verify the fix is in the container
        result = r.run(
            "docker exec regent-api grep -n 'session.begin' "
            "/app/core/src/regent/novel/api/novel.py"
        )
        print("Verification (should show session.begin):")
        print(result)

        # 5. Quick auth test
        result = r.run(
            "curl -s -X POST "
            "'http://localhost:8000/v1/novel/auth/session?subject=hotfix-test'"
        )
        print("Auth session response:")
        print(result)

        if result.out:
            import json
            try:
                data = json.loads(result.out.strip())
                token = data.get("token", "")
                if token:
                    # Test /me endpoint with the token
                    result2 = r.run(
                        "curl -s http://localhost:8000/v1/novel/me "
                        "-H 'Authorization: Bearer {}'".format(token)
                    )
                    print("/me response:")
                    print(result2)
                    if "principal_id" in result2.out:
                        print("\n*** FIX VERIFIED: Token authentication works! ***")
                    else:
                        print("\n*** FIX FAILED: Token auth still broken ***")
                else:
                    print("\n*** No token in auth response ***")
            except json.JSONDecodeError:
                print(f"\n*** Could not parse auth response: {result.out[:200]} ***")


if __name__ == "__main__":
    main()

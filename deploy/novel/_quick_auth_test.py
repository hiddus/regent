"""Quick auth test after hotfix."""

from __future__ import annotations

import json
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))
from _ssh import Remote


def main() -> None:
    with Remote() as r:
        # Write test script on host, copy to container, run
        test_script = """
import urllib.request, json

# Step 1: Create session
req = urllib.request.Request(
    "http://localhost:8000/v1/novel/auth/session?subject=auth-verify-test",
    method="POST",
)
with urllib.request.urlopen(req, timeout=10) as resp:
    data = json.loads(resp.read().decode())
    token = data["token"]
    print(f"1. Auth session created: token={token[:20]}...")
    print(f"   principal_id={data['principal_id']}")

# Step 2: Use token to access /me
req2 = urllib.request.Request("http://localhost:8000/v1/novel/me")
req2.add_header("Authorization", f"Bearer {token}")
try:
    with urllib.request.urlopen(req2, timeout=10) as resp:
        data2 = json.loads(resp.read().decode())
        print(f"2. /me OK: {data2}")
except Exception as e:
    print(f"2. /me FAILED: {e}")
    import urllib.error
    if hasattr(e, 'read'):
        print(f"   Body: {e.read().decode()}")

# Step 3: Create a work
req3 = urllib.request.Request(
    "http://localhost:8000/v1/novel/works",
    method="POST",
    data=json.dumps({
        "raw_intent": "A watchmaker who hears memories in old objects",
        "client_nonce": "auth-verify-001"
    }).encode(),
)
req3.add_header("Content-Type", "application/json")
req3.add_header("Authorization", f"Bearer {token}")
try:
    with urllib.request.urlopen(req3, timeout=10) as resp:
        data3 = json.loads(resp.read().decode())
        print(f"3. Create work OK: work_id={data3.get('work_id', '???')}")
        print(f"   state={data3.get('state', '???')}")
except Exception as e:
    print(f"3. Create work FAILED: {e}")
    if hasattr(e, 'read'):
        print(f"   Body: {e.read().decode()[:500]}")
"""
        r.write_text("/tmp/_auth_test.py", test_script)
        r.run("docker cp /tmp/_auth_test.py regent-api:/tmp/_auth_test.py")
        result = r.run("docker exec regent-api python /tmp/_auth_test.py", timeout=30)
        print(result.out)
        if result.err.strip():
            print(f"STDERR: {result.err.strip()}")


if __name__ == "__main__":
    main()

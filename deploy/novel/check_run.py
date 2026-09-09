"""检查 run 状态。"""
import httpx

BASE = "http://118.31.171.159:8000/v1/novel"
WORK_ID = "a1310e66-d83f-4523-97a9-0b0cb1951fc9"

r = httpx.post(f"{BASE}/auth/session", json={"display_name": "葬仙v3"}, timeout=30)
token = r.json()["token"]
h = {"Authorization": f"Bearer {token}"}

r2 = httpx.get(f"{BASE}/works/{WORK_ID}/runs", headers=h, timeout=30)
print(f"Status: {r2.status_code}")
if r2.status_code == 200:
    p = r2.json()
    print(f"chapter_no: {p['chapter_no']}")
    print(f"state: {p['state']}")
    print(f"step: {p.get('current_step', '-')}")
    print(f"steps: {p.get('steps', {})}")
else:
    print(r2.text[:500])

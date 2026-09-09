"""检查 worker 状态和卡住的 run。"""
import httpx

BASE = "http://118.31.171.159:8000/v1/novel"

def auth(name="debug-worker"):
    r = httpx.post(f"{BASE}/auth/session", json={"display_name": name}, timeout=30)
    return r.json()["token"]

def main():
    token = auth()
    h = {"Authorization": f"Bearer {token}"}

    # 列出所有作品
    r = httpx.get(f"{BASE}/works", headers=h, timeout=30)
    print(f"Works: {r.status_code}")
    if r.status_code == 200:
        works = r.json()
        for w in works:
            wid = w["work_id"]
            title = w.get("title", "?")
            state = w.get("state", "?")
            ch = w.get("latest_chapter_no", 0)
            print(f"  [{wid[:8]}] {title} state={state} ch={ch}")

            # 检查 run 状态
            r2 = httpx.get(f"{BASE}/works/{wid}/runs", headers=h, timeout=30)
            if r2.status_code == 200:
                p = r2.json()
                print(f"    run: ch={p['chapter_no']} state={p['state']} step={p.get('current_step','-')}")
                steps = p.get("steps", {})
                done = [s for s, st in steps.items() if st == "SUCCEEDED"]
                failed = [s for s, st in steps.items() if st == "FAILED"]
                pending = [s for s, st in steps.items() if st == "PENDING"]
                print(f"    done={done} failed={failed} pending={pending}")

if __name__ == "__main__":
    main()

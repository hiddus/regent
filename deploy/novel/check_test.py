"""检查葬仙测试进度并继续生成。"""
import httpx, time

BASE = "http://118.31.171.159:8000/v1/novel"
WORK_ID = "d83da96f-713c-406e-a9dd-0631f3e4a8d6"

def auth():
    r = httpx.post(f"{BASE}/auth/session", json={"display_name": "葬仙测试"}, timeout=30)
    return r.json()["token"]

def main():
    token = auth()
    h = {"Authorization": f"Bearer {token}"}

    # Check progress
    r = httpx.get(f"{BASE}/works/{WORK_ID}/runs", headers=h, timeout=30)
    print(f"Progress: {r.status_code}")
    if r.status_code != 200:
        print(r.text)
        return
    progress = r.json()
    print(f"  chapter_no: {progress['chapter_no']}")
    print(f"  state: {progress['state']}")
    print(f"  current_step: {progress.get('current_step', '-')}")
    print(f"  steps: {progress.get('steps', {})}")

    # If chapter 1 is done, get it
    if progress["state"] == "CANONIZED":
        r = httpx.get(f"{BASE}/works/{WORK_ID}/chapters/{progress['chapter_no']}", headers=h, timeout=30)
        if r.status_code == 200:
            ch = r.json()
            print(f"\n第 {progress['chapter_no']} 章: 《{ch['title']}》")
            print(f"  字数: {ch['word_count']}")
            print(f"  内容预览:\n{ch['content'][:300]}...")

            # Continue to next chapters
            for ch_no in range(progress['chapter_no'] + 1, 7):
                print(f"\n--- 生成第 {ch_no} 章 ---")
                # Start run
                r = httpx.post(f"{BASE}/works/{WORK_ID}/runs",
                    headers={**h, "Idempotency-Key": f"run-ch{ch_no}"}, timeout=30)
                if r.status_code != 202:
                    print(f"  start_run failed: {r.status_code} {r.text[:200]}")
                    continue

                # Poll until done
                for _ in range(120):  # max 10 minutes
                    time.sleep(5)
                    r = httpx.get(f"{BASE}/works/{WORK_ID}/runs", headers=h, timeout=30)
                    if r.status_code != 200:
                        continue
                    p = r.json()
                    if p["chapter_no"] != ch_no:
                        continue
                    step = p.get("current_step", "-")
                    state = p["state"]
                    steps = p.get("steps", {})
                    done_steps = [s for s, st in steps.items() if st == "SUCCEEDED"]
                    print(f"  [{state}] step={step} done={done_steps}")
                    if state in ("CANONIZED", "TERMINAL_FAILED", "CANCELLED"):
                        break

                if p["state"] == "CANONIZED":
                    r = httpx.get(f"{BASE}/works/{WORK_ID}/chapters/{ch_no}", headers=h, timeout=30)
                    if r.status_code == 200:
                        ch = r.json()
                        print(f"  ✅ 第 {ch_no} 章: 《{ch['title']}》 {ch['word_count']}字")
                        print(f"     {ch['content'][:200]}...")
                else:
                    print(f"  ❌ 第 {ch_no} 章: {p['state']}")

    elif progress["state"] in ("RUNNING", "QUEUED"):
        # Still processing, just poll
        print("\n等待当前章节完成...")
        ch_no = progress["chapter_no"]
        for _ in range(120):
            time.sleep(5)
            r = httpx.get(f"{BASE}/works/{WORK_ID}/runs", headers=h, timeout=30)
            if r.status_code != 200:
                continue
            p = r.json()
            step = p.get("current_step", "-")
            state = p["state"]
            steps = p.get("steps", {})
            done_steps = [s for s, st in steps.items() if st == "SUCCEEDED"]
            print(f"  [{state}] step={step} done={done_steps}")
            if state in ("CANONIZED", "TERMINAL_FAILED", "CANCELLED"):
                break

        if p["state"] == "CANONIZED":
            r = httpx.get(f"{BASE}/works/{WORK_ID}/chapters/{ch_no}", headers=h, timeout=30)
            if r.status_code == 200:
                ch = r.json()
                print(f"\n  ✅ 第 {ch_no} 章: 《{ch['title']}》 {ch['word_count']}字")
                print(f"     {ch['content'][:300]}...")

if __name__ == "__main__":
    main()

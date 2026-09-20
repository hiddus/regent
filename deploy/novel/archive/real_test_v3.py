"""真实测试 v3：网文爽点风格验证。"""
import httpx, time

BASE = "http://118.31.171.159:8000/v1/novel"
TARGET_CHAPTERS = 4

def main():
    print("=" * 60)
    print("《葬仙》v3 网文爽点风格测试")
    print("=" * 60)

    # Auth
    token = httpx.post(f"{BASE}/auth/session", json={"display_name": "葬仙v3"}, timeout=30).json()["token"]
    h = {"Authorization": f"Bearer {token}"}

    # Create work - 网文风格 premise
    print("\n[1] 创建作品…")
    r = httpx.post(f"{BASE}/works", headers=h, json={
        "raw_intent": (
            "沈听雨是青城的废柴盲女，灵根被夺，沦为全族笑柄。"
            "但她能听见葬仙谷的钟声——那是万古前仙路断绝时留下的余响。"
            "当所有人嘲笑她时，远古传承觉醒，她获得了逆天金手指。"
            "从此一路打脸碾压，从苍梧大陆打到天外虚空，"
            "揭开万古之前谁葬了仙的终极谜团。"
            "修炼体系：炼气筑基金丹元婴化神渡劫大乘真仙。"
        ),
        "title": "葬仙",
        "genre": "东方玄幻",
        "client_nonce": f"zangxian-v3-{time.time_ns()}",
    }, timeout=30)
    r.raise_for_status()
    work_id = r.json()["work_id"]
    onboarding = r.json()["onboarding"]
    print(f"  ✓ work_id: {work_id}")

    # Clarify
    print("\n[2] 澄清…")
    r = httpx.post(f"{BASE}/works/{work_id}/clarify", headers=h, json={
        "answers": {
            "genre": "东方玄幻",
            "cultivation": "废柴流（绝处逢生）",
            "desire": "变强",
            "conflict": "一个更强的对手",
        },
        "accept_defaults": False,
    }, timeout=30)
    r.raise_for_status()
    onboarding = r.json()

    # Choose direction
    cards = onboarding.get("directions", [])
    print(f"\n[3] 方向卡:")
    for c in cards:
        print(f"  [{c['card_id']}] {c['title']}")
        print(f"    爽点承诺: {c.get('genre_promise', '-')}")
    target = next((c for c in cards if "打脸" in c.get("title", "")), cards[0])
    print(f"\n  选择: {target['title']}")

    r = httpx.post(f"{BASE}/works/{work_id}/directions", headers=h, json={
        "card_id": target["card_id"], "client_nonce": f"dir-{time.time_ns()}",
    }, timeout=300)  # LLM 大纲生成需要更长时间
    r.raise_for_status()
    path_out = r.json()
    nodes = path_out.get("nodes", [])
    print(f"\n[4] 关键路径 ({len(nodes)} 节点):")
    for n in nodes[:8]:
        print(f"  [{n['ordinal']:2d}] {n['title']}")
    if len(nodes) > 8:
        print(f"  ... +{len(nodes)-8}")

    # Generate chapters
    print(f"\n[5] 生成 {TARGET_CHAPTERS} 章…")
    total_words = 0
    stats = []

    for ch_no in range(1, TARGET_CHAPTERS + 1):
        print(f"\n{'='*50}")
        print(f"第 {ch_no} 章")
        # Start run
        r = httpx.post(f"{BASE}/works/{work_id}/runs",
            headers={**h, "Idempotency-Key": f"run-{ch_no}-{time.time_ns()}"}, timeout=30)
        if r.status_code not in (200, 202):
            print(f"  失败: {r.status_code}")
            continue

        # Poll
        t0 = time.time()
        last_print = ""
        for _ in range(120):
            time.sleep(5)
            r = httpx.get(f"{BASE}/works/{work_id}/runs", headers=h, timeout=30)
            if r.status_code != 200: continue
            p = r.json()
            if p["chapter_no"] != ch_no: continue
            step = p.get("current_step", "-")
            state = p["state"]
            key = f"{step}:{state}"
            if key != last_print:
                done = [s for s, st in p.get("steps", {}).items() if st == "SUCCEEDED"]
                print(f"  [{time.time()-t0:5.0f}s] {step} → {state} ✓{done}")
                last_print = key
            if state in ("CANONIZED", "TERMINAL_FAILED", "CANCELLED"):
                break

        elapsed = time.time() - t0
        if p["state"] == "CANONIZED":
            r = httpx.get(f"{BASE}/works/{work_id}/chapters/{ch_no}", headers=h, timeout=30)
            if r.status_code == 200:
                ch = r.json()
                words = ch.get("word_count", 0)
                total_words += words
                stats.append({"no": ch_no, "title": ch.get("title",""), "words": words})
                print(f"\n  ✅ 《{ch.get('title','')}》 {words}字 / {elapsed:.0f}s")
                print(f"  内容:\n{ch.get('content','')[:500]}")
                print(f"  ...")
        else:
            print(f"  ❌ {p['state']}")

    # Report
    print(f"\n{'='*60}")
    print("报告")
    print(f"{'='*60}")
    print(f"完成: {len(stats)}/{TARGET_CHAPTERS} 章")
    print(f"总字数: {total_words}")
    if stats:
        avg = total_words // len(stats)
        print(f"平均: {avg} 字/章")
        for s in stats:
            print(f"  第{s['no']}章 《{s['title']}》 {s['words']}字")
    print(f"{'='*60}")

if __name__ == "__main__":
    main()

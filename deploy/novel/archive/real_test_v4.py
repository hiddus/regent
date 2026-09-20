"""真实测试 v4：求生+修仙，性格渐变但有坚守。"""
import httpx, time

BASE = "http://118.31.171.159:8000/v1/novel"
TARGET_CHAPTERS = 4

def main():
    print("=" * 60)
    print("求生+修仙 测试：性格渐变 + 坚守底线")
    print("=" * 60)

    # Auth
    token = httpx.post(f"{BASE}/auth/session", json={"display_name": "求生修仙v4"}, timeout=30).json()["token"]
    h = {"Authorization": f"Bearer {token}"}

    # Create work
    print("\n[1] 创建作品…")
    r = httpx.post(f"{BASE}/works", headers=h, json={
        "raw_intent": (
            "陆沉是边荒矿奴，十二岁被掳入玄铁矿脉，每日挖矿石换取一碗稀粥。"
            "矿脉深处有上古修士遗留的禁制，矿奴触之即死，尸体被矿监扔进废矿坑。"
            "陆沉在矿坑中捡到一枚残破的储物戒，里面有一卷无名竹简——"
            "不是功法，而是一位散修的生前手记，记录了他如何在绝境中苟活百年。"
            "陆沉没有灵根，但他从手记中学会了一件事：活着本身就是修炼。"
            "他用矿渣淬体，以毒虫为食，在矿脉底层一步步走出自己的路。"
            "他变强了，也变得冷漠了——但他始终不杀无辜之人。"
            "这是他唯一的底线，也是他与这个世界的最后一点温柔。"
            "修炼体系：淬体→炼气→筑基→金丹→元婴→化神。"
            "世界观：修仙界弱肉强食，散修如蝼蚁，宗门垄断资源。"
        ),
        "title": "矿奴修仙传",
        "genre": "东方玄幻",
        "client_nonce": f"kuangnu-v4-{time.time_ns()}",
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
            "cultivation": "凡人流（苦修逆袭）",
            "desire": "活下去",
            "conflict": "修仙界的弱肉强食",
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
        print(f"    承诺: {c.get('genre_promise', '-')}")
    # 优先选悬疑修炼流（最契合"求生+渐变"主题）
    target = next((c for c in cards if "悬疑" in c.get("title", "")), cards[0])
    print(f"\n  选择: {target['title']}")

    r = httpx.post(f"{BASE}/works/{work_id}/directions", headers=h, json={
        "card_id": target["card_id"], "client_nonce": f"dir-{time.time_ns()}",
    }, timeout=300)
    r.raise_for_status()
    path_out = r.json()
    nodes = path_out.get("nodes", [])
    print(f"\n[4] 关键路径 ({len(nodes)} 节点):")
    for n in nodes[:10]:
        print(f"  [{n['ordinal']:2d}] {n['title']}")
        if n.get('promise'):
            print(f"       承诺: {n['promise']}")
    if len(nodes) > 10:
        print(f"  ... +{len(nodes)-10}")

    # Generate chapters
    print(f"\n[5] 生成 {TARGET_CHAPTERS} 章…")
    total_words = 0
    stats = []

    for ch_no in range(1, TARGET_CHAPTERS + 1):
        print(f"\n{'='*50}")
        print(f"第 {ch_no} 章")
        r = httpx.post(f"{BASE}/works/{work_id}/runs",
            headers={**h, "Idempotency-Key": f"run-{ch_no}-{time.time_ns()}"}, timeout=30)
        if r.status_code not in (200, 202):
            print(f"  失败: {r.status_code}")
            continue

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
                print(f"  内容:\n{ch.get('content','')[:600]}")
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

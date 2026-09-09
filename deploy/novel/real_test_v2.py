"""真实测试 v2：东方玄幻长篇小说生成验证（轮询方式）。"""
import httpx, time, sys

BASE = "http://118.31.171.159:8000/v1/novel"
TARGET_CHAPTERS = 6

def main():
    # Auth
    print("[1] 认证…")
    token = httpx.post(f"{BASE}/auth/session", json={"display_name": "葬仙v2"}, timeout=30).json()["token"]
    h = {"Authorization": f"Bearer {token}"}
    print(f"  ✓ token: {token[:20]}...")

    # Create work
    print("\n[2] 创建《葬仙》…")
    r = httpx.post(f"{BASE}/works", headers=h, json={
        "raw_intent": (
            "青城小镇永远在下雨，盲女沈听雨是唯一能听见葬仙谷钟声的人。"
            "钟声意味着万古前仙路断绝的封印正在松动。"
            "她必须踏上修仙之路，穿越苍梧大陆、四海仙域、天外虚空，"
            "揭开万古之前谁葬了仙的终极谜团。修炼体系：炼气筑基金丹元婴化神渡劫大乘真仙。"
        ),
        "title": "葬仙",
        "genre": "东方玄幻",
        "client_nonce": f"zangxian-v2-{time.time_ns()}",
    }, timeout=30)
    r.raise_for_status()
    work_id = r.json()["work_id"]
    onboarding = r.json()["onboarding"]
    print(f"  ✓ work_id: {work_id}")
    print(f"  ✓ 澄清问题: {len(onboarding.get('questions', []))} 个")
    for q in onboarding.get("questions", []):
        print(f"    - {q['prompt']}: {q['options']}")

    # Clarify
    print("\n[3] 澄清…")
    r = httpx.post(f"{BASE}/works/{work_id}/clarify", headers=h, json={
        "answers": {
            "genre": "东方玄幻",
            "cultivation": "凡人流（苦修逆袭）",
            "desire": "变强",
            "conflict": "规则本身",
        },
        "accept_defaults": False,
    }, timeout=30)
    r.raise_for_status()
    onboarding = r.json()
    print(f"  ✓ status: {onboarding['status']}")

    # Choose direction
    cards = onboarding.get("directions", [])
    target = next((c for c in cards if "升级" in c.get("title", "")), cards[0] if cards else None)
    if not target:
        print("ERROR: no direction cards")
        return
    print(f"\n[4] 选择: {target['title']}")
    r = httpx.post(f"{BASE}/works/{work_id}/directions", headers=h, json={
        "card_id": target["card_id"], "client_nonce": f"dir-{time.time_ns()}",
    }, timeout=30)
    r.raise_for_status()
    path_out = r.json()
    nodes = path_out.get("nodes", [])
    print(f"  ✓ 节点: {len(nodes)} 个")
    for n in nodes[:6]:
        print(f"    [{n['ordinal']:2d}] {n['title']}")
    if len(nodes) > 6:
        print(f"    ... +{len(nodes)-6} 个")

    # Check volumes
    print("\n[5] 卷结构…")
    r = httpx.get(f"{BASE}/works/{work_id}/volumes", headers=h, timeout=30)
    if r.status_code == 200:
        vols = r.json()
        for v in vols:
            print(f"  第{v['volume_no']}卷: {v['title']} | {v['start_chapter_no']}–{v['end_chapter_no']}章 | {v['state']}")
            print(f"    弧段: {len(v.get('arcs',[]))} 个")
    else:
        print(f"  volumes API: {r.status_code}")

    # Generate chapters using polling
    print(f"\n[6] 生成 {TARGET_CHAPTERS} 章（轮询模式）…")
    total_words = 0
    stats = []

    for ch_no in range(1, TARGET_CHAPTERS + 1):
        print(f"\n{'='*50}")
        print(f"第 {ch_no} 章")
        print(f"{'='*50}")

        # Start run
        r = httpx.post(f"{BASE}/works/{work_id}/runs",
            headers={**h, "Idempotency-Key": f"run-ch{ch_no}-{time.time_ns()}"}, timeout=30)
        if r.status_code not in (200, 202):
            print(f"  start_run 失败: {r.status_code} {r.text[:200]}")
            continue

        # Poll until done (max 10 min)
        t0 = time.time()
        last_step = ""
        for i in range(120):
            time.sleep(5)
            r = httpx.get(f"{BASE}/works/{work_id}/runs", headers=h, timeout=30)
            if r.status_code != 200:
                continue
            p = r.json()
            if p["chapter_no"] != ch_no:
                continue
            step = p.get("current_step", "-")
            state = p["state"]
            steps = p.get("steps", {})

            # Print step transitions
            step_key = f"{step}:{state}"
            if step_key != last_step:
                elapsed = time.time() - t0
                done = [s for s, st in steps.items() if st == "SUCCEEDED"]
                mark = "✓" if steps.get(step) == "SUCCEEDED" else "…"
                print(f"  [{elapsed:5.0f}s] {mark} {step} → {state} (done: {done})")
                last_step = step_key

            if state in ("CANONIZED", "TERMINAL_FAILED", "CANCELLED"):
                break

        elapsed = time.time() - t0
        if p["state"] == "CANONIZED":
            r = httpx.get(f"{BASE}/works/{work_id}/chapters/{ch_no}", headers=h, timeout=30)
            if r.status_code == 200:
                ch = r.json()
                words = ch.get("word_count", len(ch.get("content", "")))
                total_words += words
                stats.append({"no": ch_no, "title": ch.get("title",""), "words": words, "time": round(elapsed)})
                print(f"\n  ✅ 《{ch.get('title','')}》 {words}字 / {elapsed:.0f}s")
                # Show first 300 chars
                content = ch.get("content", "")
                print(f"  预览: {content[:300]}…")
            else:
                print(f"  get_chapter 失败: {r.status_code}")
        else:
            print(f"\n  ❌ {p['state']}")

    # Report
    print(f"\n{'='*60}")
    print("《葬仙》生成报告")
    print(f"{'='*60}")
    print(f"作品: {work_id}")
    print(f"节点: {len(nodes)}")
    print(f"完成: {len(stats)}/{TARGET_CHAPTERS} 章")
    print(f"总字数: {total_words}")
    if stats:
        avg = total_words // len(stats)
        print(f"平均: {avg} 字/章")
        for s in stats:
            print(f"  第{s['no']}章 《{s['title']}》 {s['words']}字 / {s['time']}s")
        # 30万字外推
        if avg > 0:
            need_ch = 300000 // avg
            need_vol = need_ch // 25
            print(f"\n30万字外推: 需{need_ch}章 / {need_vol}卷")
    print(f"{'='*60}")

if __name__ == "__main__":
    main()

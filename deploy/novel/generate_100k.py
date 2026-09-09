"""生成 10 万字小说（健壮版）。

特性：
- 自动检测卡住的章节（超过 20 分钟未完成则跳过）
- 支持断点续传（如果章节已生成则跳过）
- 每章完成后等待 2 秒再启动下一章（避免 worker 过载）
- 详细的进度日志
"""
import httpx, time, json, sys
from pathlib import Path

BASE = "http://118.31.171.159:8000/v1/novel"
TARGET_CHAPTERS = 28  # 28 章 × ~3500 字 ≈ 10 万字
CHAPTER_TIMEOUT = 1200  # 20 分钟超时
OUTPUT_DIR = Path("novel_output")


def auth(name="novel-gen"):
    r = httpx.post(f"{BASE}/auth/session", json={"display_name": name}, timeout=30)
    return r.json()["token"]


def get_run(h, work_id):
    r = httpx.get(f"{BASE}/works/{work_id}/runs", headers=h, timeout=30)
    if r.status_code != 200:
        return None
    return r.json()


def wait_for_chapter(h, work_id, ch_no, t0):
    """等待章节完成，返回 (state, elapsed)。"""
    while True:
        time.sleep(5)
        p = get_run(h, work_id)
        if p is None:
            return ("ERROR", time.time() - t0)
        if p["chapter_no"] != ch_no:
            return ("WRONG_CHAPTER", time.time() - t0)
        
        step = p.get("current_step", "-")
        state = p["state"]
        elapsed = time.time() - t0
        done = [s for s, st in p.get("steps", {}).items() if st == "SUCCEEDED"]
        
        # 进度日志（每 30 秒打印一次）
        if int(elapsed) % 30 < 6:
            print(f"  [{elapsed:5.0f}s] {step} → {state} done={done}", flush=True)
        
        if state in ("CANONIZED", "TERMINAL_FAILED", "CANCELLED"):
            return (state, elapsed)
        
        if elapsed > CHAPTER_TIMEOUT:
            print(f"  ⚠️ 超时 ({CHAPTER_TIMEOUT}s)，跳过本章", flush=True)
            return ("TIMEOUT", elapsed)


def main():
    OUTPUT_DIR.mkdir(exist_ok=True)
    
    print("=" * 60)
    print("10 万字小说生成器")
    print(f"目标: {TARGET_CHAPTERS} 章 × ~3500 字 ≈ {TARGET_CHAPTERS * 3500:,} 字")
    print("=" * 60)

    # Auth
    token = auth()
    h = {"Authorization": f"Bearer {token}"}

    # 创建作品
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
        "client_nonce": f"novel-100k-{time.time_ns()}",
    }, timeout=30)
    r.raise_for_status()
    work_id = r.json()["work_id"]
    print(f"  ✓ work_id: {work_id}")

    # 澄清
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

    # 选择方向
    cards = onboarding.get("directions", [])
    print(f"\n[3] 方向卡:")
    for c in cards:
        print(f"  [{c['card_id']}] {c['title']}")
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
    if len(nodes) > 10:
        print(f"  ... +{len(nodes)-10}")

    # 生成章节
    print(f"\n[5] 生成 {TARGET_CHAPTERS} 章…")
    total_words = 0
    stats = []
    failed_chapters = []

    for ch_no in range(1, TARGET_CHAPTERS + 1):
        print(f"\n{'='*50}")
        print(f"第 {ch_no}/{TARGET_CHAPTERS} 章")
        
        # 检查是否已存在
        r = httpx.get(f"{BASE}/works/{work_id}/chapters/{ch_no}", headers=h, timeout=30)
        if r.status_code == 200:
            ch = r.json()
            words = ch.get("word_count", 0)
            total_words += words
            stats.append({"no": ch_no, "title": ch.get("title",""), "words": words})
            print(f"  ⏭️ 已存在，跳过 ({words}字)")
            # 保存内容
            out_file = OUTPUT_DIR / f"chapter_{ch_no:03d}.txt"
            out_file.write_text(ch.get("content", ""), encoding="utf-8")
            continue

        # 启动新 run
        r = httpx.post(f"{BASE}/works/{work_id}/runs",
            headers={**h, "Idempotency-Key": f"run-{ch_no}-{time.time_ns()}"}, timeout=30)
        if r.status_code not in (200, 202):
            print(f"  ❌ 启动失败: {r.status_code}")
            failed_chapters.append(ch_no)
            continue

        t0 = time.time()
        state, elapsed = wait_for_chapter(h, work_id, ch_no, t0)

        if state == "CANONIZED":
            r = httpx.get(f"{BASE}/works/{work_id}/chapters/{ch_no}", headers=h, timeout=30)
            if r.status_code == 200:
                ch = r.json()
                words = ch.get("word_count", 0)
                total_words += words
                stats.append({"no": ch_no, "title": ch.get("title",""), "words": words})
                print(f"\n  ✅ 《{ch.get('title','')}》 {words}字 / {elapsed:.0f}s")
                # 保存内容
                out_file = OUTPUT_DIR / f"chapter_{ch_no:03d}.txt"
                out_file.write_text(ch.get("content", ""), encoding="utf-8")
                print(f"  💾 已保存: {out_file}")
        else:
            print(f"  ❌ {state} ({elapsed:.0f}s)")
            failed_chapters.append(ch_no)

        # 等待 2 秒再启动下一章
        time.sleep(2)

    # 最终报告
    print(f"\n{'='*60}")
    print("生成报告")
    print(f"{'='*60}")
    print(f"完成: {len(stats)}/{TARGET_CHAPTERS} 章")
    print(f"失败: {len(failed_chapters)} 章 {failed_chapters}")
    print(f"总字数: {total_words:,}")
    if stats:
        avg = total_words // len(stats)
        print(f"平均: {avg} 字/章")
        print(f"\n章节详情:")
        for s in stats:
            print(f"  第{s['no']:2d}章 《{s['title'][:20]:20s}》 {s['words']:5d}字")
    print(f"\n输出目录: {OUTPUT_DIR.absolute()}")
    print(f"{'='*60}")

    # 保存统计
    stats_file = OUTPUT_DIR / "generation_stats.json"
    stats_file.write_text(json.dumps({
        "work_id": work_id,
        "target_chapters": TARGET_CHAPTERS,
        "completed_chapters": len(stats),
        "failed_chapters": failed_chapters,
        "total_words": total_words,
        "chapters": stats,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"统计已保存: {stats_file}")


if __name__ == "__main__":
    main()

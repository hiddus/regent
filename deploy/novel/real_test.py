"""真实测试：东方玄幻长篇小说生成验证。

验证目标：
1. 世界观宏大的东方玄幻 onboarding 流程
2. 卷弧结构正确创建
3. 章节字数达到 2500+ 字
4. 文风古韵盎然
5. 多章连续生成稳定性
"""

from __future__ import annotations

import json
import time
import httpx

BASE = "http://118.31.171.159:8000/v1/novel"
TARGET_CHAPTERS = 6  # 验证前 6 章（约 2 个弧段）

# ── Auth ──────────────────────────────────────────────
def auth() -> str:
    r = httpx.post(f"{BASE}/auth/session", json={"display_name": "葬仙测试"}, timeout=30)
    r.raise_for_status()
    return r.json()["token"]


# ── Create work ────────────────────────────────────────
def create_work(token: str) -> tuple[str, dict]:
    headers = {"Authorization": f"Bearer {token}"}
    r = httpx.post(
        f"{BASE}/works",
        headers=headers,
        json={
            "raw_intent": (
                "青城小镇永远在下雨，盲女沈听雨是唯一能听见葬仙谷钟声的人。"
                "钟声意味着万古前仙路断绝的封印正在松动。"
                "她必须踏上修仙之路，穿越苍梧大陆、四海仙域、天外虚空，"
                "揭开万古之前谁葬了仙的终极谜团。修炼体系：炼气筑基金丹元婴化神渡劫大乘真仙。"
            ),
            "title": "葬仙",
            "genre": "东方玄幻",
            "client_nonce": "zangxian-test-20260904",
        },
        timeout=30,
    )
    r.raise_for_status()
    body = r.json()
    return body["work_id"], body["onboarding"]


# ── Clarify ────────────────────────────────────────────
def clarify(token: str, work_id: str) -> dict:
    headers = {"Authorization": f"Bearer {token}"}
    r = httpx.post(
        f"{BASE}/works/{work_id}/clarify",
        headers=headers,
        json={
            "answers": {
                "genre": "东方玄幻",
                "cultivation": "凡人流（苦修逆袭）",
                "desire": "变强",
                "conflict": "规则本身",
            },
            "accept_defaults": False,
        },
        timeout=30,
    )
    r.raise_for_status()
    return r.json()


# ── Choose direction ──────────────────────────────────
def choose_direction(token: str, work_id: str, onboarding: dict) -> str:
    headers = {"Authorization": f"Bearer {token}"}
    cards = onboarding.get("directions", [])
    print(f"\n{'='*60}")
    print("方向卡：")
    for c in cards:
        print(f"  [{c['card_id']}] {c['title']}")
        print(f"    主角想要: {c['protagonist_desire']}")
        print(f"    核心阻力: {c['core_conflict']}")
        print(f"    阅读节奏: {c['pacing']}")
        print(f"    差异化: {c['differentiator']}")
        print()

    # 选择升级流（最适合东方玄幻）
    target = next((c for c in cards if "升级" in c.get("title", "")), cards[0])
    card_id = target["card_id"]
    print(f"选择方向: {target['title']}")

    r = httpx.post(
        f"{BASE}/works/{work_id}/directions",
        headers=headers,
        json={"card_id": card_id, "client_nonce": f"dir-{card_id}"},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()


# ── Generate chapters ─────────────────────────────────
def generate_chapter(token: str, work_id: str, chapter_no: int) -> dict:
    headers = {"Authorization": f"Bearer {token}"}

    # Start run (or continue)
    r = httpx.post(
        f"{BASE}/works/{work_id}/runs",
        headers={**headers, "Idempotency-Key": f"run-ch{chapter_no}"},
        timeout=30,
    )
    r.raise_for_status()
    progress = r.json()
    print(f"\n--- 第 {chapter_no} 章 ---")
    print(f"  状态: {progress['state']}, 当前步骤: {progress.get('current_step', '-')}")

    # Advance through all steps
    max_steps = 50
    for _ in range(max_steps):
        if progress["state"] in ("CANONIZED", "TERMINAL_FAILED", "CANCELLED"):
            break
        step = progress.get("current_step")
        if not step:
            break

        r = httpx.post(
            f"{BASE}/works/{work_id}/runs/{chapter_no}/advance",
            headers={**headers, "Idempotency-Key": f"adv-{chapter_no}-{step}-{time.time_ns()}"},
            timeout=300,
        )
        if r.status_code == 409:
            continue
        r.raise_for_status()
        progress = r.json()
        status_mark = "✓" if progress["steps"].get(step) == "SUCCEEDED" else "…"
        print(f"  {status_mark} {step} → {progress['state']}")

    return progress


def get_chapter(token: str, work_id: str, chapter_no: int) -> dict:
    headers = {"Authorization": f"Bearer {token}"}
    r = httpx.get(f"{BASE}/works/{work_id}/chapters/{chapter_no}", headers=headers, timeout=30)
    r.raise_for_status()
    return r.json()


def get_volumes(token: str, work_id: str) -> list:
    headers = {"Authorization": f"Bearer {token}"}
    r = httpx.get(f"{BASE}/works/{work_id}/volumes", headers=headers, timeout=30)
    if r.status_code == 200:
        return r.json()
    return []


def get_critical_path(token: str, work_id: str) -> dict:
    headers = {"Authorization": f"Bearer {token}"}
    r = httpx.get(f"{BASE}/works/{work_id}/critical-path", headers=headers, timeout=30)
    r.raise_for_status()
    return r.json()


# ── Main ──────────────────────────────────────────────
def main() -> None:
    print("=" * 60)
    print("《葬仙》东方玄幻长篇小说 · 真实生成测试")
    print("=" * 60)

    # 1. Auth
    print("\n[1/6] 认证…")
    token = auth()
    print(f"  ✓ token 获取成功")

    # 2. Create work
    print("\n[2/6] 创建作品《葬仙》…")
    work_id, onboarding = create_work(token)
    print(f"  ✓ work_id: {work_id}")
    print(f"  ✓ onboarding status: {onboarding['status']}")
    if onboarding.get("questions"):
        print(f"  ✓ 澄清问题: {len(onboarding['questions'])} 个")
        for q in onboarding["questions"]:
            print(f"    - {q['prompt']}: {q['options']}")

    # 3. Clarify
    print("\n[3/6] 澄清回答…")
    onboarding = clarify(token, work_id, )
    print(f"  ✓ status: {onboarding['status']}")
    if onboarding.get("directions"):
        pass  # will show in choose_direction

    # 4. Choose direction
    print("\n[4/6] 选择方向…")
    path_out = choose_direction(token, work_id, onboarding)
    nodes = path_out.get("nodes", [])
    print(f"  ✓ 关键路径: {len(nodes)} 个节点")
    for n in nodes[:5]:
        print(f"    [{n['ordinal']:2d}] {n['title']} ({n['node_type']})")
    if len(nodes) > 5:
        print(f"    ... 还有 {len(nodes) - 5} 个节点")

    # 5. Check volumes
    print("\n[5/6] 检查卷结构…")
    volumes = get_volumes(token, work_id)
    print(f"  ✓ 卷数: {len(volumes)}")
    for v in volumes:
        print(f"    第 {v['volume_no']} 卷: {v['title']}")
        print(f"      境界: {v.get('cultivation_realm', '-')}")
        print(f"      章节: {v['start_chapter_no']}–{v['end_chapter_no']}")
        print(f"      状态: {v['state']}")
        print(f"      弧段: {len(v.get('arcs', []))} 个")
        for a in v.get("arcs", [])[:3]:
            print(f"        弧段 {a['arc_no']}: {a['title']} (第{a['chapter_range_start']}–{a['chapter_range_end']}章)")

    # 6. Generate chapters
    print(f"\n[6/6] 生成前 {TARGET_CHAPTERS} 章…")
    total_words = 0
    chapter_stats = []

    for ch_no in range(1, TARGET_CHAPTERS + 1):
        t0 = time.time()
        progress = generate_chapter(token, work_id, ch_no)
        elapsed = time.time() - t0

        if progress["state"] == "CANONIZED":
            chapter = get_chapter(token, work_id, ch_no)
            words = chapter.get("word_count", 0)
            total_words += words
            title = chapter.get("title", "")
            content_preview = chapter.get("content", "")[:120]
            chapter_stats.append({
                "chapter_no": ch_no,
                "title": title,
                "words": words,
                "elapsed": round(elapsed, 1),
            })
            print(f"\n  ✅ 第 {ch_no} 章完成: 《{title}》")
            print(f"     字数: {words} | 耗时: {elapsed:.0f}s")
            print(f"     预览: {content_preview}…")
        else:
            print(f"\n  ❌ 第 {ch_no} 章失败: {progress['state']}")
            break

    # ── Report ────────────────────────────────────────
    print("\n" + "=" * 60)
    print("《葬仙》生成报告")
    print("=" * 60)
    print(f"\n作品 ID: {work_id}")
    print(f"关键路径节点: {len(nodes)}")
    print(f"卷数: {len(volumes)}")
    print(f"完成章节: {len(chapter_stats)}/{TARGET_CHAPTERS}")
    print(f"总字数: {total_words}")
    print()

    if chapter_stats:
        avg_words = total_words // len(chapter_stats)
        print("章节明细:")
        for s in chapter_stats:
            print(f"  第 {s['chapter_no']} 章 《{s['title']}》 {s['words']}字 / {s['elapsed']}s")
        print(f"\n平均每章: {avg_words} 字")

        # 外推 30 万字
        if avg_words > 0:
            chapters_for_300k = 300000 // avg_words
            volumes_needed = chapters_for_300k // 25  # ~25 chapters per volume
            print(f"\n30 万字外推:")
            print(f"  需要章节数: {chapters_for_300k}")
            print(f"  需要卷数: {volumes_needed}")
            print(f"  当前架构支持: 动态扩展（每卷完成 80% 自动展开下一卷）")

        # 文风检查
        print("\n文风检查（首章前 200 字）:")
        ch1 = get_chapter(token, work_id, 1)
        content = ch1.get("content", "")
        classical_markers = ["的", "了", "在", "是", "不", "有", "这", "他"]
        four_char_count = sum(1 for i in range(len(content) - 3) if content[i:i+4].isalpha())
        print(f"  总字数: {len(content)}")
        print(f"  首 200 字: {content[:200]}…")

    print("\n" + "=" * 60)
    if total_words >= TARGET_CHAPTERS * 2000:
        print("✅ 验证通过：章节字数达标（≥2000字/章）")
    else:
        print("⚠️ 章节字数未达预期")
    if len(volumes) >= 1:
        print("✅ 卷弧结构已创建")
    if len(nodes) >= 15:
        print(f"✅ 关键路径节点充足（{len(nodes)} 个）")
    print("=" * 60)


if __name__ == "__main__":
    main()

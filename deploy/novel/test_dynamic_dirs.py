"""测试 LLM 动态生成方向卡。"""
import httpx, time

BASE = "http://118.31.171.159:8000/v1/novel"

def main():
    token = httpx.post(f"{BASE}/auth/session", json={"display_name": "test-dir"}, timeout=30).json()["token"]
    h = {"Authorization": f"Bearer {token}"}

    r = httpx.post(f"{BASE}/works", headers=h, json={
        "raw_intent": (
            "陆沉是边荒矿奴，十二岁被入玄铁矿脉，每日挖矿石换取一碗稀粥。"
            "矿脉深处有上古修士遗留的禁制，矿奴触之即死。"
            "陆沉在矿坑中捡到一枚残破的储物戒，里面有一卷无名竹简——"
            "不是功法，而是一位散修的生前手记，记录了他如何在绝境中苟活百年。"
            "陆沉没有灵根，但他从手记中学会了一件事：活着本身就是修炼。"
            "他用矿渣淬体，以毒虫为食，在矿脉底层一步步走出自己的路。"
            "他变强了，也变得冷漠了——但他始终不杀无辜之人。"
        ),
        "title": "矿奴修仙传",
        "genre": "东方玄幻",
        "client_nonce": f"dir-test-{time.time_ns()}",
    }, timeout=300)
    r.raise_for_status()
    data = r.json()
    work_id = data["work_id"]
    ob = data.get("onboarding", {})
    qs = ob.get("questions", [])

    answers = {}
    for q in qs:
        opts = q.get("options", [])
        answers[q["question_id"]] = opts[0] if opts else q.get("default_assumption", "")

    print(f"Answers: {answers}")

    r2 = httpx.post(f"{BASE}/works/{work_id}/clarify", headers=h, json={
        "answers": answers,
        "accept_defaults": False,
    }, timeout=300)
    r2.raise_for_status()
    ob2 = r2.json()
    dirs = ob2.get("directions", [])
    print(f"\n方向卡 ({len(dirs)}):")
    for i, d in enumerate(dirs):
        print(f"\n--- 方向 {i+1} ---")
        print(f"  标题: {d.get('title', '')}")
        print(f"  主角: {d.get('protagonist_desire', '')}")
        print(f"  冲突: {d.get('core_conflict', '')}")
        print(f"  承诺: {d.get('genre_promise', '')}")
        print(f"  节奏: {d.get('pacing', '')}")
        print(f"  差异: {d.get('differentiator', '')}")

if __name__ == "__main__":
    main()

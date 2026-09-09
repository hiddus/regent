"""测试 LLM 动态生成澄清问题。"""
import httpx, time

BASE = "http://118.31.171.159:8000/v1/novel"

def main():
    token = httpx.post(f"{BASE}/auth/session", json={"display_name": "test-dynamic"}, timeout=30).json()["token"]
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
        "client_nonce": f"dynamic-test-{time.time_ns()}",
    }, timeout=300)
    r.raise_for_status()
    data = r.json()
    ob = data.get("onboarding", {})
    qs = ob.get("questions", [])
    print(f"Status: {ob.get('status')}")
    print(f"Questions ({len(qs)}):")
    for q in qs:
        print(f"  [{q['question_id']}] {q['prompt']}")
        print(f"    options: {q.get('options', [])}")
        print(f"    default: {q.get('default_assumption', '')}")
        print()

if __name__ == "__main__":
    main()

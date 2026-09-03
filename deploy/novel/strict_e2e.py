"""Strict production E2E: two real chapters plus share/export contracts."""

from __future__ import annotations

import os
import time
import uuid
from urllib.parse import urlparse

import httpx


BASE = f"http://{os.environ.get('SERVER_IP', '118.31.171.159')}:8000"


def main() -> None:
    with httpx.Client(base_url=BASE, timeout=60) as client:
        session = client.post(
            "/v1/novel/auth/session",
            params={"subject": f"strict-e2e-{uuid.uuid4().hex[:10]}"},
        )
        session.raise_for_status()
        headers = {"Authorization": f"Bearer {session.json()['token']}"}

        created = client.post(
            "/v1/novel/works",
            headers=headers,
            json={
                "raw_intent": (
                    "一个能听见旧物记忆的修表匠，为寻找失踪的父亲，追查一块倒着走的怀表。"
                    "对手是控制城市时间交易的钟楼协会，冲突会迫使他在真相与亲人之间选择。"
                ),
                "genre": "都市奇幻",
                "client_nonce": uuid.uuid4().hex,
            },
        )
        created.raise_for_status()
        work_id = created.json()["work_id"]
        onboarding = created.json()["onboarding"]
        if onboarding["questions"]:
            clarified = client.post(
                f"/v1/novel/works/{work_id}/clarify",
                headers=headers,
                json={"answers": {}, "accept_defaults": True},
            )
            clarified.raise_for_status()
            onboarding = clarified.json()
        card_id = onboarding["directions"][0]["card_id"]
        direction = client.post(
            f"/v1/novel/works/{work_id}/directions",
            headers=headers,
            json={"card_id": card_id, "client_nonce": uuid.uuid4().hex},
        )
        direction.raise_for_status()
        assert 10 <= len(direction.json()["nodes"]) <= 20

        for chapter_no in (1, 2):
            started = client.post(
                f"/v1/novel/works/{work_id}/runs",
                headers={**headers, "Idempotency-Key": uuid.uuid4().hex},
            )
            started.raise_for_status()
            assert started.json()["chapter_no"] == chapter_no
            deadline = time.monotonic() + 900
            while time.monotonic() < deadline:
                progress = client.get(
                    f"/v1/novel/works/{work_id}/runs", headers=headers
                )
                progress.raise_for_status()
                state = progress.json()["state"]
                if state == "CANONIZED":
                    break
                if state in {"TERMINAL_FAILED", "CANCELLED"}:
                    raise RuntimeError(f"chapter {chapter_no} ended in {state}")
                time.sleep(5)
            else:
                raise TimeoutError(f"chapter {chapter_no} did not finish")
            chapter = client.get(
                f"/v1/novel/works/{work_id}/chapters/{chapter_no}", headers=headers
            )
            chapter.raise_for_status()
            assert chapter.json()["word_count"] >= 600
            print(f"chapter_{chapter_no}=PASS words={chapter.json()['word_count']}")

        events = client.get(f"/v1/novel/works/{work_id}/events", headers=headers)
        events.raise_for_status()
        types = {event["type"] for event in events.json()["events"]}
        assert "chapter.step_succeeded" in types and "chapter.done" in types
        print("durable_events=PASS")

        costs = client.get(f"/v1/novel/works/{work_id}/costs", headers=headers)
        costs.raise_for_status()
        assert costs.json()["consumed_minor"] > 0
        print("cost_ledger=PASS")

        share = client.post(
            f"/v1/novel/works/{work_id}/shares",
            headers=headers,
            json={"invitee_label": "E2E", "scope": "FULL", "expires_in_hours": 1},
        )
        share.raise_for_status()
        share_path = urlparse(share.json()["share_url"]).path
        token = share_path.rsplit("/", 1)[-1]
        public = client.get(f"/v1/novel/public/shares/{token}")
        public.raise_for_status()
        assert len(public.json()["chapters"]) == 2
        assert "noindex" in public.headers.get("x-robots-tag", "")
        print("public_share=PASS")

        notice = client.get(f"/v1/novel/works/{work_id}/export-notice", headers=headers)
        notice.raise_for_status()
        ack = client.post(
            f"/v1/novel/works/{work_id}/export-notice/acknowledge",
            headers=headers,
            json={"notice_version": notice.json()["notice_version"]},
        )
        ack.raise_for_status()
        export = client.post(
            f"/v1/novel/works/{work_id}/exports",
            headers=headers,
            json={"format": "txt"},
        )
        export.raise_for_status()
        content = client.get(urlparse(export.json()["download_url"]).path, headers=headers)
        content.raise_for_status()
        assert "AI" in content.text and len(content.content) > 1200
        print("export=PASS")
        print("STRICT_MVP_E2E=PASS")


if __name__ == "__main__":
    main()

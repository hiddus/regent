#!/usr/bin/env python3
"""缓存命中测量管线：默认可模拟；NOVEL_CACHE_LIVE=1 时走真实模型。

测量三组对照（同场重试 / 跨场 / 跨章），落盘：
- system 公共前缀长度
- 供应商返回的 cached_input_tokens（模拟或真实）
- price_book 计费口径（billed / avoided）

用法：
  PYTHONPATH=core/src python deploy/novel/run_cache_hit_measure.py
  NOVEL_CACHE_LIVE=1 PYTHONPATH=core/src python deploy/novel/run_cache_hit_measure.py --out deploy/novel/artifacts/cache_hit_measure.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve()
ROOT = next(
    (
        p
        for p in [_HERE.parents[i] for i in range(min(4, len(_HERE.parents)))]
        if (p / "core" / "src" / "regent").exists()
    ),
    Path("."),
)
_CORE = ROOT / "core" / "src"
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))

from pydantic import BaseModel, Field  # noqa: E402

from regent.model import ModelUsage, StructuredModelResponse  # noqa: E402
from regent.novel.domain.price_book import cache_usage_report  # noqa: E402
from regent.novel.domain.scene_card import SCENE_WRITE_SYSTEM  # noqa: E402
from regent.novel.experiments.quality_ab import BudgetMeter, MeteredProvider  # noqa: E402


class ProbeOut(BaseModel):
    ok: bool = True
    note: str = Field(default="cache-probe")


def _dump(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _common_prefix_len(a: str, b: str) -> int:
    n = min(len(a), len(b))
    i = 0
    while i < n and a[i] == b[i]:
        i += 1
    return i


class PrefixCacheSimProvider:
    """按「与上一请求的公共前缀」估算 cached_input_tokens，验证测量管线。"""

    model_name = "sim-cache-probe"

    def __init__(self) -> None:
        self._last_system = ""
        self._last_user = ""

    async def generate_structured(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        response_model: type[BaseModel],
        temperature: float = 0,
    ) -> StructuredModelResponse[Any]:
        prev = self._last_system + "\n" + self._last_user
        cur = system_prompt + "\n" + user_prompt
        pref = _common_prefix_len(prev, cur) if prev else 0
        # 字符≈token 粗估：用于管线，不宣称真实供应商命中
        cached = max(0, pref // 2) if prev else 0
        inp = max(8, len(cur) // 2)
        cached = min(cached, inp)
        self._last_system = system_prompt
        self._last_user = user_prompt
        out = response_model(ok=True, note=hashlib.sha1(cur.encode()).hexdigest()[:8])
        return StructuredModelResponse(
            output=out,
            usage=ModelUsage(
                input_tokens=inp,
                output_tokens=16,
                cached_input_tokens=cached,
                request_id="sim-cache",
            ),
            model=self.model_name,
        )


def _build_live_provider():
    from regent.config import get_settings
    from regent.model.factory import build_model_provider

    return build_model_provider(get_settings())


def _cases() -> list[tuple[str, str, dict[str, Any]]]:
    base_ctx = {
        "work_rules": "金手指规则固定",
        "canon": [{"statement": "沈星野已入组"}],
        "story_ledger_block": "【故事段】推进既有代价",
    }
    s1 = {
        "context": base_ctx,
        "scene_card": {"scene_id": "s1"},
        "working_state": {"a": "1"},
        "target_chars": 700,
    }
    s1_retry = {
        **s1,
        "previous_draft": "旧稿",
        "revision_instruction": "补节拍",
        "is_revision": True,
    }
    s2 = {
        "context": base_ctx,
        "scene_card": {"scene_id": "s2"},
        "working_state": {"a": "2"},
        "target_chars": 800,
    }
    ch2 = {
        "context": {**base_ctx, "chapter_no": 2, "story_ledger_block": "【故事段】第2章"},
        "scene_card": {"scene_id": "s1"},
        "working_state": {},
        "target_chars": 700,
    }
    return [
        ("warmup_same_scene", SCENE_WRITE_SYSTEM, s1),
        ("same_scene_retry", SCENE_WRITE_SYSTEM, s1_retry),
        ("cross_scene", SCENE_WRITE_SYSTEM, s2),
        ("cross_chapter", SCENE_WRITE_SYSTEM, ch2),
    ]


async def run_measure(*, live: bool) -> dict[str, Any]:
    provider = _build_live_provider() if live else PrefixCacheSimProvider()
    meter = BudgetMeter(call_cap=20, model_hint=getattr(provider, "model_name", "test") or "test")
    wrapped = MeteredProvider(provider, meter)  # type: ignore[arg-type]
    rows: list[dict[str, Any]] = []
    prev_blob = ""
    for name, system, payload in _cases():
        user = _dump(payload)
        blob = system + "\n" + user
        pref = _common_prefix_len(prev_blob, blob) if prev_blob else 0
        result = await wrapped.generate_structured(
            system_prompt=system,
            user_prompt=user,
            response_model=ProbeOut,
            temperature=0,
        )
        usage = result.usage
        report = cache_usage_report(
            getattr(result, "model", "") or meter.model_hint,
            input_tokens=int(usage.input_tokens or 0),
            output_tokens=int(usage.output_tokens or 0),
            cached_input_tokens=int(usage.cached_input_tokens or 0),
        )
        rows.append(
            {
                "case": name,
                "common_prefix_chars_vs_prev": pref,
                "usage": {
                    "input_tokens": usage.input_tokens,
                    "output_tokens": usage.output_tokens,
                    "cached_input_tokens": usage.cached_input_tokens,
                    "model": getattr(result, "model", ""),
                },
                "billing": report,
            }
        )
        prev_blob = blob
    return {
        "created_at": datetime.now(UTC).isoformat(),
        "mode": "live" if live else "sim",
        "note": (
            "live 模式记录供应商 cached_input_tokens；"
            "sim 模式用公共前缀估算以验证管线。前缀稳定≠真实命中。"
        ),
        "meter_cache_usage": meter.cache_report(),
        "calls": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument(
        "--live",
        action="store_true",
        default=os.environ.get("NOVEL_CACHE_LIVE", "").strip() in {"1", "true", "yes"},
    )
    args = parser.parse_args()
    import asyncio

    report = asyncio.run(run_measure(live=bool(args.live)))
    text = json.dumps(report, ensure_ascii=False, indent=2)
    print(text)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
        print(f"wrote {args.out}", file=sys.stderr)


if __name__ == "__main__":
    main()

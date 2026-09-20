"""D-12b：模型层测试改说 SSE。

``generate_structured`` 现在是流式接收，罐装的一次性 JSON 响应不再走同一条路——
不改测试的话它们要么红，要么因为"拿不到 usage"而**因为错误的理由**通过（那更糟：
测试还绿着，但守的东西已经不是它以为的那个）。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TARGET = ROOT / "tests/unit/model/test_provider.py"

HELPER = '''

def sse(
    content: str,
    *,
    model: str = "test-model",
    prompt_tokens: int = 4,
    completion_tokens: int = 2,
    finish_reason: str = "stop",
) -> httpx.Response:
    """把一次性 JSON 响应写成 SSE 流：generate_structured 现在是**流式接收**。

    末帧必须带 usage——拿不到 usage 等于按 0 记账，provider 会直接判失败而不是
    假装这次调用不要钱。
    """
    frames = [
        {"model": model, "choices": [{"delta": {"content": content}}]},
        {
            "model": model,
            "choices": [{"delta": {}, "finish_reason": finish_reason}],
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
            },
        },
    ]
    body = "".join(
        f"data: {json.dumps(frame, ensure_ascii=False)}\\n\\n" for frame in frames
    )
    return httpx.Response(
        200,
        content=(body + "data: [DONE]\\n\\n").encode("utf-8"),
        headers={"Content-Type": "text/event-stream"},
    )
'''

EDITS: list[tuple[str, str, str]] = [
    (
        "helper",
        '''async def test_openai_compatible_provider_validates_structured_output() -> None:''',
        HELPER.strip("\n")
        + '''


async def test_openai_compatible_provider_validates_structured_output() -> None:''',
    ),
    (
        "t1",
        '''        return httpx.Response(
            200,
            json={
                "model": "test-model",
                "choices": [{"message": {"content": '{"answer":"ok"}'}}],
                "usage": {"prompt_tokens": 4, "completion_tokens": 2},
            },
        )''',
        '''        return sse('{"answer":"ok"}')''',
    ),
    (
        "t2",
        '''        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": '{"answer":"ok"}'}}]},
        )''',
        '''        return sse('{"answer":"ok"}')''',
    ),
    (
        "t3",
        '''        return httpx.Response(
            200,
            json={
                "model": "test-model",
                "choices": [{"message": {"content": content}}],
                "usage": {"prompt_tokens": 4, "completion_tokens": 2},
            },
        )''',
        '''        return sse(content)''',
    ),
    (
        "t4",
        '''        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": '{"wrong":"shape"}'}}]},
        )''',
        '''        return sse('{"wrong":"shape"}')''',
    ),
    (
        "t5",
        '''        return httpx.Response(200, json={"choices": [{"message": {"content": "no"}}]})''',
        '''        return sse("no")''',
    ),
    (
        "t6",
        '''        return httpx.Response(
            200,
            json={
                "model": "test-model",
                "choices": [{"message": {"content": '{"answer":"ok"}'}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            },
        )''',
        '''        return sse('{"answer":"ok"}', prompt_tokens=1, completion_tokens=1)''',
    ),
]


def main() -> int:
    src = TARGET.read_text(encoding="utf-8")
    orig = src
    for name, old, new in EDITS:
        count = src.count(old)
        assert count == 1, f"{name}: anchor hit {count} times, expected 1"
        src = src.replace(old, new, 1)
    assert src != orig
    compile(src, str(TARGET), "exec")
    TARGET.write_text(src, encoding="utf-8")
    print(f"[OK] test_provider.py 已改写（{len(EDITS)} 处）")
    return 0


if __name__ == "__main__":
    sys.exit(main())

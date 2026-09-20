"""D-12：结构化输出改为「先流式接收，后校验」。

三个层次，缺一不可：

1. **流式接收**。httpx 的 timeout 量的是「两次读到字节之间的等待」，不是整通调用
   的墙钟。推理模型思考期间一个字节都不发——非流式会因为"沉默"被判超时，而实际
   上再等一会儿就成了；反过来，服务端每 299 秒吐一个字节就能把一通调用拖到无限
   长。流式把「等多久、等不等得到第一个字节」收回自己手里。

2. **收完再校验**。增量解析会把半截 JSON 当成"已经有结果"，被截断的输出会当成
   合法输出放行。所以先攒完整段，再走同一套 ``model_validate_json`` 判据——判据
   一次都没变，只是不再对着半成品判。

3. **usage 缺失必须报错，不能按 0 记账**。记账靠 usage，流式拿不到 usage 就等于
   按 0 计费，货币上限会被静默算穿。这是"先流式"引入的新风险面，必须显式堵上。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TARGET = ROOT / "core/src/regent/model/provider.py"

# ---------------------------------------------------------------- 构造参数
OLD_INIT = """        max_http_retries: int = 3,
        retry_deadline_seconds: float | None = None,
        thinking_mode: str = "disabled",
        client: httpx.AsyncClient | None = None,
    ) -> None:"""
NEW_INIT = """        max_http_retries: int = 3,
        retry_deadline_seconds: float | None = None,
        thinking_mode: str = "disabled",
        stream: bool = True,
        client: httpx.AsyncClient | None = None,
    ) -> None:"""

OLD_SELF = """        self._thinking_mode = mode
        # Budget must cover multiple slow 504/timeouts — not just one request."""
NEW_SELF = """        self._thinking_mode = mode
        # Kill switch: 流式接收有问题的场合可以退回一次性读取，不需要重新发版。
        self._stream = bool(stream)
        # Budget must cover multiple slow 504/timeouts — not just one request."""

# ---------------------------------------------------------------- 请求载荷
OLD_PAYLOAD = """                if self._max_output_tokens is not None:
                    payload["max_tokens"] = int(self._max_output_tokens)
                self._apply_thinking_mode(payload)
                # Same M1-2 HTTP retry as chat(): production artifact-backed
                # generation uses this path; previously 504 raised immediately.
                response = await self._post_chat_completions(client, payload)
                try:
                    body = response.json()"""
NEW_PAYLOAD = """                if self._max_output_tokens is not None:
                    payload["max_tokens"] = int(self._max_output_tokens)
                self._apply_thinking_mode(payload)
                # Same M1-2 HTTP retry as chat(): production artifact-backed
                # generation uses this path; previously 504 raised immediately.
                if self._stream:
                    payload["stream"] = True
                    # 没有这一项，最后一帧不带 usage —— 那就只能按 0 记账。
                    payload["stream_options"] = {"include_usage": True}
                    response = await self._post_chat_completions(client, payload, stream=True)
                    try:
                        body = await self._accumulate_stream(response)
                    finally:
                        await response.aclose()
                else:
                    response = await self._post_chat_completions(client, payload)
                    try:
                        body = response.json()
                    except ValueError as exc:
                        raise ModelOutputError("model response is not JSON") from exc
                try:"""

# ---------------------------------------------------------------- 重试循环支持流式
OLD_STATUS_BLOCK = """            status = int(response.status_code)
            self.last_http_attempts.append(
                {
                    "attempt": attempt,
                    "status": status,
                    "retryable": status in _RETRYABLE_STATUS,
                }
            )
            if status in _NO_RETRY_STATUS:
                response.raise_for_status()
            if status in _RETRYABLE_STATUS:
                if not self._should_retry_transport(attempt=attempt, started=started):
                    response.raise_for_status()
                retry_after = response.headers.get("Retry-After")
                await self._sleep_backoff(attempt=attempt, retry_after=retry_after)
                continue
            response.raise_for_status()
            return response"""

NEW_STATUS_BLOCK = """            status = int(response.status_code)
            self.last_http_attempts.append(
                {
                    "attempt": attempt,
                    "status": status,
                    "retryable": status in _RETRYABLE_STATUS,
                }
            )
            try:
                if status in _NO_RETRY_STATUS:
                    response.raise_for_status()
                if status in _RETRYABLE_STATUS:
                    if not self._should_retry_transport(attempt=attempt, started=started):
                        response.raise_for_status()
                    retry_after = response.headers.get("Retry-After")
                    await self._sleep_backoff(attempt=attempt, retry_after=retry_after)
                    continue
                response.raise_for_status()
            finally:
                # 流式响应体不读就丢会一直占着连接；只在 200 时交给调用方关闭。
                # continue 也会先走 finally，所以重试前一定会把连接还回去。
                if stream and status not in _OK_STATUS:
                    await response.aclose()
            return response"""

OLD_SIG = '''    async def _post_chat_completions(
        self, client: httpx.AsyncClient, payload: dict[str, Any]
    ) -> httpx.Response:
        """POST /chat/completions with M1-2 retry for 408/429/5xx and transport errors."""
        started = time.monotonic()
        attempt = 0
        while True:
            attempt += 1
            try:
                response = await client.post(
                    f"{self._base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    json=payload,
                )'''
NEW_SIG = '''    async def _post_chat_completions(
        self,
        client: httpx.AsyncClient,
        payload: dict[str, Any],
        *,
        stream: bool = False,
    ) -> httpx.Response:
        """POST /chat/completions with M1-2 retry for 408/429/5xx and transport errors.

        ``stream=True`` 时响应体**不读**，由调用方负责读完并关闭。
        """
        started = time.monotonic()
        attempt = 0
        while True:
            attempt += 1
            try:
                if stream:
                    request = client.build_request(
                        "POST",
                        f"{self._base_url}/chat/completions",
                        headers={"Authorization": f"Bearer {self._api_key}"},
                        json=payload,
                    )
                    response = await client.send(request, stream=True)
                else:
                    response = await client.post(
                        f"{self._base_url}/chat/completions",
                        headers={"Authorization": f"Bearer {self._api_key}"},
                        json=payload,
                    )'''

# ---------------------------------------------------------------- 流式累积
OLD_HELPER = '''    def _should_retry_transport(self, *, attempt: int, started: float) -> bool:'''
NEW_HELPER = '''    async def _accumulate_stream(self, response: httpx.Response) -> dict[str, Any]:
        """把 SSE 流攒成与非流式同形的响应信封；**校验不在这里做**。

        先收完整段再校验，是因为增量解析会把半截 JSON 当成"已经有结果"——被截断的
        输出会被当成合法输出放行。收完以后调用方走的是同一套 ``model_validate_json``
        判据，判据本身一次都没有放宽。
        """
        parts: list[str] = []
        model_name = self._model
        usage: dict[str, Any] = {}
        finish_reason = "stop"
        async for line in response.aiter_lines():
            line = line.strip()
            if not line or not line.startswith("data:"):
                continue
            data = line[len("data:"):].strip()
            if data == "[DONE]":
                break
            try:
                chunk = json.loads(data)
            except ValueError:
                # 心跳或注释行：不因为一行噪声判死整通调用。
                continue
            if chunk.get("model"):
                model_name = str(chunk["model"])
            chunk_usage = chunk.get("usage")
            if isinstance(chunk_usage, dict) and chunk_usage:
                usage = chunk_usage
            for choice in chunk.get("choices") or []:
                reason = choice.get("finish_reason")
                if isinstance(reason, str) and reason:
                    finish_reason = reason
                delta = choice.get("delta") or {}
                piece = delta.get("content")
                if isinstance(piece, str):
                    parts.append(piece)
        if not usage:
            # 记账靠 usage：拿不到就等于按 0 计费，货币上限会被静默算穿。
            # 宁可判失败（由调用方挂账等待对账），也不能假装这次调用不要钱。
            raise ModelOutputError("streamed response carried no usage; refusing to bill zero")
        return {
            "model": model_name,
            "choices": [
                {
                    "message": {"content": "".join(parts)},
                    "finish_reason": finish_reason,
                }
            ],
            "usage": usage,
        }

    def _should_retry_transport(self, *, attempt: int, started: float) -> bool:'''

# ---------------------------------------------------------------- 状态码集合
OLD_STATUS = '''_RETRYABLE_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 504})
_NO_RETRY_STATUS = frozenset({400, 401, 402, 403})'''
NEW_STATUS = '''_RETRYABLE_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 504})
_NO_RETRY_STATUS = frozenset({400, 401, 402, 403})
_OK_STATUS = frozenset({200})'''

EDITS = [
    ("init.param", OLD_INIT, NEW_INIT),
    ("init.self", OLD_SELF, NEW_SELF),
    ("status", OLD_STATUS, NEW_STATUS),
    ("sig", OLD_SIG, NEW_SIG),
    ("status_block", OLD_STATUS_BLOCK, NEW_STATUS_BLOCK),
    ("payload", OLD_PAYLOAD, NEW_PAYLOAD),
    ("helper", OLD_HELPER, NEW_HELPER),
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
    print(f"[OK] provider.py 已改写（{len(EDITS)} 处）")
    return 0


if __name__ == "__main__":
    sys.exit(main())

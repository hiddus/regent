import asyncio
import json
import logging
import random
import time
from dataclasses import dataclass
from typing import Any, Protocol, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from regent.model.chat import ChatMessage, ChatResponse, ChatUsage, ToolCall, ToolSpec

logger = logging.getLogger(__name__)

ResponseT = TypeVar("ResponseT", bound=BaseModel)

# M1-2: retryable HTTP statuses; 400/401/403 never retry.
_RETRYABLE_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 504})
_NO_RETRY_STATUS = frozenset({400, 401, 402, 403})
_OK_STATUS = frozenset({200})


class ModelConfigurationError(RuntimeError):
    pass


class ModelOutputError(RuntimeError):
    failure_code: str = "UNKNOWN"


class ModelTruncatedError(ModelOutputError):
    """finish_reason=length or output truncated before a complete tool turn."""

    failure_code = "MODEL_TRUNCATED"


class ToolCallInvalidError(ModelOutputError):
    """Malformed tool call envelope or non-JSON function arguments."""

    failure_code = "TOOL_CALL_INVALID"


def extract_cached_tokens(usage: dict[str, Any]) -> int | None:
    """Best-effort prompt-cache hit count from OpenAI-compatible usage blobs.

    Returns None when the provider omits cache fields (unknown), otherwise an int.
    """
    if not isinstance(usage, dict):
        return None
    for key in ("cached_tokens", "prompt_cache_hit_tokens", "cache_read_input_tokens"):
        if key in usage and usage.get(key) is not None:
            try:
                return max(0, int(usage[key]))
            except (TypeError, ValueError):
                pass
    details = usage.get("prompt_tokens_details")
    if isinstance(details, dict):
        for key in ("cached_tokens", "cache_read_input_tokens", "cached"):
            if key in details and details.get(key) is not None:
                try:
                    return max(0, int(details[key]))
                except (TypeError, ValueError):
                    pass
    return None


_REQUEST_ID_HEADERS = ("x-request-id", "x-fc-request-id", "cf-ray", "apim-request-id")


def extract_request_id(response: Any) -> str:
    """Best-effort supplier request id for reconciliation (Tech-Spec §4.4).

    Empty string means unknown: the caller must then treat an interrupted call
    as UNKNOWN and reconcile instead of blind-retrying.
    """
    headers = getattr(response, "headers", None)
    if headers is None:
        return ""
    for key in _REQUEST_ID_HEADERS:
        value = headers.get(key)
        if value:
            return str(value)[:128]
    return ""


@dataclass(frozen=True, slots=True)
class ModelUsage:
    input_tokens: int
    output_tokens: int
    # 供应商缓存命中的输入 token，单独计价；无报告时为 0。
    cached_input_tokens: int = 0
    # 供应商请求标识：外部结果不确定时用于查询/对账（Tech-Spec §4.4）。
    request_id: str = ""


@dataclass(frozen=True, slots=True)
class StructuredModelResponse[ResponseT: BaseModel]:
    output: ResponseT
    usage: ModelUsage
    model: str


class ModelProvider(Protocol):
    async def generate_structured(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        response_model: type[ResponseT],
        temperature: float = 0,
    ) -> StructuredModelResponse[ResponseT]: ...

    async def chat(
        self,
        *,
        messages: list[ChatMessage],
        tools: list[ToolSpec] | None = None,
        temperature: float = 0,
    ) -> ChatResponse: ...


class OpenAICompatibleProvider:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout_seconds: float = 180,
        max_structured_attempts: int = 2,
        max_output_tokens: int | None = 8192,
        max_http_retries: int = 3,
        retry_deadline_seconds: float | None = None,
        thinking_mode: str = "disabled",
        stream: bool = True,
        stream_idle_seconds: float | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not base_url or not api_key or not model:
            raise ModelConfigurationError("base URL, API key and model are required")
        if max_structured_attempts < 1:
            raise ModelConfigurationError("structured output attempts must be positive")
        if max_output_tokens is not None and max_output_tokens < 1:
            raise ModelConfigurationError("max_output_tokens must be positive when set")
        if max_http_retries < 0:
            raise ModelConfigurationError("max_http_retries must be >= 0")
        mode = str(thinking_mode or "disabled").strip().lower()
        if mode not in {"disabled", "enabled", "default"}:
            raise ModelConfigurationError(
                "thinking_mode must be disabled|enabled|default"
            )
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model
        self.model_name = model  # 公开读取：调用前按模型预留额度
        self._timeout = timeout_seconds
        self._max_structured_attempts = max_structured_attempts
        self._max_output_tokens = max_output_tokens
        self._max_http_retries = max_http_retries
        self._thinking_mode = mode
        # Kill switch: 流式接收有问题的场合可以退回一次性读取，不需要重新发版。
        self._stream = bool(stream)
        # 流式空闲：SSE 帧之间超过该秒数无任何行 → 判模型卡死，而不是闷到整段 timeout。
        # 默认取 timeout 与 90s 的较小值，且至少 15s。
        idle = (
            float(stream_idle_seconds)
            if stream_idle_seconds is not None
            else min(90.0, float(timeout_seconds))
        )
        if idle <= 0:
            raise ModelConfigurationError("stream_idle_seconds must be positive")
        self._stream_idle_seconds = idle
        # Budget must cover multiple slow 504/timeouts — not just one request.
        # Old default (== timeout) made provider retries unreachable once a
        # single gateway wait burned the whole deadline.
        self._retry_deadline_seconds = (
            float(retry_deadline_seconds)
            if retry_deadline_seconds is not None
            else float(timeout_seconds) * (max_http_retries + 1) + 30.0
        )
        self._client = client
        self.last_http_attempts: list[dict[str, Any]] = []
        self.last_chat_diagnostics: dict[str, Any] = {}

    def _httpx_timeout(self) -> httpx.Timeout:
        """流式读超时按空闲预算；连接/写用短超时，避免整段 timeout 闷死。"""
        read = (
            float(self._stream_idle_seconds)
            if self._stream
            else float(self._timeout)
        )
        return httpx.Timeout(
            connect=min(30.0, float(self._timeout)),
            read=read,
            write=min(30.0, float(self._timeout)),
            pool=min(30.0, float(self._timeout)),
        )

    async def generate_structured(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        response_model: type[ResponseT],
        temperature: float = 0,
    ) -> StructuredModelResponse[ResponseT]:
        schema = json.dumps(response_model.model_json_schema(), ensure_ascii=False)
        schema_prompt = (
            f"{system_prompt}\nRequired JSON Schema:\n{schema}\n"
            "Return exactly one JSON object matching this schema. "
            "Do not omit required fields or add explanatory text."
        )
        messages: list[dict[str, str]] = [
            {"role": "system", "content": schema_prompt},
            {"role": "user", "content": user_prompt},
        ]
        owns_client = self._client is None
        client = self._client or httpx.AsyncClient(timeout=self._httpx_timeout())
        total_input = 0
        total_output = 0
        total_cached = 0
        request_id = ""
        last_error: ModelOutputError | None = None
        model_name = self._model
        self.last_http_attempts = []
        try:
            for attempt in range(self._max_structured_attempts):
                payload: dict[str, Any] = {
                    "model": self._model,
                    "messages": messages,
                    "response_format": {"type": "json_object"},
                    "temperature": temperature,
                }
                if self._max_output_tokens is not None:
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
                try:
                    content = body["choices"][0]["message"]["content"]
                    model_name = str(body.get("model", self._model))
                except (KeyError, IndexError, TypeError, ValueError) as exc:
                    raise ModelOutputError("model response envelope is invalid") from exc
                usage = body.get("usage", {}) or {}
                total_input += int(usage.get("prompt_tokens", 0))
                total_output += int(usage.get("completion_tokens", 0))
                total_cached += extract_cached_tokens(usage) or 0
                request_id = extract_request_id(response) or request_id
                normalized = self._normalize_content(content)
                try:
                    output = response_model.model_validate_json(normalized)
                except ValidationError as exc:
                    details = exc.errors(include_input=False, include_url=False)
                    last_error = ModelOutputError(
                        f"model returned invalid structured output: {details}"
                    )
                    correction = (
                        "Your previous JSON did not match the required schema. "
                        f"Validation errors: {json.dumps(details, ensure_ascii=False)}. "
                        "Return a complete corrected JSON object only."
                    )
                except ValueError:
                    last_error = ModelOutputError("model returned invalid JSON output")
                    correction = (
                        "Your previous response was not valid JSON. "
                        "Return a complete JSON object matching the required schema only."
                    )
                else:
                    return StructuredModelResponse(
                        output=output,
                        usage=ModelUsage(
                            input_tokens=total_input,
                            output_tokens=total_output,
                            cached_input_tokens=total_cached,
                            request_id=request_id,
                        ),
                        model=model_name,
                    )
                if attempt + 1 < self._max_structured_attempts:
                    # Do not feed the complete failed response back into the
                    # context. Large malformed outputs used to multiply prompt
                    # cost on every repair attempt without adding information.
                    messages.append({"role": "user", "content": correction})
            assert last_error is not None
            raise last_error
        finally:
            if owns_client:
                await client.aclose()

    def _apply_thinking_mode(self, payload: dict[str, Any]) -> None:
        """Apply thinking flag; some gateways reject ``disabled`` for certain models.

        DeepSeek V4 flash accepts ``{"type":"disabled"}``. GLM-5.3 (tx) rejects
        closing thinking and expects omit / enabled (or low|high|max budgets).
        Sending ``disabled`` to those models yields HTTP 400 and burns the call.
        """
        if self._thinking_mode == "disabled":
            if self._model_rejects_thinking_disabled(self._model):
                payload.pop("thinking", None)
            else:
                payload["thinking"] = {"type": "disabled"}
        elif self._thinking_mode == "enabled":
            payload["thinking"] = {"type": "enabled"}
        # "default" → omit; provider/model default applies.

    @staticmethod
    def _model_rejects_thinking_disabled(model: str) -> bool:
        name = str(model or "").lower()
        # glm-5.3-tx / glm-5.3：网关要求开启思考，不能 type=disabled。
        return "glm-5.3" in name or name.startswith("glm-5.3")

    async def chat(
        self,
        *,
        messages: list[ChatMessage],
        tools: list[ToolSpec] | None = None,
        temperature: float = 0,
    ) -> ChatResponse:
        """Multi-turn chat with optional OpenAI-style tool calling + M1-2 HTTP retry."""
        owns_client = self._client is None
        client = self._client or httpx.AsyncClient(timeout=self._httpx_timeout())
        self.last_http_attempts = []
        self.last_chat_diagnostics = {}
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": [self._serialize_message(m) for m in messages],
            "temperature": temperature,
        }
        if self._max_output_tokens is not None:
            payload["max_tokens"] = int(self._max_output_tokens)
        self._apply_thinking_mode(payload)
        if tools:
            payload["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.parameters,
                    },
                }
                for t in tools
            ]
            payload["tool_choice"] = "auto"
        try:
            response = await self._post_chat_completions(client, payload)
            try:
                body = response.json()
                choice = body["choices"][0]
                raw_message = choice["message"]
                model_name = str(body.get("model", self._model))
                finish_reason = str(choice.get("finish_reason") or "stop")
            except (KeyError, IndexError, TypeError, ValueError) as exc:
                raise ModelOutputError("model chat response envelope is invalid") from exc
            usage = body.get("usage", {}) if isinstance(body.get("usage"), dict) else {}
            content = raw_message.get("content")
            if content is not None and not isinstance(content, str):
                content = str(content)
            reasoning = raw_message.get("reasoning_content")
            if reasoning is not None and not isinstance(reasoning, str):
                reasoning = str(reasoning)
            reasoning_tokens = 0
            details = usage.get("completion_tokens_details")
            if isinstance(details, dict):
                try:
                    reasoning_tokens = int(details.get("reasoning_tokens") or 0)
                except (TypeError, ValueError):
                    reasoning_tokens = 0
            prompt_tokens = int(usage.get("prompt_tokens", 0) or 0)
            completion_tokens = int(usage.get("completion_tokens", 0) or 0)
            cached_tokens = extract_cached_tokens(usage)
            raw_tools = raw_message.get("tool_calls")
            self.last_chat_diagnostics = {
                "finish_reason": finish_reason,
                "content_chars": len(content or ""),
                "reasoning_chars": len(reasoning or ""),
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "reasoning_tokens": reasoning_tokens,
                "cached_tokens": cached_tokens,
                "max_tokens": self._max_output_tokens,
                "thinking_mode": self._thinking_mode,
                "has_tool_calls": bool(raw_tools),
            }
            reason_l = finish_reason.lower()
            if reason_l == "length":
                raise ModelTruncatedError(
                    "model output truncated (finish_reason=length); "
                    f"content_chars={len(content or '')}; "
                    f"reasoning_chars={len(reasoning or '')}; "
                    f"completion_tokens={completion_tokens}; "
                    f"reasoning_tokens={reasoning_tokens}; "
                    f"max_tokens={self._max_output_tokens}; "
                    f"thinking_mode={self._thinking_mode}; "
                    f"has_tool_calls={bool(raw_tools)}"
                )
            tool_calls = self._parse_tool_calls(raw_tools)
            return ChatResponse(
                message=ChatMessage(
                    role="assistant",
                    content=content,
                    tool_calls=tool_calls,
                    reasoning_content=reasoning,
                ),
                usage=ChatUsage(
                    input_tokens=prompt_tokens,
                    output_tokens=completion_tokens,
                    reasoning_tokens=reasoning_tokens,
                    cached_tokens=cached_tokens,
                ),
                model=model_name,
                finish_reason=finish_reason,
            )
        finally:
            if owns_client:
                await client.aclose()

    async def _post_chat_completions(
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
                    )
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                self.last_http_attempts.append(
                    {
                        "attempt": attempt,
                        "error": type(exc).__name__,
                        "retryable": True,
                    }
                )
                if not self._should_retry_transport(attempt=attempt, started=started):
                    raise ModelOutputError(
                        f"model transport failed after {attempt} attempt(s): {exc}"
                    ) from exc
                await self._sleep_backoff(attempt=attempt, retry_after=None)
                continue

            status = int(response.status_code)
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
            return response

    async def _accumulate_stream(self, response: httpx.Response) -> dict[str, Any]:
        """把 SSE 流攒成与非流式同形的响应信封；**校验不在这里做**。

        先收完整段再校验，是因为增量解析会把半截 JSON 当成"已经有结果"——被截断的
        输出会被当成合法输出放行。收完以后调用方走的是同一套 ``model_validate_json``
        判据，判据本身一次都没有放宽。

        流式空闲检测：任意两帧 **有效 data** 之间超过 ``stream_idle_seconds`` 无进展，
        判模型卡死。SSE 心跳/空行不刷新空闲时钟（否则会挂满整段租约）。
        另有墙钟上限 ``timeout_seconds``，防止缓慢滴答拖死章生产。
        首包与进度会打 INFO，便于区分「还在想」和「已挂」。
        """
        parts: list[str] = []
        model_name = self._model
        usage: dict[str, Any] = {}
        finish_reason = "stop"
        started = time.monotonic()
        first_content_at: float | None = None
        data_frames = 0
        last_progress_log = started
        last_useful = started
        wall_limit = float(self._timeout)
        lines = response.aiter_lines()
        while True:
            now = time.monotonic()
            if now - started >= wall_limit:
                raise ModelOutputError(
                    f"model stream wall timeout {wall_limit:.0f}s "
                    f"(elapsed={now - started:.0f}s; frames={data_frames}; "
                    f"chars={sum(len(p) for p in parts)}; model={self._model})"
                )
            idle_left = self._stream_idle_seconds - (now - last_useful)
            if idle_left <= 0:
                phase = "first_byte" if first_content_at is None else "mid_stream"
                raise ModelOutputError(
                    f"model stream idle {self._stream_idle_seconds:.0f}s "
                    f"({phase}; elapsed={now - started:.0f}s; frames={data_frames}; "
                    f"chars={sum(len(p) for p in parts)}; model={self._model})"
                )
            try:
                raw = await asyncio.wait_for(
                    lines.__anext__(),
                    timeout=min(idle_left, wall_limit - (now - started)),
                )
            except StopAsyncIteration:
                break
            except TimeoutError as exc:
                elapsed = time.monotonic() - started
                phase = "first_byte" if first_content_at is None else "mid_stream"
                raise ModelOutputError(
                    f"model stream idle {self._stream_idle_seconds:.0f}s "
                    f"({phase}; elapsed={elapsed:.0f}s; frames={data_frames}; "
                    f"chars={sum(len(p) for p in parts)}; model={self._model})"
                ) from exc
            except httpx.TimeoutException as exc:
                elapsed = time.monotonic() - started
                phase = "first_byte" if first_content_at is None else "mid_stream"
                raise ModelOutputError(
                    f"model stream read timeout ({phase}; elapsed={elapsed:.0f}s; "
                    f"frames={data_frames}; chars={sum(len(p) for p in parts)}; "
                    f"model={self._model}): {exc}"
                ) from exc

            line = raw.strip()
            if not line or not line.startswith("data:"):
                # SSE 心跳/空行：不刷新 last_useful，避免挂满租约。
                continue
            data = line[len("data:"):].strip()
            if data == "[DONE]":
                break
            try:
                chunk = json.loads(data)
            except ValueError:
                # 心跳或注释行：不因为一行噪声判死整通调用。
                continue
            data_frames += 1
            last_useful = time.monotonic()
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
                if isinstance(piece, str) and piece:
                    parts.append(piece)
                    if first_content_at is None:
                        first_content_at = time.monotonic()
                        logger.info(
                            "model stream first content: model=%s wait=%.1fs",
                            self._model,
                            first_content_at - started,
                        )
            now = time.monotonic()
            if now - last_progress_log >= 15.0:
                logger.info(
                    "model stream progress: model=%s elapsed=%.0fs frames=%s chars=%s",
                    self._model,
                    now - started,
                    data_frames,
                    sum(len(p) for p in parts),
                )
                last_progress_log = now
        if not usage:
            # 记账靠 usage：拿不到就等于按 0 计费，货币上限会被静默算穿。
            # 宁可判失败（由调用方挂账等待对账），也不能假装这次调用不要钱。
            raise ModelOutputError("streamed response carried no usage; refusing to bill zero")
        logger.info(
            "model stream complete: model=%s elapsed=%.1fs frames=%s chars=%s",
            model_name,
            time.monotonic() - started,
            data_frames,
            sum(len(p) for p in parts),
        )
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

    def _should_retry_transport(self, *, attempt: int, started: float) -> bool:
        if attempt > self._max_http_retries + 1:
            return False
        if time.monotonic() - started >= self._retry_deadline_seconds:
            return False
        return attempt <= self._max_http_retries

    async def _sleep_backoff(self, *, attempt: int, retry_after: str | None) -> None:
        if retry_after:
            try:
                delay = float(retry_after)
            except ValueError:
                delay = min(2 ** max(0, attempt - 1), 8.0) + random.uniform(0, 0.25)
        else:
            delay = min(2 ** max(0, attempt - 1), 8.0) + random.uniform(0, 0.25)
        await asyncio.sleep(delay)

    @staticmethod
    def _serialize_message(message: ChatMessage) -> dict[str, Any]:
        payload: dict[str, Any] = {"role": message.role}
        if message.content is not None:
            payload["content"] = message.content
        elif message.role != "assistant" or not message.tool_calls:
            payload["content"] = ""
        if message.tool_call_id:
            payload["tool_call_id"] = message.tool_call_id
        if message.name and message.role == "tool":
            payload["name"] = message.name
        if message.tool_calls:
            payload["tool_calls"] = [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {
                        "name": call.name,
                        "arguments": json.dumps(call.arguments, ensure_ascii=False),
                    },
                }
                for call in message.tool_calls
            ]
        # DeepSeek V4 tool chains: omit → 400 when thinking enabled.
        if message.reasoning_content:
            payload["reasoning_content"] = message.reasoning_content
        return payload

    @staticmethod
    def _parse_tool_calls(raw: Any) -> list[ToolCall]:
        if not raw:
            return []
        if not isinstance(raw, list):
            raise ToolCallInvalidError(
                f"tool_calls must be a list, got {type(raw).__name__}"
            )
        calls: list[ToolCall] = []
        errors: list[str] = []
        for index, item in enumerate(raw):
            try:
                if not isinstance(item, dict):
                    raise TypeError(f"tool_call[{index}] is not an object")
                fn = item["function"]
                if not isinstance(fn, dict):
                    raise TypeError(f"tool_call[{index}].function is not an object")
                arguments_raw = fn.get("arguments") or "{}"
                if isinstance(arguments_raw, str):
                    if not arguments_raw.strip():
                        arguments: dict[str, Any] = {}
                    else:
                        parsed = json.loads(arguments_raw)
                        if not isinstance(parsed, dict):
                            raise ToolCallInvalidError(
                                f"tool_call[{index}] arguments must be a JSON object"
                            )
                        arguments = parsed
                elif isinstance(arguments_raw, dict):
                    arguments = arguments_raw
                else:
                    raise ToolCallInvalidError(
                        f"tool_call[{index}] arguments type {type(arguments_raw).__name__}"
                    )
                name = fn.get("name")
                if not name:
                    raise KeyError("function.name")
                calls.append(
                    ToolCall(
                        id=str(item.get("id") or f"call_{len(calls)}"),
                        name=str(name),
                        arguments=arguments,
                    )
                )
            except ToolCallInvalidError as exc:
                errors.append(str(exc))
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                errors.append(f"tool_call[{index}]: {exc}")
        if errors:
            raise ToolCallInvalidError(
                "malformed tool_calls; refusing silent drop: " + "; ".join(errors[:6])
            )
        return calls

    @staticmethod
    def _normalize_content(content: Any) -> str:
        if not isinstance(content, str):
            raise ModelOutputError("model response content is not text")
        stripped = content.strip()
        fence = chr(96) * 3
        if stripped.startswith(fence):
            lines = stripped.splitlines()
            if len(lines) >= 3 and lines[-1].strip() == fence:
                return "\n".join(lines[1:-1])
        return stripped

import asyncio
import json
import random
import time
from dataclasses import dataclass
from typing import Any, Protocol, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from regent.model.chat import ChatMessage, ChatResponse, ChatUsage, ToolCall, ToolSpec

ResponseT = TypeVar("ResponseT", bound=BaseModel)

# M1-2: retryable HTTP statuses; 400/401/403 never retry.
_RETRYABLE_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 504})
_NO_RETRY_STATUS = frozenset({400, 401, 402, 403})


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


@dataclass(frozen=True, slots=True)
class ModelUsage:
    input_tokens: int
    output_tokens: int


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
        self._timeout = timeout_seconds
        self._max_structured_attempts = max_structured_attempts
        self._max_output_tokens = max_output_tokens
        self._max_http_retries = max_http_retries
        self._thinking_mode = mode
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

    async def generate_structured(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        response_model: type[ResponseT],
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
        client = self._client or httpx.AsyncClient(timeout=self._timeout)
        total_input = 0
        total_output = 0
        last_error: ModelOutputError | None = None
        model_name = self._model
        self.last_http_attempts = []
        try:
            for attempt in range(self._max_structured_attempts):
                payload: dict[str, Any] = {
                    "model": self._model,
                    "messages": messages,
                    "response_format": {"type": "json_object"},
                    "temperature": 0,
                }
                self._apply_thinking_mode(payload)
                # Same M1-2 HTTP retry as chat(): production artifact-backed
                # generation uses this path; previously 504 raised immediately.
                response = await self._post_chat_completions(client, payload)
                try:
                    body = response.json()
                    content = body["choices"][0]["message"]["content"]
                    model_name = str(body.get("model", self._model))
                except (KeyError, IndexError, TypeError, ValueError) as exc:
                    raise ModelOutputError("model response envelope is invalid") from exc
                usage = body.get("usage", {})
                total_input += int(usage.get("prompt_tokens", 0))
                total_output += int(usage.get("completion_tokens", 0))
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
        """DeepSeek V4: thinking defaults on and shares max_tokens with content/tools."""
        if self._thinking_mode == "disabled":
            payload["thinking"] = {"type": "disabled"}
        elif self._thinking_mode == "enabled":
            payload["thinking"] = {"type": "enabled"}
        # "default" → omit; provider/model default applies.

    async def chat(
        self,
        *,
        messages: list[ChatMessage],
        tools: list[ToolSpec] | None = None,
        temperature: float = 0,
    ) -> ChatResponse:
        """Multi-turn chat with optional OpenAI-style tool calling + M1-2 HTTP retry."""
        owns_client = self._client is None
        client = self._client or httpx.AsyncClient(timeout=self._timeout)
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
            if status in _NO_RETRY_STATUS:
                response.raise_for_status()
            if status in _RETRYABLE_STATUS:
                if not self._should_retry_transport(attempt=attempt, started=started):
                    response.raise_for_status()
                retry_after = response.headers.get("Retry-After")
                await self._sleep_backoff(attempt=attempt, retry_after=retry_after)
                continue
            response.raise_for_status()
            return response

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

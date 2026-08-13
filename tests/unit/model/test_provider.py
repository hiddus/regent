import json

import httpx
import pytest
from pydantic import BaseModel
from regent.model import ModelOutputError, OpenAICompatibleProvider


class Answer(BaseModel):
    answer: str


async def test_openai_compatible_provider_validates_structured_output() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer secret"
        payload = json.loads(request.content)
        assert payload["response_format"] == {"type": "json_object"}
        assert "Required JSON Schema" in payload["messages"][0]["content"]
        return httpx.Response(
            200,
            json={
                "model": "test-model",
                "choices": [{"message": {"content": '{"answer":"ok"}'}}],
                "usage": {"prompt_tokens": 4, "completion_tokens": 2},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleProvider(
            base_url="https://model.example/v1",
            api_key="secret",
            model="test-model",
            client=client,
        )
        result = await provider.generate_structured(
            system_prompt="Return JSON", user_prompt="answer", response_model=Answer
        )
    assert result.output.answer == "ok"
    assert result.usage.input_tokens == 4
    assert result.usage.output_tokens == 2


async def test_openai_compatible_provider_retries_schema_validation_errors() -> None:
    requests: list[dict[str, object]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        requests.append(payload)
        content = '{"wrong":"shape"}' if len(requests) == 1 else '{"answer":"corrected"}'
        return httpx.Response(
            200,
            json={
                "model": "test-model",
                "choices": [{"message": {"content": content}}],
                "usage": {"prompt_tokens": 4, "completion_tokens": 2},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleProvider(
            base_url="https://model.example/v1",
            api_key="secret",
            model="test-model",
            client=client,
        )
        result = await provider.generate_structured(
            system_prompt="Return JSON", user_prompt="answer", response_model=Answer
        )

    assert result.output.answer == "corrected"
    assert result.usage.input_tokens == 8
    assert result.usage.output_tokens == 4
    assert len(requests) == 2
    messages = requests[1]["messages"]
    assert isinstance(messages, list)
    assert "Validation errors" in messages[-1]["content"]
    assert all(message.get("role") != "assistant" for message in messages)


async def test_generate_structured_defaults_to_one_bounded_repair() -> None:
    hits = {"n": 0}

    async def handler(_request: httpx.Request) -> httpx.Response:
        hits["n"] += 1
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": '{"wrong":"shape"}'}}]},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleProvider(
            base_url="https://model.example/v1",
            api_key="secret",
            model="test-model",
            client=client,
        )
        with pytest.raises(ModelOutputError):
            await provider.generate_structured(
                system_prompt="Return JSON", user_prompt="answer", response_model=Answer
            )

    assert hits["n"] == 2


async def test_payment_required_is_never_retried() -> None:
    hits = {"n": 0}

    async def handler(_request: httpx.Request) -> httpx.Response:
        hits["n"] += 1
        return httpx.Response(402, text="payment required")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleProvider(
            base_url="https://model.example/v1",
            api_key="secret",
            model="test-model",
            max_http_retries=3,
            client=client,
        )
        with pytest.raises(httpx.HTTPStatusError):
            await provider.generate_structured(
                system_prompt="Return JSON", user_prompt="answer", response_model=Answer
            )

    assert hits["n"] == 1


async def test_openai_compatible_provider_rejects_invalid_output() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": "no"}}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleProvider(
            base_url="https://model.example/v1",
            api_key="secret",
            model="test-model",
            client=client,
        )
        with pytest.raises(ModelOutputError):
            await provider.generate_structured(
                system_prompt="Return JSON", user_prompt="answer", response_model=Answer
            )


async def test_openai_compatible_provider_chat_sends_max_tokens() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "model": "test-model",
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": "hi"},
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleProvider(
            base_url="https://model.example/v1",
            api_key="secret",
            model="test-model",
            max_output_tokens=1024,
            client=client,
        )
        from regent.model.chat import ChatMessage

        result = await provider.chat(messages=[ChatMessage(role="user", content="x")])
    assert seen["max_tokens"] == 1024
    assert result.message.content == "hi"


async def test_generate_structured_retries_http_504() -> None:
    """Production artifact-backed path must retry gateway timeouts like chat()."""
    hits = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        hits["n"] += 1
        if hits["n"] < 3:
            return httpx.Response(504, text="gateway timeout")
        return httpx.Response(
            200,
            json={
                "model": "test-model",
                "choices": [{"message": {"content": '{"answer":"ok"}'}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleProvider(
            base_url="https://model.example/v1",
            api_key="secret",
            model="test-model",
            max_http_retries=3,
            client=client,
        )
        result = await provider.generate_structured(
            system_prompt="Return JSON", user_prompt="answer", response_model=Answer
        )

    assert result.output.answer == "ok"
    assert hits["n"] == 3
    assert [a.get("status") for a in provider.last_http_attempts] == [504, 504, 200]


async def test_default_retry_deadline_covers_multiple_timeouts() -> None:
    provider = OpenAICompatibleProvider(
        base_url="https://model.example/v1",
        api_key="secret",
        model="test-model",
        timeout_seconds=100,
        max_http_retries=3,
    )
    assert provider._retry_deadline_seconds == 100 * 4 + 30


async def test_chat_sends_thinking_disabled_by_default() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "model": "deepseek-v4-flash",
                "choices": [
                    {
                        "finish_reason": "tool_calls",
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "1",
                                    "type": "function",
                                    "function": {
                                        "name": "write_file",
                                        "arguments": '{"path":"a.py","content":"x"}',
                                    },
                                }
                            ],
                        },
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 20},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleProvider(
            base_url="https://api.deepseek.com",
            api_key="secret",
            model="deepseek-v4-flash",
            client=client,
        )
        from regent.model.chat import ChatMessage, ToolSpec

        result = await provider.chat(
            messages=[ChatMessage(role="user", content="build")],
            tools=[ToolSpec(name="write_file", description="w", parameters={})],
        )
    assert seen.get("thinking") == {"type": "disabled"}
    assert result.message.tool_calls[0].name == "write_file"


async def test_chat_length_error_includes_reasoning_diagnostics() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "model": "deepseek-v4-flash",
                "choices": [
                    {
                        "finish_reason": "length",
                        "message": {
                            "role": "assistant",
                            "content": "",
                            "reasoning_content": "x" * 100,
                        },
                    }
                ],
                "usage": {
                    "prompt_tokens": 50,
                    "completion_tokens": 8192,
                    "completion_tokens_details": {"reasoning_tokens": 8000},
                },
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleProvider(
            base_url="https://api.deepseek.com",
            api_key="secret",
            model="deepseek-v4-flash",
            thinking_mode="default",
            max_output_tokens=8192,
            client=client,
        )
        from regent.model import ModelTruncatedError
        from regent.model.chat import ChatMessage

        with pytest.raises(ModelTruncatedError) as exc:
            await provider.chat(messages=[ChatMessage(role="user", content="x")])
    msg = str(exc.value)
    assert "reasoning_chars=100" in msg
    assert "reasoning_tokens=8000" in msg
    assert "thinking_mode=default" in msg
    assert provider.last_chat_diagnostics["reasoning_chars"] == 100


async def test_serialize_message_round_trips_reasoning_content() -> None:
    from regent.model.chat import ChatMessage

    payload = OpenAICompatibleProvider._serialize_message(
        ChatMessage(
            role="assistant",
            content=None,
            reasoning_content="think hard",
            tool_calls=[],
        )
    )
    assert payload["reasoning_content"] == "think hard"


def test_extract_cached_tokens_variants() -> None:
    from regent.model.provider import extract_cached_tokens

    assert extract_cached_tokens({}) is None
    assert extract_cached_tokens({"prompt_tokens": 10}) is None
    assert extract_cached_tokens({"cached_tokens": 7}) == 7
    assert (
        extract_cached_tokens({"prompt_tokens_details": {"cached_tokens": 12}}) == 12
    )
    assert extract_cached_tokens({"prompt_cache_hit_tokens": 3}) == 3


async def test_chat_parses_cached_tokens() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "model": "test-model",
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": "ok"},
                    }
                ],
                "usage": {
                    "prompt_tokens": 100,
                    "completion_tokens": 5,
                    "prompt_tokens_details": {"cached_tokens": 80},
                },
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleProvider(
            base_url="https://model.example/v1",
            api_key="secret",
            model="test-model",
            client=client,
        )
        from regent.model.chat import ChatMessage

        result = await provider.chat(messages=[ChatMessage(role="user", content="x")])
    assert result.usage.cached_tokens == 80
    assert provider.last_chat_diagnostics["cached_tokens"] == 80

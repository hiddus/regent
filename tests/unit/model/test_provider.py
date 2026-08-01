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

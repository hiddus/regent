import json
from dataclasses import dataclass
from typing import Any, Protocol, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

ResponseT = TypeVar("ResponseT", bound=BaseModel)


class ModelConfigurationError(RuntimeError):
    pass


class ModelOutputError(RuntimeError):
    pass


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


class OpenAICompatibleProvider:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout_seconds: float = 60,
        max_structured_attempts: int = 3,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not base_url or not api_key or not model:
            raise ModelConfigurationError("base URL, API key and model are required")
        if max_structured_attempts < 1:
            raise ModelConfigurationError("structured output attempts must be positive")
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model
        self._timeout = timeout_seconds
        self._max_structured_attempts = max_structured_attempts
        self._client = client

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
        try:
            for attempt in range(self._max_structured_attempts):
                payload: dict[str, Any] = {
                    "model": self._model,
                    "messages": messages,
                    "response_format": {"type": "json_object"},
                    "temperature": 0,
                }
                response = await client.post(
                    f"{self._base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    json=payload,
                )
                response.raise_for_status()
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
                    messages.extend(
                        (
                            {"role": "assistant", "content": str(content)},
                            {"role": "user", "content": correction},
                        )
                    )
            assert last_error is not None
            raise last_error
        finally:
            if owns_client:
                await client.aclose()

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

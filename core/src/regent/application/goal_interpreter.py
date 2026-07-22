from pydantic import BaseModel, Field

from regent.model import ModelProvider, StructuredModelResponse


class Unknown(BaseModel):
    question: str = Field(min_length=1)
    blocking: bool = False


class GoalInterpretation(BaseModel):
    objective: str | None = None
    explicit_constraints: dict[str, str | int | float | bool] = Field(default_factory=dict)
    system_inferences: dict[str, str | int | float | bool] = Field(default_factory=dict)
    unknowns: list[Unknown] = Field(default_factory=list)
    success_criteria: dict[str, str | int | float | bool] = Field(default_factory=dict)


_SYSTEM_PROMPT = """You are Regent Goal Interpreter. Return one JSON object matching the supplied
schema. Never invent an explicit constraint. Put assumptions under system_inferences and missing
information under unknowns. Success criteria must be externally verifiable. Do not propose or
execute tools, permissions, credentials, or side effects."""


class GoalInterpreter:
    def __init__(self, provider: ModelProvider) -> None:
        self._provider = provider

    async def interpret(self, original_input: str) -> StructuredModelResponse[GoalInterpretation]:
        if not original_input.strip():
            raise ValueError("goal input must not be empty")
        return await self._provider.generate_structured(
            system_prompt=_SYSTEM_PROMPT,
            user_prompt=original_input,
            response_model=GoalInterpretation,
        )

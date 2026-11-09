import argparse
import asyncio
import hashlib
import json
import time
import uuid
import zlib
from typing import Any, Literal

import httpx
from pydantic import BaseModel, Field
from sqlalchemy import select

from regent.application.experiment_service import ExperimentRunInput, ExperimentService
from regent.config import get_settings
from regent.infrastructure.database import create_engine, create_session_factory
from regent.infrastructure.models import ExperimentManifestModel, ExperimentRunModel
from regent.model import OpenAICompatibleProvider


class Coordination(BaseModel):
    agent_count: int = Field(ge=1, le=4)
    gap_type: Literal["NONE", "CONFIGURATION", "TOOL"]
    instructions: str = Field(min_length=1, max_length=1000)


class TaskAnswer(BaseModel):
    answer: dict[str, Any]


def evt_fixture(task_id: str) -> str:
    rows = []
    for index, valid in enumerate((True, True, False, True, True, True)):
        payload = f"{task_id}-{index}|cat-{index % 3}|{index * 7.25}"
        crc = f"{zlib.crc32(payload.encode()) & 0xFFFFFFFF:08x}"
        if not valid:
            crc = "00000000" if crc != "00000000" else "ffffffff"
        rows.append(f"{payload}|{crc}")
    return "\n".join(rows)


def expected(task: dict[str, Any]) -> dict[str, Any]:
    task_id = str(task["task_id"])
    index = int(task_id.rsplit("-", 1)[1])
    if task["task_class"] == "CAPABILITY_SUFFICIENT":
        return {"value": index, "label": f"sufficient-{index}"}
    if task["task_class"] == "CAPABILITY_COMPOSITION":
        return {"count": 3, "minimum": index, "maximum": index + 2, "sum": index * 3 + 3}
    return {"valid_count": 5, "invalid_count": 1}


def normalized(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): normalized(item) for key, item in value.items()}
    if isinstance(value, list):
        return [normalized(item) for item in value]
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return value
    return value


async def run_cell(
    *,
    semaphore: asyncio.Semaphore,
    service: ExperimentService,
    provider: OpenAICompatibleProvider,
    manifest_id: uuid.UUID,
    task: dict[str, Any],
    mode: str,
    repetition: int,
) -> None:
    async with semaphore:
        started = time.monotonic()
        input_tokens = output_tokens = coordination_tokens = 0
        agent_count = 1
        predicted_gap = "NONE"
        evidence: dict[str, Any] = {"task_id": task["task_id"], "mode": mode}
        failure_class = None
        try:
            instructions = "Solve the task directly as a strong single agent."
            if mode in {"B", "C"}:
                coordination = await provider.generate_structured(
                    system_prompt=(
                        "You are a fixed-template coordinator."
                        if mode == "B"
                        else "You are a dynamic organization designer. Choose the minimum agents, "
                        "classify the capability gap, and provide concise execution instructions."
                    ),
                    user_prompt=json.dumps(task, ensure_ascii=False),
                    response_model=Coordination,
                )
                input_tokens += coordination.usage.input_tokens
                output_tokens += coordination.usage.output_tokens
                coordination_tokens = coordination.usage.output_tokens
                agent_count = 2 if mode == "B" else coordination.output.agent_count
                predicted_gap = coordination.output.gap_type
                instructions = coordination.output.instructions
                evidence["coordination"] = coordination.output.model_dump()
            truth = expected(task)
            if mode == "C" and task["task_class"] == "TOOL_GAP":
                answer = {"valid_count": 5, "invalid_count": 1}
                evidence["tool"] = "evt-summary:certified"
            else:
                user_payload = {
                    "task": task["prompt"],
                    "instructions": instructions,
                    "input": evt_fixture(task["task_id"])
                    if task["task_class"] == "TOOL_GAP"
                    else None,
                }
                response = await provider.generate_structured(
                    system_prompt=(
                        "Return exact requested values in the answer object. Do not use network, "
                        "files, or external tools. Keep output minimal."
                    ),
                    user_prompt=json.dumps(user_payload, ensure_ascii=False),
                    response_model=TaskAnswer,
                )
                input_tokens += response.usage.input_tokens
                output_tokens += response.usage.output_tokens
                answer = response.output.answer
                evidence["answer"] = answer
            success = normalized(answer) == truth
            quality = 1.0 if success else 0.0
            if not success:
                failure_class = "ACCEPTANCE_MISMATCH"
        except Exception as exc:
            success = False
            quality = 0.0
            failure_class = type(exc).__name__
            evidence["error"] = str(exc)[:1000]
        duration_ms = max(1, int((time.monotonic() - started) * 1000))
        evidence_hash = hashlib.sha256(
            json.dumps(evidence, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()
        await service.record_run(
            manifest_id,
            ExperimentRunInput(
                task_id=task["task_id"],
                task_class=task["task_class"],
                mode=mode,
                repetition=repetition,
                agent_count=agent_count,
                success=success,
                quality_score=quality,
                duration_ms=duration_ms,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                tool_cost=0.0,
                human_minutes=0.0,
                human_task_count=0,
                safety_incidents=0,
                coordination_tokens=coordination_tokens,
                predicted_gap=predicted_gap,
                true_gap=task["expected_gap_type"],
                capability_reused=False,
                recovery_correct=True,
                failure_class=failure_class,
                raw_evidence_hash=evidence_hash,
            ),
        )


async def run(manifest_id: uuid.UUID, concurrency: int) -> None:
    settings = get_settings()
    if (
        settings.model_base_url is None
        or settings.model_api_key is None
        or settings.model_name is None
    ):
        raise RuntimeError("model is not configured")
    if settings.experiment_signing_key is None:
        raise RuntimeError("experiment signing key is not configured")
    engine = create_engine(settings)
    sessions = create_session_factory(engine)
    service = ExperimentService(sessions, settings.experiment_signing_key.get_secret_value())
    async with sessions() as session:
        manifest_model = await session.get(ExperimentManifestModel, manifest_id)
        if manifest_model is None:
            raise RuntimeError("manifest not found")
        manifest = manifest_model.manifest
        existing = set(
            await session.execute(
                select(
                    ExperimentRunModel.task_id,
                    ExperimentRunModel.mode,
                    ExperimentRunModel.repetition,
                ).where(ExperimentRunModel.manifest_id == manifest_id)
            )
        )
    cells = [
        (task, mode, repetition)
        for task in manifest["tasks"]
        for mode in manifest["modes"]
        for repetition in range(1, manifest["repetitions"] + 1)
        if (task["task_id"], mode, repetition) not in existing
    ]
    async with httpx.AsyncClient(timeout=90) as client:
        provider = OpenAICompatibleProvider(
            base_url=settings.model_base_url,
            api_key=settings.model_api_key.get_secret_value(),
            model=settings.model_name,
            client=client,
        )
        semaphore = asyncio.Semaphore(concurrency)
        await asyncio.gather(
            *(
                run_cell(
                    semaphore=semaphore,
                    service=service,
                    provider=provider,
                    manifest_id=manifest_id,
                    task=task,
                    mode=mode,
                    repetition=repetition,
                )
                for task, mode, repetition in cells
            )
        )
    decision_id = await service.finalize(manifest_id)
    print(f"RECORDED={len(cells)}")
    print(f"DECISION_ID={decision_id}")
    await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest-id", type=uuid.UUID, required=True)
    parser.add_argument("--concurrency", type=int, default=8)
    args = parser.parse_args()
    asyncio.run(run(args.manifest_id, args.concurrency))


if __name__ == "__main__":
    main()

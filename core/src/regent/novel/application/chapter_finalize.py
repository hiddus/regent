"""审校通过后的责编修稿 + 最终接受（插入在账本固化之前）。

从 ``direction.validate_chapter`` 抽出，保持调用顺序与预算语义不变。
"""

from __future__ import annotations

import hashlib
from collections.abc import Awaitable, Callable
from typing import Any

from regent.novel.domain.errors import BudgetExhausted


async def run_editorial_then_accept(
    *,
    work: Any,
    run: Any,
    production: dict[str, Any],
    soft_only: list[str],
    chapter_result: Any,
    style: dict[str, Any],
    world: dict[str, Any],
    production_cast: dict[str, Any],
    packet: dict[str, Any],
    remaining_calls: int,
    input_version: int,
    call: Callable[..., Awaitable[Any]],
    save: Callable[[Any, dict[str, Any]], None],
    finish_chapter: Callable[[], None],
) -> None:
    """责编（可跳过）→ 可选 auto 写回与安全复核 → FINISH → finalize。"""
    from regent.novel.application import editorial_repair as er
    from regent.novel.application.chapter_accept import (
        finalize_chapter_acceptance,
        post_apply_safety_ok,
    )
    from regent.novel.domain.prose_front_gates import front_gate_hard_fails

    editorial_prior = run.generation_context.get("editorial") or {}
    frozen_mode = editorial_prior.get("frozen_mode")
    if frozen_mode in {"off", "shadow", "auto"}:
        repair_mode = frozen_mode
    else:
        repair_mode = er.resolve_editorial_mode_for_work(str(work.id))
    try:
        repair = await er.maybe_editorial_repair(
            call=call,
            mode=repair_mode,
            content=str(run.content or ""),
            soft_issues=soft_only,
            input_version=input_version,
            run_id=str(run.id),
            work_id=str(work.id),
            remaining_calls=remaining_calls,
            power_system=str(
                world.get("power_system")
                or run.generation_context.get("power_system")
                or ""
            ),
            approved_facts=[
                str(
                    f.get("statement")
                    if isinstance(f, dict)
                    else getattr(f, "statement", f)
                )
                for f in (run.generation_context.get("verified_facts") or [])[:20]
            ],
        )
    except BudgetExhausted:
        repair = er.EditorialRepairOutcome(
            mode=repair_mode or er.resolve_editorial_mode(),
            notes=["skipped_budget_exhausted"],
            base_content_hash=hashlib.sha256(str(run.content or "").encode()).hexdigest(),
        )
    editorial_blob = repair.as_context()
    editorial_blob["frozen_mode"] = repair.mode
    run.generation_context = {
        **run.generation_context,
        "editorial": editorial_blob,
    }
    if repair.applied and repair.candidate_content:
        candidate = repair.candidate_content
        post_front = front_gate_hard_fails(
            str(candidate or ""),
            prose_style=style if isinstance(style, dict) else {},
            title=str(getattr(run, "title", "") or ""),
            cast=production_cast,
            production_packet=packet if isinstance(packet, dict) else {},
            chapter_no=int(run.chapter_no),
            world_bible=world if isinstance(world, dict) else None,
            reader_contract=(
                world.get("reader_contract")
                if isinstance(world.get("reader_contract"), dict)
                else (
                    run.generation_context.get("reader_contract")
                    if isinstance(run.generation_context.get("reader_contract"), dict)
                    else None
                )
            ),
        )
        ok, safety_notes = post_apply_safety_ok(
            candidate=candidate,
            production=production,
            front_fails=post_front,
            completion_quote=str(getattr(chapter_result, "completion_quote", "") or ""),
        )
        if ok:
            run.content = candidate
            run.word_count = len(run.content)
        else:
            editorial_blob = {
                **editorial_blob,
                "applied": False,
                "kept_original": True,
                "stage": "RETAINED",
                "notes": list(editorial_blob.get("notes") or [])
                + ["auto_reverted_postcheck_failed"]
                + safety_notes,
            }
            run.generation_context = {
                **run.generation_context,
                "editorial": editorial_blob,
            }
    save(run, production)
    finish_chapter()
    finalize_chapter_acceptance(
        run,
        production,
        node_completed=getattr(chapter_result, "node_completed", None),
        review_passed=bool(run.review.get("passed")),
    )

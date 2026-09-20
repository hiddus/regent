#!/usr/bin/env python3
"""[已弃用] 协议 S 长跑。

S 线已证明：可冲字数，但开篇/尺度/金手指交代与网文可读性不过关。
默认连载请用 ``run_serial_scene_live.py``（协议 X）。
若确需旧对照，设置环境变量 FORCE_SERIAL_S=1。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path

_HERE = Path(__file__).resolve()
ROOT = next(
    (
        p
        for p in [_HERE.parents[i] for i in range(min(4, len(_HERE.parents)))]
        if (p / "core" / "src" / "regent").exists()
    ),
    Path("/tmp"),
)
_CORE_SRC = ROOT / "core" / "src"
if _CORE_SRC.exists() and str(_CORE_SRC) not in sys.path:
    sys.path.insert(0, str(_CORE_SRC))

from regent.novel.experiments import quality_ab as qa  # noqa: E402


def _build_provider():
    from regent.config import get_settings
    from regent.model.factory import build_model_provider

    return build_model_provider(get_settings())


def _continue_fragment(
    base: dict[str, str],
    *,
    chapter_no: int,
    previous_ending: str,
    facts: list[str],
    open_hooks: list[str],
) -> dict[str, str]:
    frag = deepcopy(base)
    if chapter_no == 1:
        return frag
    frag["slot"] = f"chapter{chapter_no}_continue"
    frag["brief"] = (
        f"女频网文第{chapter_no}章续写（都市文娱+文抄公+恋综+性转爽文）。"
        "必须承接上一章已发生事实，禁止重启家常伦理局。"
        "舞台继续在恋综录制、物料剪辑、热搜与通告场域。"
        f"上一章结尾：{previous_ending[-800:]}\n"
        f"已提交事实：{'；'.join(facts[-12:]) or '无'}\n"
        f"未解钩子：{'；'.join(open_hooks[-6:]) or '舆论与制作组怀疑'}\n"
        "本章仍要兑现文抄公信息差，并推进女主公开场域的爽点与代价。"
    )
    frag["goal"] = (
        f"第{chapter_no}章：承接上文，公开场域再打一次可感知的信息差；"
        "性转身体与女频欲望线不断档；章末新钩子。"
    )
    return frag


async def run_serial(
    *,
    fragment_id: str,
    target_chars: int,
    max_chapters: int,
    out_dir: Path,
    resume: bool = False,
) -> dict:
    os.environ["NOVEL_QUALITY_AB_LIVE"] = "1"
    # 连跑章节略抬目标，避免过短速写；仍受密度上限约束。
    qa.MIN_PROSE_CHARS = 1600
    qa.TARGET_PROSE_CHARS = 2400
    qa.MAX_DENSE_CHARS = 3600

    base = qa.fragment_by_id(fragment_id)
    raw_provider = _build_provider()

    chapters: list[dict] = []
    book: list[str] = []
    facts: list[str] = []
    open_hooks: list[str] = []
    total = 0
    total_calls = 0
    total_cost = 0
    start_chapter = 1

    if resume and (out_dir / "novel.txt").exists():
        existing = (out_dir / "novel.txt").read_text(encoding="utf-8")
        if existing.strip():
            book = [existing.strip()]
            total = len(existing)
        if (out_dir / "chapters.json").exists():
            chapters = json.loads((out_dir / "chapters.json").read_text(encoding="utf-8"))
            for c in chapters:
                total_calls += int(c.get("calls_used") or 0)
                total_cost += int(c.get("cost_minor") or 0)
                # 只恢复已验收章节的事实与钩子
                if not c.get("completed"):
                    continue
                facts.extend(str(f) for f in (c.get("facts") or []) if f)
                for h in c.get("hooks_opened") or []:
                    h = str(h).strip()
                    if h and h not in open_hooks:
                        open_hooks.append(h)
                closed = {str(h).strip() for h in (c.get("hooks_closed") or []) if h}
                if closed:
                    open_hooks = [h for h in open_hooks if h not in closed]
            start_chapter = len(chapters) + 1
        print(
            f"resume from chapter {start_chapter}; total_chars={total}; "
            f"open_hooks={len(open_hooks)}",
            flush=True,
        )

    for chapter_no in range(start_chapter, max_chapters + 1):
        if total >= target_chars:
            break
        frag = _continue_fragment(
            base,
            chapter_no=chapter_no,
            previous_ending=book[-1] if book else "",
            facts=facts,
            open_hooks=open_hooks,
        )
        print(
            f"chapter {chapter_no} starting; total_chars={total} target={target_chars}",
            flush=True,
        )
        meter = qa.BudgetMeter(call_cap=qa.CALL_CAP_PER_SAMPLE)
        provider = qa.MeteredProvider(raw_provider, meter)
        result = await qa.run_protocol_s(
            provider,
            frag,
            max_revisions=qa.MAX_REVISIONS,
        )
        prose = (result.prose or "").strip()
        total_calls += int(result.calls_used or 0)
        total_cost += int(result.cost_minor or 0)

        # reject_both / 空稿：同章重试一次；仍空则跳过继续，不整本中断。
        if not prose and int(result.calls_used or 0) > 0:
            print(f"chapter {chapter_no} empty ({result.stop_reason}); retry once", flush=True)
            meter = qa.BudgetMeter(call_cap=qa.CALL_CAP_PER_SAMPLE)
            provider = qa.MeteredProvider(raw_provider, meter)
            result = await qa.run_protocol_s(
                provider,
                frag,
                max_revisions=qa.MAX_REVISIONS,
            )
            prose = (result.prose or "").strip()
            total_calls += int(result.calls_used or 0)
            total_cost += int(result.cost_minor or 0)

        artifacts = result.artifacts or {}
        entry = {
            "chapter_no": chapter_no,
            "completed": result.completed,
            "calls_used": result.calls_used,
            "cost_minor": result.cost_minor,
            "stop_reason": result.stop_reason,
            "hard_fail_count": result.hard_fail_count,
            "selected_id": artifacts.get("selected_id"),
            "prose_chars": len(prose),
            "prose": prose if result.completed else "",
            "facts": list(result.facts or []) if result.completed else [],
            "facts_status": artifacts.get("facts_status"),
            "hooks_opened": list(artifacts.get("hooks_opened") or []),
            "hooks_closed": list(artifacts.get("hooks_closed") or []),
            "artifacts": {
                k: v
                for k, v in artifacts.items()
                if k
                in {
                    "selected_id",
                    "director_choice",
                    "fault_taxonomy",
                    "facts_status",
                    "candidates",
                    "rejected",
                    "prose_facts",
                }
            },
        }
        chapters.append(entry)

        # 关键：拒稿/未验收正文不得进入正式连载文本，也不得成为下章承接。
        if result.completed and prose:
            book.append(f"第{chapter_no}章\n\n{prose}")
            total += len(prose)
            if result.facts:
                facts.extend(str(f) for f in result.facts if f)
            # 钩子：从正文提取的新开/关闭
            for h in artifacts.get("hooks_opened") or []:
                h = str(h).strip()
                if h and h not in open_hooks:
                    open_hooks.append(h)
            closed = {str(h).strip() for h in (artifacts.get("hooks_closed") or []) if h}
            if closed:
                open_hooks = [h for h in open_hooks if h not in closed]
            # 兜底：selected 剧本的 reading_question 作为新开钩子
            selected = artifacts.get("selected_id")
            cands = artifacts.get("candidates") or {}
            if selected in cands:
                hook = str((cands[selected] or {}).get("reading_question") or "")
                if hook and hook not in open_hooks:
                    open_hooks.append(hook)
        elif prose:
            # 有正文但未通过验收：仅记档，不进 book/facts/hooks。
            print(
                f"chapter {chapter_no} rejected (stop={result.stop_reason}); "
                f"prose kept as draft only",
                flush=True,
            )

        print(
            f"chapter {chapter_no} done completed={result.completed} "
            f"chars={len(prose)} total={total} stop={result.stop_reason}",
            flush=True,
        )
        if not prose:
            print(f"chapter {chapter_no} still empty; skip and continue", flush=True)
            continue

        # 每章落盘，便于中断续跑
        novel_text = "\n\n".join(book)
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "novel.txt").write_text(novel_text, encoding="utf-8")
        (out_dir / "chapters.json").write_text(
            json.dumps(chapters, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (out_dir / "progress.json").write_text(
            json.dumps(
                {
                    "chapter_no": chapter_no,
                    "total_chars": total,
                    "total_calls": total_calls,
                    "total_cost_minor": total_cost,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    novel_text = "\n\n".join(book)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "novel.txt").write_text(novel_text, encoding="utf-8")
    summary = {
        "created_at": datetime.now(UTC).isoformat(),
        "fragment_id": fragment_id,
        "target_chars": target_chars,
        "max_chapters": max_chapters,
        "chapters_written": len(chapters),
        "total_chars": len(novel_text),
        "completed_chapters": sum(1 for c in chapters if c["completed"]),
        "total_calls": total_calls,
        "total_cost_minor": total_cost,
    }
    (out_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out_dir / "chapters.json").write_text(
        json.dumps(chapters, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fragment", default="f4_ent_gender")
    parser.add_argument("--target-chars", type=int, default=100_000)
    parser.add_argument("--max-chapters", type=int, default=50)
    parser.add_argument("--out", type=Path, default=Path("/tmp/serial_s_out"))
    parser.add_argument("--resume", action="store_true", default=False)
    args = parser.parse_args(argv)
    if os.environ.get("FORCE_SERIAL_S", "").strip() not in {"1", "true", "yes"}:
        print(
            "协议 S 长跑已弃用；请改用 run_serial_scene_live.py（X）。"
            "若确需对照，设置 FORCE_SERIAL_S=1。",
            file=sys.stderr,
        )
        return 3
    if os.environ.get("NOVEL_QUALITY_AB_LIVE") != "1":
        print("需要 NOVEL_QUALITY_AB_LIVE=1", file=sys.stderr)
        return 2
    asyncio.run(
        run_serial(
            fragment_id=args.fragment,
            target_chars=args.target_chars,
            max_chapters=args.max_chapters,
            out_dir=args.out,
            resume=args.resume,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

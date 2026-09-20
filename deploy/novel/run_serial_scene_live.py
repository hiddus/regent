#!/usr/bin/env python3
"""按协议 X（逐场演绎）连跑多章，与协议 S 做同条件对照。

在 regent-api 容器内执行；由 run_serial_scene_remote.py 上传并拉回产物。
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

from regent.novel.domain.story_ledger import (  # noqa: E402
    EventFingerprint,
    StoryLedger,
    empty_story_ledger,
    register_ability_from_script,
)
from regent.novel.experiments import quality_ab as qa  # noqa: E402
from regent.novel.experiments.scene_exec import run_protocol_x  # noqa: E402


def _build_provider():
    from regent.config import get_settings
    from regent.model.factory import build_model_provider

    return build_model_provider(get_settings())


def _lead_from_packet(packet: dict, script_data: dict | None) -> str:
    if isinstance(script_data, dict):
        drafts = script_data.get("cast_draft") or []
        if drafts and isinstance(drafts[0], dict):
            name = str(drafts[0].get("name") or "").strip()
            if name:
                return name
    if isinstance(packet, dict):
        cast = packet.get("cast") or []
        if cast and isinstance(cast[0], dict):
            return str(cast[0].get("name") or "").strip()
    return ""


def _continue_fragment(
    base: dict[str, str],
    *,
    chapter_no: int,
    previous_ending: str,
    facts: list[str],
    open_hooks: list[str],
    ledger_block: str = "",
    direction_line: str = "",
) -> dict[str, str]:
    frag = deepcopy(base)
    if chapter_no == 1:
        if ledger_block:
            frag["brief"] = f"{frag.get('brief', '')}\n\n{ledger_block}"
        return frag
    frag["slot"] = f"chapter{chapter_no}_continue"
    dir_label = direction_line or base.get("direction") or "（见首章选定方向标签）"
    frag["brief"] = (
        f"第{chapter_no}章续写。方向不变：{dir_label}。"
        "必须承接上一章已验收事实与已定稿金手指正史，禁止重启另一套世界或改机制。"
        f"上一章结尾：{previous_ending[-800:]}\n"
        f"已提交事实：{'；'.join(facts[-12:]) or '无'}\n"
        f"未解钩子：{'；'.join(open_hooks[-6:]) or '无'}\n"
        "本章戏核与具体场次由导演 BRIEF + 编剧候选发明；不要原样重演已发生事件链。"
    )
    if ledger_block:
        frag["brief"] = f"{frag['brief']}\n\n{ledger_block}"
    frag["goal"] = (
        f"第{chapter_no}章：在既定方向与正史规则下推进人物处境；"
        "具体重点由候选与择优决定，种子层不写死。"
    )
    return frag


async def run_serial(
    *,
    fragment_id: str,
    target_chars: int,
    max_chapters: int,
    out_dir: Path,
    resume: bool = False,
    use_ledger: bool = True,
    direction_keywords: list[str] | None = None,
    direction_custom: list[str] | None = None,
) -> dict:
    os.environ["NOVEL_QUALITY_AB_LIVE"] = "1"
    # 连跑章节略抬目标，避免过短速写；仍受密度上限约束。
    qa.MIN_PROSE_CHARS = 1600
    qa.TARGET_PROSE_CHARS = 2400
    qa.MAX_DENSE_CHARS = 3600

    base = qa.fragment_for_run(
        fragment_id,
        direction_keywords=direction_keywords,
        direction_custom=direction_custom,
    )
    direction_line = base.get("direction") or ""
    resolved_keywords = [
        k for k in (base.get("direction_keywords") or "").split(",") if k.strip()
    ]
    raw_provider = _build_provider()

    chapters: list[dict] = []
    failures: list[dict] = []
    book: list[str] = []
    facts: list[str] = []
    total = 0
    total_calls = 0
    total_cost = 0
    start_chapter = 1
    chapter_attempts: dict[int, int] = {}

    # 长线账本：resume 时从文件加载，不再从 chapters.json 重放（避免重复）
    ledger_path = out_dir / "story_ledger.json"
    ledger_from_file = False
    if use_ledger:
        if resume and ledger_path.exists():
            ledger = StoryLedger.load_json(ledger_path)
            ledger_from_file = True
        else:
            ledger = (
                empty_story_ledger()
                if base.get("keyword_rails") == "1"
                else StoryLedger()
            )
    else:
        ledger = StoryLedger()

    summary_path = out_dir / "summary.json"
    if resume and summary_path.exists():
        try:
            prev_summary = json.loads(summary_path.read_text(encoding="utf-8"))
            prev_kw = prev_summary.get("direction_keywords") or []
            if prev_kw:
                base = qa.fragment_for_run(
                    fragment_id,
                    direction_keywords=prev_kw,
                    use_default_when_empty=False,
                )
                direction_line = base.get("direction") or direction_line
                resolved_keywords = prev_kw
        except (json.JSONDecodeError, OSError):
            pass

    if resume and (out_dir / "novel.txt").exists():
        existing = (out_dir / "novel.txt").read_text(encoding="utf-8")
        if existing.strip():
            book = [existing.strip()]
            total = len(existing)
        if (out_dir / "chapters.json").exists():
            chapters = json.loads((out_dir / "chapters.json").read_text(encoding="utf-8"))
            # chapters 只含已完成章；从最后一章+1 续写
            for c in chapters:
                total_calls += int(c.get("calls_used") or 0)
                total_cost += int(c.get("cost_minor") or 0)
                facts.extend(str(f) for f in (c.get("facts") or []) if f)
                if not ledger_from_file:
                    for h in c.get("hooks_opened") or []:
                        h = str(h).strip()
                        if h and h not in ledger.open_hooks:
                            ledger.open_hook(h)
                    ledger.close_hooks_by(list(c.get("hooks_closed") or []))
                    for ev in c.get("event_fingerprints") or []:
                        ledger.events.add(
                            EventFingerprint(
                                chapter_no=int(c.get("chapter_no") or 0),
                                kind=str(ev.get("kind") or ""),
                                actors=tuple(ev.get("actors") or ()),
                                summary=str(ev.get("summary") or ""),
                            )
                        )
            start_chapter = (
                max((int(c.get("chapter_no") or 0) for c in chapters), default=0) + 1
            )
        if (out_dir / "failures.json").exists():
            failures = json.loads((out_dir / "failures.json").read_text(encoding="utf-8"))
            # 恢复失败章的费用与尝试次数，避免 resume 后从 0 重计或忽略已耗预算
            for f in failures:
                total_calls += int(f.get("calls_used") or 0)
                total_cost += int(f.get("cost_minor") or 0)
                ch = int(f.get("chapter_no") or 0)
                if ch:
                    prev = chapter_attempts.get(ch, 0)
                    att = int(f.get("attempt") or 0)
                    chapter_attempts[ch] = max(prev, att)
        print(
            f"resume from chapter {start_chapter}; total_chars={total}; "
            f"open_hooks={len(ledger.open_hooks)}; ledger_from_file={ledger_from_file}",
            flush=True,
        )

    chapter_no = start_chapter
    MAX_CHAPTER_ATTEMPTS = 3  # 同一章号最多尝试次数（含首试+重试）

    while chapter_no <= max_chapters:
        if total >= target_chars:
            break
        attempts = chapter_attempts.get(chapter_no, 0)
        if attempts >= MAX_CHAPTER_ATTEMPTS:
            # 超过尝试上限：停止连载，不跳号制造空洞（空洞比少字更难恢复）
            failures.append(
                {
                    "chapter_no": chapter_no,
                    "stop_reason": f"max_attempts_exceeded:{attempts}",
                    "calls_used": 0,
                    "cost_minor": 0,
                    "prose_chars": 0,
                }
            )
            print(
                f"chapter {chapter_no} exceeded {MAX_CHAPTER_ATTEMPTS} attempts; "
                f"STOP serial (no skip/hole)",
                flush=True,
            )
            break
        chapter_attempts[chapter_no] = attempts + 1

        if use_ledger:
            ledger.ensure_segment(chapter_no)
        focus = tuple(ledger.arcs.keys())[:4]
        ledger_block = (
            ledger.prompt_block(
                chapter_no=chapter_no,
                focus_characters=focus,
                max_events=10,
            )
            if use_ledger
            else ""
        )
        frag = _continue_fragment(
            base,
            chapter_no=chapter_no,
            previous_ending=book[-1] if book else "",
            facts=facts,
            open_hooks=ledger.open_hooks if use_ledger else [],
            direction_line=direction_line,
            ledger_block=ledger_block,
        )
        print(
            f"chapter {chapter_no} starting (attempt {attempts + 1}/{MAX_CHAPTER_ATTEMPTS}); "
            f"total_chars={total} target={target_chars}",
            flush=True,
        )
        # 协议 X：BRIEF + 编剧 Hive×4 + 分场执笔，调用更多
        call_cap = max(qa.CALL_CAP_PER_SAMPLE, 56)
        meter = qa.BudgetMeter(call_cap=call_cap)
        provider = qa.MeteredProvider(raw_provider, meter)
        result = await run_protocol_x(
            provider,
            frag,
            max_revisions=2,
        )
        prose = (result.prose or "").strip()
        chapter_calls = int(result.calls_used or 0)
        chapter_cost = int(result.cost_minor or 0)
        # 空稿/失败由外层 while 同号重试（MAX_CHAPTER_ATTEMPTS），此处不再内层重试

        total_calls += chapter_calls
        total_cost += chapter_cost

        artifacts = result.artifacts or {}
        event_fps: list[dict] = []
        entry_dup_warn = ""
        force_reject = False
        if result.completed and prose:
            packet = artifacts.get("production_packet") or {}
            script_data = packet.get("script") if isinstance(packet, dict) else None
            if isinstance(script_data, dict):
                register_ability_from_script(ledger, script_data)
            # 账本只从**最终正文**的 prose_facts 更新，不从剪辑前的 scene_audits
            prose_facts = artifacts.get("prose_facts") or {}

            # 重复事件硬检查：多数事实与已发生事件高度相似 → 拒稿
            dup_hits: list[str] = []
            fact_pool = [str(f).strip() for f in (result.facts or [])[:5] if str(f).strip()]
            for fact_text in fact_pool:
                similar = ledger.events.find_similar(
                    kind="committed_fact",
                    summary=fact_text[:80],
                )
                if similar:
                    dup_hits.append(
                        f"fact[{fact_text[:30]}]~ch{similar[-1].chapter_no}"
                    )
            if fact_pool and len(dup_hits) >= max(1, len(fact_pool) * 2 // 3):
                entry_dup_warn = "；".join(dup_hits[:3])
                print(
                    f"  DUP_REJECT ch{chapter_no}: {len(dup_hits)}/{len(fact_pool)} "
                    f"facts duplicate — {entry_dup_warn}",
                    flush=True,
                )
                force_reject = True
                artifacts["facts_status"] = "rejected_duplicate_events"
            elif dup_hits:
                entry_dup_warn = "；".join(dup_hits[:3])
                print(f"  DUP_WARN ch{chapter_no}: {entry_dup_warn}", flush=True)

            # 耗尽资源硬检查：按稳定 resource_id + 动作分类（use 才拒，mention 不拒）
            if not force_reject and ledger.ability:
                for fact_text in fact_pool:
                    rid = ledger.ability.fact_reuses_exhausted(fact_text)
                    if rid:
                        print(
                            f"  EXHAUST_REJECT ch{chapter_no}: "
                            f"fact reuses exhausted resource_id [{rid}]",
                            flush=True,
                        )
                        force_reject = True
                        artifacts["facts_status"] = "rejected_exhausted_resource"
                        entry_dup_warn = f"exhausted:{rid}"
                        break

            if not force_reject:
                # 与生产共用 ingest：钩子/事实/人物弧一次提交（含能力 resource_id）
                shifts = list(
                    prose_facts.get("character_shifts")
                    or artifacts.get("character_shifts")
                    or []
                )
                try:
                    ledger.ingest_chapter_outcome(
                        chapter_no=chapter_no,
                        facts=fact_pool,
                        hooks_opened=list(
                            prose_facts.get("hooks_opened")
                            or artifacts.get("hooks_opened")
                            or []
                        ),
                        hooks_closed=list(
                            prose_facts.get("hooks_closed")
                            or artifacts.get("hooks_closed")
                            or []
                        ),
                        character_shifts=[str(s) for s in shifts if s],
                        protagonist=_lead_from_packet(packet, script_data),
                    )
                except ValueError as exc:
                    print(f"  EXHAUST_REJECT ch{chapter_no}: {exc}", flush=True)
                    force_reject = True
                    artifacts["facts_status"] = "rejected_exhausted_resource"
                    entry_dup_warn = str(exc)[:80]
                if not force_reject:
                    for fact_text in fact_pool:
                        # 保留 event_fps 诊断字段（ingest 已写入 events）
                        from regent.novel.domain.story_ledger import EventFingerprint as _EF

                        ev = _EF(
                            chapter_no=chapter_no,
                            kind="committed_fact",
                            summary=fact_text[:80],
                        )
                        event_fps.append(
                            {"kind": ev.kind, "summary": ev.summary, "fp": ev.fp}
                        )
                    # 能力 use/deplete 已由 ingest_chapter_outcome 按 resource_id 写入

        entry = {
            "chapter_no": chapter_no,
            "completed": result.completed,
            "calls_used": chapter_calls,
            "cost_minor": chapter_cost,
            "stop_reason": result.stop_reason,
            "hard_fail_count": result.hard_fail_count,
            "selected_id": artifacts.get("selected_id"),
            "prose_chars": len(prose),
            "prose": prose if result.completed else "",
            "facts": list(result.facts or []) if result.completed else [],
            "facts_status": artifacts.get("facts_status"),
            "hooks_opened": list(artifacts.get("hooks_opened") or []) if result.completed else [],
            "hooks_closed": list(artifacts.get("hooks_closed") or []) if result.completed else [],
            "character_shifts": list(artifacts.get("character_shifts") or []) if result.completed else [],
            "event_fingerprints": event_fps,
            "dup_warnings": entry_dup_warn,
            "scene_count": len(artifacts.get("scene_plan", {}).get("cards") or []),
            "steps_log": list(result.steps_log or [])[-20:],
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
                    "scene_plan",
                }
            },
        }
        # 只有已验收章节进入正式序列；失败/重复拒稿记入 failures，同号重试
        if result.completed and prose and not force_reject:
            chapters.append(entry)
            book.append(f"第{chapter_no}章\n\n{prose}")
            total += len(prose)
            if result.facts:
                facts.extend(str(f) for f in result.facts if f)
            chapter_no += 1  # 成功才前进
        else:
            fail_reason = result.stop_reason
            if force_reject:
                fail_reason = "rejected_duplicate_events"
            failures.append(
                {
                    "chapter_no": chapter_no,
                    "stop_reason": fail_reason,
                    "calls_used": chapter_calls,
                    "cost_minor": chapter_cost,
                    "prose_chars": len(prose),
                    "dup_warnings": entry_dup_warn,
                    "attempt": attempts + 1,
                    "hard_fails": list(
                        (artifacts.get("scene_hard_fails") or [])[:12]
                    ),
                    "scene_hard_fails": list(artifacts.get("scene_hard_fails") or [])[:12],
                    "scene_audits": artifacts.get("scene_audits") or {},
                    "soft_evidence_warns": list(artifacts.get("soft_evidence_warns") or [])[:8],
                    "steps_tail": list(result.steps_log or [])[-12:],
                    "failed_prose_excerpt": (prose[:800] if prose else ""),
                }
            )
            print(
                f"chapter {chapter_no} FAILED (stop={fail_reason}, "
                f"attempt {attempts + 1}/{MAX_CHAPTER_ATTEMPTS}); will retry same number",
                flush=True,
            )
            # 不推进 chapter_no：下一轮 while 重试同号

        print(
            f"chapter {chapter_no} done completed={result.completed} "
            f"chars={len(prose)} total={total} stop={result.stop_reason} "
            f"calls={result.calls_used}",
            flush=True,
        )

        # 落盘
        novel_text = "\n\n".join(book)
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "novel.txt").write_text(novel_text, encoding="utf-8")
        (out_dir / "chapters.json").write_text(
            json.dumps(chapters, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (out_dir / "failures.json").write_text(
            json.dumps(failures, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        if use_ledger:
            ledger.save_json(ledger_path)
        (out_dir / "progress.json").write_text(
            json.dumps(
                {
                    "chapter_no": chapter_no,
                    "total_chars": total,
                    "total_calls": total_calls,
                    "total_cost_minor": total_cost,
                    "chapters_completed": len(chapters),
                    "failures": len(failures),
                    "protocol": "X",
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
        "protocol": "X",
        "fragment_id": fragment_id,
        "direction": direction_line,
        "direction_keywords": resolved_keywords,
        "target_chars": target_chars,
        "max_chapters": max_chapters,
        "chapters_written": len(chapters),
        "failures": len(failures),
        "total_chars": len(novel_text),
        "completed_chapters": len(chapters),
        "total_calls": total_calls,
        "total_cost_minor": total_cost,
        "open_hooks_remaining": len(ledger.open_hooks),
        "events_tracked": len(ledger.events.events),
    }
    (out_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out_dir / "chapters.json").write_text(
        json.dumps(chapters, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if use_ledger:
        ledger.save_json(ledger_path)
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fragment", default="f4_ent_gender")
    parser.add_argument(
        "--direction-keywords",
        default="",
        help="逗号分隔方向标签（勾选池）；留空则用产品默认勾选",
    )
    parser.add_argument(
        "--direction-custom",
        default="",
        help="逗号分隔自填方向标签，与 --direction-keywords 合并",
    )
    parser.add_argument("--target-chars", type=int, default=100_000)
    parser.add_argument("--max-chapters", type=int, default=50)
    parser.add_argument("--out", type=Path, default=Path("/tmp/serial_x_out"))
    parser.add_argument("--resume", action="store_true", default=False)
    parser.add_argument("--no-ledger", action="store_true", default=False)
    args = parser.parse_args(argv)
    from regent.novel.domain.story_direction import parse_direction_keyword_text

    kw_cli = (
        parse_direction_keyword_text(args.direction_keywords)
        if args.direction_keywords.strip()
        else None
    )
    custom_cli = (
        parse_direction_keyword_text(args.direction_custom)
        if args.direction_custom.strip()
        else None
    )
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
            use_ledger=not args.no_ledger,
            direction_keywords=kw_cli,
            direction_custom=custom_cli,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""R4 评估链受控命令行：prepare / rater-view / ingest / report / decide。

用法（数据库取 --database-url 或回退到 settings）::

    python -m regent.novel.eval_cli prepare --eval-id pilot-1 \
        --config-file eval_config.json --limit 10
    python -m regent.novel.eval_cli rater-view --eval-id pilot-1
    python -m regent.novel.eval_cli ingest --eval-id pilot-1 \
        --rater r1 --file ratings.json
    python -m regent.novel.eval_cli report --eval-id pilot-1
    python -m regent.novel.eval_cli decide --eval-id pilot-1

``--config-file`` 是 EvalConfig 的 JSON 载荷（arms 用执行器名，如
``["director_v2", "legacy_v1"]``）。评分与标准确认为人工步骤，本工具只负责
把样本、评分、证据、结论在真实数据上贯通并落库。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from regent.config import get_settings
from regent.infrastructure.database import create_engine, create_session_factory
from regent.novel.application import production
from regent.novel.application import eval_runner
from regent.novel.domain import evaluation as domain


def _config_from_file(path: Path) -> domain.EvalConfig:
    payload: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    sample = domain.SampleSpec(
        total=int((payload.get("sample") or {}).get("total") or 0),
        calm_scene_ratio=float((payload.get("sample") or {}).get("calm_scene_ratio") or 0.2),
        solo_scene_ratio=float((payload.get("sample") or {}).get("solo_scene_ratio") or 0.2),
        levels=tuple((payload.get("sample") or {}).get("levels") or ("chapter",)),
    )
    budget_payload = payload.get("budget") or {}
    budget = domain.BudgetBand(
        model=str(budget_payload.get("model") or ""),
        max_cost_minor_per_scene=int(budget_payload.get("max_cost_minor_per_scene") or 0),
        max_cost_minor_per_chapter=int(budget_payload.get("max_cost_minor_per_chapter") or 0),
        max_latency_ms_per_scene=int(budget_payload.get("max_latency_ms_per_scene") or 0),
    )
    thresholds_payload = payload.get("thresholds") or {}
    thresholds = domain.PromotionThresholds(
        min_samples=int(thresholds_payload.get("min_samples") or 0),
        min_read_willingness=float(thresholds_payload.get("min_read_willingness") or 0.0),
        min_character_credibility=float(
            thresholds_payload.get("min_character_credibility") or 0.0
        ),
        min_emotion_and_payoff=float(thresholds_payload.get("min_emotion_and_payoff") or 0.0),
        max_fact_error=float(thresholds_payload.get("max_fact_error") or 1.0),
        max_cost_minor_per_chapter=int(
            thresholds_payload.get("max_cost_minor_per_chapter") or 0
        ),
    )
    return domain.EvalConfig(
        eval_id=str(payload.get("eval_id") or ""),
        arms=tuple(str(a) for a in (payload.get("arms") or ())),
        sample=sample,
        budget=budget,
        thresholds=thresholds,
        raters=tuple(str(r) for r in (payload.get("raters") or ())),
    )


async def _run(args: argparse.Namespace) -> int:
    settings = get_settings()
    if args.database_url:
        from sqlalchemy.ext.asyncio import create_async_engine

        engine = create_async_engine(args.database_url, pool_pre_ping=True)
    else:
        engine = create_engine(settings)
    sessions = create_session_factory(engine)
    production.configure_session_factory(sessions)
    try:
        async with sessions() as session:
            if args.command == "prepare":
                out = await eval_runner.prepare(
                    session,
                    config=_config_from_file(Path(args.config_file)),
                    limit=int(args.limit),
                )
            elif args.command == "rater-view":
                from regent.novel.application import evaluation as eval_app

                out = {"samples": await eval_app.rater_view(session, eval_id=args.eval_id)}
            elif args.command == "ingest":
                payload = json.loads(Path(args.file).read_text(encoding="utf-8"))
                count = await eval_runner.ingest_ratings(
                    session, eval_id=args.eval_id, rater=args.rater, payload=payload
                )
                out = {"accepted": count}
            elif args.command == "report":
                report = await eval_runner.build_report_from_runs(
                    session, eval_id=args.eval_id
                )
                out = {
                    "verdict_hint": None,
                    "arms": [
                        {"arm": a["arm"], "n": a["n"], "ratings": a["ratings"]}
                        for a in report.get("arms", [])
                    ],
                    "evidence": report.get("evidence"),
                }
            elif args.command == "decide":
                verdict, reasons = await eval_runner.finish(session, eval_id=args.eval_id)
                out = {"verdict": verdict, "reasons": list(reasons)}
            else:  # pragma: no cover - argparse 限定
                raise RuntimeError(f"unknown command: {args.command}")
            await session.commit()
        print(json.dumps(out, ensure_ascii=False, indent=2))
    finally:
        production.configure_session_factory(None)
        await engine.dispose()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="novel-eval", description=__doc__)
    parser.add_argument("--database-url", default="", help="覆盖数据库连接串")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("prepare", help="冻结配置并从真实运行采样")
    p.add_argument("--eval-id", required=True)
    p.add_argument("--config-file", required=True)
    p.add_argument("--limit", type=int, default=10)

    p = sub.add_parser("rater-view", help="导出评者可见视图（仅 A/B 位置）")
    p.add_argument("--eval-id", required=True)

    p = sub.add_parser("ingest", help="录入一位评者的评分文件")
    p.add_argument("--eval-id", required=True)
    p.add_argument("--rater", required=True)
    p.add_argument("--file", required=True)

    p = sub.add_parser("report", help="从真实账本与运行记录出报告")
    p.add_argument("--eval-id", required=True)

    p = sub.add_parser("decide", help="按冻结阈值裁决")
    p.add_argument("--eval-id", required=True)

    args = parser.parse_args(argv)
    try:
        # CLI 正常在无事件循环的进程里运行；测试环境可能已有循环在跑——
        # 此时挪到独立线程跑，保证两条路径行为一致。
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(_run(args))
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, _run(args)).result()
    except Exception as exc:  # CLI 边界：带退出码失败，不静默
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

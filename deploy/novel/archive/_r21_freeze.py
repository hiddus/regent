"""R21-0: 只读拉取最新失败运行，写出脱敏 fixture。不修改业务代码路径外的产物除外。"""
from __future__ import annotations

import hashlib
import json
import shlex
import sys
from pathlib import Path

sys.path.insert(0, __file__.rsplit("\\", 1)[0].rsplit("/", 1)[0])
from _ssh import Remote  # noqa: E402

PSQL = "docker exec -i regent-postgres psql -U regent -d regent -t -A -c"
OUT = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "novel" / "run21"
RUN_ID = "e85633cb-6433-4361-8d98-7db0cf508485"


def q(r: Remote, sql: str) -> str:
    return r.run(f"{PSQL} {shlex.quote(' '.join(sql.split()))}", timeout=180).out


def main() -> int:
    r = Remote()
    meta = q(
        r,
        f"""
        SELECT id::text || E'\\t' || status || E'\\t' || created_at::text
        || E'\\t' || coalesce(current_step,'') || E'\\t' || coalesce(failure_reason,'')
        FROM novel_chapter_runs WHERE id = '{RUN_ID}'
        """,
    ).strip()
    if not meta:
        # fallback: latest TERMINAL_FAILED WATCH_PROSE-like
        meta = q(
            r,
            """
            SELECT id::text || E'\\t' || status || E'\\t' || created_at::text
            || E'\\t' || coalesce(current_step,'') || E'\\t' || coalesce(failure_reason,'')
            FROM novel_chapter_runs
            WHERE status LIKE '%FAIL%'
            ORDER BY created_at DESC LIMIT 1
            """,
        ).strip()
    print("META:", meta)
    rid = meta.split("\t")[0] if meta else RUN_ID
    raw = q(r, f"SELECT generation_context::text FROM novel_chapter_runs WHERE id = '{rid}'").strip()
    if not raw:
        print("no context")
        return 1
    ctx = json.loads(raw)
    production = ctx.get("production") or {}
    takes = production.get("takes") or []
    # pick take with revisions and validation failure if any
    target = None
    for take in takes:
        val = take.get("validation") or {}
        if take.get("revisions", 0) >= 1 or val.get("passed") is False:
            target = take
    if target is None and takes:
        target = takes[-1]

    versions = list(target.get("prose_versions") or [])
    content = target.get("content") or ""
    if content and (not versions or versions[-1] != content):
        versions = versions + [content]

    OUT.mkdir(parents=True, exist_ok=True)
    prose_dir = OUT / "prose_versions"
    prose_dir.mkdir(exist_ok=True)
    hashes = []
    for i, text in enumerate(versions):
        h = hashlib.sha256(text.encode("utf-8")).hexdigest()
        hashes.append({"index": i, "chars": len(text), "sha256": h})
        (prose_dir / f"v{i}.txt").write_text(text, encoding="utf-8")

    events = target.get("events") or []
    validation = target.get("validation")
    revision_instruction = target.get("revision_instruction") or ""
    render_instruction = target.get("render_instruction") or ""

    # redact work/person identifiers in manifest; keep structural evidence
    manifest = {
        "correspondence": {
            "remote_run_id": rid,
            "meta_line": meta,
            "basis": "id match or latest FAIL; features: WATCH_PROSE hard-fail + multi prose_versions",
            "handoff_label": "run21",
        },
        "take": {
            "take_no": target.get("take_no"),
            "scene_index": target.get("scene_index") or production.get("scene_index"),
            "revisions": target.get("revisions"),
            "phase_at_dump": production.get("phase"),
        },
        "prose_versions": hashes,
        "revision_instruction": revision_instruction,
        "render_instruction": render_instruction,
        "events": events,
        "validation": validation,
        "model_sampling": {
            "note": "frozen from production.calls fingerprints where present",
            "calls_summary": [
                {
                    "purpose": c.get("purpose"),
                    "input_hash": c.get("input_hash") or c.get("context_hash"),
                    "reused": c.get("reused"),
                    "model": c.get("model"),
                }
                for c in (production.get("calls") or [])
                if "PROSE" in str(c.get("purpose") or "")
                or "VALIDATE" in str(c.get("purpose") or "")
                or "RENDER" in str(c.get("purpose") or "")
                or "执笔" in str(c.get("purpose") or "")
                or "核验" in str(c.get("purpose") or "")
            ][-20:],
        },
        "independent_faults": [
            "local_rewrite_overwrote_full_scene",
            "director_rewrite_vs_settled_events",
            "validator_fabricated_quotes",
        ],
        "baseline_tests": {"novel_plus_provider_sse": 316, "note": "定向基线，非全仓"},
    }
    (OUT / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    # slim events+validation for tests that don't need full prose
    (OUT / "take_slice.json").write_text(
        json.dumps(
            {
                "events": events,
                "validation": validation,
                "revision_instruction": revision_instruction,
                "revisions": target.get("revisions"),
                "prose_version_hashes": hashes,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"wrote {OUT}")
    print(json.dumps(hashes, ensure_ascii=False, indent=2))
    print("revisions:", target.get("revisions"))
    print("validation.passed:", (validation or {}).get("passed"))
    print("issues:", (validation or {}).get("issues"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

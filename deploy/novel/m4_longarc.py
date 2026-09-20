"""M4 长篇分级认证：指标采集 + 生长曲线判定（NE-LongArc-v1）。

M4 的出口条件是「上下文和成本**不随全文长度无界线性膨胀**」。这条判据要的是
**每章真实携带的上下文量随章号怎么变**，不是「跑没跑完 150 章」。所以本工具
分两件事，且刻意分开：

1. **采集（免费、可复现）**：从 `novel_model_calls` / `novel_cost_entries` /
   `novel_chapter_runs` 按章聚合真实调用数据。这是跑过的真实运行留下的账，
   不需要再花钱，也不会因为重跑而变形。
2. **判定**：早/中/晚三段均值对比 + 线性/对数两种拟合并报 R²，
   给出「次线性/线性/超线性」的结论与置信说明。

**不做的事**：不把 24 章的结果说成「30 万字长篇能力」。计划写明 150 章通过后
才允许那么宣称，本工具的输出必须自带这句话。

用法：
    python deploy/novel/m4_longarc.py                 # 自动挑章数最多的作品
    python deploy/novel/m4_longarc.py --work <uuid>
产物：deploy/novel/artifacts/m4_growth_<work8>.json / .md
"""

from __future__ import annotations

import json
import math
import os
import shlex
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _ssh import Remote  # noqa: E402

BASE = os.path.dirname(os.path.abspath(__file__))
ART = os.path.join(BASE, "artifacts")
PG = "docker exec regent-postgres psql -U regent -d regent -tA -F'|' -c"
# 注意两件事，都会静默给出空结果：
# 1. 少了 `-c` 时 SQL 会被当成位置参数（dbname），psql 只打一条 warning 就返回空；
# 2. 把多行 SQL 塞进 json.dumps 会把换行转义成字面量 \n，SQL 直接语法错。
# 所以：SQL 压成单行 + shlex.quote 做 shell 转义，别用 JSON 转义。

# 逐章指标：真实调用账（不是估算）
Q_CHAPTERS = """
SELECT c.chapter_no,
       count(*),
       COALESCE(sum(c.input_tokens),0),
       COALESCE(sum(c.cached_input_tokens),0),
       COALESCE(sum(c.output_tokens),0)
FROM novel_model_calls c
WHERE c.work_id = '{wid}' AND c.chapter_no IS NOT NULL AND c.status = 'SUCCEEDED'
GROUP BY c.chapter_no ORDER BY c.chapter_no
"""

Q_COST = """
SELECT chapter_no, COALESCE(sum(amount_minor),0)
FROM novel_cost_entries
WHERE work_id = '{wid}' AND entry_kind = 'CONSUME' AND chapter_no IS NOT NULL
GROUP BY chapter_no ORDER BY chapter_no
"""

Q_RUNS = """
SELECT r.chapter_no, r.state, r.word_count
FROM novel_chapter_runs r
WHERE r.work_id = '{wid}' AND r.attempt = 1
ORDER BY r.chapter_no
"""

# 真实墙钟耗时：run 的 updated_at 与 created_at 常常相等（同事务写入），
# 所以改用该章模型调用的首末时间戳之差——这是真正花掉的时间。
Q_LATENCY = """
SELECT chapter_no,
       EXTRACT(EPOCH FROM (max(created_at) - min(created_at)))::int
FROM novel_model_calls
WHERE work_id = '{wid}' AND chapter_no IS NOT NULL
GROUP BY chapter_no ORDER BY chapter_no
"""

Q_MEMORY = """
SELECT source_chapter_no, count(*)
FROM novel_memory_items
WHERE work_id = '{wid}' AND source_chapter_no > 0
GROUP BY source_chapter_no ORDER BY source_chapter_no
"""

Q_MEMORY_TOTAL = "SELECT count(*) FROM novel_memory_items"

Q_WORK = """
SELECT id::text, COALESCE(title,''), state, latest_chapter_no,
       COALESCE(total_volume_count,0)
FROM novel_works WHERE id = '{wid}'
"""

Q_PICK = """
SELECT work_id::text, count(DISTINCT chapter_no)::int
FROM novel_model_calls WHERE chapter_no IS NOT NULL
GROUP BY work_id ORDER BY 2 DESC LIMIT 3
"""


def q(r: Remote, sql: str, timeout: int = 300) -> list[list[str]]:
    """跑一条查询，返回「逐行、逐列」的字符串矩阵。"""
    one_line = " ".join(sql.split())
    out = r.run(f"{PG} {shlex.quote(one_line)}", timeout=timeout)
    if out.err.strip():
        print("[psql]", out.err.strip()[:200])
    rows: list[list[str]] = []
    for line in out.text.splitlines():
        line = line.strip()
        if line:
            rows.append(line.split("|"))
    return rows


def _int(value: str) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def collect(r: Remote, wid: str) -> dict:
    chapters = q(r, Q_CHAPTERS.format(wid=wid))
    cost = {_int(row[0]): _int(row[1]) for row in q(r, Q_COST.format(wid=wid)) if len(row) >= 2}
    runs = {_int(row[0]): row for row in q(r, Q_RUNS.format(wid=wid)) if len(row) >= 3}
    latency = {
        _int(row[0]): _int(row[1]) for row in q(r, Q_LATENCY.format(wid=wid)) if len(row) >= 2
    }
    memory = {
        _int(row[0]): _int(row[1]) for row in q(r, Q_MEMORY.format(wid=wid)) if len(row) >= 2
    }
    mem_total = q(r, Q_MEMORY_TOTAL)
    wrow = q(r, Q_WORK.format(wid=wid))
    work = {}
    if wrow and len(wrow[0]) >= 5:
        work = {
            "id": wrow[0][0], "title": wrow[0][1], "state": wrow[0][2],
            "latest_chapter_no": _int(wrow[0][3]), "total_volume_count": _int(wrow[0][4]),
        }
    rows = []
    for row in chapters:
        if len(row) < 5:
            continue
        ch = _int(row[0])
        run = runs.get(ch, [])
        rows.append({
            "ch": ch,
            "calls": _int(row[1]),
            "in_tok": _int(row[2]),
            "cached_tok": _int(row[3]),
            "out_tok": _int(row[4]),
            "cost_minor": cost.get(ch, 0),
            "words": _int(run[2]) if len(run) > 2 else 0,
            "secs": latency.get(ch),
            "memory_items": memory.get(ch, 0),
        })
    return {
        "work": work,
        "rows": rows,
        "memory_items_total": _int(mem_total[0][0]) if mem_total else 0,
    }


# ---------------------------------------------------------------------------
# 判定：次线性 / 线性 / 超线性
# ---------------------------------------------------------------------------
def _fit(xs: list[float], ys: list[float], log_x: bool) -> tuple[float, float]:
    """最小二乘；返回 (斜率, R²)。log_x=True 时对 x 取自然对数。"""
    u = [math.log(x) if log_x else x for x in xs]
    n = len(u)
    if n < 3:
        return float("nan"), float("nan")
    mu, mv = sum(u) / n, sum(ys) / n
    sxx = sum((x - mu) ** 2 for x in u)
    if sxx == 0:
        return float("nan"), float("nan")
    sxy = sum((x - mu) * (y - mv) for x, y in zip(u, ys))
    slope = sxy / sxx
    inter = mv - slope * mu
    ss_tot = sum((y - mv) ** 2 for y in ys)
    ss_res = sum((y - (inter + slope * x)) ** 2 for x, y in zip(u, ys))
    r2 = 1 - ss_res / ss_tot if ss_tot else float("nan")
    return slope, r2


def analyse(rows: list[dict]) -> dict:
    """逐章上下文（in_tok）与成本的生长形状。"""
    used = [r for r in rows if r["in_tok"] > 0]
    if len(used) < 6:
        return {"verdict": "证据不足", "n": len(used)}
    xs = [float(r["ch"]) for r in used]
    seg = max(1, len(used) // 3)
    early = used[:seg]
    late = used[-seg:]

    def mean(key: str, group: list[dict]) -> float:
        return sum(g[key] for g in group) / len(group)

    out: dict = {
        "n": len(used),
        "first_ch": used[0]["ch"],
        "last_ch": used[-1]["ch"],
        "segments": {
            "early": {"chapters": [early[0]["ch"], early[-1]["ch"]],
                      "in_tok": round(mean("in_tok", early)),
                      "cost_minor": round(mean("cost_minor", early), 1),
                      "words": round(mean("words", early))},
            "late": {"chapters": [late[0]["ch"], late[-1]["ch"]],
                     "in_tok": round(mean("in_tok", late)),
                     "cost_minor": round(mean("cost_minor", late), 1),
                     "words": round(mean("words", late))},
        },
    }
    ctx_ratio = out["segments"]["late"]["in_tok"] / max(1, out["segments"]["early"]["in_tok"])
    ch_ratio = (sum(r["ch"] for r in late) / len(late)) / (
        sum(r["ch"] for r in early) / len(early)
    )
    out["context_growth_ratio"] = round(ctx_ratio, 3)
    out["chapter_span_ratio"] = round(ch_ratio, 3)

    ys = [float(r["in_tok"]) for r in used]
    lin_slope, lin_r2 = _fit(xs, ys, log_x=False)
    log_slope, log_r2 = _fit(xs, ys, log_x=True)
    out["fit"] = {
        "linear": {"slope_per_chapter": round(lin_slope, 1), "r2": round(lin_r2, 4)},
        "log": {"slope_per_ln_chapter": round(log_slope, 1), "r2": round(log_r2, 4)},
        "better": "log" if (log_r2 or 0) > (lin_r2 or 0) else "linear",
    }

    # 判定只看比值：上下文涨幅若明显小于章号涨幅，就是次线性（有界）。
    if ctx_ratio <= ch_ratio * 0.6:
        verdict = "次线性（有界）"
    elif ctx_ratio <= ch_ratio * 1.1:
        verdict = "近似线性"
    else:
        verdict = "超线性（危险）"
    out["verdict"] = verdict
    out["note"] = (
        "此判定仅覆盖已观测章数；计划规定 150 章通过后才允许宣称「30 万字长篇能力」。"
    )
    return out


def gaps(rows: list[dict], memory_total: int) -> list[str]:
    """这份数据**证不了**什么——必须写在报告里，否则很容易被当成全量认证。"""
    out: list[str] = []
    if memory_total == 0:
        out.append(
            "`novel_memory_items` 全库 0 行：R3 长期记忆**从未在真实运行里跑过**，"
            "因此「实体召回曲线」在本报告中无证据（只有单测覆盖）。"
        )
    if rows and all(r["cached_tok"] == 0 for r in rows):
        out.append(
            "`cached_input_tokens` 全为 0：prompt 缓存要么未生效、要么未被上报。"
            "若确实未命中，成本还有可观的下降空间（未验证）。"
        )
    if rows and max(r["ch"] for r in rows) < 150:
        out.append(
            f"仅观测到第 {max(r['ch'] for r in rows)} 章，未达 150 章。"
            "按计划，150 章通过前不得宣称「30 万字长篇能力」。"
        )
    out.append(
        "本作品是历史运行留下的账，早于 B-04/R3/R4 等改动；"
        "它证明的是**上下文生长形状**，不等于当前构建的全功能认证。"
    )
    return out


def render(work: dict, rows: list[dict], a: dict, wid: str, note_gaps: list[str]) -> str:
    lines = [
        "# M4 长篇生长曲线（NE-LongArc-v1）",
        "",
        f"- 作品：`{wid}`（{work.get('title') or '未命名'}）",
        f"- 状态：{work.get('state')}，latest_chapter_no={work.get('latest_chapter_no')}"
        f"，卷数={work.get('total_volume_count')}",
        f"- 观测章数：{a.get('n')}（第 {a.get('first_ch')}–{a.get('last_ch')} 章），"
        "数据来自真实运行留下的 `novel_model_calls` / `novel_cost_entries`。",
        "",
        "## 逐章明细",
        "",
        "| 章 | 调用 | 输入 token | 其中缓存 | 输出 token | 成本(分) | 字数 | 耗时(s) | 记忆条目 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        lines.append(
            f"| {r['ch']} | {r['calls']} | {r['in_tok']} | {r['cached_tok']} | "
            f"{r['out_tok']} | {r['cost_minor']} | {r['words']} | "
            f"{r['secs'] if r['secs'] is not None else '-'} | {r['memory_items']} |"
        )
    seg = a.get("segments", {})
    lines += ["", "## 早/晚对照", ""]
    if seg:
        lines += [
            "| 段 | 章区间 | 平均输入 token | 平均成本(分) | 平均字数 |",
            "|---|---|---|---|---|",
            f"| 早 | {seg['early']['chapters'][0]}–{seg['early']['chapters'][1]} | "
            f"{seg['early']['in_tok']} | {seg['early']['cost_minor']} | {seg['early']['words']} |",
            f"| 晚 | {seg['late']['chapters'][0]}–{seg['late']['chapters'][1]} | "
            f"{seg['late']['in_tok']} | {seg['late']['cost_minor']} | {seg['late']['words']} |",
            "",
            f"- 上下文涨幅：**{a['context_growth_ratio']}×**；同期章号跨度 "
            f"**{a['chapter_span_ratio']}×**",
        ]
    if "fit" in a:
        lines += [
            f"- 线性拟合 R²={a['fit']['linear']['r2']}（斜率 "
            f"{a['fit']['linear']['slope_per_chapter']} token/章）",
            f"- 对数拟合 R²={a['fit']['log']['r2']}（斜率 "
            f"{a['fit']['log']['slope_per_ln_chapter']} token/ln(章)）",
            f"- 拟合形状更接近：**{a['fit']['better']}**",
        ]
    lines += ["", f"## 判定：**{a.get('verdict')}**", "", a.get("note", ""), ""]
    if note_gaps:
        lines += ["## 这份数据证不了什么（必须一并阅读）", ""]
        lines += [f"- {g}" for g in note_gaps]
        lines += [""]
    return "\n".join(lines)


def main() -> int:
    os.makedirs(ART, exist_ok=True)
    wid = None
    if "--work" in sys.argv:
        wid = sys.argv[sys.argv.index("--work") + 1]
    with Remote() as r:
        if wid is None:
            picks = q(r, Q_PICK)
            if not picks:
                print("[FAIL] 库里没有任何带章号的模型调用记录，无法采集")
                return 1
            print("[pick] 候选作品（work|章数）：", picks)
            wid = picks[0][0]
        print(f"[collect] work={wid}")
        data = collect(r, wid)
        if not data["rows"]:
            print("[FAIL] 该作品没有可用的逐章指标")
            return 1
        a = analyse(data["rows"])
        note_gaps = gaps(data["rows"], data["memory_items_total"])
        short = wid[:8]
        with open(os.path.join(ART, f"m4_growth_{short}.json"), "w", encoding="utf-8") as fh:
            json.dump({"work_id": wid, **data, "analysis": a, "gaps": note_gaps},
                      fh, ensure_ascii=False, indent=2)
        md = render(data["work"], data["rows"], a, wid, note_gaps)
        with open(os.path.join(ART, f"m4_growth_{short}.md"), "w", encoding="utf-8") as fh:
            fh.write(md)
        print(md)
        print(f"[OK] 产物：artifacts/m4_growth_{short}.json / .md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

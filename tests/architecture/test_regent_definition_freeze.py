"""Freeze guard for the active Regent definition — CI fails on drift or duplicate active sources."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEF_PATH = ROOT / "docs" / "definitions" / "REGENT-DEFINITION-3.0.txt"
HASH_PATH = ROOT / "docs" / "definitions" / "REGENT-DEFINITION-3.0.sha256"
LEGACY_DEF_PATHS = {
    ROOT / "docs" / "definitions" / "REGENT-DEFINITION-1.0.txt",
    ROOT / "docs" / "definitions" / "REGENT-DEFINITION-2.0.txt",
}
PRD_PATH = ROOT / "Regent-PRD.md"
TECH_PATH = ROOT / "Regent-Technical-Spec.md"

DEFINITION_ID = "REGENT-DEFINITION-3.0"


def test_freeze_guard_paths_exist() -> None:
    """Meta-guard: wrong paths must fail as clear asserts, not FileNotFoundError."""
    assert PRD_PATH.is_file(), f"PRD baseline missing at {PRD_PATH}"
    assert TECH_PATH.is_file(), f"Technical Spec baseline missing at {TECH_PATH}"
    assert DEF_PATH.is_file(), f"canonical definition missing at {DEF_PATH}"
    assert HASH_PATH.is_file(), f"canonical hash missing at {HASH_PATH}"


def normalize_definition_bytes(raw: bytes) -> bytes:
    text = unicodedata.normalize("NFC", raw.decode("utf-8"))
    lines = [ln.rstrip() for ln in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    while lines and lines[-1] == "":
        lines.pop()
    return ("\n".join(lines) + "\n").encode("utf-8")


def content_hash(path: Path) -> str:
    return hashlib.sha256(normalize_definition_bytes(path.read_bytes())).hexdigest()


def extract_definition_text_field(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    match = re.search(
        r"^DEFINITION_TEXT=\n(.+?)(?=\n\nATTRIBUTE_|\Z)",
        text,
        flags=re.MULTILINE | re.DOTALL,
    )
    assert match is not None, "DEFINITION_TEXT block missing"
    return match.group(1).strip()


def test_definition_id_and_hash_file_match() -> None:
    assert DEF_PATH.is_file(), "canonical definition file missing"
    assert HASH_PATH.is_file(), "canonical hash file missing"
    assert DEFINITION_ID in DEF_PATH.read_text(encoding="utf-8")
    expected = HASH_PATH.read_text(encoding="utf-8").strip().split()[0]
    assert content_hash(DEF_PATH) == expected


def test_prd_links_canonical_definition_without_copying_body() -> None:
    body = extract_definition_text_field(DEF_PATH)
    prd = PRD_PATH.read_text(encoding="utf-8")
    assert DEFINITION_ID in prd
    assert "docs/definitions/REGENT-DEFINITION-3.0.txt" in prd
    assert body not in prd, "PRD must link to the definition, not copy DEFINITION_TEXT"


def test_tech_spec_references_without_redefining() -> None:
    tech = TECH_PATH.read_text(encoding="utf-8")
    assert DEFINITION_ID in tech
    body = extract_definition_text_field(DEF_PATH)
    # Tech spec may mention the ID but must not embed the full normative paragraph.
    assert body not in tech, "Technical Spec must reference definition, not copy DEFINITION_TEXT"


def test_no_document_copies_definition_body() -> None:
    """3.0 line 4: other documents must link here, not copy the body verbatim."""
    body = extract_definition_text_field(DEF_PATH)
    skipped_parts = {".venv", "node_modules", ".git", "archive", "__pycache__"}
    offenders: list[str] = []
    for path in list(ROOT.rglob("*.md")) + list(ROOT.rglob("*.txt")):
        if not path.is_file() or skipped_parts & set(path.parts):
            continue
        if path.resolve() in {DEF_PATH.resolve(), *(p.resolve() for p in LEGACY_DEF_PATHS)}:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if body in text:
            offenders.append(str(path.relative_to(ROOT)))
    assert offenders == [], f"documents copy DEFINITION_TEXT verbatim: {offenders}"


def iter_current_text_docs() -> list[Path]:
    """Docs that speak in the project's own voice today.

    `archive/` and the definition files themselves are excluded: they exist to
    preserve superseded text. `deliverables/` audits quote defects on purpose.

    `.html` is in scope because the training and post-mortem decks under
    `regent-pptx/` are the documents that actually carried stale rules through
    the 2026-08-10 recalibration; a guard that only reads `.md`/`.txt` cannot
    see them.
    """
    skipped_parts = {
        ".venv",
        "node_modules",
        ".git",
        "archive",
        "__pycache__",
        "deliverables",
        ".pytest_cache",
    }
    docs: list[Path] = []
    for suffix in ("*.md", "*.txt", "*.html"):
        for path in ROOT.rglob(suffix):
            if not path.is_file() or skipped_parts & set(path.parts):
                continue
            if path.resolve() in {DEF_PATH.resolve(), *(p.resolve() for p in LEGACY_DEF_PATHS)}:
                continue
            docs.append(path)
    return docs


def extract_normative_bodies(path: Path) -> list[str]:
    """Every DEFINITION_TEXT / ATTRIBUTE_* paragraph in a definition file."""
    text = path.read_text(encoding="utf-8")
    blocks = re.findall(
        r"^(?:DEFINITION_TEXT|ATTRIBUTE_\w+)=\n(.+?)(?=\n\n|\Z)",
        text,
        flags=re.MULTILINE | re.DOTALL,
    )
    return [block.strip() for block in blocks if block.strip()]


def test_no_current_doc_copies_a_superseded_definition_body() -> None:
    """A verbatim paragraph from 2.0 asserts a rule 3.0 replaced.

    2.0 ATTRIBUTE_3 required a team topology and treated a single agent as an
    exception; 3.0 ATTRIBUTE_4 forbids using single-agent capability as a
    precondition at all. Copying the old paragraph into a live document
    reinstates the superseded rule without touching the frozen source, which is
    exactly how the 2026-08-10 recalibration left stale text behind in the
    training decks.
    """
    superseded: list[tuple[str, str]] = []
    for legacy_path in LEGACY_DEF_PATHS:
        if not legacy_path.is_file():
            continue
        for body in extract_normative_bodies(legacy_path):
            superseded.append((legacy_path.name, body))
    assert superseded, "no legacy definition bodies found to guard against"

    offenders: list[str] = []
    for path in iter_current_text_docs():
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for source_name, body in superseded:
            if body in text:
                offenders.append(f"{path.relative_to(ROOT)} copies {source_name}")
    assert offenders == [], f"current docs restate superseded definition bodies: {offenders}"


# Sentences that were normative before 2026-08-10 and were retired because they
# gated exploration itself rather than real-world effects. They survive only as
# explicitly-labelled history; restating one unlabelled reinstates the rule.
RETIRED_ASSERTIONS = (
    "不在 P2-4 DecisionRecord 前开放自适应自由拓扑",
    "再决定是否允许固定模板或自适应组织进入候选",
    "没有正向预期净收益证据时不得增员",
    "只有净收益为正且护栏不退化",
    "只有统计 Gate 证明净收益后启用",
    "必须通过冻结实验验证净收益",
)

HISTORICAL_QUOTE_MARKERS = (
    "已被定义 3.0 修订",
    "已被取代",
    "已修订",
    "口径更新",
    "当时",
    "旧 PRD",
    "旧批次",
    "原文",
    "反面教材",
)

# A retired sentence and its revision label rarely sit on one line: in Markdown
# the quote is a list item and the label is a following blockquote. Widen the
# check to the surrounding lines instead of demanding same-line labelling.
MARKER_WINDOW = 8


def test_retired_assertions_appear_only_as_labelled_history() -> None:
    """Verbatim-copy guards miss a paraphrased or re-typed superseded rule.

    `Regent失败案例复盘.html` quoted the pre-recalibration Plan §12.5 sentence as
    current policy after Plan §12.5 itself had been narrowed, so the deck
    re-asserted a ban the project had already withdrawn. Copy detection against
    the definition files cannot catch that: the sentence never lived in a
    definition file.
    """
    offenders: list[str] = []
    for path in iter_current_text_docs():
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (UnicodeDecodeError, OSError):
            continue
        for index, line in enumerate(lines):
            for assertion in RETIRED_ASSERTIONS:
                if assertion not in line:
                    continue
                window = "\n".join(
                    lines[max(0, index - MARKER_WINDOW) : index + MARKER_WINDOW + 1]
                )
                if not any(marker in window for marker in HISTORICAL_QUOTE_MARKERS):
                    offenders.append(f"{path.relative_to(ROOT)}:{index + 1} → {assertion}")
    assert offenders == [], f"retired rules restated as current policy: {offenders}"


# `RETIRED_ASSERTIONS` matches fixed strings, so it only ever catches the exact
# wording someone already found. The same rule re-typed in a new form walks past
# it: the 2026-08-11 fourth-round review found `P2-4 统计 Gate 正净收益` as a bare
# precondition in `docs/p1-remaining-coding-plan.md` and a whole untouched deck
# (`Regent失败案例复盘-课堂版.html`) teaching `先证明单人能干，才考虑加人`, neither of
# which shares a substring with any registered sentence.
#
# The invariant behind all of them is one sentence: proof, net benefit and Gates
# may gate *real-world effects* — production defaults, irreversible permissions,
# rollout — and may not gate candidacy, sandbox trials or where a team is allowed
# to think. So match the gating *construction* and require the scope that makes
# it legitimate.
GATING_CONSTRUCTIONS = (
    # "…统计 Gate 正净收益" / "必须冻结实验验证净收益" as a bare prerequisite.
    # The gating word is required: `Regent-Plan.md` §11 lists "冻结实验任务集…和净收益
    # 公式" as an S4 exit artifact, which freezes a formula rather than gating
    # anyone's exploration, and must not trip this.
    re.compile(
        r"(?:统计\s*Gate|冻结实验|Eval\s*(?:Harness|DecisionRecord))[^。\n]{0,24}"
        r"(?:正净收益|必须[^。\n]{0,8}净收益|净收益[^。\n]{0,4}(?:才|前|后))"
    ),
    re.compile(r"必须[^。\n]{0,12}(?:验证|证明)[^。\n]{0,8}净收益"),
    re.compile(r"净收益[^。\n]{0,16}(?:才|后)[^。\n]{0,16}(?:启用|进入|晋级|允许|增员|开放)"),
    # "先证明…才…" / "…稳了才上多 Agent" / "…前不做自适应组织"
    re.compile(r"先证明[^。\n]{0,24}(?:才|再)[^。\n]{0,16}(?:加人|增员|上多|扩|开放|进入)"),
    re.compile(r"(?:稳了|闭环)[^。\n]{0,8}才[^。\n]{0,8}(?:上|谈|做)[^。\n]{0,8}多\s*Agent"),
    re.compile(r"(?:没闭环|没稳)[^。\n]{0,8}前[^。\n]{0,12}不(?:做|得|allowed)"),
)

# Words that scope a gate to real-world effects, or demote it from a permission
# gate to advice about where to spend effort. Either makes the sentence legal.
EFFECT_SCOPE_MARKERS = (
    "生产默认",
    "生产权限",
    "现实权限",
    "现实生产权限",
    "生产 rollout",
    "生产扩流",
    "生产晋级",
    "晋级",
    "扩大",
    "不可逆",
    "投入顺序",
    "投入重心",
    "重心",
    "沙箱",
)


def test_proof_gates_are_scoped_to_real_world_effects() -> None:
    """A Gate on candidacy is a withdrawn rule even in fresh wording.

    `test_retired_assertions_appear_only_as_labelled_history` compares against
    fixed strings, so it certifies only the sentences a human already noticed.
    This checks the shape instead: any "prove X before you may Y" construction
    has to name the real-world effect it gates (production default, irreversible
    permission, rollout) or mark itself as investment-order advice. Without that
    scope the sentence gates exploration, which 3.0 ATTRIBUTE_2/5/6/7 withdrew.
    """
    offenders: list[str] = []
    for path in iter_current_text_docs():
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (UnicodeDecodeError, OSError):
            continue
        for index, line in enumerate(lines):
            if not any(pattern.search(line) for pattern in GATING_CONSTRUCTIONS):
                continue
            window = "\n".join(lines[max(0, index - MARKER_WINDOW) : index + MARKER_WINDOW + 1])
            if any(marker in line for marker in EFFECT_SCOPE_MARKERS):
                continue
            if any(marker in window for marker in HISTORICAL_QUOTE_MARKERS):
                continue
            offenders.append(f"{path.relative_to(ROOT)}:{index + 1}")
    assert offenders == [], f"proof gates exploration instead of real-world effects: {offenders}"


def test_docs_do_not_claim_a_stale_number_of_invariant_attributes() -> None:
    """A wrong attribute count points readers at a superseded attribute list.

    3.0 carries nine attributes; 1.0 carried seven, and its seventh was "明确终止"
    where 3.0's seventh is "边界落在现实影响上" — opposite governance meanings. The
    2026-08-11 review found `七个恒定属性` still presented as the product identity
    in the leader briefing and as the current `PRD §0.2` in two decks, so a reader
    following the count landed on rules 3.0 had withdrawn. Counting the frozen
    source keeps this honest if a future definition adds an attribute.
    """
    actual = len(re.findall(r"^ATTRIBUTE_\w+=", DEF_PATH.read_text(encoding="utf-8"), re.MULTILINE))
    assert actual == 9, f"attribute count changed to {actual}; update the docs and this guard"
    digits = {"七": 7, "八": 8, "九": 9, "十": 10}
    # A bare digit needs 个/条 to be a count: `### 0.2 不可分割的恒定属性` is a section
    # number, not a claim that there are two attributes.
    claim = re.compile(
        r"(?:([一二三四五六七八九十])\s*(?:个|条)?|(\d+)\s*(?:个|条))\s*(?:不可分割的)?恒定属性"
    )
    offenders: list[str] = []
    for path in iter_current_text_docs():
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (UnicodeDecodeError, OSError):
            continue
        for index, line in enumerate(lines):
            for chinese, arabic in claim.findall(line):
                raw = chinese or arabic
                claimed = digits.get(raw, int(raw) if raw.isdigit() else None)
                if claimed is None or claimed == actual:
                    continue
                window = "\n".join(
                    lines[max(0, index - MARKER_WINDOW) : index + MARKER_WINDOW + 1]
                )
                if any(marker in window for marker in HISTORICAL_QUOTE_MARKERS):
                    continue
                offenders.append(f"{path.relative_to(ROOT)}:{index + 1} claims {claimed}")
    assert offenders == [], f"docs claim a stale invariant-attribute count: {offenders}"


# Phrases that make a citation an authority claim ("this is the governing
# definition") rather than a narrative mention ("we compared against 1.0").
DEFINITION_AUTHORITY_MARKERS = (
    "唯一规范",
    "规范源",
    "规范定义源",
    "规范身份源",
    "永久定义",
    "冻结定义",
    "FROZEN",
    "定义：",
    "定义依据",
    "不得改写",
    "只引用",
    "锚定",
    "对齐",
    "依据文档",
)

# Markers that scope the citation to the past instead of asserting it as current.
SUPERSEDED_CITATION_MARKERS = (
    "已被取代",
    "已被 2.0",
    "已被 3.0",
    "当时",
    "成稿时",
    "现行",
    "历史",
    "SUPERSEDED",
    "只读保留",
)

# Signed evidence packs record what was decided under the definition in force at
# the time; rewriting them would falsify the evidence, so they are read-only
# like `archive/`.
FROZEN_RECORD_DIRS = ("graduation-evidence", "experiments")


def test_current_docs_cite_only_the_active_definition_as_authority() -> None:
    """A live doc naming 1.0/2.0 as its governing definition misroutes readers.

    `test_source_code_cites_only_the_active_definition` guards `core/src` only,
    so the 2026-08-10 recalibration left `docs/README.md` — the index every
    reader enters through — declaring `REGENT-DEFINITION-1.0.txt` the "唯一规范
    定义源" while 3.0 was the frozen baseline. That is not a stale link: 1.0
    ATTRIBUTE_7 means "explicit termination" where 3.0 ATTRIBUTE_7 means
    "boundaries at real-world effects", so following the pointer yields a rule
    3.0 withdrew.

    A dated report may still name the definition it was written against; the
    citation just has to be scoped to the past.
    """
    stale = re.compile(r"REGENT-DEFINITION-(?!3\.0)\d+\.\d+")
    offenders: list[str] = []
    for path in iter_current_text_docs():
        if set(path.parts) & set(FROZEN_RECORD_DIRS):
            continue
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (UnicodeDecodeError, OSError):
            continue
        for index, line in enumerate(lines):
            if not stale.search(line):
                continue
            if not any(marker in line for marker in DEFINITION_AUTHORITY_MARKERS):
                continue
            window = "\n".join(lines[max(0, index - MARKER_WINDOW) : index + MARKER_WINDOW + 1])
            if not any(marker in window for marker in SUPERSEDED_CITATION_MARKERS):
                offenders.append(f"{path.relative_to(ROOT)}:{index + 1}")
    assert offenders == [], f"docs cite a superseded definition as current authority: {offenders}"


def test_source_code_cites_only_the_active_definition() -> None:
    """Runtime code stamps definition ids into persisted metadata and user messages.

    A superseded id is not a cosmetic typo: 1.0 ATTRIBUTE_7 meant "explicit
    termination" while 3.0 ATTRIBUTE_7 means "boundaries at real-world effects",
    so a stale citation silently asserts the wrong governance rule.
    """
    stale = re.compile(r"REGENT-DEFINITION-(?!3\.0)\d+\.\d+")
    offenders: list[str] = []
    for path in (ROOT / "core" / "src").rglob("*.py"):
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if stale.search(line):
                offenders.append(f"{path.relative_to(ROOT)}:{lineno}")
    assert offenders == [], f"code cites a superseded definition: {offenders}"


def test_no_second_normative_definition_file() -> None:
    forbidden = list(ROOT.rglob("*DEFINITION*.txt")) + list(ROOT.rglob("*DEFINITION*.md"))
    allowed = {
        DEF_PATH.resolve(),
        *(path.resolve() for path in LEGACY_DEF_PATHS),
        (ROOT / "docs" / "definitions" / "README.md").resolve(),
    }
    extras = [
        path
        for path in forbidden
        if path.is_file()
        and path.resolve() not in allowed
        and "REGENT-DEFINITION" in path.name
        and "node_modules" not in path.parts
        and ".venv" not in path.parts
    ]
    assert extras == [], f"extra definition artifacts: {extras}"

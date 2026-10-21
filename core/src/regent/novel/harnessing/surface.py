"""写作配置树：版本化、内容寻址、按运行钉住。

可编辑面：提示词、载荷旋钮、写作技巧、软检查、阶段钩子、写作经验。
冻结面：硬门、正史、台账、预算上限、命令白名单——覆盖层碰到即拒收。

配置是一棵树：基础包 ← 本书覆盖层 ← 本章递归覆盖层。
每次运行创建时解析基础包与本书覆盖层并钉住 ``harness_id`` + ``harness_hash``。
"""

from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml

PACK_ROOT = Path(__file__).resolve().parent.parent / "harness_packs"
BASE_HARNESS_ID = "base/v1"

FROZEN_KEYS: frozenset[str] = frozenset(
    {
        "hard_gates",
        "canon",
        "ledger",
        "budget_caps",
        "command_whitelist",
    }
)
EDITABLE_KEYS: frozenset[str] = frozenset(
    {"prompts", "knobs", "skills", "soft_checks", "hooks", "memory"}
)
EDITABLE_PROMPTS: frozenset[str] = frozenset({"write", "repair"})
PROMPT_MIN_CHARS = 20
PROMPT_MAX_CHARS = 3000
MEMORY_MAX_CHARS = 2000
INSTRUCTION_MAX_CHARS = 300
GUIDANCE_MAX_CHARS = 200
MESSAGE_MAX_CHARS = 100
PATTERN_MAX_CHARS = 80
PATTERNS_PER_CHECK = 5
# 章首重合只看正文开头这么多字，对上一章结尾两倍长度。
OPENING_CHARS = 300

# 0 表示不限制：基础包取 0 时载荷与默认臂逐字一致。
DEFAULT_KNOB_RANGES: dict[str, dict[str, Any]] = {
    "previous_chapter_tail": {"min": 200, "max": 1000, "default": 500},
    "earlier_results_count": {"min": 0, "max": 10, "default": 0},
    "open_promises_max": {"min": 0, "max": 20, "default": 0},
    "soft_check_repetition_ngram": {"min": 40, "max": 120, "default": 80},
    "soft_check_chapter_opening_overlap": {"min": 20, "max": 100, "default": 50},
}

# 技巧的 ``when`` 只能用这些条件；值类型固定，程序求值，不执行模型写的代码。
SKILL_CONDITIONS: dict[str, type] = {
    "shows_rule": bool,
    "payoff_due": bool,
    "previous_ends_mid_action": bool,
    "first_chapter": bool,
    "choice_any": list,
    "result_any": list,
}
SKILL_MODES = frozenset({"auto", "always", "off"})

# 钩子条件 → 可用占位符。
HOOK_CONDITIONS: dict[str, tuple[str, ...]] = {
    "previous_ends_mid_action": (),
    "open_promise_age_exceeds": ("age",),
    "method_rejected_at_least": ("rounds",),
}
SOFT_CHECK_KINDS = frozenset({"repetition", "overlap", "pattern"})
_ID = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
# 量词套量词（``(a+)+``、``(a*)*``、``(a+){2,}``）会回溯爆炸。
_NESTED_QUANTIFIER = re.compile(r"\([^()]*[+*][^()]*\)\s*[+*{]")
_BACKREF = re.compile(r"\\[1-9]")
_END_MARKS = frozenset('。！？!?…」』”"）)')


class ConfigTree:
    """一棵版本化的写作配置。

    ``tree`` 是纯 dict，可 JSON 序列化。``content_hash`` 由 tree 的规范 JSON 计算，
    用于钉住和比较。
    """

    def __init__(self, tree: dict[str, Any], *, parent_id: str | None = None) -> None:
        self.tree = deepcopy(tree)
        self.parent_id = parent_id
        self._hash: str | None = None

    @property
    def content_hash(self) -> str:
        if self._hash is None:
            canonical = json.dumps(self.tree, ensure_ascii=False, sort_keys=True)
            self._hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
        return self._hash

    def get_prompt(self, key: str) -> str:
        return str((self.tree.get("prompts") or {}).get(key) or "")

    def get_knob(self, key: str) -> Any:
        knobs = self.tree.get("knobs") or {}
        if key in knobs:
            return knobs[key]
        return DEFAULT_KNOB_RANGES.get(key, {}).get("default")

    def get_skills(self) -> dict[str, Any]:
        return {k: v for k, v in (self.tree.get("skills") or {}).items() if isinstance(v, dict)}

    def get_soft_checks(self) -> dict[str, Any]:
        return {
            k: v
            for k, v in (self.tree.get("soft_checks") or {}).items()
            if isinstance(v, dict) and v.get("enabled", True)
        }

    def get_hooks(self) -> dict[str, Any]:
        return {
            k: v
            for k, v in (self.tree.get("hooks") or {}).items()
            if isinstance(v, dict) and v.get("enabled", True)
        }

    def get_memory(self) -> str:
        lines = str(self.tree.get("memory") or "").splitlines()
        return "\n".join(
            line for line in lines if line.strip() and not line.lstrip().startswith("#")
        )

    def diff(self, other: ConfigTree) -> dict[str, Any]:
        return _deep_diff(self.tree, other.tree)


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"配置文件必须是映射: {path}")
    return raw


def load_base_pack(version: str = "v1") -> ConfigTree:
    """从 ``harness_packs/base/<version>/`` 加载基础配置包。"""
    pack_dir = PACK_ROOT / "base" / version
    if not pack_dir.is_dir():
        raise FileNotFoundError(f"配置包不存在: {pack_dir}")

    prompts: dict[str, str] = {}
    prompts_dir = pack_dir / "prompts"
    if prompts_dir.is_dir():
        for path in sorted(prompts_dir.glob("*.md")):
            prompts[path.stem] = path.read_text(encoding="utf-8").strip()

    knobs: dict[str, Any] = {}
    for key, spec in _read_yaml(pack_dir / "knobs.yaml").items():
        knobs[key] = spec.get("default") if isinstance(spec, dict) else spec

    memory_path = pack_dir / "memory.md"
    tree = {
        "pack_version": version,
        "prompts": prompts,
        "knobs": knobs,
        "skills": _read_yaml(pack_dir / "skills" / "skills.yaml"),
        "soft_checks": _read_yaml(pack_dir / "soft_checks.yaml"),
        "hooks": _read_yaml(pack_dir / "hooks.yaml"),
        "memory": memory_path.read_text(encoding="utf-8").strip() if memory_path.is_file() else "",
    }
    return ConfigTree(tree)


def load_harness(harness_id: str) -> ConfigTree:
    """按钉住的 ``harness_id``（``base/<version>``）加载基础包。"""
    kind, _, version = str(harness_id or BASE_HARNESS_ID).partition("/")
    if kind != "base" or not version:
        raise ValueError(f"未知配置包: {harness_id}")
    return load_base_pack(version)


def load_improver_prompt(kind: str) -> str:
    """加载改进器提示词。kind: propose / compose。"""
    path = PACK_ROOT / "improver" / f"{kind}.md"
    if not path.is_file():
        raise FileNotFoundError(f"改进器提示词不存在: {path}")
    return path.read_text(encoding="utf-8").strip()


def validate_knob_value(key: str, value: Any) -> int:
    """校验旋钮取值在登记范围内，越界或未登记都拒收。"""
    spec = DEFAULT_KNOB_RANGES.get(key)
    if not spec:
        raise ValueError(f"未登记的旋钮: {key}")
    if isinstance(value, bool):
        raise ValueError(f"旋钮 {key} 取值不是数字: {value!r}")
    try:
        num = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"旋钮 {key} 取值不是数字: {value!r}") from None
    if num < spec["min"] or num > spec["max"]:
        raise ValueError(f"旋钮 {key} 取值 {num} 越界 [{spec['min']}, {spec['max']}]")
    return num


def safe_pattern(pattern: Any) -> re.Pattern[str]:
    """软检查的正则只收短的、不会回溯爆炸的。"""
    text = str(pattern or "")
    if not text or len(text) > PATTERN_MAX_CHARS:
        raise ValueError(f"正则长度须在 1-{PATTERN_MAX_CHARS} 字: {text[:20]!r}")
    if _NESTED_QUANTIFIER.search(text):
        raise ValueError(f"正则含嵌套量词: {text!r}")
    if _BACKREF.search(text):
        raise ValueError(f"正则含反向引用: {text!r}")
    try:
        return re.compile(text)
    except re.error as exc:
        raise ValueError(f"正则无法编译: {text!r}（{exc}）") from None


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for v in value.values() for s in _strings(v)]
    if isinstance(value, list):
        return [s for v in value for s in _strings(v)]
    return []


def mentions_names(value: Any, names: list[str] | tuple[str, ...]) -> list[str]:
    """值里出现的本书人名。通用候选与通用经验不许带本书事实。"""
    text = "\n".join(_strings(value))
    return [name for name in names if len(name) >= 2 and name in text]


def _check_text(value: Any, label: str, low: int, high: int) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} 必须是文本")
    body = value.strip()
    if len(body) < low or len(body) > high:
        raise ValueError(f"{label} 长度须在 {low}-{high} 字")
    return body


def _check_when(when: Any) -> Any:
    clauses = when if isinstance(when, list) else [when]
    if not clauses:
        raise ValueError("when 不能为空")
    for clause in clauses:
        if not isinstance(clause, dict) or not clause:
            raise ValueError("when 每一项必须是非空映射")
        for key, value in clause.items():
            kind = SKILL_CONDITIONS.get(key)
            if kind is None:
                raise ValueError(f"未登记的技巧条件: {key}")
            if not isinstance(value, kind):
                raise ValueError(f"技巧条件 {key} 取值类型不对")
            if kind is list and not all(isinstance(item, str) and item for item in value):
                raise ValueError(f"技巧条件 {key} 须是非空词列表")
    return when


def _check_skill(skill_id: str, value: Any, base: dict[str, Any]) -> Any:
    if isinstance(value, bool):
        return value
    if not isinstance(value, dict):
        raise ValueError(f"技巧 {skill_id} 必须是映射或开关")
    clean: dict[str, Any] = {}
    for key, item in value.items():
        if key == "instruction":
            clean[key] = _check_text(item, f"技巧 {skill_id}.instruction", 4, INSTRUCTION_MAX_CHARS)
        elif key in ("label", "trigger"):
            clean[key] = _check_text(item, f"技巧 {skill_id}.{key}", 1, 60)
        elif key == "when":
            clean[key] = _check_when(item)
        elif key == "mode":
            if item not in SKILL_MODES:
                raise ValueError(f"技巧 {skill_id}.mode 只能是 {sorted(SKILL_MODES)}")
            clean[key] = item
        else:
            raise ValueError(f"技巧 {skill_id} 不认识字段 {key}")
    if skill_id not in base:
        if "instruction" not in clean:
            raise ValueError(f"新技巧 {skill_id} 缺 instruction")
        if "when" not in clean and clean.get("mode") != "always":
            raise ValueError(f"新技巧 {skill_id} 要么给 when，要么 mode 为 always")
    return clean


def _check_soft_check(check_id: str, value: Any, base: dict[str, Any]) -> Any:
    if isinstance(value, bool):
        return value
    if not isinstance(value, dict):
        raise ValueError(f"软检查 {check_id} 必须是映射或开关")
    clean: dict[str, Any] = {}
    for key, item in value.items():
        if key == "kind":
            if item not in SOFT_CHECK_KINDS:
                raise ValueError(f"软检查 {check_id}.kind 只能是 {sorted(SOFT_CHECK_KINDS)}")
            clean[key] = item
        elif key == "patterns":
            if not isinstance(item, list) or not item or len(item) > PATTERNS_PER_CHECK:
                raise ValueError(f"软检查 {check_id}.patterns 须是 1-{PATTERNS_PER_CHECK} 条")
            for pattern in item:
                safe_pattern(pattern)
            clean[key] = [str(p) for p in item]
        elif key in ("label", "message"):
            clean[key] = _check_text(item, f"软检查 {check_id}.{key}", 1, MESSAGE_MAX_CHARS)
        elif key == "severity":
            clean[key] = str(item)[:16]
        elif key == "enabled":
            clean[key] = bool(item)
        else:
            raise ValueError(f"软检查 {check_id} 不认识字段 {key}")
    kind = clean.get("kind") or (base.get(check_id) or {}).get("kind")
    if kind not in SOFT_CHECK_KINDS:
        raise ValueError(f"软检查 {check_id} 缺 kind")
    if kind == "pattern" and not (
        clean.get("patterns") or (base.get(check_id) or {}).get("patterns")
    ):
        raise ValueError(f"软检查 {check_id} 缺 patterns")
    return clean


def _check_hook(hook_id: str, value: Any, base: dict[str, Any]) -> Any:
    if isinstance(value, bool):
        return value
    if not isinstance(value, dict):
        raise ValueError(f"钩子 {hook_id} 必须是映射或开关")
    clean: dict[str, Any] = {}
    for key, item in value.items():
        if key == "condition":
            if item not in HOOK_CONDITIONS:
                raise ValueError(f"未登记的钩子条件: {item}")
            clean[key] = item
        elif key == "threshold":
            if isinstance(item, bool) or not isinstance(item, int) or not 1 <= item <= 50:
                raise ValueError(f"钩子 {hook_id}.threshold 须是 1-50 的整数")
            clean[key] = item
        elif key == "guidance":
            clean[key] = _check_text(item, f"钩子 {hook_id}.guidance", 4, GUIDANCE_MAX_CHARS)
        elif key == "label":
            clean[key] = _check_text(item, f"钩子 {hook_id}.label", 1, 60)
        elif key == "enabled":
            clean[key] = bool(item)
        else:
            raise ValueError(f"钩子 {hook_id} 不认识字段 {key}")
    if hook_id not in base and not ("condition" in clean and "guidance" in clean):
        raise ValueError(f"新钩子 {hook_id} 缺 condition 或 guidance")
    return clean


def validate_overrides(
    overrides: Any,
    base: ConfigTree,
    *,
    cast_names: list[str] | tuple[str, ...] = (),
    generic: bool = False,
) -> tuple[dict[str, Any], list[str]]:
    """只留下可编辑面里合法的修改，返回 (干净的覆盖层, 拒收理由)。"""
    if not overrides:
        return {}, []
    if not isinstance(overrides, dict):
        return {}, ["覆盖层必须是映射"]
    clean: dict[str, Any] = {}
    errors: list[str] = []
    for top, value in overrides.items():
        if top in FROZEN_KEYS:
            errors.append(f"{top} 是冻结面，不许改")
            continue
        if top not in EDITABLE_KEYS:
            errors.append(f"不认识的配置项: {top}")
            continue
        if generic and mentions_names(value, cast_names):
            errors.append(
                f"通用候选的 {top} 带了本书人名：{'、'.join(mentions_names(value, cast_names))}"
            )
            continue
        try:
            if top == "memory":
                clean[top] = _check_text(value, "memory", 1, MEMORY_MAX_CHARS)
                continue
            if not isinstance(value, dict):
                raise ValueError(f"{top} 必须是映射")
        except ValueError as exc:
            errors.append(str(exc))
            continue
        section: dict[str, Any] = {}
        existing = base.tree.get(top) or {}
        for key, item in value.items():
            try:
                if top == "prompts":
                    if key not in EDITABLE_PROMPTS:
                        raise ValueError(f"不许改提示词 {key}")
                    section[key] = _check_text(
                        item, f"提示词 {key}", PROMPT_MIN_CHARS, PROMPT_MAX_CHARS
                    )
                elif top == "knobs":
                    section[key] = validate_knob_value(key, item)
                else:
                    if not _ID.match(str(key)):
                        raise ValueError(f"{top} 的标识不合法: {key}")
                    checker = {
                        "skills": _check_skill,
                        "soft_checks": _check_soft_check,
                        "hooks": _check_hook,
                    }[top]
                    section[key] = checker(str(key), item, existing)
            except ValueError as exc:
                errors.append(str(exc))
        if section:
            clean[top] = section
    return clean, errors


def _normalize(overrides: dict[str, Any]) -> dict[str, Any]:
    """开关写法展开成映射：技巧 True/False → mode always/off；软检查、钩子 → enabled。"""
    out = deepcopy(overrides)
    for skill_id, value in list((out.get("skills") or {}).items()):
        if isinstance(value, bool):
            out["skills"][skill_id] = {"mode": "always" if value else "off"}
    for top in ("soft_checks", "hooks"):
        for item_id, value in list((out.get(top) or {}).items()):
            if isinstance(value, bool):
                out[top][item_id] = {"enabled": value}
    return out


def merge_overrides(lower: dict[str, Any] | None, upper: dict[str, Any] | None) -> dict[str, Any]:
    """两层覆盖层叠成一层，上层优先。"""
    merged = deepcopy(lower or {})
    _deep_merge(merged, _normalize(upper or {}))
    return _normalize(merged)


def apply_overrides(base: ConfigTree, overrides: dict[str, Any] | None) -> ConfigTree:
    """把覆盖层合并到基础配置上。非法修改丢弃，不进入配置。"""
    clean, _ = validate_overrides(overrides or {}, base)
    if not clean:
        return base
    merged = deepcopy(base.tree)
    _deep_merge(merged, _normalize(clean))
    return ConfigTree(merged, parent_id=base.content_hash)


def ends_mid_action(text: str) -> bool:
    body = (text or "").rstrip()
    return bool(body) and body[-1] not in _END_MARKS


def _clause_holds(clause: dict[str, Any], view: dict[str, Any]) -> bool:
    for key, value in clause.items():
        if key == "choice_any":
            if not any(word in str(view.get("choice") or "") for word in value):
                return False
        elif key == "result_any":
            if not any(word in str(view.get("result") or "") for word in value):
                return False
        elif key == "first_chapter":
            if (int(view.get("chapter_no") or 1) == 1) != value:
                return False
        elif bool(view.get(key)) != value:
            return False
    return True


def when_holds(when: Any, view: dict[str, Any]) -> bool:
    """映射是「同时成立」，列表是「任一成立」。"""
    if isinstance(when, list):
        return any(isinstance(c, dict) and _clause_holds(c, view) for c in when)
    if isinstance(when, dict):
        return _clause_holds(when, view)
    return False


def resolve_skills(config: ConfigTree, view: dict[str, Any]) -> str:
    """按本章视图选取写作技巧，返回追加到执笔提示的文本。"""
    parts: list[str] = []
    for spec in config.get_skills().values():
        instruction = str(spec.get("instruction") or "").strip()
        mode = spec.get("mode") or "auto"
        if not instruction or mode == "off":
            continue
        if mode == "always" or when_holds(spec.get("when"), view):
            parts.append(instruction)
    return "\n".join(parts)


def _fill(template: str, values: dict[str, Any]) -> str:
    text = template
    for key, value in values.items():
        text = text.replace("{" + key + "}", str(value))
    return text


def resolve_hooks(config: ConfigTree, view: dict[str, Any]) -> list[str]:
    """评估阶段钩子，返回满足条件的指引。占位符只填登记过的几个。"""
    guidance: list[str] = []
    for spec in config.get_hooks().values():
        text = str(spec.get("guidance") or "").strip()
        condition = str(spec.get("condition") or "")
        threshold = int(spec.get("threshold") or 1)
        if not text:
            continue
        if condition == "previous_ends_mid_action" and view.get("previous_ends_mid_action"):
            guidance.append(text)
        elif condition == "open_promise_age_exceeds":
            age = int(view.get("oldest_promise_age") or 0)
            if age > threshold:
                guidance.append(_fill(text, {"age": age}))
        elif condition == "method_rejected_at_least":
            rounds = int(view.get("same_method_rejects") or 0)
            if rounds >= threshold:
                guidance.append(_fill(text, {"rounds": rounds}))
    return guidance


def run_soft_checks(content: str, config: ConfigTree, *, previous_chapter: str = "") -> list[str]:
    """执行声明式软检查，返回提示。软检查只送改稿，不放行硬门未过的稿件。"""
    from regent.novel.application.directing_chapter import repeated_span
    from regent.novel.domain.prose_copy import is_legitimate_repetition, longest_common_substring

    text = content or ""
    issues: list[str] = []
    for spec in config.get_soft_checks().values():
        kind = str(spec.get("kind") or "")
        template = str(spec.get("message") or spec.get("label") or kind)
        if kind == "repetition":
            span = repeated_span(text, min_len=int(config.get_knob("soft_check_repetition_ngram")))
            if span:
                issues.append(_fill(template, {"matched_len": len(span)}))
        elif kind == "overlap":
            if not previous_chapter:
                continue
            min_len = int(config.get_knob("soft_check_chapter_opening_overlap"))
            span = longest_common_substring(
                text[:OPENING_CHARS], previous_chapter[-OPENING_CHARS * 2 :], min_len=min_len
            )
            if span and not is_legitimate_repetition(span):
                issues.append(_fill(template, {"matched_len": len(span)}))
        elif kind == "pattern":
            for pattern in spec.get("patterns") or []:
                try:
                    match = safe_pattern(pattern).search(text)
                except ValueError:
                    continue
                if match:
                    issues.append(_fill(template, {"matched": match.group()[:30]}))
                    break
    return issues


def _deep_diff(base: dict[str, Any], other: dict[str, Any]) -> dict[str, Any]:
    diff: dict[str, Any] = {}
    for key in set(base) | set(other):
        bv = base.get(key)
        ov = other.get(key)
        if bv == ov:
            continue
        if isinstance(bv, dict) and isinstance(ov, dict):
            sub = _deep_diff(bv, ov)
            if sub:
                diff[key] = sub
        else:
            diff[key] = {"old": bv, "new": ov}
    return diff


def _deep_merge(base: dict[str, Any], overrides: dict[str, Any]) -> None:
    for key, value in overrides.items():
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            _deep_merge(base[key], value)
        else:
            base[key] = deepcopy(value)


def flatten(tree: dict[str, Any], prefix: tuple[str, ...] = ()) -> dict[tuple[str, ...], Any]:
    """覆盖层拍平成 路径 → 叶值。"""
    out: dict[tuple[str, ...], Any] = {}
    for key, value in (tree or {}).items():
        path = (*prefix, str(key))
        if isinstance(value, dict) and value:
            out.update(flatten(value, path))
        else:
            out[path] = value
    return out

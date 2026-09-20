"""故事圣经：编剧在开写前锁定的世界、人物、戏剧引擎、读者契约与行文风格。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from regent.novel.domain.dossiers import (
    cast_as_persona_list,
    dual_dossier_gaps,
    needs_dual_dossiers,
)
from regent.novel.domain.principle_lenses import lenses_as_prompt_block, resolve_lenses
from regent.novel.domain.work_conventions import WorkConvention, conventions_as_rails


class WorldPersona(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    identity: str = Field(default="", description="一句话对外身份")
    kind: str = Field(
        default="",
        description="traveler | host_body | 空（普通角色）",
    )
    bio: str = Field(min_length=20, description="可演小传 ≥20 字")
    voice: str = Field(min_length=1, description="声纹")
    drives: str = Field(default="", description="核心欲望")


class DramaticEngine(BaseModel):
    """核心矛盾如何可执行地演下去——源自方向卡，由编剧落地。"""

    protagonist_want: str = Field(min_length=8, description="主角此刻要什么")
    opposing_force: str = Field(min_length=8, description="谁/什么在挡，如何挡")
    conflict_price: str = Field(
        min_length=8,
        description="推进或获胜要付出什么可感知代价",
    )
    escalation_logic: str = Field(
        min_length=8,
        description="矛盾如何升级，而不是原地重复吵架",
    )


class ReaderContract(BaseModel):
    """读者承诺：这部书要兑现什么、禁止写成什么。"""

    must_deliver: list[str] = Field(
        min_length=1,
        max_length=8,
        description="类型/情绪上必须兑现的点",
    )
    forbidden: list[str] = Field(
        default_factory=list,
        max_length=8,
        description="明确禁止的写法或结局姿态",
    )
    emotional_payoff: str = Field(
        min_length=8,
        description="读者主要情绪回报（爽/虐/甜/悬等）如何落地",
    )


class ProseStyle(BaseModel):
    """行文风格：执笔遵守，导演不得章章更换。"""

    viewpoint: str = Field(min_length=4, description="人称与视角，如『第一人称有限·林晚』")
    narrative_distance: str = Field(min_length=1, description="远/中/近或具体说明")
    tone: str = Field(min_length=4, description="语气气质，如克制锋利、轻快毒舌")
    dialogue_density: str = Field(
        min_length=4,
        description="对白与叙述比重偏好",
    )
    avoid: list[str] = Field(
        default_factory=list,
        max_length=8,
        description="禁用套路，如说明书式系统播报、总结腔",
    )


class WorldBible(BaseModel):
    """编剧故事圣经：开章前须锁定；导演/执笔只读引用。"""

    world_premise: str = Field(min_length=12, description="一句话故事世界前提")
    underlying_rules: list[str] = Field(
        min_length=1,
        max_length=12,
        description="底层可核对规则（物理/社会/穿越或外挂边界）",
    )
    background: str = Field(min_length=40, description="时代、行业、权力结构等背景")
    power_system: str = Field(
        default="",
        description="外挂/金手指机制；无外挂可空",
    )
    personas: list[WorldPersona] = Field(min_length=2, max_length=8)
    dramatic_engine: DramaticEngine = Field(
        description="核心矛盾的可执行戏剧引擎（源自方向卡）",
    )
    reader_contract: ReaderContract = Field(
        description="读者承诺与禁区",
    )
    prose_style: ProseStyle = Field(
        description="行文风格；执笔遵守",
    )
    conventions: list[WorkConvention] = Field(
        default_factory=list,
        max_length=12,
        description="本作公约（由原则透镜落地）",
    )
    open_questions: list[str] = Field(
        default_factory=list,
        max_length=6,
        description="仍可留给后文的开放问题；不得是底层规则空白",
    )


SCREENWRITER_SYSTEM = (
    "你是小说编剧，负责在开拍前写『故事圣经』，不是导演、不写章节正文。\n"
    "必须包含：世界设定、人物小传、dramatic_engine（戏剧引擎）、"
    "reader_contract（读者契约）、prose_style（行文风格）、必要时 power_system；"
    "conventions 只写已与戏剧引擎/读者契约/双小传对齐的最小集，拿不准就留空并写入 open_questions，"
    "禁止把单点偏好、器物 UX 细则、审校教训堆进共享公约。\n"
    "dramatic_engine 必须把方向卡里的欲望与阻力落成可执行条目："
    "主角要什么、谁挡、代价、升级逻辑；禁止只复述标签。\n"
    "reader_contract 写清必须兑现与明确禁止；prose_style 写清视角、距离、语气、对白比重与禁用套路。"
    "长篇网文默认第三人称有限（紧贴主角），除非用户明确要求第一人称。\n"
    "必须为本作发明具体规则、地名行业、人物姓名；禁止套用他书模板梗。\n"
    "若方向含穿越/换身/性转：personas 必须含 kind=traveler 与 kind=host_body，"
    "穿越者须有可读称呼（前世名或明确标签），不得与原身共用一套让读者分不清的写法；"
    "系统面板区分躯壳登记与当前操控意识时，必须让读者认出操控者即主角，"
    "禁止出现『操作员』及任何字母/数字代号；且不得把穿越者死亡/前职写进原身。\n"
    "若方向含系统/金手指：power_system 须写清资源池名、边界、"
    "绑定/觉醒后针对眼前困境的第一份可选项，以及首次弹出的一句接入提示；"
    "外挂是工具箱不是项圈。\n"
    "underlying_rules 写可核对底层规则，不要写文风口号（文风放在 prose_style）。\n"
)


def _coerce_bible(raw: WorldBible | dict[str, Any]) -> WorldBible:
    if isinstance(raw, WorldBible):
        return raw
    data = dict(raw or {})
    # 遗留草稿缺字段时，用方向痕迹补最小可校验结构（仍可能在 lock 时被拒）
    locked = {}
    if not data.get("dramatic_engine"):
        data["dramatic_engine"] = {
            "protagonist_want": "待编剧补全主角欲望",
            "opposing_force": "待编剧补全阻力来源",
            "conflict_price": "待编剧补全推进代价",
            "escalation_logic": "待编剧补全升级逻辑",
        }
    if not data.get("reader_contract"):
        data["reader_contract"] = {
            "must_deliver": ["待编剧补全读者须兑现的点"],
            "forbidden": [],
            "emotional_payoff": "待编剧补全情绪回报",
        }
    if not data.get("prose_style"):
        data["prose_style"] = {
            "viewpoint": "待定视角",
            "narrative_distance": "近",
            "tone": "待定语气",
            "dialogue_density": "对白与叙述均衡",
            "avoid": [],
        }
    del locked  # silence lint
    return WorldBible.model_validate(data)


def bible_validation_issues(
    bible: WorldBible | dict[str, Any],
    *,
    direction_keywords: list[str] | None = None,
) -> list[str]:
    """锁定前硬检查。"""
    try:
        bible = _coerce_bible(bible)
    except Exception as exc:  # noqa: BLE001
        return [f"故事圣经格式无效：{exc}"]
    issues: list[str] = []
    kw = list(direction_keywords or [])
    personas = [
        {
            "name": p.name,
            "identity": {"role": p.identity, "kind": p.kind, "bio": p.bio},
            "drives": {"primary": p.drives},
            "voice": {"style": p.voice},
        }
        for p in bible.personas
    ]
    if needs_dual_dossiers(kw):
        issues.extend(
            dual_dossier_gaps(cast_as_persona_list({p["name"]: p for p in personas}))
        )
    lenses = resolve_lenses(kw)
    if lenses and not bible.conventions:
        issues.append("已激活原则透镜但圣经缺少本作公约 conventions")
    if any(x.lens_id == "power_as_toolkit" for x in lenses) and not (
        bible.power_system or ""
    ).strip():
        issues.append("方向含外挂/系统，power_system 不能为空")

    # 戏剧/契约/文风不得仍是占位
    placeholders = ("待编剧", "待定", "TODO", "占位")
    eng = bible.dramatic_engine
    for label, text in (
        ("主角欲望", eng.protagonist_want),
        ("阻力", eng.opposing_force),
        ("代价", eng.conflict_price),
        ("升级逻辑", eng.escalation_logic),
    ):
        if any(p in text for p in placeholders) or len(text.strip()) < 8:
            issues.append(f"dramatic_engine.{label} 未写实，须由编剧落地可执行条目")
    rc = bible.reader_contract
    if not rc.must_deliver or any(
        any(p in x for p in placeholders) for x in rc.must_deliver
    ):
        issues.append("reader_contract.must_deliver 未写实")
    if any(p in rc.emotional_payoff for p in placeholders):
        issues.append("reader_contract.emotional_payoff 未写实")
    ps = bible.prose_style
    if any(p in ps.viewpoint for p in placeholders) or any(
        p in ps.tone for p in placeholders
    ):
        issues.append("prose_style 视角/语气未写实，执笔将无法统一文风")
    return issues


def bible_as_context(bible: WorldBible | dict[str, Any] | None) -> dict[str, Any]:
    """写入 generation_context 的精简正史。

    共享公约（bible.conventions）仅在非空时注入；空列表视为未统一锁定，
    不把「空壳」当成已定公约塞进 commons，以免单点草稿冒充四人编剧共识。
    """
    if not bible:
        return {}
    try:
        model = _coerce_bible(bible)
        data = model.model_dump(mode="json")
    except Exception:  # noqa: BLE001
        data = dict(bible) if isinstance(bible, dict) else {}
    conventions = [
        c
        for c in (data.get("conventions") or [])
        if isinstance(c, dict) and str(c.get("statement") or "").strip()
    ]
    out: dict[str, Any] = {
        "world_bible": data,
        "dramatic_engine": data.get("dramatic_engine") or {},
        "reader_contract": data.get("reader_contract") or {},
        "prose_style": data.get("prose_style") or {},
    }
    if conventions:
        out["work_conventions"] = {"conventions": conventions}
        out["commons_extra"] = conventions_as_rails({"conventions": conventions})
    else:
        out["work_conventions"] = {}
        out["commons_extra"] = []
    return out


def bible_director_block(bible: dict[str, Any] | None) -> str:
    """给导演/执笔的只读摘要。"""
    if not bible:
        return ""
    eng = bible.get("dramatic_engine") or {}
    rc = bible.get("reader_contract") or {}
    ps = bible.get("prose_style") or {}
    parts = ["【故事圣经·只读】"]
    if bible.get("world_premise"):
        parts.append(f"前提：{bible['world_premise']}")
    if eng:
        parts.append(
            "戏剧引擎："
            f"要={eng.get('protagonist_want','')}；"
            f"挡={eng.get('opposing_force','')}；"
            f"代价={eng.get('conflict_price','')}；"
            f"升级={eng.get('escalation_logic','')}"
        )
    if rc:
        must = "、".join(str(x) for x in (rc.get("must_deliver") or [])[:4])
        forbid = "、".join(str(x) for x in (rc.get("forbidden") or [])[:4])
        parts.append(
            f"读者契约：须兑现〔{must}〕；禁〔{forbid or '无'}〕；"
            f"情绪回报={rc.get('emotional_payoff','')}"
        )
    if ps:
        avoid = "、".join(str(x) for x in (ps.get("avoid") or [])[:4])
        parts.append(
            f"行文：视角={ps.get('viewpoint','')}；距离={ps.get('narrative_distance','')}；"
            f"语气={ps.get('tone','')}；对白={ps.get('dialogue_density','')}；"
            f"禁用={avoid or '无'}"
        )
    return "\n".join(parts)


def screenwriter_user_payload(
    *,
    raw_intent: str,
    genre: str,
    direction_keywords: list[str],
    locked_direction: dict[str, Any],
    revise_notes: str = "",
    previous_bible: dict[str, Any] | None = None,
) -> dict[str, Any]:
    lenses = resolve_lenses(direction_keywords)
    return {
        "raw_intent": raw_intent,
        "genre": genre,
        "direction_keywords": direction_keywords,
        "locked_direction": locked_direction,
        "principle_lenses_block": lenses_as_prompt_block(lenses),
        "principle_lenses": [
            {
                "lens_id": x.lens_id,
                "category": x.category,
                "when": x.when,
                "questions": list(x.questions),
                "anti_patterns": list(x.anti_patterns),
            }
            for x in lenses
        ],
        "direction_seed": {
            "protagonist_desire": (locked_direction or {}).get("protagonist_desire"),
            "core_conflict": (locked_direction or {}).get("core_conflict"),
            "genre_promise": (locked_direction or {}).get("genre_promise"),
            "pacing": (locked_direction or {}).get("pacing"),
            "differentiator": (locked_direction or {}).get("differentiator"),
        },
        "revise_notes": revise_notes,
        "previous_bible": previous_bible or {},
    }


def fallback_bible_from_direction(chosen: Any, *, raw_intent: str = "") -> dict[str, Any]:
    """无模型时的最小草稿；锁定前须重写写实。"""
    title = getattr(chosen, "title", "") or "故事"
    desire = getattr(chosen, "protagonist_desire", "") or "推进目标"
    conflict = getattr(chosen, "core_conflict", "") or "阻力"
    promise = getattr(chosen, "genre_promise", "") or desire
    pacing = getattr(chosen, "pacing", "") or "按情节推进"
    return {
        "world_premise": (title + "：" + (raw_intent or desire)[:60])[:120],
        "underlying_rules": [
            "以方向卡锁定的冲突与欲望为底层约束，细节由编剧后补",
        ],
        "background": (
            f"{promise}。节奏倾向：{pacing}。"
            "具体时代、行业与权力结构待编剧补全为可核对设定。"
        ),
        "power_system": "",
        "personas": [
            {
                "name": "主角",
                "identity": "待编剧命名",
                "kind": "",
                "bio": "根据方向卡推进的核心人物，小传待编剧补全具体前史与目标。",
                "voice": "待定",
                "drives": desire[:80],
            },
            {
                "name": "对手",
                "identity": "阻力方",
                "kind": "",
                "bio": f"体现核心阻力：{conflict[:60]}。具体人设待编剧补全。",
                "voice": "待定",
                "drives": "阻挠或考验主角",
            },
        ],
        "dramatic_engine": {
            "protagonist_want": desire[:200] or "待编剧补全主角欲望",
            "opposing_force": conflict[:200] or "待编剧补全阻力来源",
            "conflict_price": "待编剧补全推进代价",
            "escalation_logic": "待编剧补全升级逻辑",
        },
        "reader_contract": {
            "must_deliver": [promise[:120] if promise else "待编剧补全读者须兑现的点"],
            "forbidden": ["禁止写成与方向卡承诺无关的另一套故事"],
            "emotional_payoff": "待编剧补全情绪回报",
        },
        "prose_style": {
            "viewpoint": "待定视角",
            "narrative_distance": "近",
            "tone": "待定语气",
            "dialogue_density": "对白与叙述均衡",
            "avoid": ["说明书式系统播报", "总结腔复盘"],
        },
        "conventions": [],
        "open_questions": ["请用意见让编剧重写完整故事圣经"],
    }


def resolve_prose_style(context: dict[str, Any] | None) -> dict[str, Any]:
    """从 generation_context / 世界书取出行文契约（执笔与 VALIDATE 共用）。"""
    ctx = context or {}
    style = ctx.get("prose_style")
    if isinstance(style, dict) and any(str(style.get(k) or "").strip() for k in ("viewpoint", "narrative_distance")):
        return dict(style)
    bible = ctx.get("world_bible")
    if isinstance(bible, dict):
        nested = bible.get("prose_style")
        if isinstance(nested, dict) and nested:
            return dict(nested)
    return {}


def prose_style_writer_hint(prose_style: dict[str, Any] | None) -> str:
    """执笔 system 追加：把人称/距离钉死在写作前，不留给责编补救。"""
    ps = prose_style or {}
    if not ps:
        return ""
    avoid = "、".join(str(x) for x in (ps.get("avoid") or [])[:6]) or "无"
    return (
        "【行文契约·强制】"
        f"视角={ps.get('viewpoint') or '未指定'}；"
        f"距离={ps.get('narrative_distance') or '未指定'}；"
        f"语气={ps.get('tone') or '未指定'}；"
        f"对白={ps.get('dialogue_density') or '未指定'}；"
        f"禁用={avoid}。"
        "若视角写明宿主身体名与意识名：叙述主语与第三人称代词必须跟宿主壳"
        "（如沈栀用『她』），穿越者名只可作内心身份/回忆，不得改成以其为主的人称叙述。"
    )


def _style_blob(prose_style: dict[str, Any]) -> str:
    return (
        f"{prose_style.get('viewpoint') or ''}"
        f"{prose_style.get('narrative_distance') or ''}"
        f"{prose_style.get('tone') or ''}"
    )


def wants_she_host_narration(prose_style: dict[str, Any] | None) -> bool:
    """行文是否要求『她』/宿主壳第三人称（明显契约，应前置硬拦）。"""
    blob = _style_blob(prose_style or {})
    if not blob.strip():
        return False
    if "『她』" in blob or "用『她』" in blob or "叙述用『她』" in blob:
        return True
    if "第三人称" in blob and "她" in blob:
        return True
    return False


def pov_contract_hard_fails(
    content: str,
    prose_style: dict[str, Any] | None,
    *,
    min_chars: int = 600,
    cast: dict[str, Any] | list[str] | None = None,
    world_bible: dict[str, Any] | None = None,
) -> list[str]:
    """明显违背行文人称/宿主壳时返回 hard_fails（在 VALIDATE，不进责编）。

    宿主/穿越者名从作品 personas / 行文契约解析，不写死样本书人名。
    """
    from regent.novel.domain.dossiers import resolve_narration_roles

    text = (content or "").strip()
    ps = prose_style or {}
    if len(text) < min_chars or not ps:
        return []
    fails: list[str] = []
    she_n = text.count("她")
    he_n = text.count("他")
    total = she_n + he_n
    if wants_she_host_narration(ps) and total >= 30:
        ratio = she_n / total
        if ratio < 0.2:
            fails.append(
                "[pov:pronoun] 行文契约要求第三人称『她』/宿主壳叙述，"
                f"但正文『她』{she_n}『他』{he_n}（她占比{ratio:.0%}），"
                "明显写成了另一套人称；须在执笔/核验阶段改回，不得交责编。"
            )
    roles = resolve_narration_roles(
        prose_style=ps, cast=cast, world_bible=world_bible
    )
    host = str(roles.get("host_name") or "")
    traveler = str(roles.get("traveler_name") or "")
    if host and traveler and host != traveler:
        hc = text.count(host)
        tc = text.count(traveler)
        if tc >= 8 and tc > hc * 2:
            fails.append(
                f"[pov:host_name] 行文契约以宿主『{host}』为叙述壳，"
                f"但正文『{traveler}』{tc}次压过『{host}』{hc}次；"
                "穿越者名不得替代宿主成为叙述主语主名。"
            )
    return fails

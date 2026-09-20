"""Chapter planning services."""

# ruff: noqa: RUF001
from __future__ import annotations

import json
import re
import uuid
from copy import deepcopy
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from regent.model import ModelProvider
from regent.novel.application.directing_calls import (
    _call,
    _input_version,
    _save,
)
from regent.novel.application.directing_cast import (
    MAX_NEW_PERSONAS_PER_CHAPTER,
    _brief_issues,
    _declared_cast,
    _register_new_personas,
)
from regent.novel.application.directing_contracts import (
    ChapterDirection,
    DualDossierBundle,
)
from regent.novel.application.directing_protocol import (
    PROTOCOL_BEAT,
    production_protocol,
)
from regent.novel.application.directing_types import (
    PROTOCOL_SCRIPT,
    PROTOCOL_SCRIPT_SCENE,
)
from regent.novel.application.runtime import (
    NO_SCENE,
    RuntimeState,
)
from regent.novel.domain.commands import CommandKind
from regent.novel.domain.commands import command as director_command
from regent.novel.domain.errors import (
    ProductionStopped,
)
from regent.novel.domain.memory import project_payloads as _memory_view
from regent.novel.domain.script_protocol import (
    empty_script_state,
)
from regent.novel.infrastructure.models import ChapterRunModel, PersonaSpecModel, StoryWorkModel

MAX_PLAN_REPAIRS = 2

from regent.novel.application.directing_runtime import _RUNTIME, _new_take


def _plan_story_rails_issues(
    direction: ChapterDirection,
    *,
    chapter_no: int,
    context: dict[str, Any],
) -> list[str]:
    """有锁定方向/关键词时的硬检查：标题锚点、身份、真正回避机制的话术。

    注意：``disclosure_rule`` 里的「本场不解释穿越全貌/系统身世」是限知叙事，
    不是「无法解释的库」式回避；不得把限知当成硬失败。
    """
    locked = context.get("locked_direction") or {}
    kw = [str(x) for x in (context.get("direction_keywords") or [])]
    clarify = list(context.get("clarify_rails") or [])
    commons = list(context.get("commons_rails") or [])
    # 无用户方向正史时不启用（避免干扰无方向上下文的旧单测链路）
    if not locked and not kw and not clarify and not commons:
        return []

    issues: list[str] = []
    title = str(direction.title or "")
    target = context.get("target_node") or {}
    target_title = str(target.get("title") or "")
    if "开播前夜" in target_title and "重生前夜" in title and "开播" not in title:
        issues.append(
            f"章题『{title}』与节点『{target_title}』冲突：读者会读成尚未重生，"
            "应改为含开播/录制时间锚点的标题"
        )
    # 章题里的「X穿越/重生…」人名必须落在卡司（或本轮 new_personas）
    cast_ctx = context.get("cast") if isinstance(context.get("cast"), dict) else {}
    cast_bases: set[str] = set()
    for key in cast_ctx:
        base = str(key).split("（")[0].strip()
        if base:
            cast_bases.add(base)
    for spec in direction.new_personas:
        base = str(getattr(spec, "name", "") or "").split("（")[0].strip()
        if base:
            cast_bases.add(base)
    if cast_bases:
        for m in re.finditer(
            r"([\u4e00-\u9fff]{2,4})(?:穿越|重生|变成|成为|变身|醒来|初入|登场|苏醒)",
            title,
        ):
            name = m.group(1)
            if name not in cast_bases and not any(name in b or b in name for b in cast_bases):
                sample = "、".join(sorted(cast_bases)[:8])
                issues.append(
                    f"章题人名『{name}』不在本册卡司（可用：{sample}），"
                    "禁止引入未登记主角名；改用在册名或先声明 new_personas"
                )
    blob = title + "｜" + str(direction.reader_intent or "")
    for scene in direction.scenes:
        blob += "｜" + str(scene.purpose or "")
        narr = scene.narrative
        rule = str(getattr(narr, "disclosure_rule", "") or "")
        blob += "｜" + rule
        # 只拦真正的机制回避；限知「本场不揭穿越大幕」放行
        if any(
            bad in rule
            for bad in (
                "无法解释",
                "不去想它从哪来",
                "不去想从哪来",
                "不解释文娱库",
                "神秘来源不必交代",
            )
        ):
            issues.append(
                f"场景 disclosure_rule 含机制回避话术（{rule[:40]}），"
                "须改为可核对的机制一句；限知披露可以保留"
            )
    if any(x in blob for x in ("无法解释的", "不去想它从哪来", "不去想从哪来")):
        issues.append("计划含『无法解释/不去想来源』话术，须改为可核对的机制一句")

    # 行文风格若明确外卖/配送声口，禁止计划用 disclosure 把前世职业自述一刀切掉
    style = context.get("prose_style") or {}
    style_blob = (
        json.dumps(style, ensure_ascii=False) if isinstance(style, dict) else str(style or "")
    )
    bible = context.get("world_bible") or {}
    if isinstance(bible, dict):
        style_blob += json.dumps(bible.get("prose_style") or {}, ensure_ascii=False)
    if any(t in style_blob for t in ("外卖", "配送", "跑单", "超时")):
        for scene in direction.scenes:
            rule = str(getattr(getattr(scene, "narrative", None), "disclosure_rule", "") or "")
            if any(
                bad in rule
                for bad in (
                    "前世职业",
                    "不得出现任何指向具体前世",
                    "不得提及外卖",
                    "禁止外卖",
                    "不得用任何指向具体前世职业",
                )
            ):
                issues.append(
                    "prose_style/世界书行文含外卖声口，场景 disclosure_rule 不得禁止前世职业/"
                    "外卖自述；限知可限制剧透，不可阉割声口"
                )
                break

    if int(chapter_no) == 1:
        identity_hints = (
            "身份",
            "职业",
            "演员",
            "明星",
            "嘉宾",
            "出道",
            "性转",
            "女身",
            "男身",
            "身体",
            "穿越",
            "原身",
        )
        plan_text = (
            blob
            + "｜"
            + "｜".join(f"{a.persona}:{a.role}" for s in direction.scenes for a in s.actors)
        )
        if not any(h in plan_text for h in identity_hints):
            issues.append("第一章计划未亮明主角此刻身份/处境，读者会对『谁』发懵")
        if "性转" in kw and not any(
            h in plan_text for h in ("性转", "女身", "男身", "身体", "性别")
        ):
            issues.append("方向含『性转』，第一章计划须落地可感知的身体/性别处境")
        desire = str(locked.get("protagonist_desire") or "")
        if any(t in desire for t in ("记忆", "文抄", "系统", "金手指")) and not any(
            t in blob for t in ("记忆", "文抄", "系统", "金手指", "抽取", "技能", "情报", "库")
        ):
            issues.append("锁定方向含金手指/记忆承诺，计划须写明本章如何兑现机制，不得省略")

    # 双小传：结构缺口（由 Agent 补齐后仍缺则拦）
    from regent.novel.domain.dossiers import (
        cast_as_persona_list,
        dual_dossier_gaps,
        needs_dual_dossiers,
    )

    if needs_dual_dossiers(kw):
        cast_ctx = context.get("cast") if isinstance(context.get("cast"), dict) else None
        if cast_ctx:
            for gap in dual_dossier_gaps(cast_as_persona_list(cast_ctx)):
                issues.append(f"dossier:{gap}")

    # 已激活原则透镜却无本作公约：应已由编剧世界书覆盖
    lenses = list(context.get("principle_lenses") or [])
    work_conv = context.get("work_conventions") or {}
    has_bible = bool(context.get("world_bible"))
    if (
        lenses
        and not has_bible
        and not (work_conv.get("conventions") if isinstance(work_conv, dict) else None)
        and not any(str(r.get("kind") or "") == "work_convention" for r in commons)
    ):
        issues.append("已激活原则透镜但缺少本作公约：须先由编剧锁定世界书")

    # 仅机械器物 UX（IM）做计划级启发式；叙事公约交给本作公约 + 正文 LLM 审校
    rule_ids = {str(r.get("rule_id") or "") for r in commons}
    for scene in direction.scenes:
        setting = str(getattr(scene, "setting", "") or "")
        purpose = str(getattr(scene, "purpose", "") or "")
        scene_text = setting + purpose + str(getattr(scene, "conflict", "") or "")
        if (
            "im_contact_density" in rule_ids
            and any(t in scene_text for t in ("微信", "通讯录", "聊天", "置顶", "名单"))
            and re.search(r"(只有|仅有|一共|就这)[一二三四五六七八两1-8]个", scene_text)
            and not any(s in scene_text for s in ("还有", "一长串", "其余", "往下", "划"))
        ):
            issues.append(
                "commons:im_contact_density：IM/名单场景写成个位数全貌，"
                "须改为可聚焦头部并暗示其余列表仍在"
            )

    # 去重保序
    seen_issues: set[str] = set()
    uniq: list[str] = []
    for item in issues:
        if item not in seen_issues:
            seen_issues.add(item)
            uniq.append(item)
    return uniq


async def _ensure_dual_dossiers(
    session: AsyncSession,
    *,
    provider: ModelProvider,
    work: StoryWorkModel,
    run: ChapterRunModel,
    production: dict[str, Any],
    cast: dict[str, Any],
) -> dict[str, Any]:
    """穿越/性转/系统作：若缺穿越者与原身小传，由导演补齐并入库。"""
    from regent.novel.domain.dossiers import (
        HOST_BODY_KIND,
        TRAVELER_KIND,
        cast_as_persona_list,
        dual_dossier_gaps,
        needs_dual_dossiers,
        persona_bio,
        persona_kind,
    )

    kw = list(run.generation_context.get("direction_keywords") or [])
    if not needs_dual_dossiers(kw):
        return cast
    gaps = dual_dossier_gaps(cast_as_persona_list(cast))
    if not gaps:
        return cast
    bundle = await _call(
        session,
        provider,
        work,
        run,
        production,
        DualDossierBundle,
        "你是小说导演，为本作补齐『穿越者意识』与『原身躯壳』两份小传并自审。"
        "traveler=穿越者前史（职业/死因或契机/声纹/目标，具体内容从 premise 发明）；"
        "host_body=原身对外身份（资源、为何能独自活动、过人之处或金丝笼风险）。"
        "禁止把穿越者的死亡/前职写进原身；宿主对外姓名用原身名。"
        "小传须具体可演，供后文角色 Agent 使用；勿套用他书模板人名。",
        {
            "direction_keywords": kw,
            "locked_direction": run.generation_context.get("locked_direction") or {},
            "raw_intent": run.generation_context.get("raw_intent") or "",
            "existing_cast": cast,
            "gaps": gaps,
        },
        "dual_dossier",
        f"v{_input_version(run)}:dual_dossier",
    )
    merged = dict(cast)
    traveler_key = bundle.traveler_name.strip()
    host_key = bundle.host_name.strip()
    if traveler_key == host_key:
        traveler_key = f"{host_key}·意识"

    existing_rows = {
        p.name: p
        for p in (
            await session.scalars(
                select(PersonaSpecModel).where(PersonaSpecModel.work_id == work.id)
            )
        ).all()
    }

    def _upsert(
        key: str,
        kind: str,
        bio: str,
        voice: str,
        drives: str,
    ) -> None:
        row_meta = merged.get(key)
        if row_meta is not None:
            current_kind = persona_kind({"name": key, **row_meta})
            # 同名已是另一类：不覆盖，跳过（改用旁侧键）
            if current_kind and current_kind != kind:
                return
            ident = dict(row_meta.get("identity") or {})
            if len(persona_bio({"name": key, **row_meta})) < 20 or not current_kind:
                ident.update(
                    {
                        "kind": kind,
                        "bio": bio,
                        "role": ident.get("role") or bio[:80],
                    }
                )
                row_meta["identity"] = ident
                if not (row_meta.get("voice") or {}).get("style"):
                    row_meta["voice"] = {"style": voice}
                if not (row_meta.get("drives") or {}).get("primary"):
                    row_meta["drives"] = {"primary": drives}
                db_row = existing_rows.get(key)
                if db_row is not None:
                    db_row.identity = ident
                    db_row.voice = row_meta.get("voice") or db_row.voice
                    db_row.drives = row_meta.get("drives") or db_row.drives
            return
        entry = {
            "identity": {"role": bio[:80], "kind": kind, "bio": bio},
            "drives": {"primary": drives},
            "voice": {"style": voice},
        }
        merged[key] = entry
        session.add(
            PersonaSpecModel(
                id=uuid.uuid4(),
                work_id=work.id,
                name=key,
                stable_traits=[drives, voice],
                **entry,
            )
        )

    _upsert(
        traveler_key,
        TRAVELER_KIND,
        bundle.traveler_bio,
        bundle.traveler_voice,
        "活下去并掌控新处境",
    )
    _upsert(
        host_key,
        HOST_BODY_KIND,
        bundle.host_bio,
        bundle.host_voice,
        "维持对外人设与资源",
    )
    # 若宿主名已被占用且无法标 kind，补一条旁侧原身档案
    if dual_dossier_gaps(cast_as_persona_list(merged)):
        if HOST_BODY_KIND not in {persona_kind({"name": n, **m}) for n, m in merged.items()}:
            alt = f"{host_key}·原身"
            _upsert(alt, HOST_BODY_KIND, bundle.host_bio, bundle.host_voice, "维持对外人设")
            host_key = alt
        if TRAVELER_KIND not in {persona_kind({"name": n, **m}) for n, m in merged.items()}:
            alt = f"{traveler_key}·穿越者" if "·" not in traveler_key else traveler_key
            _upsert(
                alt,
                TRAVELER_KIND,
                bundle.traveler_bio,
                bundle.traveler_voice,
                "活下去并掌控新处境",
            )
            traveler_key = alt

    await session.flush()
    run.generation_context = {
        **run.generation_context,
        "dual_dossiers": {
            "traveler": traveler_key,
            "host_body": host_key,
        },
    }
    return merged


async def _ensure_work_conventions(
    session: AsyncSession,
    *,
    provider: ModelProvider,
    work: StoryWorkModel,
    run: ChapterRunModel,
    production: dict[str, Any],
    cast: dict[str, Any],
) -> None:
    """按原则透镜为本作推导公约，写入 context.commons_rails（无确定性 check）。"""
    from regent.novel.domain.genre_packs import commons_rails_for_keywords
    from regent.novel.domain.principle_lenses import (
        lenses_as_prompt_block,
        lenses_payload,
        resolve_lenses,
    )
    from regent.novel.domain.work_conventions import (
        SYNTHESIZE_SYSTEM,
        WorkConventionBundle,
        conventions_as_rails,
    )

    kw = list(run.generation_context.get("direction_keywords") or [])
    lenses = resolve_lenses(kw)
    ctx = dict(run.generation_context or {})
    ctx["principle_lenses"] = lenses_payload(lenses)

    bible = dict(getattr(work, "story_bible", None) or {})
    # 用户显式清空共享约束后，不得由单点 Agent 再自动堆回公约。
    if bible.get("shared_constraints_cleared") is True:
        mechanical = commons_rails_for_keywords(kw)
        ctx["work_conventions"] = {"conventions": []}
        ctx["commons_rails"] = mechanical
        run.generation_context = ctx
        return

    existing = ctx.get("work_conventions")
    if isinstance(existing, dict) and existing.get("conventions"):
        mechanical = commons_rails_for_keywords(kw)
        ctx["commons_rails"] = mechanical + conventions_as_rails(existing)
        run.generation_context = ctx
        return
    if not lenses:
        ctx["commons_rails"] = commons_rails_for_keywords(kw)
        run.generation_context = ctx
        return

    bundle = await _call(
        session,
        provider,
        work,
        run,
        production,
        WorkConventionBundle,
        SYNTHESIZE_SYSTEM + "\n\n" + lenses_as_prompt_block(lenses),
        {
            "direction_keywords": kw,
            "locked_direction": ctx.get("locked_direction") or {},
            "raw_intent": ctx.get("raw_intent") or "",
            "clarify_rails": ctx.get("clarify_rails") or [],
            "cast": cast,
            "dual_dossiers": ctx.get("dual_dossiers") or {},
            "principle_lenses": lenses_payload(lenses),
        },
        "work_conventions",
        f"v{_input_version(run)}:work_conventions",
    )
    dumped = bundle.model_dump(mode="json")
    mechanical = commons_rails_for_keywords(kw)
    ctx["work_conventions"] = dumped
    ctx["commons_rails"] = mechanical + conventions_as_rails(dumped)
    run.generation_context = ctx


async def plan_chapter(
    session: AsyncSession,
    *,
    provider: ModelProvider,
    work: StoryWorkModel,
    run: ChapterRunModel,
) -> None:
    production = deepcopy(run.generation_context.get("production", {}))
    if production.get("plan"):
        return
    personas = (
        await session.scalars(
            select(PersonaSpecModel)
            .where(
                PersonaSpecModel.work_id == work.id,
            )
            .order_by(PersonaSpecModel.name)
        )
    ).all()
    cast = {
        p.name: {"identity": p.identity, "drives": p.drives, "voice": p.voice} for p in personas
    }
    if not cast:
        raise ProductionStopped("没有可供导演调度的角色")
    # 世界书已锁定时只读引用；仅遗留章无世界书时才临时补齐
    if not (run.generation_context or {}).get("world_bible"):
        cast = await _ensure_dual_dossiers(
            session,
            provider=provider,
            work=work,
            run=run,
            production=production,
            cast=cast,
        )
        await _ensure_work_conventions(
            session,
            provider=provider,
            work=work,
            run=run,
            production=production,
            cast=cast,
        )
    else:
        # 仍注入透镜块供导演 prompt；公约已在 ASSEMBLE
        from regent.novel.domain.principle_lenses import lenses_payload, resolve_lenses

        ctx = dict(run.generation_context or {})
        if not ctx.get("principle_lenses"):
            ctx["principle_lenses"] = lenses_payload(
                resolve_lenses(ctx.get("direction_keywords") or [])
            )
            run.generation_context = ctx
    # 规划也是一条命令：没有场景时只允许 PLAN_SCENE（P1-1）
    _RUNTIME.validate(
        director_command(
            CommandKind.PLAN_SCENE,
            command_id=f"v{_input_version(run)}:plan",
            input_version=_input_version(run),
            scene_index=int(production.get("scene_index", 0) or 0),
            take_no=1,
            payload={"cast": sorted(cast)},
        ),
        RuntimeState(scene_state=NO_SCENE, input_version=_input_version(run)),
    )
    if production_protocol(run) in {PROTOCOL_SCRIPT, PROTOCOL_SCRIPT_SCENE}:
        await _plan_script_chapter(run, cast=cast)
        return
    from regent.novel.domain.principle_lenses import lenses_as_prompt_block, resolve_lenses
    from regent.novel.domain.world_bible import bible_director_block

    lens_block = lenses_as_prompt_block(
        resolve_lenses(run.generation_context.get("direction_keywords") or [])
    )
    bible_block = run.generation_context.get("story_bible_block") or bible_director_block(
        run.generation_context.get("world_bible")
        if isinstance(run.generation_context.get("world_bible"), dict)
        else None
    )
    system_prompt = (
        "你是持续负责小说创作的导演。先设计本章阅读体验，再安排1至4个必要场景。"
        "每场明确人物欲望冲突、表演指导、视角、信息差和结束理由。允许舒缓和关系戏，"
        "不要机械升级冲突或套用三章结构。人物合理的选择可以改变未锁定计划。"
        "只使用给定角色；角色指令不泄露本人未知秘密，不预写结果和台词。"
        "不擅自决定用户锁定的重大节点。正文总目标1800至2500字。"
        "scenes[].actors[].persona必须是给定角色表里的确切名字，一字不差，"
        "不得添加括号、头衔、别称或本场说明；"
        "人物在本场的作用写在actors[].role。"
        "现有角色撑不起这场戏时，可以在new_personas里声明新人物并写清声纹voice；"
        f"一章最多新增{MAX_NEW_PERSONAS_PER_CHAPTER}个。"
        "persona要么是既有角色的确切名字，要么是本次在new_personas里声明的名字。"
        "硬约束（违反必须改计划）："
        "①章题时间锚点必须与 target_node/volume 一致，禁止歧义标题；"
        "章题若含人名，必须是 cast/new_personas 在册名，禁止未登记主角名；"
        "②第一章必须让读者核对主角此刻身份/身体处境；"
        "③金手指/外挂须写清可核对机制；disclosure_rule 可限知，不得『无法解释来源』；"
        "首章或首次登场外挂时，至少一场的 purpose/exit 必须包含"
        "『让读者听懂：能做什么、不能做什么、用一次付什么代价』，"
        "禁止只写『触发界面/完成抽取』而无读者向释义；"
        "本作发明术语（资源池名、鱼塘类燃料、数值标签等）须在计划里安排释义节拍；"
        "④优先遵守 locked_direction、clarify_rails、world_bible（故事圣经）与 work_conventions；"
        "戏剧引擎/读者契约/行文风格已锁定时不得另起互相矛盾的冲突写法或文风；"
        "⑤commons_rails 中机械器物规则与本作公约须遵守；"
        "⑥穿越/换身作须区分穿越者与原身小传，系统登记不得混串两套身份；"
        "系统面板指代当前操控意识时必须用读者已认识的称呼（前世名/穿越者名），"
        "禁止出现『操作员』『操作员A』『用户001』等代号；"
        "外挂首次弹出须有一句接入/绑定提示（点名绑定谁、叫什么），禁止无声刷屏。"
        + (f"\n{bible_block}" if bible_block else "")
        + (f"\n{lens_block}" if lens_block else "")
    )
    plan_command = f"v{_input_version(run)}:plan"
    repair: list[str] = []
    for repair_no in range(MAX_PLAN_REPAIRS + 1):
        payload: dict[str, Any] = {
            # D-03：导演计划请求拿**导演视图**的长期记忆（含未兑现承诺与导演
            # 笔记），不再把召回的全量 memory 原样塞进请求——那是 C-03 投影
            # 纪律，六步旧流程早已如此，director_v2 不得成为例外。
            "context": {
                k: v for k, v in run.generation_context.items() if k not in ("production", "memory")
            },
            "cast": cast,
            "user_guidance": run.user_guidance or {},
            "director_memory": _memory_view(run.generation_context.get("memory", []), "director"),
        }
        if repair:
            payload["repair_instructions"] = repair
        result = await _call(
            session,
            provider,
            work,
            run,
            production,
            ChapterDirection,
            system_prompt,
            payload,
            "plan",
            # 自修是**新的逻辑调用**：沿用原 command_id 会被幂等键挡住（或更糟，
            # 复用上一次的坏结果），所以带序号另起一条。
            plan_command if not repair_no else f"{plan_command}:r{repair_no}",
        )
        # 先并入导演声明的新人物再校验：声明过的新名字是合法引用。
        candidate, declare_issues = _declared_cast(result, cast)
        issues: list[str] = list(declare_issues)
        for brief in result.scenes:
            issues += _brief_issues(brief, candidate)
        issues += _plan_story_rails_issues(
            result,
            chapter_no=int(run.chapter_no),
            context={**(run.generation_context or {}), "cast": candidate},
        )
        if not issues:
            break
        # 反馈必须具体：说清哪个名字不能用、能用的是哪些，而不是再说一遍规则。
        repair = [
            "上一版规划未通过：" + "；".join(issues) + "。",
            "既有角色的确切名字是：" + "、".join(sorted(cast)) + "。"
            "actors[].persona 必须一字不差地取用既有名字，或取本次在 new_personas "
            "里声明过的名字；不得添括号、头衔或本场说明，本场作用写在 actors[].role。"
            f"需要新人物就在 new_personas 里声明并写清声纹，一章最多新增"
            f"{MAX_NEW_PERSONAS_PER_CHAPTER} 个。"
            "若问题涉及章题/身份/金手指：按 locked_direction、clarify_rails、"
            "direction_keywords 与 target_node 改到可核对，禁止『无法解释』回避。",
        ]
    else:
        raise ProductionStopped("导演计划自修次数已用尽：" + "；".join(issues))
    cast = await _register_new_personas(session, work, cast, result)
    protocol = production_protocol(run)
    production.update(
        {
            "schema_version": 1,
            "protocol": protocol,
            "plan": result.model_dump(mode="json"),
            "cast": cast,
            "scene_index": 0,
            "phase": "ACT" if protocol == PROTOCOL_BEAT else "SCENE",
            "takes": [],
            "accepted": [],
            "decisions": [],
            "working_state": run.generation_context.get("actual_state", {}),
        }
    )
    _new_take(production, result.scenes[0].model_dump(mode="json"))
    run.title = result.title
    _save(run, production)


async def _plan_script_chapter(run: ChapterRunModel, *, cast: dict[str, Any]) -> None:
    """剧本择优臂：只初始化草稿容器，不预写结果、不登记正式新人。"""
    proto = production_protocol(run)
    production = {
        "schema_version": 1,
        "protocol": proto,
        "plan": {
            "title": "",
            "reader_intent": (
                "剧本择优逐场演绎" if proto == PROTOCOL_SCRIPT_SCENE else "剧本择优整章"
            ),
            "ending_reason": "",
            "scenes": [
                {
                    "purpose": "script_chapter",
                    "setting": "",
                    "conflict": "",
                    "exit_condition": "",
                    "actors": [
                        {
                            "persona": name,
                            "objective": "",
                            "instruction": "按选定剧本演绎",
                            "role": "",
                        }
                        for name in sorted(cast)
                    ],
                    "narrative": {
                        "viewpoint": next(iter(cast)),
                        "distance": "近",
                        "style": "",
                        "reader_effect": "",
                        "disclosure_rule": "不泄露未知秘密",
                    },
                }
            ],
        },
        "cast": cast,
        "scene_index": 0,
        "phase": "BRIEF",
        "takes": [],
        "accepted": [],
        "decisions": [],
        "working_state": deepcopy(run.generation_context.get("actual_state", {}) or {}),
        "script_protocol": empty_script_state(),
    }
    run.title = run.title or f"第{run.chapter_no}章"
    _save(run, production)

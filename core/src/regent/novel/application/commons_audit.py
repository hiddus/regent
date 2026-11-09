"""独立常识审校：确定性规则 + 触发式 LLM 审核者。

只出可定位 hard/soft issues，不打综合分；失败并入 VALIDATE/REVIEW 硬失败通道。
"""

from __future__ import annotations

import re
from typing import Any, Sequence

from pydantic import BaseModel, Field

from regent.novel.domain.genre_packs import active_rule_ids

# 触发词：命中才值得跑机械检查 / LLM（不含某书专属梗）
_TRIGGER_SCAN = re.compile(
    r"微信|置顶|未读|已读|头像|聊天列表|通讯录|会话|手机|系统|宿主|绑定|抽取|抽奖|技能"
)

# IM 具名/备注：只认「备注是X」或紧贴名单语境的短称呼；不扫全文对话引号
_NAMED_CONTACT = re.compile(
    r"备注[是为]?[「\"“]?([\u4e00-\u9fffA-Za-z0-9_]{1,12})"
)
_LIST_QUOTED_NAME = re.compile(
    r"(?:置顶|聊天框|会话列表|通讯录|点赞列表|备注)[^。！？\n]{0,24}"
    r"[「\"“]([\u4e00-\u9fffA-Za-z0-9_]{1,8})[」\"”]"
)


def _looks_like_contact_label(name: str) -> bool:
    """过滤对话句、省略号残片等非备注名。"""
    n = (name or "").strip()
    if not n or len(n) > 8:
        return False
    if re.search(r"[。！？…，、：；\s·]", n):
        return False
    if n.startswith("…") or n.endswith("…"):
        return False
    # 「的号码」「好呀」等不是备注名
    if n.startswith(("的", "了", "在", "和", "与", "把", "被")):
        return False
    if n in {"好呀", "好的", "嗯", "啊", "哦", "号码", "未读", "置顶"}:
        return False
    return True


def _named_contacts(text: str) -> list[str]:
    names: list[str] = []
    for match in _NAMED_CONTACT.finditer(text or ""):
        name = (match.group(1) or "").strip()
        if _looks_like_contact_label(name) and name not in names:
            names.append(name)
    for match in _LIST_QUOTED_NAME.finditer(text or ""):
        name = (match.group(1) or "").strip()
        if _looks_like_contact_label(name) and name not in names:
            names.append(name)
    return names


_REMAINDER_SIGNALS = (
    "还有",
    "一长串",
    "划了很久",
    "划了上",
    "几百",
    "上百",
    "下面还",
    "往下翻",
    "翻了半天",
    "名单很长",
    "刷不完",
    "滑了很久",
    "不计其数",
    "密密麻麻",
    "折叠",
    "未读会话",
    "角标叠",
)

_SCALE_SIGNALS = (
    "上百",
    "几百",
    "上千",
    "一长串",
    "划了很久",
    "密密麻麻",
    "远超",
    "异常",
    "多到",
    "刷不完",
    "上百个",
    "几百个",
)

_KILL_LANDING = re.compile(
    r"(弄死|处决|杀死|电击(?:死|毙)|宿主死亡|直接死亡|当场死亡|"
    r"系统把.{0,6}弄死|惩罚落地.{0,8}死)"
)

_REBIRTH_BUFFER = ("重生", "假死", "复活", "读档", "时间回溯", "未真正死去")


class CommonsIssue(BaseModel):
    rule_id: str = Field(min_length=1)
    quote: str = ""
    reason: str = Field(min_length=1)
    hard: bool = True


class CommonsAuditResult(BaseModel):
    passed: bool
    issues: list[CommonsIssue] = Field(default_factory=list)


def scan_triggers(text: str) -> list[str]:
    return sorted(set(_TRIGGER_SCAN.findall(text or "")))


def _has_remainder(text: str) -> bool:
    return any(sig in (text or "") for sig in _REMAINDER_SIGNALS)


def _has_scale(text: str) -> bool:
    return any(sig in (text or "") for sig in _SCALE_SIGNALS)


def _format_issue(rule_id: str, reason: str, quote: str = "", *, hard: bool = True) -> str:
    prefix = f"[commons:{rule_id}] {reason}"
    if quote.strip():
        return f"{prefix}｜摘录「{quote.strip()[:40]}」"
    return prefix


def check_im_contact_density(text: str) -> list[str]:
    """全貌式个位数联系人且无余量信号 → hard。"""
    if not any(t in text for t in ("微信", "置顶", "聊天", "未读", "通讯录", "会话", "名单", "列表")):
        return []
    # 「一共/只有 N 个」且 N 很小
    small_total = re.search(
        r"(?:一共|总共|只有|仅有|就这)([二三四五六七八两]|[1-8])个",
        text,
    )
    names = _named_contacts(text)
    if small_total and not _has_remainder(text):
        # 「就这四个」常指鱼塘/技能选项，须落在 IM 名单窗口才硬拦
        w0 = max(0, small_total.start() - 48)
        # 只看数字前的语境：后面另起『微信置顶』不得反咬前面的『就这四个选项』
        window = text[w0 : small_total.end()]
        if any(
            k in window
            for k in ("微信", "置顶", "聊天", "会话", "通讯录", "备注", "列表", "聊天框")
        ):
            return [
                _format_issue(
                    "im_contact_density",
                    "IM/名单写成个位数全貌，缺少其余列表仍在的余量信号",
                    small_total.group(0),
                )
            ]
    if 1 <= len(names) <= 5 and not _has_remainder(text):
        # 仅当明确在列名单语境
        if any(k in text for k in ("置顶", "聊天框", "备注", "名单", "列表", "通讯录", "点赞列表")):
            return [
                _format_issue(
                    "im_contact_density",
                    f"具名联系人/备注仅 {len(names)} 个且当作全貌，须暗示列表其余仍在",
                    "、".join(names[:5]),
                )
            ]
    return []


def check_im_recency_sort(text: str) -> list[str]:
    """静态点名册观感：按固定顺序介绍且无「刚聊/置顶/刚才」时间线索。"""
    if "微信" not in text and "聊天" not in text and "会话" not in text:
        return []
    roster = re.search(r"(?:分别是|依次是|第一个.{0,4}第二个.{0,4}第三个)", text)
    if not roster:
        return []
    time_cues = ("刚才", "刚刚", "昨晚", "今天", "置顶", "未读", "最新", "最近")
    if any(c in text for c in time_cues):
        return []
    return [
        _format_issue(
            "im_recency_sort",
            "会话/名单像静态点名册，缺少按最近互动排序的时间线索",
            roster.group(0)[:30],
        )
    ]


def check_urban_prop_ux(text: str) -> list[str]:
    """轻量：出现微信却完全没有交互痕迹时提示。"""
    if "微信" not in (text or ""):
        return []
    ux = ("点开", "划", "置顶", "未读", "气泡", "对话框", "备注", "语音", "红点")
    if any(u in text for u in ux):
        return []
    if "微信" in text and len(text) < 80:
        return [
            _format_issue(
                "urban_prop_ux",
                "提到微信但缺少当代交互痕迹（划动/未读/置顶/备注等）",
                "微信",
                hard=True,
            )
        ]
    return []


_WECHAT_READ_RECEIPT = re.compile(
    r"已读[，,、。．\s]*未回|已读[，,、。．\s]*不回|"
    r"(?:显示|下面显示|消息.{0,6}|气泡.{0,4})[「\"“]?已读|"
    r"(?:[「\"“]已读[」\"”]|』已读|」已读)(?![，,、。．\s]*[未不]回)"
)
_WECHAT_GRAY_AVATAR = re.compile(
    r"头像\s*灰|灰\s*着?的?\s*头像|头像灰的|灰掉的头像|灰色头像"
)


def check_wechat_false_ux(text: str) -> list[str]:
    """微信器物假象：私聊已读回执、灰头像闲置态。"""
    raw = text or ""
    if "微信" not in raw and "会话" not in raw and "置顶" not in raw:
        # 无 IM 语境不拦（「视野发灰」「深灰衬衫」等）
        if not any(t in raw for t in ("聊天", "未读", "通讯录", "对话框")):
            return []
    issues: list[str] = []
    m = _WECHAT_READ_RECEIPT.search(raw)
    if m:
        issues.append(
            _format_issue(
                "wechat_false_ux",
                "微信私聊没有已读回执，不得写成界面可见『已读/已读未回』",
                m.group(0)[:40],
            )
        )
    # 灰头像：须在会话/列表语境，避免误伤「视野发灰」
    for m in _WECHAT_GRAY_AVATAR.finditer(raw):
        w0 = max(0, m.start() - 40)
        w1 = min(len(raw), m.end() + 24)
        window = raw[w0:w1]
        if any(
            k in window
            for k in ("微信", "会话", "置顶", "未读", "聊天", "联系人", "列表", "通讯录")
        ):
            issues.append(
                _format_issue(
                    "wechat_false_ux",
                    "微信会话列表不以灰头像表示闲置/不活跃；勿写成『头像灰的』常态",
                    m.group(0)[:40],
                )
            )
            break
    return issues


# 仅机械器物 UX：叙事/设定类（体感、双身份、外挂契约）由本作公约 + LLM 审校
_CHECKERS = {
    "im_contact_density": lambda text, **kw: check_im_contact_density(text),
    "im_recency_sort": lambda text, **kw: check_im_recency_sort(text),
    "urban_prop_ux": lambda text, **kw: check_urban_prop_ux(text),
    "wechat_false_ux": lambda text, **kw: check_wechat_false_ux(text),
}


def deterministic_commons_issues(
    text: str,
    *,
    commons_rails: Sequence[dict[str, Any]] | None,
    chapter_no: int = 1,
) -> list[str]:
    """对已装备规则跑确定性检查；无装备的 rule 不跑。"""
    rails = list(commons_rails or [])
    if not rails:
        return []
    if not scan_triggers(text) and not any(
        any(t in text for t in (r.get("trigger_terms") or [])) for r in rails
    ):
        # 正文完全无相关触发词：不误伤
        return []
    allowed = active_rule_ids(rails)
    issues: list[str] = []
    for rule in rails:
        check_id = rule.get("check")
        rule_id = str(rule.get("rule_id") or "")
        if not check_id or rule_id not in allowed:
            continue
        fn = _CHECKERS.get(str(check_id))
        if fn is None:
            continue
        issues.extend(fn(text, chapter_no=chapter_no))
    # 去重保序
    seen: set[str] = set()
    out: list[str] = []
    for item in issues:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def should_run_llm_commons_audit(
    text: str,
    *,
    commons_rails: Sequence[dict[str, Any]] | None,
    deterministic_hits: Sequence[str] | None = None,
) -> bool:
    """确定性已命中，或存在本作公约/原则透镜相关触发时，呼叫 LLM 审核者。"""
    if not commons_rails:
        return False
    if deterministic_hits:
        return True
    # 有本作推导公约：章内值得审（叙事类无稳定 regex）
    if any(str(r.get("kind") or "") == "work_convention" for r in commons_rails):
        return True
    if scan_triggers(text):
        return True
    return any(
        any(t in text for t in (r.get("trigger_terms") or [])) for r in commons_rails
    )


def commons_audit_system_prompt() -> str:
    return (
        "你是设定与常识审核者，不是文风打分器。"
        "优先按给定 commons_rails 中的『本作公约』(kind=work_convention) 与器物规则审核；"
        "若同时给出 principle_lenses，用透镜的问题与反模式做推理，但结论必须落到本作正文证据。"
        "每条问题必须给出 rule_id（优先用公约 convention_id）、正文摘录 quote、reason。"
        "范围约束：公约写明『苏醒/接入/开篇第一感知』的，只用于正文正在写苏醒或首次接入的片段；"
        "后续场景（录制、社交、对峙等）不得因开篇未重复体感清单而判违规。"
        "公约写明『首次出现某器物/面板时』的，只在该器物首次入镜时检查。"
        "无证据则不要捏造问题（issues 可空，passed=true）。"
        "对带确定性 check 的器物规则（im_*、urban_prop_ux、wechat_false_ux）："
        "正文已有置顶/划动/未读红点/备注等任一真实交互痕迹时，不得再因『缺角标/缺备注名』标 hard；"
        "此类只允许在完全无交互痕迹时由确定性检查触发。"
        "微信硬常识：私聊没有已读回执（禁止『已读，未回』当界面状态）；"
        "会话列表不以灰头像表示闲置联系人；可用未读红点、置顶、备注、语音来电。"
        "禁止把其他小说的具体梗当成规则；禁止综合分；禁止改写正文。"
    )


def merge_commons_into_issues(
    issues: list[str],
    commons_issues: Sequence[str],
) -> list[str]:
    merged = list(issues)
    for item in commons_issues:
        if item not in merged:
            merged.append(item)
    return merged


def issues_from_llm_result(
    result: CommonsAuditResult,
    *,
    allowed_rule_ids: set[str] | None = None,
    advisory_rule_ids: set[str] | None = None,
    deterministic_hits: Sequence[str] | None = None,
) -> list[str]:
    """把 LLM 审核者的 hard issues 转成与确定性检查同一前缀的字符串。

    ``advisory_rule_ids``（通常是本作公约）只作提示，不进硬失败列表——否则
    「苏醒第一感知」类偏好会被 LLM 标 hard，把后续场或略有偏差的苏醒场直接卡死。

    带确定性 check 的器物规则（im_*、urban_prop_ux 等）必须以确定性命中为前置：
    LLM 常把『置顶三个会话』误判成缺角标/备注而硬拦，没有 regex 实证时不得硬拦。
    """
    allowed = allowed_rule_ids
    advisory = advisory_rule_ids or set()
    det_ids = {
        m.group(1)
        for item in (deterministic_hits or [])
        for m in [re.match(r"\[commons:([^\]]+)\]", str(item))]
        if m
    }
    checked = set(_CHECKERS)
    out: list[str] = []
    for item in result.issues:
        if not item.hard:
            continue
        if item.rule_id in advisory:
            continue
        if allowed is not None and item.rule_id not in allowed:
            continue
        if item.rule_id in checked and item.rule_id not in det_ids:
            continue
        out.append(_format_issue(item.rule_id, item.reason, item.quote, hard=True))
    return out


def advisory_rule_ids(commons_rails: Sequence[dict[str, Any]] | None) -> set[str]:
    """本作公约与显式 soft 规则：审校可提示，不拦放行。"""
    out: set[str] = set()
    for rule in commons_rails or []:
        rid = str(rule.get("rule_id") or "").strip()
        if not rid:
            continue
        if str(rule.get("kind") or "") == "work_convention" or rule.get("hard") is False:
            out.add(rid)
    return out

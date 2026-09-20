"""常识审校接入：issues 合并后应阻断 ACCEPT（has_hard_failure）。"""

from __future__ import annotations

from regent.novel.application import commons_audit as ca
from regent.novel.application.runtime import CommandRuntime, RuntimeState
from regent.novel.domain.commands import CommandKind
from regent.novel.domain.commands import command as director_command
from regent.novel.domain.genre_packs import commons_rails_for_keywords
from regent.novel.domain.states import SceneArtifact, SceneRunState


def test_im_density_ignores_non_im_closed_count():
    """『就这四个』若指技能/鱼塘而非 IM 名单，不得硬拦。"""
    rails = commons_rails_for_keywords(["都市文娱", "系统流"])
    draft = (
        "系统弹出四个选项。就这四个。我选了镜头感。"
        "手机亮了，微信置顶最上面是周予安。"
    )
    issues = ca.deterministic_commons_issues(draft, commons_rails=rails, chapter_no=1)
    assert not any("im_contact_density" in x for x in issues)


def test_im_density_ignores_dialogue_quotes():
    """正文对话引号不得当成联系人备注。"""
    rails = commons_rails_for_keywords(["都市文娱", "系统流"])
    draft = (
        "我说「这单……超时了。」又听见「好呀。」"
        "床头柜上的手机亮着，微信置顶会话最上面是周予安。"
    )
    issues = ca.deterministic_commons_issues(draft, commons_rails=rails, chapter_no=1)
    assert not any("im_contact_density" in x for x in issues)


def test_wechat_false_ux_blocks_read_receipt_and_gray_avatar():
    rails = commons_rails_for_keywords(["都市文娱", "恋综"])
    draft = (
        "我打开微信。置顶最上面是周予安：『明天先导片。』已读，未回。"
        "往下还有一长串会话，头像灰的、亮的、带红点的。"
    )
    issues = ca.deterministic_commons_issues(draft, commons_rails=rails, chapter_no=1)
    assert any("wechat_false_ux" in x and "已读" in x for x in issues)
    assert any("wechat_false_ux" in x and "灰" in x for x in issues)


def test_wechat_false_ux_ignores_non_im_gray():
    """『视野发灰』『深灰衬衫』不得误伤。"""
    rails = commons_rails_for_keywords(["都市文娱", "恋综"])
    draft = "视野发灰。低血糖。顾淮穿一件深灰衬衫坐在监视器旁。"
    issues = ca.deterministic_commons_issues(draft, commons_rails=rails, chapter_no=1)
    assert not any("wechat_false_ux" in x for x in issues)


def test_im_density_still_catches_closed_roster():
    rails = commons_rails_for_keywords(["都市文娱", "系统流"])
    draft = (
        "我打开微信，置顶聊天框一共四个，备注是“阿深”“默默”“小狼狗”“苏瑶”。"
    )
    issues = ca.deterministic_commons_issues(draft, commons_rails=rails, chapter_no=1)
    assert any("im_contact_density" in x for x in issues)


def test_commons_hard_issue_blocks_accept_scene():
    rails = commons_rails_for_keywords(["都市文娱", "系统流", "爽文"])
    draft = (
        "【绑定成功。宿主林晚，欢迎使用鱼塘系统。】"
        "我打开微信，置顶聊天框一共四个，备注是“阿深”“默默”“小狼狗”“苏瑶”。"
    )
    issues = ca.deterministic_commons_issues(draft, commons_rails=rails, chapter_no=1)
    assert issues, "三类翻车应至少命中一条 commons 硬门"

    runtime = CommandRuntime()
    state = RuntimeState(
        scene_state=SceneRunState.DIRECTOR_VIEW.value,
        artifact=SceneArtifact.PROSE.value,
        input_version=1,
        scene_index=0,
        take_no=1,
        has_prose=True,
        has_hard_failure=True,
    )
    cmd = director_command(
        CommandKind.ACCEPT_SCENE,
        command_id="t:accept",
        input_version=1,
        scene_index=0,
        take_no=1,
        evidence=["绑定成功"],
    )
    try:
        runtime.validate(cmd, state)
        raised = False
    except Exception as exc:  # noqa: BLE001 — 只要被拒即可
        raised = True
        assert "硬失败" in str(exc) or "不能接受" in str(exc)
    assert raised, "存在 commons 硬失败时 ACCEPT_SCENE 必须被 Runtime 拒绝"


def test_llm_result_filters_unknown_rules():
    result = ca.CommonsAuditResult(
        passed=False,
        issues=[
            ca.CommonsIssue(rule_id="im_contact_density", quote="一共四个", reason="全貌"),
            ca.CommonsIssue(rule_id="made_up_rule", quote="x", reason="应被过滤"),
        ],
    )
    # IM 硬门须有确定性命中，否则 LLM 不得单独硬拦
    out = ca.issues_from_llm_result(
        result,
        allowed_rule_ids={"im_contact_density"},
        deterministic_hits=["[commons:im_contact_density] IM/名单写成个位数全貌"],
    )
    assert len(out) == 1
    assert "im_contact_density" in out[0]


def test_llm_im_without_deterministic_is_dropped():
    result = ca.CommonsAuditResult(
        passed=False,
        issues=[
            ca.CommonsIssue(
                rule_id="im_contact_density",
                quote="弹出两条新消息",
                reason="误判通知气泡为全貌",
            ),
        ],
    )
    out = ca.issues_from_llm_result(
        result, allowed_rule_ids={"im_contact_density"}, deterministic_hits=[]
    )
    assert out == []


def test_llm_urban_prop_without_deterministic_is_dropped():
    """正文已有置顶时，LLM 不得单凭『缺角标』硬拦 urban_prop_ux。"""
    result = ca.CommonsAuditResult(
        passed=False,
        issues=[
            ca.CommonsIssue(
                rule_id="urban_prop_ux",
                quote="微信置顶三个会话",
                reason="未体现未读角标、备注名",
            ),
        ],
    )
    out = ca.issues_from_llm_result(
        result, allowed_rule_ids={"urban_prop_ux"}, deterministic_hits=[]
    )
    assert out == []

from regent.novel.domain import story_direction as sd
from regent.novel.experiments import quality_ab as qa


def test_normalize_dedupes_and_merges_custom():
    kw = sd.normalize_direction_keywords(
        ["都市文娱", "文抄公"],
        ["自定义标签", "都市文娱"],
    )
    assert kw == ["都市文娱", "文抄公", "自定义标签"]


def test_fragment_for_run_injects_user_keywords_not_fixed_string():
    frag = qa.fragment_for_run(
        "f4_ent_gender",
        direction_keywords=["悬疑推理", "穿书"],
        use_default_when_empty=False,
    )
    assert frag["direction"] == "悬疑推理 + 穿书"
    assert "悬疑推理" in frag["brief"]
    assert "都市文娱 + 文抄公" not in frag["direction"]


def test_default_selection_when_cli_omits_keywords():
    frag = qa.fragment_for_run("f4_ent_gender")
    assert sd.format_direction_line(sd.DEFAULT_DIRECTION_KEYWORD_SELECTION) == frag["direction"]


def test_assumption_roundtrip():
    line = sd.assumption_line(["甜宠", "职场"])
    assert sd.keywords_from_assumptions([line, "other"]) == ["甜宠", "职场"]


def test_locked_direction_and_clarify_rails():
    lock = sd.LOCKED_DIRECTION_PREFIX + '{"title":"重生逆袭","protagonist_desire":"前世记忆"}'
    assumptions = [
        sd.assumption_line(["文抄公", "性转"]),
        lock,
        "澄清：文抄公设定 → 直接搬运地球经典",
    ]
    assert sd.keywords_from_assumptions(assumptions) == ["文抄公", "性转"]
    assert sd.locked_direction_from_assumptions(assumptions)["title"] == "重生逆袭"
    assert sd.clarify_rails_from_assumptions(assumptions)[0].startswith("澄清：")

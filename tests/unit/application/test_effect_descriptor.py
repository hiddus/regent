from regent.application.effect_descriptor import EffectDescriptor, EffectRisk


def test_pure_offline_exploration_is_low_risk() -> None:
    effect = EffectDescriptor(purpose="offline hypothesis generation")
    assert effect.risk_tier() is EffectRisk.LOW
    assert effect.requires_permit() is False


def test_reversible_public_action_requires_permit() -> None:
    effect = EffectDescriptor(
        external_visibility=EffectRisk.HIGH,
        contacts_people=True,
        compensatable=True,
        affected_people=50,
    )
    assert effect.risk_tier() is EffectRisk.HIGH
    assert effect.requires_permit() is True


def test_cumulative_actions_raise_risk() -> None:
    assert EffectDescriptor(cumulative_count=500).risk_tier() is EffectRisk.HIGH


def test_legal_decision_is_critical() -> None:
    assert EffectDescriptor(legal_or_safety_decision=True).risk_tier() is EffectRisk.CRITICAL

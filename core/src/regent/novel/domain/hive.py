"""Hive 路由决策（PRD 执行拓扑 / Tech-Spec §13 G-24）。

持续 Agent loop 是唯一主核心流程，Hive 只是 loop 内的局部执行器：只有当
「必须信息隔离」且「可并发」**两个条件同时成立**时，同一节拍的多个角色才
被打包成一次 Hive 调用；否则退回逐角色串行。

本模块只做**决策与隔离证明**，不发起模型调用、不改生产状态：

- 可并发：同一节拍内有两个及以上角色，且它们的上下文彼此独立；
- 信息隔离：任一角色上下文里不得出现他不应当知道的事实或观察。

隔离不是靠"我们写的时候很小心"，而是靠逐条核对 ``known_by``：只要有一条
越界，路由就关闭，并留下证据说明是谁泄露给了谁。
"""

# Chinese docstrings and messages deliberately use full-width punctuation.
# ruff: noqa: RUF001

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

AUDIENCE = "actor"


@dataclass(frozen=True)
class HiveRoute:
    """一次节拍的路由决策。``enabled`` 为假时必须照 ``reason`` 记录原因。"""

    enabled: bool
    reason: str
    personas: tuple[str, ...] = ()
    # 每个角色上下文的指纹：用于重放与核对"同一节拍给了谁什么"
    fingerprints: dict[str, str] = field(default_factory=dict)
    # 泄露证据：非空即代表隔离条件不成立，路由必须关闭
    leaks: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "reason": self.reason,
            "personas": list(self.personas),
            "fingerprints": dict(self.fingerprints),
            "leaks": list(self.leaks),
        }


def _leaks(persona: str, payload: dict[str, Any]) -> list[str]:
    """逐条核对：角色上下文里每条事实/观察都必须对他可见（G-03）。"""
    found: list[str] = []
    for key in ("known_facts", "observations"):
        for item in payload.get(key) or []:
            if not isinstance(item, dict):
                continue
            known = item.get("known_by") or []
            if persona in known or "ALL" in known:
                continue
            statement = str(item.get("statement") or item.get("summary") or "")[:40]
            found.append(f"{persona} 看到了不该知道的内容（{key}）：{statement}")
    # 角色上下文里只能出现自己的私有来源
    for source in payload.get("_sources") or []:
        if not isinstance(source, dict):
            continue
        if source.get("kind") == "persona" and source.get("ref") not in (persona, None, ""):
            found.append(f"{persona} 的上下文混入了 {source.get('ref')} 的私有来源")
    return found


def route_beat(contexts: dict[str, Any]) -> HiveRoute:
    """判定同一节拍的角色集合是否可以打包为 Hive 调用。

    ``contexts`` 是 ``persona -> CompiledContext``。只有 audience 为 actor、
    且全部通过隔离核对、且角色数不少于 2 时才启用。
    """
    if len(contexts) < 2:
        return HiveRoute(
            enabled=False,
            reason="single_actor_beat" if contexts else "empty_beat",
            personas=tuple(contexts),
        )

    leaks: list[str] = []
    fingerprints: dict[str, str] = {}
    for persona, compiled in contexts.items():
        if getattr(compiled, "audience", "") != AUDIENCE:
            return HiveRoute(
                enabled=False,
                reason="non_actor_context_in_beat",
                personas=tuple(contexts),
            )
        payload = dict(getattr(compiled, "payload", {}) or {})
        payload["_sources"] = [
            s.model_dump(mode="json") for s in (compiled.manifest.sources or [])
        ]
        leaks.extend(_leaks(persona, payload))
        fingerprints[persona] = compiled.manifest_hash

    if leaks:
        return HiveRoute(
            enabled=False,
            reason="isolation_violated",
            personas=tuple(contexts),
            fingerprints=fingerprints,
            leaks=tuple(leaks),
        )
    if len(set(fingerprints.values())) != len(fingerprints):
        # 两个角色拿到完全一样的上下文，说明裁剪没有生效：不能并发，也不能算隔离。
        return HiveRoute(
            enabled=False,
            reason="identical_contexts",
            personas=tuple(contexts),
            fingerprints=fingerprints,
        )
    return HiveRoute(
        enabled=True,
        reason="isolated_and_concurrent",
        personas=tuple(contexts),
        fingerprints=fingerprints,
    )


__all__ = ["AUDIENCE", "HiveRoute", "route_beat"]

"""导演协议与执行器归属判定（纯函数，无 IO）。"""

from __future__ import annotations

from typing import Any

from regent.novel.application.directing_types import (
    PROTOCOL_SCENE,
    PROTOCOL_SCRIPT,
    PROTOCOL_SCRIPT_SCENE,
    SCRIPT_ARCHITECTURE,
    SCRIPT_SCENE_ARCHITECTURE,
)

ARCHITECTURE = "director_v2"
BEAT_ARCHITECTURE = "director_v2_beat"
PROTOCOL_BEAT = "beat"

DIRECTED_ARCHITECTURES = frozenset(
    {ARCHITECTURE, BEAT_ARCHITECTURE, SCRIPT_ARCHITECTURE, SCRIPT_SCENE_ARCHITECTURE}
)


def is_directed(run: Any) -> bool:
    return (run.generation_context or {}).get("architecture_version") in DIRECTED_ARCHITECTURES


def production_protocol(run_or_production: Any) -> str:
    """返回本 run / production 钉死的 PRODUCE 协议。

    默认 ``scene``（director_v2@2）。对照臂 ``director_v2_beat`` / ``director_script``
    / ``director_script_scene`` 或显式 ``protocol`` 钉定时走对应链路。
    """
    known = {PROTOCOL_SCENE, PROTOCOL_BEAT, PROTOCOL_SCRIPT, PROTOCOL_SCRIPT_SCENE}
    if isinstance(run_or_production, dict):
        pinned = run_or_production.get("protocol")
        if pinned in known:
            return str(pinned)
        return PROTOCOL_SCENE
    ctx = getattr(run_or_production, "generation_context", None) or {}
    production = ctx.get("production") or {}
    pinned = production.get("protocol")
    if pinned in known:
        return str(pinned)
    arch = ctx.get("architecture_version") or ARCHITECTURE
    if arch == BEAT_ARCHITECTURE:
        return PROTOCOL_BEAT
    if arch == SCRIPT_ARCHITECTURE:
        return PROTOCOL_SCRIPT
    if arch == SCRIPT_SCENE_ARCHITECTURE:
        return PROTOCOL_SCRIPT_SCENE
    return PROTOCOL_SCENE

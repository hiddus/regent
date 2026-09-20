"""导演侧共享常量（从 direction 抽出；搬家不改行为）。

执行器名、协议名与上限在此为权威来源；direction 再导出以兼容旧 import。
模型返回类型仍定义在 direction，待后续批次迁入。
"""

from __future__ import annotations

PROTOCOL_SCENE = "scene"
PROTOCOL_SCRIPT = "script"
PROTOCOL_SCRIPT_SCENE = "script_scene"

STABLE_EXECUTOR = "director_script_scene"
SCRIPT_ARCHITECTURE = "director_script"
SCRIPT_SCENE_ARCHITECTURE = "director_script_scene"

MAX_TAKES = 4
MAX_CHAPTER_REPAIRS = 2
MAX_CHAPTER_CREATIVE_REPAIRS = 1

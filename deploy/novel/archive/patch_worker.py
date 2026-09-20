"""一次性补丁：worker 启动与周期 tick 接入小说调用恢复（P0-1）。"""
from __future__ import annotations

import pathlib

path = pathlib.Path("core/src/regent/worker/main.py")
text = path.read_text(encoding="utf-8")


def sub(old: str, new: str) -> None:
    global text
    assert text.count(old) == 1, f"count={text.count(old)} for {old[:70]!r}"
    text = text.replace(old, new)


# 1) 计时器字段
sub(
    """        self._behavior_monitor_interval = 600.0
        self._next_behavior_monitor = 0.0
        self._behavior_monitor_enabled = True
""",
    """        self._behavior_monitor_interval = 600.0
        self._next_behavior_monitor = 0.0
        self._behavior_monitor_enabled = True
        self._novel_recovery_interval = 30.0
        self._next_novel_recovery = 0.0
""",
)

# 2) 启动即恢复：worker 重启后先把上一任进程留下的调用收口
sub(
    """        if self.event_engine is not None:
            await self.event_engine.start()
        try:
""",
    """        if self.event_engine is not None:
            await self.event_engine.start()
        # 重启即恢复：先把崩溃进程留下的 RESERVED/UNKNOWN 调用收口，再开始推进
        if self.sessions is not None:
            await self._novel_recovery_tick(startup=True)
        try:
""",
)

# 3) 周期恢复：先清扫，再推进，避免刚恢复的调用被当成新失败
sub(
    """                if self.sessions is not None and self.novel_provider is not None:
                    try:
                        from regent.novel.application.works import advance_background_run
""",
    """                if self.sessions is not None and monotonic() >= self._next_novel_recovery:
                    await self._novel_recovery_tick()
                    self._next_novel_recovery = monotonic() + self._novel_recovery_interval
                if self.sessions is not None and self.novel_provider is not None:
                    try:
                        from regent.novel.application.works import advance_background_run
""",
)

# 4) tick 实现
sub(
    """    async def _scheduler_tick(self) -> None:
""",
    '''    async def _novel_recovery_tick(self, *, startup: bool = False) -> None:
        """回收过期调用并对账 UNKNOWN 调用（Tech-Spec §4.4 / P0-1）。

        崩溃留下的 ``RESERVED`` 记录先判定为 ``UNKNOWN``（保留预留额、不猜结果），
        再逐条对账：供应商可查则据实结算，查不到按对账次数有界终止。
        失败只记录日志，不得让整个 worker 循环停摆。
        """
        try:
            from regent.novel.application.production import recover_novel_calls

            async with self.sessions() as session:  # type: ignore[misc]
                stats = await recover_novel_calls(
                    session, provider=self.novel_provider
                )
                await session.commit()
        except Exception:
            logger.exception("novel call recovery tick failed")
            return
        if any(stats.values()):
            logger.info(
                "novel call recovery",
                extra={"startup": startup, **stats},
            )

    async def _scheduler_tick(self) -> None:
''',
)

path.write_text(text, encoding="utf-8")
print("patched", path)

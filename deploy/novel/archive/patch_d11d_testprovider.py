"""D-11d：让测试 Provider 能服务自修调用，并把 tick 的调用上限改成真实契约。

两处：

1. ``Provider`` 原来只会从队列头部取输出。自修调用会**错位消费**下一个罐装输出
   （拿到别的结构体），于是"Runtime 拒绝越界命令"那批测试不是因为拒绝而失败，
   而是因为拿到错类型的输出——反证就假了。
   自修调用默认返回**上一次的输出**（深拷贝），即"导演坚持原判"——这是最坏情况，
   也正是那批测试要守的情形。深拷贝是必须的：``_coerce_illegal_action`` 会就地改
   ``result.action``，返回同一个对象会让第二次尝试带着第一次的收束结果。

2. ``tick_to_done`` 断言"每 tick 至多一次模型调用"。自修让一个 tick 可以发多次。
   被守住的应当是**一次提交的决策**（检查点），不是一次 HTTP 往返；预算另有
   ``MAX_CALLS``（按模型调用计数）与 ``MAX_COST_MINOR``（金额）兜底。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TARGET = ROOT / "tests/unit/novel/test_direction.py"

OLD_PROVIDER = '''class Provider:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.requests = []

    async def generate_structured(self, *, response_model, **kwargs):
        self.requests.append({"schema": response_model, **kwargs})
        output = self.outputs.pop(0)
        assert isinstance(output, response_model), (output, response_model)
        return StructuredModelResponse(output=output, usage=ModelUsage(10, 20), model="test")
'''

NEW_PROVIDER = '''class Provider:
    """罐装输出。``repair`` 可给出"自修后改对了"的输出，缺省是**坚持原判**。

    自修调用（payload 带 ``repair_instructions``）不消费队列：否则它会取到下一个
    罐装输出，拿到别的结构体，测试失败的原因就不是"被 Runtime 拒绝"而是"类型不对"。
    """

    def __init__(self, outputs, repair=None):
        self.outputs = list(outputs)
        self.requests = []
        self.repair = repair
        self.last = None

    async def generate_structured(self, *, response_model, **kwargs):
        self.requests.append({"schema": response_model, **kwargs})
        prompt = str(kwargs.get("user_prompt") or "")
        if "repair_instructions" in prompt:
            output = self.repair if self.repair is not None else self.last
        else:
            output = self.outputs.pop(0)
        assert isinstance(output, response_model), (output, response_model)
        self.last = output
        # 深拷贝：自修的就地收束不能污染下一次尝试（生产里每次调用都是新对象）。
        return StructuredModelResponse(
            output=output.model_copy(deep=True), usage=ModelUsage(10, 20), model="test"
        )
'''

OLD_TICK = '''async def tick_to_done(session, provider, work, run):
    for _ in range(50):
        before = len(provider.requests)
        done = await execute_step(
            session, provider=provider, work=work, run=run, step=ChapterStep.PRODUCE
        )
        assert len(provider.requests) - before <= 1
        if done:
            return
    pytest.fail("director did not converge")
'''
NEW_TICK = '''async def tick_to_done(session, provider, work, run):
    # 一个 tick 提交**一个决策**，但可能发出多次模型调用：引文或命令被拒时会
    # 带反馈自修（上限见 MAX_JUDGE_REPAIRS / MAX_COMMAND_REPAIRS）。预算不以
    # tick 计——call_count 按模型调用累加并受 MAX_CALLS 约束，金额另有上限。
    max_calls_per_tick = 1 + d.MAX_JUDGE_REPAIRS + d.MAX_COMMAND_REPAIRS
    for _ in range(50):
        before = len(provider.requests)
        done = await execute_step(
            session, provider=provider, work=work, run=run, step=ChapterStep.PRODUCE
        )
        assert len(provider.requests) - before <= max_calls_per_tick
        if done:
            return
    pytest.fail("director did not converge")
'''

EDITS = [
    ("provider", OLD_PROVIDER, NEW_PROVIDER),
    ("tick", OLD_TICK, NEW_TICK),
]


def main() -> int:
    src = TARGET.read_text(encoding="utf-8")
    orig = src
    for name, old, new in EDITS:
        count = src.count(old)
        assert count == 1, f"{name}: anchor hit {count} times, expected 1"
        src = src.replace(old, new, 1)
    assert src != orig
    compile(src, str(TARGET), "exec")
    TARGET.write_text(src, encoding="utf-8")
    print(f"[OK] test_direction.py 已改写（{len(EDITS)} 处）")
    return 0


if __name__ == "__main__":
    sys.exit(main())

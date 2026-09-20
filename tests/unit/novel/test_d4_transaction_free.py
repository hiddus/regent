"""缺陷 4：事务不得跨越模型调用（2026-09-10 生产自锁事故）。

真实故障：worker 的业务会话停在「事务中空闲」15 分钟并持有 xid，同容器另一条
连接阻塞在这个 xid 上等 ``FOR UPDATE``——worker 自己等自己。``skip_locked``
绕不过（等的是事务结束，不是行锁），库里 ``idle_in_transaction_session_timeout=0``
时泄漏可永久存活，且**完全静默**：无报错、无日志、健康检查 200。

根因：``CallBroker.run`` 的 ``commit_before_call`` 只保护走 broker 的调用；
直接 ``provider.generate_structured(...)`` 的路径不受保护。修复不是"多传一个
参数"，而是把约束收口到网络出口本身。

三条命题，各自都能被退回旧行为打红：
1. 走包装器的调用会在发出请求前提交事务；
2. ``chat`` 同样受保护（协议里有两个网络出口，漏一个就等于没保护）；
3. 未覆盖的属性照旧透传（包装不得改变 provider 的其他行为）。

ruff: noqa: RUF001
"""

from __future__ import annotations

import pytest

from regent.novel.application.production import TransactionFreeProvider


class FakeSession:
    """记录提交次数；``in_transaction`` 前两次为真，模拟"事务已开"。"""

    def __init__(self, open_count: int = 2) -> None:
        self.commits: list[str] = []
        self._open = open_count

    def in_transaction(self) -> bool:
        return self._open > 0

    async def commit(self) -> None:
        self.commits.append("commit")
        if self._open > 0:
            self._open -= 1


class FakeProvider:
    model_name = "fake-model"

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def generate_structured(self, **kwargs):  # noqa: ANN201
        self.calls.append("structured")
        return kwargs

    async def chat(self, **kwargs):  # noqa: ANN201
        self.calls.append("chat")
        return kwargs


def _wrap(session_open_count: int = 2):
    session = FakeSession(open_count=session_open_count)
    inner = FakeProvider()
    return session, inner, TransactionFreeProvider(inner, session)


@pytest.mark.asyncio
async def test_generate_commits_before_the_request_goes_out():
    session, inner, provider = _wrap()
    await provider.generate_structured(system_prompt="s", user_prompt="u")
    # 提交必须发生在调用之前：先发出请求再提交，事务照样跨越整个网络往返。
    assert session.commits == ["commit"]
    assert inner.calls == ["structured"]


@pytest.mark.asyncio
async def test_chat_is_protected_too():
    """协议有两个网络出口；只保护一个等于没保护。"""
    session, inner, provider = _wrap()
    await provider.chat(messages=[])
    assert session.commits == ["commit"]
    assert inner.calls == ["chat"]


@pytest.mark.asyncio
async def test_no_commit_when_there_is_no_open_transaction():
    """没有开事务就不该凭空提交：提交会 flush，无谓写库是副作用。"""
    session, inner, provider = _wrap(session_open_count=0)
    await provider.generate_structured(system_prompt="s")
    assert session.commits == []
    assert inner.calls == ["structured"]


@pytest.mark.asyncio
async def test_unwrapped_attributes_pass_through():
    _, inner, provider = _wrap()
    assert provider.model_name == "fake-model"
    assert provider._inner is inner


@pytest.mark.asyncio
async def test_every_call_detaches_again_after_new_writes():
    """每步写完都会另起事务：一次调用一个事务，不是只在第一次收口。"""
    session, inner, provider = _wrap(session_open_count=3)
    await provider.generate_structured(system_prompt="a")
    await provider.generate_structured(system_prompt="b")
    await provider.chat(messages=[])
    assert session.commits == ["commit", "commit", "commit"]
    assert inner.calls == ["structured", "structured", "chat"]

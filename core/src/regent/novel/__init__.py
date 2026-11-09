"""Novel Engine — 小说导演领域包。

分层（Tech-Spec §1.2）：
  domain         领域模型与状态机，不依赖基础设施
  ports          仓储协议，由 infrastructure 实现
  infrastructure SQLAlchemy 表与仓储实现
  application    loop 编排、事件、账本、鉴权
  api            C 端 HTTP 契约

硬约束：C 端不得 import 内部运维组件（G-16）。
"""

__all__ = ["__version__"]

__version__ = "0.1.0"

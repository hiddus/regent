# core/src/regent/operations

运维操作层：**打包进 wheel 的**运维命令入口（`p0_audit.py` 为 P0 审计导出）。

与其他两处运维代码的边界：

| 位置 | 定位 |
|---|---|
| `core/src/regent/operations/`（本目录） | 随 Core 发布、可在容器内执行的运维操作 |
| `ops/` | 仓库级运维脚本与 CI 门禁，**不随 wheel 发布** |
| `scripts/` | 非运行时的仓库辅助脚本（凭据扫描、发布打标） |

> 产品/运行时代码不得留在 `ops/` 根目录（见 `ops/README.md` 规则 1）。

## 目录内容

文件：
- `__init__.py`
- `p0_audit.py`

> 本 README 由目录实际内容生成，反映当前结构；如用途有变请同步更新。

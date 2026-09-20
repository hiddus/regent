# 858a 修复 W0 基线（2026-09-19）

依据：[858a-failure-fix-plan-2026-09-19.md](../../docs/858a-failure-fix-plan-2026-09-19.md) 修订条款。

## 1. 证据包

| 项 | 路径 / 状态 |
|---|---|
| a4/a5 权威包 | `deploy/novel/artifacts/r3/858a_a4a5_2026-09-17/`（README + supervise_529818.txt） |
| 重复副本 | `858a_a4a5_2026-09-19/supervise_529818.txt`（同内容，不替代） |
| 仍缺 | scene_id、双重复位置、修订前后 hash、模型请求/响应、完整部署指纹 |

## 2. 源码指纹（W0 采集时）

git HEAD：`b97ac725e0af2ff50e68c190abde875feb99267f`（工作树大量未提交；HEAD ≠ 运行态）

| SHA256 前 12 位 | 文件 |
|---|---|
| `83ab58305057` | `core/src/regent/novel/application/directing_script_loop.py` |
| `3b00e6d50748` | `core/src/regent/novel/application/directing_contracts.py` |
| `4a2b8c9fe5ad` | `core/src/regent/novel/domain/repair_locate.py` |
| `ce26b03b8fb8` | `core/src/regent/novel/domain/prose_front_gates.py` |
| `b74272ac8d2c` | `core/src/regent/novel/application/chapter_review.py` |
| `a51b845dfced` | `core/src/regent/novel/application/works_run_control.py` |

PR-B 合并前须重算 `directing_script_loop.py` 指纹。

### PR-B 后指纹（实施完成）

| SHA256 前 12 位 | 文件 |
|---|---|
| `41284ea9cc4c` | `core/src/regent/novel/application/directing_script_loop.py`（已改） |
| `3b00e6d50748` | `core/src/regent/novel/application/directing_contracts.py` |
| `4a2b8c9fe5ad` | `core/src/regent/novel/domain/repair_locate.py` |

### 最终指纹（离线开发完成）

| SHA256 前 12 位 | 文件 |
|---|---|
| `443c103ee485` | `directing_script_loop.py` |
| `cdb943a9a37f` | `works_run_control.py` |
| `a798d4c06f7e` | `works_continuation.py` |
| `5077c76af2f7` | `production.py` |
| `e0700fe9ad99` | `directing_calls.py` |
| `ce26b03b8fb8` | `prose_front_gates.py` |
| `ab368b4d04de` | `chapter_review.py` |

## 3. novel 定向测试基线（实施完成后）

命令：`pytest tests/unit/novel tests/unit/test_novel_generation.py`

**结果：全绿（0 failed）** — 含恢复账本、调用键、soft 边界、定位接线、连续创作、resume 路由。

新增/关键行为测试：
- `test_repair_locate_wiring.py`（PR-B）
- `test_pr_cde_behavior.py`（PR-C/D/E）
- 变异反证：`mutate_check_repair_locate.py`、`mutate_check_pr_cde.py`

## 4. 代码现状（最终）

- `repair_locate.LocatedIssue` / `select_repair_target` / `apply_repair_target_to_script_state` / `verify_repair_progress` **已存在**
- `ScriptChapterValidation.located_issues` **已有字段**
- `WRITE_SCENE` **已**显式 `scene_texts[idx]` + 票据 scene_id/index 校验
- **PR-B 已落地**：`VALIDATE` / `ACCEPT render` 调用 `_begin_located_scene_repair`；无默认 `last_i`；无法定位 → `ProductionStopped`
- **PR-B 已落地**：`AUDIT_SCENE` 读 `texts[idx]`，越界停机
- **PR-C 已落地**：soft 仅结构化标签；VALIDATE 修订无进展（同 hash + 问题仍在）停机；coerce 保留 `verdict_original`
- **PR-D 已落地**：`resume_work` → `WorkResumeOut`；TERMINAL_FAILED 不伪 RUNNING；租约过期→UNKNOWN→对账
- **PR-E 已落地**：`works_continuation.ensure_next_run`；policy 默认关；成章收尾与 worker 补偿挂钩
- **W4 代码侧**：`call_key_version` 1/2 分流；`start_run`/continuation 新运行写入 version=2
- **质量补强（本轮）**：
  - REVIEW 回退接入 `repair_locate`（误指末场时仍可按摘录定位）
  - 结构化 `format_revision_instruction` + WRITE/AUDIT/VALIDATE prompt 收紧
  - 调度：SQL 屏障 + 窗口 64 + `run_id` 推进；饿死其他作品用例
  - 卷末投影 `volume_expansion_pending`；扩卷 `target_volume_no` 幂等；扩卷后自动 RUNNING
  - 中场定位 / 审校回退 / code 级 verify / 投影动作 单测已补

## 5. 合成夹具

不新建目录时复用：`tests/unit/novel/test_repair_locate_wiring.py`  
覆盖：前场重复 / **中场重复** / 末场重复 / 跨场双命中 / 无法定位停机 / 审校回退定位 / 修订指令结构化。

## 6. 干预标记

a4/a5 证据轮为 **supervised regenerate**，不得当作产品「一次启动连续三章」验收。

## 7. 仍未宣称产品结案

- 真实模型 on/off 对照（付费）未跑
- 产品入口「一次启动三章」端到端未在本机认证（continuation API/UI 已接线，待真跑）
- script_scene 修订仍以自然语言整场重写为主（已带 repair_ticket）；`prose_patch` 接入为后续可选项

## 8. ChatGPT 评估 F1–F9（2026-09-19 晚）

| 项 | 状态 |
|---|---|
| F1 知识冲突 soft | 已修：`working_state` 非空不得 soft；缺 state 保硬失败 |
| F2 默认首场 | 已修：无效/缺失/越界 → `locatable=False` |
| F3 短场偏移 | 已修：去掉虚拟槽，分段规则与检测同源 |
| F4 连续创作入口 | 已修：`GET/PUT /continuation` + novel-web 开关 |
| F5 并发幂等 | 已修：作品行锁 + SAVEPOINT + IntegrityError 回读已有 |
| F6 旧调用键 | 已修：缺省 version=1；新运行显式写 2 |
| F7 事实当去重 | 已修：指令/票据分 remove_dup vs rewrite_scene |
| F8 前端 blocker | 已修：resume 展示 blocker/detail/actions |
| F9 旧 TERMINAL 挡后继 | 已修：每章只看最新 attempt |

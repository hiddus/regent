"""一次性补丁：文档记录 P1-1~P1-4 完成状态。"""
from __future__ import annotations

import pathlib


def patch(path_str: str, subs: list[tuple[str, str]]) -> None:
    path = pathlib.Path(path_str)
    text = path.read_text(encoding="utf-8")
    for old, new in subs:
        assert text.count(old) == 1, f"{path}: count={text.count(old)} for {old[:60]!r}"
        text = text.replace(old, new)
    path.write_text(text, encoding="utf-8")
    print("patched", path)


patch(
    "Novel-Engine-Plan.md",
    [
        (
            "- [ ] **P1-1 完整命令执行**：将规划、组章、结束及用户裁决接入 Runtime 分发，校验 scene/take/产物绑定；持久化完整 ContextManifest。接入同节拍满足隔离和可并发条件的 Hive 路由。验收：非法绑定被拒，完整场景轨迹可重放，角色上下文互不泄漏。",
            "- [x] **P1-1 完整命令执行**（2026-09-08 完成）：`PLAN_SCENE`、`ASSEMBLE_CHAPTER`、`FINISH_CHAPTER`、`REQUEST_USER_DECISION` 全部接入 `CommandRuntime`；Runtime 新增 scene/take 绑定与产物绑定校验（命令不得漂移到别的场景或 take，接受/组章必须有稿件）。manifest 落完整 `binding` 与 `fingerprint`，不再只存 hash。新增 `domain/hive.py`：`route_beat()` 逐条核对 `known_by` 判定隔离，仅在「≥2 角色且互不泄漏且上下文不同」时启用；执行层用 `CallBroker.run_batch()` **只并发模型调用**，预留与结算仍顺序进行。验收证据：`test_hive_and_commands.py` 11 例。",
        ),
        (
            "- [ ] **P1-2 导演裁决闭环**：导演创建持久 DecisionRequest；用户选项与 durable timer 竞争一次性落定，结果影响新输入版本并恢复章节。验收：收件箱深链完成真实裁决；重复、过期、两设备、默认时限竞争仅一个生效；刷新后选择仍影响生成。",
            "- [x] **P1-2 导演裁决闭环**（2026-09-08 完成）：`TakeDirection`/`ProseDirection` 新增 `request_decision`，导演可发起裁决；`works.create_decision()` 落持久请求并让作品/章节进入 `PENDING_DECISION`。`resolve_decision` 与 `sweep_expired_decisions`（每 30 秒随 worker 维护 tick 执行）共用 `_apply_decision()`：条件更新保证仅一方胜出，结果写入 `generation_context.decision_resolutions` 并 **递增 input_version**，旧方向调用键失效。裁决条件更新改用 ORM 表达式，不再依赖 Postgres 的 `NOW()`。验收证据：`test_decision_loop.py` 6 例（创建即等待、默认项校验、选择改变输入版本、竞争仅一方生效、到期默认落定、重复扫描不重复落定）。",
        ),
        (
            "- [ ] **P1-3 按卷记忆与路径终止**：替换最近 120 条事实近似，统一动态节点和当前卷上下文；实现最后节点完成后的结束或跨卷展开，避免重复生成末节点。验收：跨卷规则可追溯，废弃版不污染记忆，连续三章及卷边界推进正确。",
            "- [x] **P1-3 按卷记忆与路径终止**（2026-09-08 完成）：Canon 提交时用 `tag_facts()` 给事实打 `volume_no`/`chapter_no`；`_canon_facts()` 第一级改为按当前卷过滤（标签整体缺失时退回全量，避免静默清空上下文），前 1-2 卷仍走摘要。末节点完成后不再重复生成它：`story_complete` 落入上下文，`start_run` 据此把作品置为 `DONE` 并拒绝开新章；`_maybe_expand_volume()` 在末节点完成时也触发跨卷展开（不再只看 80% 完成度）。验收证据：`test_volume_memory.py` 6 例。",
        ),
        (
            "- [ ] **P1-4 产品旅程验收**：执行 §5 浏览器/网络/视口矩阵，补审核结果和投诉申诉结果留痕；验证用户无需逐场审核也能完成首章。",
            "- [x] **P1-4 后端留痕与首章旅程**（2026-09-08 完成）：新增 `works.resolve_moderation()` / `resolve_appeal()` 与对应 API（`POST .../moderation/{id}/resolve`、`.../appeal/resolve`），结论落库并留 `moderation.resolved`/`moderation.appeal_resolved`；作者不得给自己的案件下「通过」结论。`rule_hit` 纳入原因码白名单。首章旅程用例证明全程不进入 `AWAITING_INPUT`/`PENDING_DECISION` 即可 CANONIZED。验收证据：`test_journey_moderation.py` 7 例 + `test_direction.py::test_first_chapter_completes_without_per_scene_review`。**§5 浏览器/网络/视口矩阵仍为人工验收项，未执行。**",
        ),
        (
            "| 2026-09-08 | v6.6 | P0-4 落地：租约 owner/fencing token/有效期复核、调用窗口后回查数据库判定失效、`bump_input_version()` 使用户改意与路径变更作废旧方向产出、调用配置指纹入幂等键。小说域回归 107 例通过。 |",
            "| 2026-09-08 | v6.6 | P0-4 落地：租约 owner/fencing token/有效期复核、调用窗口后回查数据库判定失效、`bump_input_version()` 使用户改意与路径变更作废旧方向产出、调用配置指纹入幂等键。 |\n"
            "| 2026-09-08 | v6.7 | 第二批 P1-1~P1-4 落地：完整命令分发与绑定/产物校验、完整 manifest 留痕、Hive 路由（`domain/hive.py` + `run_batch` 只并发模型调用）、导演裁决闭环与到期默认、按卷记忆与末节点终止、审核/申诉结论留痕与首章旅程用例。小说域回归 138 例通过。 |",
        ),
    ],
)

patch(
    "Novel-Engine-Tech-Spec.md",
    [
        (
            """导演命令与上下文已部分接入（R1）：`domain/commands.py` 定义带版本与 fingerprint 的 DirectorCommand，`application/runtime.py` 校验阶段、输入版本、角色、预算、步数及局部前置条件。行动、表演观看和正文观看已使用命令校验；PLAN_SCENE、ASSEMBLE_CHAPTER、FINISH_CHAPTER、REQUEST_USER_DECISION 尚未完整进入统一命令分发。因此 Runtime 目前并非全部业务迁移的唯一入口，也未实现完整产物依赖校验。`domain/context.py` 已确定性编译角色/执笔者/导演上下文及来源、投影 hash，但运行记录未保存完整 manifest 绑定。Runtime 不校验创作质量，不替代独立审校。同节拍角色采样仍串行，Hive 的确定性路由契约尚待执行器落实。""",
            """导演命令与上下文已接入（R1，2026-09-08）：`domain/commands.py` 定义带版本与 fingerprint 的 DirectorCommand，`application/runtime.py` 校验阶段、输入版本、角色、预算、步数、**scene/take 绑定与产物绑定**。规划、行动、表演观看、正文观看、组章、完成与请求裁决全部走 Runtime 分发。`domain/context.py` 确定性编译角色/执笔者/导演上下文，运行记录保存完整 manifest（binding + sources + fingerprint）。

**Hive 只做局部执行器**：`domain/hive.py::route_beat()` 逐条核对 `known_by` 判定隔离，仅在「≥2 角色、互不泄漏、上下文不同」时启用；执行用 `CallBroker.run_batch()`，**只并发模型调用**，预留与结算顺序进行——会话不支持并发，并发只覆盖 HTTP 往返。Hive 不改变账本语义。Runtime 不校验创作质量，不替代独立审校。""",
        ),
        (
            """成章与产品边界（R2/M2）：Canon 事实仍用最近 120 条近似，尚未按卷分层；动态节点推进已接入，但末节点结束与基于完成度的跨卷展开未闭合。裁决收件箱、深链与提交接口已存在，尚无导演主动创建裁决并按用户/默认结果恢复生成的完整链路。审核扫描和投诉申诉入口不等于完整审核裁定。前端构建不等于移动端、断网恢复、无障碍和端到端旅程验收。""",
            """成章与产品边界（R2/M2，2026-09-08）：Canon 事实在提交时打 `volume_no`/`chapter_no`，取用时按当前卷过滤（标签整体缺失退回全量），前 1-2 卷走摘要；末节点完成后置 `story_complete` 并结束整本，跨卷展开在末节点完成时也触发，不再只看 80% 完成度。导演可发起持久裁决，用户提交与到期默认竞争落定，结果递增 input_version 并写入生成上下文。审核与申诉结论（`resolve_moderation`/`resolve_appeal`）已落库留痕，作者不得给自己的案件下「通过」结论。**§5 浏览器/网络/视口矩阵、移动端、断网恢复与无障碍仍为人工验收项，未执行。**""",
        ),
        (
            "| 2026-09-08 | v5.5 | §5 补调用配置指纹、租约复核须回查数据库、input_version 使旧方向产出作废；§14 同步 P0-4 已落地与剩余 P0-5 边界。 |",
            "| 2026-09-08 | v5.5 | §5 补调用配置指纹、租约复核须回查数据库、input_version 使旧方向产出作废；§14 同步 P0-4 已落地与剩余 P0-5 边界。 |\n"
            "| 2026-09-08 | v5.6 | §14 同步第二批 P1-1~P1-4：完整命令分发与绑定/产物校验、完整 manifest 留痕、Hive 只并发模型调用的局部执行器、按卷记忆与末节点终止、裁决闭环与审核/申诉结论留痕；标明旅程矩阵仍待人工验收。 |",
        ),
    ],
)

# Generation Protocol v1

状态：Frozen for P1  
日期：2026-07-18

## 目的

把 AppRequirement 转换为可审计、可重放且不能越界的文件变更。可复现性通过重放冻结 FileChangeSet 保证，不要求非确定模型再次生成逐字相同内容。

## 输入快照

GenerationPlan 必须绑定：

- goal_spec_id 与 hash；
- hypothesis_decision_id；
- requirement_revision_id 与 hash；
- capability_resolution_plan_id 与 hash；
- runtime_profile_id 与 hash；
- evidence_bundle_digest；
- generator、model、prompt 和 schema 版本；
- 预算、文件和字节上限；
- correlation_id。

任一输入变化都创建新 GenerationPlan。

## GenerationPlan

字段：

- id、version、status、input_digest；
- runtime_profile_ref；
- architecture_summary；
- component_plan；
- dependency_intents；
- planned_paths；
- verification_commands；
- acceptance_contract；
- generator_ref；
- budget；
- created_by、created_at。

状态：DRAFT → FROZEN → EXECUTING → COMPLETED 或 FAILED；FROZEN 后不可修改。

## FileChangeSet

P1 只允许完整文件操作，不允许模型直接提交任意 shell patch。

每项包含：

- relative_path；
- operation：CREATE、REPLACE、DELETE；
- content_artifact_uri；
- content_hash；
- expected_previous_hash；
- mode：普通文件或可执行文件；
- media_type；
- rationale。

初次空目录生成默认只允许 CREATE。增量修改必须提供 expected_previous_hash。禁止绝对路径、父目录跳转、符号链接、设备文件和保留目录。

## GenerationRun

状态：

REQUESTED → PLANNING → GENERATING → VALIDATING → COMMITTING → COMPLETED  
任意执行态 → FAILED 或 CANCELLED

保存 attempt、lease、heartbeat、progress、failure_code、模型用量和 change_set_digest。

## WorkspaceWriter

唯一有权落盘的可信原语。职责：

- 解析并验证规范化路径；
- 强制 workspace root；
- 拒绝 symlink 和 reparse point 逃逸；
- 限制文件数、单文件与总字节；
- 校验内容 hash 和 expected_previous_hash；
- 在临时目录应用；
- 生成 manifest；
- fsync 后原子提交；
- 失败清理临时目录；
- 不执行生成内容。

## WorkspaceSnapshot

权威数据是 manifest、hash 和 Artifact URI，不是主机路径。

字段：

- snapshot_id；
- generation_run_id；
- manifest_uri、manifest_hash；
- source_archive_uri、source_hash；
- file_count、total_bytes；
- runtime_profile_hash；
- created_at。

## 重放

重建使用冻结 FileChangeSet 和内容 Artifact。禁止为了“复现”再次调用模型。重放后 manifest hash 必须一致。

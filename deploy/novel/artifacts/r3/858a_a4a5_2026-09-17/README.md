# 858a ch3 a4/a5 证据包（R0）

日期：2026-09-17 监督复验；归档：2026-09-19  
work_id：`858ad0b6-8fd4-447e-a560-553237862565`

## 源码指纹（归档时本地工作树）

- git HEAD：`b97ac725e0af2ff50e68c190abde875feb99267f`（工作树有大量未提交改动，HEAD ≠ 完整运行态）
- `directing_script_loop.py` SHA256 前缀：`A98C9199677E`
- `chapter_review.py` SHA256 前缀：`B74272AC8D2C`
- `directing_contracts.py` SHA256 前缀：`67D9934E3591`

## 运行摘要（来自 supervise_529818.txt）

| attempt | 终态 | 关键数字 | 错误 |
|---|---|---|---|
| a4 | TERMINAL_FAILED / PRODUCE / VALIDATE | tok≈506701，calls=20，w=0 | `[front:redundant]` 硬停（章节核验硬失败） |
| a5 | TERMINAL_FAILED / REVIEW / DONE | tok≈431087，calls=17，w=2251 | 审校回退后「修复没有改变正文」类停机 |

## 证据分级

- **B**：本目录 `supervise_529818.txt`（监督轮询 + worker 警告摘录）
- **B**：`../evidence_858a_sup.json`（章状态/记忆，不能替代 a4/a5 完整 payload）
- **C**：「一定因修错场失败」——仍缺 scene_id、前后 hash、模型请求原文

## 干预说明

该轮使用监督脚本 regenerate，并曾取消僵尸 attempt=3；非纯产品入口「一次启动自动三章」。

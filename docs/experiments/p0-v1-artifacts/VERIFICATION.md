# P0#5 仓内可复核产物说明

生产目录 `/opt/regent/artifacts/experiments/p0-v1` 于 2026-07-30 同步入本目录，SHA-256 与 `docs/p0-completion-report.md` 一致：

| 文件 | SHA-256 |
|---|---|
| `raw-run-manifest.json` | `6a9b89a50942af96c581010a8c749185835f3dd21b68c68645a8b8c8230e2fcf` |
| `experiment-report.json` | `8ba5caad35b06895c0cd7e72606ef05d77f149284bc78450f1c8a974c832815b` |
| `README.md` | `156ee08dff5bd28d4098913dacdc712d448fe3f69ea32c8dce599be8b4507591` |

唯一 Product DecisionRecord：

- id: `ec17a72f-54cb-4771-89b0-70a7bd9490ef`
- decision: `STOP_GENERALIZATION`
- manifest: `0f64f746-9ec3-4409-acd4-93f4aff9eae4`
- runs: 270
- signature: 见 `experiment-report.json` → `decision.signature`

冻结任务集夹具：`docs/experiments/p0-task-set-v1.json`  
本地可复现评分路径：`tests/unit/application/test_p0_decision_record_artifacts.py`（`ExperimentService.freeze → record_run → finalize`，无 `hash%2` 桩）。

签名密钥仅在生产 secrets；仓内以产物摘要 + 签名字段作为可复核证据，不以本地重签替代。

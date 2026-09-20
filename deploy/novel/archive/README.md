# deploy/novel/archive

一次性远程补丁、诊断探针与历史变异脚本归档区。

- 勿在生产部署流程中再次执行 `patch_*`
- 正式回归以 `tests/unit/novel/` 为准
- 保留的生产入口仍在上级目录：`run_*.py`、`sync_tree.py`、`deploy_*.py`、`verify_deploy.py` 等

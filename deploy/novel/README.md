# deploy/novel

运维与远程探测入口。

- `_ssh.py`：SSH 助手（保留）
- `artifacts/`：可保留的样章与证据（如 `ch1_a41_frontgate_sample.txt`）
- `archive/`：历史一次性补丁，不进活跃运维入口
- **不要**在本目录堆积 `_tmp*` 探针；一次性脚本用完即删（见 `ops/repo_hygiene.py`）

## 服务模式（解耦后）

见 `docs/archive/regent-legacy-drain-checklist-2026-09-16.md`。

```text
REGENT_SERVICE_MODE=novel|legacy|combined
REGENT_LEGACY_ACCEPT_NEW=0|1
REGENT_EDITOR_REPAIR_MODE=off|shadow|auto
REGENT_EDITOR_REPAIR_AUTO_PERCENT=0..100
```

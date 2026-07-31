# Regent normative definitions

唯一规范源：[`REGENT-DEFINITION-1.0.txt`](REGENT-DEFINITION-1.0.txt)  
内容哈希：[`REGENT-DEFINITION-1.0.sha256`](REGENT-DEFINITION-1.0.sha256)

- 禁止在其他文档中复制 `DEFINITION_TEXT` 形成第二规范源。
- 变更必须新建 `REGENT-DEFINITION-2.0`（或新的产品身份），不得原地修改 1.0。
- CI：`tests/architecture/test_regent_definition_freeze.py` — ✅ **生效**

## 防漂移门禁状态（F-2 已闭环）

该门禁曾因基线文档改名而自身失效（L13-14 指向仅存于 `docs/archive/` 的 `-v2` 文件，永久抛 `FileNotFoundError`），使上述三条规则一度无人看守。

现已修复并加固：

- L13-14 指向 CURRENT 基线 `Regent-PRD.md` / `Regent-Technical-Spec.md`
- 新增 `test_freeze_guard_paths_exist`（L19-24）：路径缺失时给出明确断言信息，而非 `FileNotFoundError`

背景见 [`docs/doc-implementation-alignment-audit-2026-07-31.md`](../doc-implementation-alignment-audit-2026-07-31.md) F-2 与 §8。

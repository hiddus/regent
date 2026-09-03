# PPTX 源文件更新说明（2026-08-11）

> 性质：运维/文档说明（非产品承诺）

下列 JSX / HTML 幻灯片源已按定义 3.0 与审计口径更新（生产权限门禁、九属性、M6 CLAMPED 等）。**未重新导出二进制 `.pptx`**。

| 源 | 变更摘要 |
|---|---|
| `regent-pptx/pages/slide_05_constraints.jsx` 等 | 「多 Agent 非默认」→ 生产权限 / 现实影响门禁措辞 |
| `deliverables/regent-leader-pptx/pages/slide_07_definition.jsx` | 七属性 → 九属性（ATTRIBUTE_7 = 边界落在现实影响） |
| `deliverables/regent-leader-pptx/pages/slide_11_hive.jsx` | Gate 限于生产默认与现实权限 |
| `regent-pptx/Regent失败案例复盘.html` 等培训 HTML | M6 CLAMPED；沙箱 vs 生产权限区分 |

需要演示用 `.pptx` 时，从上述 JSX 源按既有 `regent-pptx` / `regent-leader-pptx` 导出流程重新生成。

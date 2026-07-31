# core/src/regent/capabilities_bootstrap

引导期能力声明 JSON，通过 `pyproject.toml` 的 `force-include` 打包进 wheel，由 Capability Pool 在引导时加载。

三个声明与 `capabilities/bootstrap/` 下的能力目录一一对应。其中 `delivery_review_v1` 与 `product_surface_v1` 由 `api/main.py` 的 lifespan 调用 `ensure_delivery_review_capability()` / `ensure_product_surface_capability()` 确保登记（**fail-open 启动、fail-closed 使用**）。

能力池的完整说明与范围边界见 [`capabilities/README.md`](../../../../capabilities/README.md)。

## 目录内容

文件：
- `allowlisted_http_source_v1.json`
- `delivery_review_v1.json`
- `product_surface_v1.json`

> 本 README 由目录实际内容生成，反映当前结构；如用途有变请同步更新。

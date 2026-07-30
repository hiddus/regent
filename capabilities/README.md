# Capabilities（认证能力池）

本目录存放 **Certified Capability Pool（可声明、可验证、可替换的通用能力）** 的引导声明与解析/沙箱运行器。能力不属于 Core Kernel，而是被 Core 在运行时按目标需求解析、验证后调用，从而把"出口、构建、评审"等通用能力从内核中解耦。

## 目录布局

- `bootstrap/` — 引导期即内置的能力声明，每个能力一个子目录，含 `capability.json`。
- `bootstrap/resolver/` — 能力解析器（`Dockerfile` + `main.py`），负责解析与验证能力清单。
- `bootstrap/sandbox/` — 依赖沙箱（`Dockerfile` + `main.py`），用于隔离构建，fail-closed。

## 当前引导能力

| 能力 | 说明 |
|---|---|
| `allowlisted-http-source-v1` | 认证的白名单 HTTP/RSS 证据连接器。仅抓取 Goal 授权 URL + 该能力认证默认 feed（TechCrunch / HN / Verge / 36氪 / 机器之心 / TechWeb 等），受平台出口策略约束，`fail_closed`。 |
| `delivery-review-v1` | 交付评审能力，由 `infrastructure/delivery_review_capability.py` 在启动时 `ensure_*` 注入。 |
| `product-surface-v1` | 产品界面能力，由 `infrastructure/product_surface_capability.py` 在启动时 `ensure_*` 注入。 |

## 与 Core 的关系

Core 启动时（见 `core/src/regent/api/main.py` 的 lifespan）会：

1. `RuntimeProfileService.seed_bootstrap()` 注入引导期运行时画像；
2. `ensure_delivery_review_capability()` / `ensure_product_surface_capability()` 确保对应能力已登记。

新增能力应遵循"声明 + 验证 + 可替换"的原则，并通过 `docs/contracts/` 中的能力契约（如 `evidence-acquisition-v1`、`dependency-sandbox-v1`）对齐接口。

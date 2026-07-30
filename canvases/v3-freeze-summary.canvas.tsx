import {
  Divider,
  Grid,
  H1,
  H2,
  H3,
  Stack,
  Stat,
  Table,
  Text,
  Callout,
  Timeline,
  type TimelineEvent,
} from "qoder/canvas";

const verificationRows = [
  ["G — Goal Engine", "PASS", "Goal 创建 + Spec 版本化"],
  ["C — Constraint Engine", "PASS", "ExecutionOrchestrator 约束检查"],
  ["V — Governance Engine", "PASS", "Permit + Audit + HumanTask"],
  ["R_t — Resource Engine", "PASS", "Capability + AgentSpec + ToolSpec"],
  ["S_t — State Engine", "PASS", "状态机 + Outbox + Artifact + Evidence"],
  ["O — Organization Engine", "PASS", "组织选择 + 动态重构 + 缺口恢复"],
  ["G0 — ExternalOperation", "PASS", "持久化外部操作模型"],
  ["P0 — 主链路", "PASS", "8 个核心服务全部可导入"],
  ["R_Acquire — 自组织获取", "PASS", "CapabilityAcquireService + ACQUIRE 步骤"],
  ["Core/App 分离", "WARN", "预期行为（尚无 apps 目录）"],
  ["Alembic 迁移链", "PASS", "head = 20260725_0027"],
];

const timelineEvents: TimelineEvent[] = [
  {
    date: "Step 1",
    title: "冻结 V3 需求文档",
    description:
      "Definition-v3 和 Architecture-v3 状态从 DRAFT 改为 CURRENT（2026-07-25 冻结）",
  },
  {
    date: "Step 2",
    title: "新增 ACQUIRE 能力获取服务",
    description:
      "capability_acquire_service.py — 下载能力包 → SHA-256 哈希校验 → 安全扫描 → 注册为 VERIFIED",
  },
  {
    date: "Step 3",
    title: "扩展能力补齐链路",
    description:
      "ResolutionMethod.ACQUIRE 枚举 + CapabilityGap.acquire_allowed 字段 + resolve() 逻辑",
  },
  {
    date: "Step 4",
    title: "集成到 DeliveryGapRecoveryService",
    description: "_apply_step() 新增 EscalationStep.ACQUIRE 处理分支",
  },
  {
    date: "Step 5",
    title: "Alembic 数据库迁移",
    description:
      "20260725_0027: capabilities 表新增 source_url / source_hash 字段",
  },
  {
    date: "Step 6",
    title: "单元测试",
    description:
      "21 个新测试（test_capability_acquire.py）+ 更新已有测试（33 项全部通过）",
  },
  {
    date: "Step 7",
    title: "部署验证",
    description:
      "部署至 118.31.171.159，健康检查通过，V3 六要素 + ACQUIRE 全部验证通过",
  },
];

const filesRows = [
  ["Regent-Definition-v3.md", "状态 DRAFT → CURRENT"],
  ["Regent-Architecture-v3.md", "状态 DRAFT → CURRENT"],
  ["capability_acquire_service.py", "新建（424 行）"],
  ["capability_resolution_service.py", "新增 ACQUIRE 枚举和逻辑"],
  ["capability_ladder.py", "新增 ACQUIRE 阶梯步骤"],
  ["delivery_gap_recovery.py", "集成 ACQUIRE 步骤"],
  ["infrastructure/models.py", "CapabilityModel +2 字段"],
  ["20260725_0027_*.py", "新建 Alembic 迁移"],
  ["test_capability_acquire.py", "新建（21 个测试）"],
  ["test_capability_ladder.py", "更新为 4 步阶梯"],
  ["test_delivery_gap_recovery.py", "更新 attempts 3→4"],
];

export default function V3FreezeSummary() {
  return (
    <Stack gap={20}>
      <H1>V3 需求冻结与自组织能力实现</H1>
      <Text tone="secondary">
        完成日期：2026-07-25 · 服务器：118.31.171.159 · Release:
        20260725-v3-frozen
      </Text>

      <Grid columns={4} gap={16}>
        <Stat value="10/11" label="V3 验证通过" tone="success" />
        <Stat value="21" label="新增单元测试" />
        <Stat value="7" label="实施步骤" />
        <Stat value="11" label="涉及文件" />
      </Grid>

      <Divider />

      <Callout tone="success">
        <H2>核心成果：ACQUIRE 自组织能力</H2>
        <Text>
          系统现在能够主动发现能力缺口，从授权 URL 下载能力包，在沙箱中验证（SHA-256
          哈希校验 + 安全扫描），并注册为 VERIFIED 能力。
        </Text>
        <Text tone="secondary" size="small">
          能力补齐链路：REUSE → CONFIGURE → COMPOSE → BUILD →{" "}
          <strong>ACQUIRE</strong> → REQUEST_HUMAN → BLOCK
        </Text>
      </Callout>

      <Divider />

      <H2>V3 六要素验证矩阵</H2>
      <Table
        headers={["要素", "状态", "验证内容"]}
        rows={verificationRows}
        rowTone={(row) =>
          row[1] === "PASS"
            ? "success"
            : row[1] === "WARN"
              ? "warning"
              : undefined
        }
      />

      <Divider />

      <H2>实施时间线</H2>
      <Timeline events={timelineEvents} />

      <Divider />

      <H2>涉及文件</H2>
      <Table headers={["文件", "变更"]} rows={filesRows} density="compact" />

      <Divider />

      <H2>安全约束</H2>
      <Grid columns={2} gap={12}>
        <Stack gap={4}>
          <H3>下载治理</H3>
          <Text size="small">
            通过 Permit + ExternalOperation 治理受控出口；来源 URL 必须在 Goal
            授权范围内
          </Text>
        </Stack>
        <Stack gap={4}>
          <H3>沙箱验证</H3>
          <Text size="small">
            SHA-256 内容哈希校验；检测 os.system / subprocess / exec / eval /
            socket 等危险模式
          </Text>
        </Stack>
      </Grid>

      <Text tone="secondary" size="small">
        所有代码已部署至生产服务器，迁移链在 head (20260725_0027)，4
        个容器正常运行。
      </Text>
    </Stack>
  );
}

import {
  Stack, Row, Grid, Divider, H1, H2, H3, Text, Tag, Stat,
  Table, Card, CardHeader, CardBody, Pill, Callout,
} from 'qoder/canvas';

const TASKS = [
  { id: 1, title: '后端 SSE 路由注册', file: 'core/src/regent/api/main.py', summary: '添加 events_router 注册，打通 SSE 实时推送基础设施' },
  { id: 2, title: '进度节点文案产品化', file: 'apps/regent-console/src/lib/progressNodes.ts', summary: '11 个阶段标题从技术语言改为产品语言；移除 eventTypes 暴露；facts 改为 highlights' },
  { id: 3, title: 'ProgressNodeCard 可折叠详情', file: 'apps/regent-console/src/components/ProgressNodeCard.tsx', summary: 'Workbuddy 风格折叠卡片：圆点 + 标题 + 状态标签 + 一句话结论；点击展开详情' },
  { id: 4, title: 'StageBar 产品化 + 进度条', file: 'apps/regent-console/src/components/Sidebar.tsx', summary: '移除原始枚举显示；中文阶段名 + 10 段进度条可视化；状态映射为产品语言' },
  { id: 5, title: '三栏布局 - 产物面板分离', file: 'App.tsx / ArtifactPanel.tsx / index.css', summary: 'CSS Grid 三栏 240px | flex-1 | 360px；新建 ArtifactPanel 含预览 iframe、下载、执行步骤' },
  { id: 6, title: 'SSE 实时推送接入', file: 'useWorkspace.ts / useSSE.ts', summary: 'SSE 连接 /events/stream；new_message 增量追加；指数退避重连；移除 3 秒轮询' },
  { id: 7, title: 'ConfirmationCard 产品化 + 视觉升级', file: 'ConfirmationCard.tsx / MessageList.tsx / index.css', summary: '约束标签化展示；成功标准 checklist 样式；移除 metadata 泄露；脉冲动画、渐变背景' },
];

const FILES_CHANGED = [
  { file: 'core/src/regent/api/main.py', change: '添加 events_router 注册' },
  { file: 'apps/regent-console/src/lib/progressNodes.ts', change: '重写文案 + 数据结构调整' },
  { file: 'apps/regent-console/src/components/ProgressNodeCard.tsx', change: '重构为可折叠卡片' },
  { file: 'apps/regent-console/src/components/Sidebar.tsx', change: 'StageBar 产品化 + 进度条' },
  { file: 'apps/regent-console/src/App.tsx', change: '三栏布局 + SSE 集成' },
  { file: 'apps/regent-console/src/hooks/useWorkspace.ts', change: 'SSE 集成，移除轮询' },
  { file: 'apps/regent-console/src/hooks/useSSE.ts', change: '增强重连逻辑' },
  { file: 'apps/regent-console/src/components/MessageList.tsx', change: '移除 metadata 泄露' },
  { file: 'apps/regent-console/src/components/ConfirmationCard.tsx', change: '产品化约束展示' },
  { file: 'apps/regent-console/src/components/ArtifactPanel.tsx', change: '新建产物面板' },
  { file: 'apps/regent-console/src/index.css', change: '视觉升级' },
];

const PROBLEMS_FIXED = [
  '技术语言泄露 - 进度节点不再显示内部系统状态',
  '原始 metadata 暴露 - facts 改为 highlights，只保留用户可理解要点',
  'StageBar 显示内部枚举 - 改为中文阶段名 + 进度条可视化',
  '对话与产物未分离 - 三栏布局，预览/下载独立产物面板',
  'SSE 未接入 - 实时推送替代 3 秒轮询',
  '进度节点不可折叠 - Workbuddy 风格可展开/折叠卡片',
];

export default function ConsoleRestructureReport() {
  return (
    <Stack gap={24} style={{ maxWidth: 960, margin: '0 auto', padding: '24px 0' }}>
      <Stack gap={8}>
        <H1>控制台全面对标重构</H1>
        <Text tone="secondary">Regent Console - 对标 Workbuddy / Codex，全面重构 Feed 流用户体验</Text>
      </Stack>
      <Divider />
      <Grid columns={4} gap={16}>
        <Stat value="7" label="任务完成" tone="success" />
        <Stat value="11" label="文件修改" />
        <Stat value="0" label="TS 编译错误" tone="success" />
        <Stat value="1094" label="移除旧 CSS 行数" tone="danger" />
      </Grid>
      <Divider />
      <Stack gap={12}>
        <H2>解决的 6 大体验问题</H2>
        <Stack gap={6}>
          {PROBLEMS_FIXED.map((p, i) => (
            <Row key={i} gap={10} align="start">
              <Pill tone="success" size="small">{i + 1}</Pill>
              <Text>{p}</Text>
            </Row>
          ))}
        </Stack>
      </Stack>
      <Divider />
      <Stack gap={12}>
        <H2>7 个任务完成情况</H2>
        {TASKS.map(t => (
          <Card key={t.id} size="default">
            <CardHeader>
              <Row gap={10} align="center" style={{ width: '100%' }}>
                <Tag tone="success">{'任务 ' + t.id}</Tag>
                <Text weight="semibold" style={{ flex: 1 }}>{t.title}</Text>
                <Tag tone="success">已完成</Tag>
              </Row>
            </CardHeader>
            <CardBody>
              <Stack gap={6}>
                <Text tone="secondary" size="small">
                  <Text as="span" tone="muted" size="small">文件: </Text>
                  <Text as="span" size="small">{t.file}</Text>
                </Text>
                <Text>{t.summary}</Text>
              </Stack>
            </CardBody>
          </Card>
        ))}
      </Stack>
      <Divider />
      <Stack gap={12}>
        <H2>进度节点文案对照</H2>
        <Table
          headers={['原始标题', '产品化标题', '示例结论']}
          rows={[
            ['理解目标', '理解你的想法', '已初步理解你的产品想法'],
            ['调研与证据', '市场调研', '已完成相关市场调研'],
            ['需求固化', '方案规划', '产品方案已规划完成'],
            ['能力规划', '技术准备', '技术方案已就绪'],
            ['生成应用', '应用生成', '应用代码已生成完成'],
            ['构建与校验', '质量检查', '质量检查已通过'],
            ['预览发布', '预览准备', '预览环境已就绪，可以体验'],
            ['交付验证', '最终验证', '所有验证已通过'],
            ['里程碑', '阶段完成', '当前阶段已达成'],
            ['需要你确认', '需要你确认', '有一个步骤需要你确认'],
            ['结论', '完成', '你的 App 已准备就绪'],
          ]}
        />
      </Stack>
      <Divider />
      <Stack gap={12}>
        <H2>涉及文件清单</H2>
        <Table headers={['文件', '改动类型']} rows={FILES_CHANGED.map(f => [f.file, f.change])} />
      </Stack>
      <Divider />
      <Stack gap={12}>
        <H2>新架构概览</H2>
        <Grid columns={3} gap={16}>
          <Card size="default">
            <CardHeader><H3>侧边栏 240px</H3></CardHeader>
            <CardBody><Stack gap={4}><Text size="small">项目列表 + 状态圆点</Text><Text size="small">StageBar 产品化状态 + 进度条</Text><Text size="small">快捷操作按钮</Text></Stack></CardBody>
          </Card>
          <Card size="default">
            <CardHeader><H3>对话区 flex-1</H3></CardHeader>
            <CardBody><Stack gap={4}><Text size="small">对话气泡 + 可折叠进度节点卡</Text><Text size="small">ConfirmationCard / TaskCard</Text><Text size="small">Composer 输入区</Text></Stack></CardBody>
          </Card>
          <Card size="default">
            <CardHeader><H3>产物面板 360px</H3></CardHeader>
            <CardBody><Stack gap={4}><Text size="small">预览 iframe 常驻展示</Text><Text size="small">下载按钮 + 状态摘要</Text><Text size="small">执行步骤 rail</Text></Stack></CardBody>
          </Card>
        </Grid>
      </Stack>
      <Divider />
      <Callout tone="success">
        <Text weight="semibold">重构完成</Text>
        <Text>所有 7 个任务已实现，TypeScript 编译零错误。技术数据泄露已全部消除，用户 Feed 流现在只展示产品化语言，对话与产物完全分离，SSE 实时推送替代轮询。</Text>
      </Callout>
    </Stack>
  );
}

<Slide style={{ width: '1280px', height: '720px', background: '#FFFFFF', fontFamily: 'PingFang SC, Microsoft YaHei, Helvetica Neue, sans-serif', overflow: 'hidden' }}>
  <Box style={{ height: 120, padding: '28px 64px 0 64px', flexDirection: 'row', justifyContent: 'space-between', alignItems: 'flex-start' }}>
    <Box>
      <Text style={{ fontSize: 16, color: '#64748B', letterSpacing: 1 }}>第三章 · 核心能力突破</Text>
      <Text style={{ fontSize: 34, fontWeight: 'bold', color: '#16335B', marginTop: 6 }}>Hive 多智能体架构</Text>
    </Box>
    <Text style={{ fontSize: 16, color: '#2E6DB4', fontWeight: 'bold', paddingTop: 10 }}>11</Text>
  </Box>

  <Box style={{ padding: '14px 64px 0 64px', flexDirection: 'row', gap: 36 }}>
    <Box style={{ flex: 1 }}>
      <Text style={{ fontSize: 18, color: '#1F2A37', lineHeight: 1.8 }}>面对复杂任务，Regent 不像「一个人硬扛」，而是<span style={{ color: '#16335B', fontWeight: 'bold' }}>按需组建一支 AI 团队</span>：由「产品经理」拆解需求，「开发」实现，「独立 QA」把关质量，彼此分工、互相校验。</Text>
      <Text style={{ fontSize: 18, color: '#1F2A37', lineHeight: 1.8, marginTop: 14 }}>多智能体在沙箱内默认可试；把某拓扑晋级为<span style={{ color: '#16335B', fontWeight: 'bold' }}>生产默认并扩大现实权限</span>才需要同预算对照与净收益证据——系统如实比较「单兵」与「团队」，<span style={{ color: '#D7263D', fontWeight: 'bold' }}>绝不盲目堆 Agent</span>。</Text>
    </Box>
    <Box style={{ flex: 1, background: '#F5F8FC', borderRadius: 14, padding: '22px 24px', border: '1px solid #E2E8F0' }}>
      <Text style={{ fontSize: 17, fontWeight: 'bold', color: '#16335B', marginBottom: 14, textAlign: 'center' }}>一个任务的 AI 团队</Text>
      {[
        ['PM', '产品经理', '拆解目标、定标准、把关', '#16335B'],
        ['DEV', '开发', '生成与构建应用', '#2E6DB4'],
        ['QA', '独立质检', '独立验证、不放松', '#D7263D'],
      ].map(([tag, role, d, c], i) => (
        <Box key={tag}>
          <Box style={{ flexDirection: 'row', alignItems: 'center', gap: 14, background: '#FFFFFF', borderRadius: 12, padding: '14px 18px', border: '1px solid #E2E8F0' }}>
            <Box style={{ width: 54, height: 40, borderRadius: 9, background: c, alignItems: 'center', justifyContent: 'center' }}>
              <Text style={{ fontSize: 15, fontWeight: 'bold', color: '#FFFFFF' }}>{tag}</Text>
            </Box>
            <Box>
              <Text style={{ fontSize: 18, fontWeight: 'bold', color: '#1F2A37' }}>{role}</Text>
              <Text style={{ fontSize: 14, color: '#64748B' }}>{d}</Text>
            </Box>
          </Box>
          {i < 2 && <Text style={{ fontSize: 18, color: '#94A3B8', textAlign: 'center', margin: '6px 0' }}>↓ 分工协作 · 互相校验</Text>}
        </Box>
      ))}
    </Box>
  </Box>

  <Box style={{ height: 60, padding: '0 64px', flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', borderTop: '1px solid #E2E8F0', position: 'absolute', bottom: 0, left: 0, right: 0 }}>
    <Text style={{ fontSize: 14, color: '#64748B' }}>Regent 系统介绍 · 树米科技</Text>
    <Text style={{ fontSize: 14, color: '#64748B' }}>11 / 20</Text>
  </Box>
</Slide>

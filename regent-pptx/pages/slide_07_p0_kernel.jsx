<Slide style={{ width: '1280px', height: '720px', background: '#FFFFFF', fontFamily: "'PingFang SC','Microsoft YaHei','Helvetica Neue',sans-serif", overflow: 'hidden' }}>
  <Box style={{ height: 120, padding: '28px 64px 0 64px', flexDirection: 'row', justifyContent: 'space-between', alignItems: 'flex-start' }}>
    <Box>
      <Text style={{ fontSize: 16, color: '#64748B', letterSpacing: 1 }}>第二章 · 各阶段框架</Text>
      <Text style={{ fontSize: 34, fontWeight: 'bold', color: '#1E293B', marginTop: 6 }}>P0 可靠内核框架</Text>
    </Box>
    <Text style={{ fontSize: 16, color: '#3B82F6', fontWeight: 'bold', paddingTop: 10 }}>07</Text>
  </Box>

  <Box style={{ height: 540, padding: '6px 64px 0 64px', flexDirection: 'row', gap: 44 }}>
    <Box style={{ flex: 1.15, background: '#F1F5F9', borderRadius: 18, padding: '26px 24px', justifyContent: 'center' }}>
      <Text style={{ fontSize: 16, color: '#64748B', fontWeight: 'bold', letterSpacing: 1, marginBottom: 14 }}>内核四件套（P0）</Text>
      <Box style={{ flexDirection: 'row', alignItems: 'center', gap: 14, background: '#FFFFFF', borderRadius: 10, padding: '14px 18px', borderLeft: '5px solid #3B82F6' }}>
        <Text style={{ fontSize: 19, fontWeight: 'bold', color: '#1E293B' }}>目标状态机</Text>
        <Text style={{ fontSize: 14, color: '#64748B' }}>GoalStateMachine</Text>
      </Box>
      <Box style={{ alignItems: 'center' }}><FAIcon name='arrow-down' style={{ fill: '#94A3B8', width: 22, height: 22 }} /></Box>
      <Box style={{ flexDirection: 'row', alignItems: 'center', gap: 14, background: '#FFFFFF', borderRadius: 10, padding: '14px 18px', borderLeft: '5px solid #06B6D4' }}>
        <Text style={{ fontSize: 19, fontWeight: 'bold', color: '#1E293B' }}>执行许可</Text>
        <Text style={{ fontSize: 14, color: '#64748B' }}>ExecutionPermit</Text>
      </Box>
      <Box style={{ alignItems: 'center' }}><FAIcon name='arrow-down' style={{ fill: '#94A3B8', width: 22, height: 22 }} /></Box>
      <Box style={{ flexDirection: 'row', alignItems: 'center', gap: 14, background: '#FFFFFF', borderRadius: 10, padding: '14px 18px', borderLeft: '5px solid #3B82F6' }}>
        <Text style={{ fontSize: 19, fontWeight: 'bold', color: '#1E293B' }}>上下文装配</Text>
        <Text style={{ fontSize: 14, color: '#64748B' }}>ContextAssembly</Text>
      </Box>
      <Box style={{ alignItems: 'center' }}><FAIcon name='arrow-down' style={{ fill: '#94A3B8', width: 22, height: 22 }} /></Box>
      <Box style={{ flexDirection: 'row', alignItems: 'center', gap: 14, background: '#FFFFFF', borderRadius: 10, padding: '14px 18px', borderLeft: '5px solid #06B6D4' }}>
        <Text style={{ fontSize: 19, fontWeight: 'bold', color: '#1E293B' }}>自校验</Text>
        <Text style={{ fontSize: 14, color: '#64748B' }}>SelfReview</Text>
      </Box>
    </Box>

    <Box style={{ flex: 1, justifyContent: 'center', gap: 18 }}>
      <Text style={{ fontSize: 22, fontWeight: 'bold', color: '#1E293B', lineHeight: 1.5 }}>这是不可再拆的最小内核</Text>
      <Text style={{ fontSize: 19, color: '#475569', lineHeight: 1.85 }}>
        它们本该串成一条「目标进来、应用出去」的主循环。
      </Text>
      <Box style={{ flexDirection: 'row', alignItems: 'center', gap: 12 }}>
        <FAIcon name='exclamation-circle' style={{ fill: '#EF4444', width: 24, height: 24 }} />
        <Text style={{ fontSize: 19, color: '#1E293B', fontWeight: 'bold' }}>但四个都被定义了，却没闭合</Text>
      </Box>
      <Text style={{ fontSize: 17, color: '#64748B', lineHeight: 1.8 }}>
        设计文档里它们各就各位；现实里，主循环从未端到端跑通一次。
      </Text>
    </Box>
  </Box>

  <Box style={{ height: 60, padding: '0 64px', flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', borderTop: '1px solid #E2E8F0' }}>
    <Text style={{ fontSize: 14, color: '#64748B' }}>Regent 失败案例复盘</Text>
    <Text style={{ fontSize: 14, color: '#64748B' }}>07 / 20</Text>
  </Box>
</Slide>

<Slide style={{ width: '1280px', height: '720px', background: '#FFFFFF', fontFamily: "'PingFang SC','Microsoft YaHei','Helvetica Neue',sans-serif", overflow: 'hidden' }}>
  <Box style={{ height: 120, padding: '28px 64px 0 64px', flexDirection: 'row', justifyContent: 'space-between', alignItems: 'flex-start' }}>
    <Box>
      <Text style={{ fontSize: 16, color: '#64748B', letterSpacing: 1 }}>第四章 · 失败原因</Text>
      <Text style={{ fontSize: 34, fontWeight: 'bold', color: '#1E293B', marginTop: 6 }}>五大失败根因</Text>
    </Box>
    <Text style={{ fontSize: 16, color: '#EF4444', fontWeight: 'bold', paddingTop: 10 }}>15</Text>
  </Box>

  <Box style={{ height: 540, padding: '8px 64px 0 64px', justifyContent: 'space-between' }}>
    <Box style={{ flexDirection: 'row', alignItems: 'center', gap: 20, background: '#FEF2F2', borderRadius: 14, padding: '16px 22px' }}>
      <Text style={{ fontSize: 32, fontWeight: 'bold', color: '#EF4444', width: 44 }}>A</Text>
      <Box>
        <Text style={{ fontSize: 21, fontWeight: 'bold', color: '#1E293B' }}>单 Agent 主循环从未闭合</Text>
        <Text style={{ fontSize: 16, color: '#64748B' }}>目标→计划→执行→校验→交付，从未端到端跑通一次</Text>
      </Box>
    </Box>
    <Box style={{ flexDirection: 'row', alignItems: 'center', gap: 20, background: '#FEF2F2', borderRadius: 14, padding: '16px 22px' }}>
      <Text style={{ fontSize: 32, fontWeight: 'bold', color: '#EF4444', width: 44 }}>B</Text>
      <Box>
        <Text style={{ fontSize: 21, fontWeight: 'bold', color: '#1E293B' }}>把控制流当成了能力</Text>
        <Text style={{ fontSize: 16, color: '#64748B' }}>调度、门禁、许可写了很多，真正交付的能力为零</Text>
      </Box>
    </Box>
    <Box style={{ flexDirection: 'row', alignItems: 'center', gap: 20, background: '#FEF2F2', borderRadius: 14, padding: '16px 22px' }}>
      <Text style={{ fontSize: 32, fontWeight: 'bold', color: '#EF4444', width: 44 }}>C</Text>
      <Box>
        <Text style={{ fontSize: 21, fontWeight: 'bold', color: '#1E293B' }}>工程卫生缺失</Text>
        <Text style={{ fontSize: 16, color: '#64748B' }}>未跟踪文件、schema 漂移、魔法字符串、源码即断言</Text>
      </Box>
    </Box>
    <Box style={{ flexDirection: 'row', alignItems: 'center', gap: 20, background: '#FEF2F2', borderRadius: 14, padding: '16px 22px' }}>
      <Text style={{ fontSize: 32, fontWeight: 'bold', color: '#EF4444', width: 44 }}>D</Text>
      <Box>
        <Text style={{ fontSize: 21, fontWeight: 'bold', color: '#1E293B' }}>优先级倒置</Text>
        <Text style={{ fontSize: 16, color: '#64748B' }}>单 Agent 闭环未稳，就把投入重心压到多 Agent 生产扩权</Text>
      </Box>
    </Box>
    <Box style={{ flexDirection: 'row', alignItems: 'center', gap: 20, background: '#FEF2F2', borderRadius: 14, padding: '16px 22px' }}>
      <Text style={{ fontSize: 32, fontWeight: 'bold', color: '#EF4444', width: 44 }}>E</Text>
      <Box>
        <Text style={{ fontSize: 21, fontWeight: 'bold', color: '#1E293B' }}>元工作替代了真实工作</Text>
        <Text style={{ fontSize: 16, color: '#64748B' }}>fork 门禁、4400 行编排器、ops 探针，替代了交付</Text>
      </Box>
    </Box>
  </Box>

  <Box style={{ height: 60, padding: '0 64px', flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', borderTop: '1px solid #E2E8F0' }}>
    <Text style={{ fontSize: 14, color: '#64748B' }}>Regent 失败案例复盘</Text>
    <Text style={{ fontSize: 14, color: '#64748B' }}>15 / 20</Text>
  </Box>
</Slide>

<Slide style={{ width: '1280px', height: '720px', background: '#FFFFFF', fontFamily: "'PingFang SC','Microsoft YaHei','Helvetica Neue',sans-serif", overflow: 'hidden' }}>
  <Box style={{ height: 120, padding: '28px 64px 0 64px', flexDirection: 'row', justifyContent: 'space-between', alignItems: 'flex-start' }}>
    <Box>
      <Text style={{ fontSize: 16, color: '#64748B', letterSpacing: 1 }}>第二章 · 系统定义</Text>
      <Text style={{ fontSize: 34, fontWeight: 'bold', color: '#16335B', marginTop: 6 }}>核心机制 · 一条可追溯、可验证的闭环</Text>
    </Box>
    <Text style={{ fontSize: 16, color: '#2E6DB4', fontWeight: 'bold', paddingTop: 10 }}>08</Text>
  </Box>

  <Box style={{ padding: '24px 64px 0 64px', flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 8 }}>
    {[
      ['目标', '一句话需求'],
      ['计划', '拆解可执行步骤'],
      ['能力', '补齐所需能力'],
      ['构建', '生成真实应用'],
      ['预览', '可运行可试用'],
      ['观测', '采集真实使用'],
      ['决策', '看证据下结论'],
    ].map(([t, d], i) => (
      <Box key={i} style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
        <Box style={{ background: 'linear-gradient(135deg,#16335B,#2E6DB4)', borderRadius: 12, padding: '16px 16px', minWidth: 92, alignItems: 'center' }}>
          <Text style={{ fontSize: 19, fontWeight: 'bold', color: '#FFFFFF' }}>{t}</Text>
          <Text style={{ fontSize: 12.5, color: '#C9DEF6', marginTop: 4, textAlign: 'center' }}>{d}</Text>
        </Box>
        {i < 6 && <Text style={{ fontSize: 24, color: '#94A3B8', fontWeight: 'bold' }}>→</Text>}
      </Box>
    ))}
  </Box>

  <Box style={{ padding: '22px 64px 0 64px', flexDirection: 'row', gap: 16, justifyContent: 'center' }}>
    <Box style={{ background: '#2E6DB4', borderRadius: 30, padding: '12px 28px' }}><Text style={{ fontSize: 16, color: '#FFFFFF', fontWeight: 'bold' }}>✓ 继续 CONTINUE</Text></Box>
    <Box style={{ background: '#D7263D', borderRadius: 30, padding: '12px 28px' }}><Text style={{ fontSize: 16, color: '#FFFFFF', fontWeight: 'bold' }}>↻ 改进 REVISE</Text></Box>
    <Box style={{ background: '#16335B', borderRadius: 30, padding: '12px 28px' }}><Text style={{ fontSize: 16, color: '#FFFFFF', fontWeight: 'bold' }}>■ 停止 STOP</Text></Box>
  </Box>

  <Text style={{ padding: '18px 64px 0 64px', fontSize: 17, color: '#64748B', textAlign: 'center', lineHeight: 1.6 }}>每一环都产出证据；真实使用数据驱动“继续 / 改进 / 停止”决策，并回流到计划与构建，形成闭环。</Text>

  <Box style={{ height: 60, padding: '0 64px', flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', borderTop: '1px solid #E2E8F0', position: 'absolute', bottom: 0, left: 0, right: 0 }}>
    <Text style={{ fontSize: 14, color: '#64748B' }}>Regent 系统介绍 · 树米科技</Text>
    <Text style={{ fontSize: 14, color: '#64748B' }}>08 / 20</Text>
  </Box>
</Slide>

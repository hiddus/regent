<Slide style={{ width: '1280px', height: '720px', background: '#FFFFFF', fontFamily: "'PingFang SC','Microsoft YaHei','Helvetica Neue',sans-serif", overflow: 'hidden' }}>
  <Box style={{ height: 120, padding: '28px 64px 0 64px', flexDirection: 'row', justifyContent: 'space-between', alignItems: 'flex-start' }}>
    <Box>
      <Text style={{ fontSize: 16, color: '#64748B', letterSpacing: 1 }}>第五章 · 落地场景</Text>
      <Text style={{ fontSize: 34, fontWeight: 'bold', color: '#16335B', marginTop: 6 }}>智慧城市 · 长期运营型中枢</Text>
    </Box>
    <Text style={{ fontSize: 16, color: '#2E6DB4', fontWeight: 'bold', paddingTop: 10 }}>16</Text>
  </Box>

  <Box style={{ padding: '16px 64px 0 64px', flexDirection: 'row', gap: 36, alignItems: 'center' }}>
    <Box style={{ flex: '0 0 240px', height: 360, borderRadius: 16, background: 'linear-gradient(135deg,#16335B,#2E6DB4)', alignItems: 'center', justifyContent: 'center' }}>
      <Text style={{ fontSize: 96 }}>🏙</Text>
      <Text style={{ fontSize: 22, color: '#FFFFFF', fontWeight: 'bold', marginTop: 10 }}>智慧城市</Text>
    </Box>
    <Box style={{ flex: 1, gap: 14 }}>
      {[
        ['城市运行监测', '汇聚城市运行数据，异常自动预警，治理从“被动响应”变“主动发现”。'],
        ['民生诉求闭环', '自动聚类群众反馈、标红风险信号，件件有回应、事事有闭环。'],
        ['政务智能问答', '政策条文问答、文书模板填表、合规自查，群众“问得清、办得顺”。'],
        ['领导数据看板', '用自然语言生成图表与解读，决策有数据、有依据。'],
      ].map(([t, d], i) => (
        <Box key={i} style={{ flexDirection: 'row', gap: 14, alignItems: 'flex-start', background: '#F5F8FC', borderRadius: 12, padding: '14px 18px', borderLeft: '5px solid #16335B' }}>
          <Text style={{ fontSize: 20 }}>{'①②③④'[i]}</Text>
          <Box>
            <Text style={{ fontSize: 19, fontWeight: 'bold', color: '#1F2A37' }}>{t}</Text>
            <Text style={{ fontSize: 15, color: '#64748B', marginTop: 4, lineHeight: 1.6 }}>{d}</Text>
          </Box>
        </Box>
      ))}
      <Text style={{ fontSize: 15, color: '#D7263D', fontWeight: 'bold', marginTop: 2 }}>关键点：不是“交付一个系统就结束”，而是持续运营、持续迭代。</Text>
    </Box>
  </Box>

  <Box style={{ height: 60, padding: '0 64px', flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', borderTop: '1px solid #E2E8F0', position: 'absolute', bottom: 0, left: 0, right: 0 }}>
    <Text style={{ fontSize: 14, color: '#64748B' }}>Regent 系统介绍 · 树米科技</Text>
    <Text style={{ fontSize: 14, color: '#64748B' }}>16 / 20</Text>
  </Box>
</Slide>

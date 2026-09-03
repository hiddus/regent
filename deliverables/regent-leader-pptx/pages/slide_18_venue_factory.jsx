<Slide style={{ width: '1280px', height: '720px', background: '#FFFFFF', fontFamily: "'PingFang SC','Microsoft YaHei','Helvetica Neue',sans-serif", overflow: 'hidden' }}>
  <Box style={{ height: 120, padding: '28px 64px 0 64px', flexDirection: 'row', justifyContent: 'space-between', alignItems: 'flex-start' }}>
    <Box>
      <Text style={{ fontSize: 16, color: '#64748B', letterSpacing: 1 }}>第五章 · 落地场景</Text>
      <Text style={{ fontSize: 34, fontWeight: 'bold', color: '#16335B', marginTop: 6 }}>智慧场馆 & 智能工厂</Text>
    </Box>
    <Text style={{ fontSize: 16, color: '#2E6DB4', fontWeight: 'bold', paddingTop: 10 }}>18</Text>
  </Box>

  <Box style={{ padding: '18px 64px 0 64px', flexDirection: 'row', gap: 28 }}>
    <Box style={{ flex: 1, background: '#F5F8FC', borderRadius: 14, padding: '20px 24px', borderTop: '5px solid #D7263D' }}>
      <Text style={{ fontSize: 24, fontWeight: 'bold', color: '#D7263D', marginBottom: 14 }}>🏟 智慧场馆 · 运营中枢</Text>
      {[
        ['运营中枢', '活动、票务、人流一体化调度，一屏掌控。'],
        ['安防与调度', '异常自动预警，安保力量智能调配。'],
        ['观众服务', '智能导引、无障碍服务，体验更顺畅。'],
        ['能耗与设施', '设备与能耗监测，降本增效、绿色运营。'],
      ].map(([t, d], i) => (
        <Box key={i} style={{ flexDirection: 'row', gap: 12, alignItems: 'flex-start', padding: '8px 0', borderBottom: i < 3 ? '1px dashed #DCE5F0' : 'none' }}>
          <Text style={{ fontSize: 18, fontWeight: 'bold', color: '#1F2A37', width: 96, flex: '0 0 96px' }}>{t}</Text>
          <Text style={{ fontSize: 15, color: '#64748B', lineHeight: 1.55 }}>{d}</Text>
        </Box>
      ))}
    </Box>
    <Box style={{ flex: 1, background: '#F5F8FC', borderRadius: 14, padding: '20px 24px', borderTop: '5px solid #1F6F5C' }}>
      <Text style={{ fontSize: 24, fontWeight: 'bold', color: '#1F6F5C', marginBottom: 14 }}>🏭 智能工厂 · 生产大脑</Text>
      {[
        ['生产数据看板', '关键指标实时可视，异常自动告警。'],
        ['质检与排产', '辅助质检与排产，提升良率与交付准时率。'],
        ['预测性维护', '设备健康预测，减少非计划停机。'],
        ['供应链协同', '需求与库存联动，降本提质、韧性更强。'],
      ].map(([t, d], i) => (
        <Box key={i} style={{ flexDirection: 'row', gap: 12, alignItems: 'flex-start', padding: '8px 0', borderBottom: i < 3 ? '1px dashed #DCE5F0' : 'none' }}>
          <Text style={{ fontSize: 18, fontWeight: 'bold', color: '#1F2A37', width: 96, flex: '0 0 96px' }}>{t}</Text>
          <Text style={{ fontSize: 15, color: '#64748B', lineHeight: 1.55 }}>{d}</Text>
        </Box>
      ))}
    </Box>
  </Box>

  <Box style={{ height: 60, padding: '0 64px', flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', borderTop: '1px solid #E2E8F0', position: 'absolute', bottom: 0, left: 0, right: 0 }}>
    <Text style={{ fontSize: 14, color: '#64748B' }}>Regent 系统介绍 · 树米科技</Text>
    <Text style={{ fontSize: 14, color: '#64748B' }}>18 / 20</Text>
  </Box>
</Slide>

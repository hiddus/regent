<Slide style={{ width: '1280px', height: '720px', background: '#FFFFFF', fontFamily: "'PingFang SC','Microsoft YaHei','Helvetica Neue',sans-serif", overflow: 'hidden' }}>
  <Box style={{ height: 120, padding: '28px 64px 0 64px', flexDirection: 'row', justifyContent: 'space-between', alignItems: 'flex-start' }}>
    <Box>
      <Text style={{ fontSize: 16, color: '#64748B', letterSpacing: 1 }}>第五章 · 落地场景</Text>
      <Text style={{ fontSize: 34, fontWeight: 'bold', color: '#16335B', marginTop: 6 }}>四类可持久运营经营的项目</Text>
    </Box>
    <Text style={{ fontSize: 16, color: '#2E6DB4', fontWeight: 'bold', paddingTop: 10 }}>15</Text>
  </Box>

  <Box style={{ padding: '20px 64px 0 64px', flexDirection: 'row', flexWrap: 'wrap', gap: 18 }}>
    {[
      ['🏙', '智慧城市', '城市运行监测、民生诉求闭环、政务智能问答、领导数据看板——持续运营，越用越准。', '#16335B'],
      ['🏞', '智慧文旅', '智能导览、客流与舆情监测、内容营销，讲好本地故事，带动消费。', '#2E6DB4'],
      ['🏟', '智慧场馆', '运营中枢、安防与调度、观众服务、能耗与设施管理，一站式运营。', '#D7263D'],
      ['🏭', '智能工厂', '生产数据看板、质检与排产辅助、设备预测性维护、供应链协同。', '#1F6F5C'],
    ].map(([ic, t, d, c], i) => (
      <Box key={i} style={{ width: 'calc(50% - 9px)', background: '#F5F8FC', border: '1px solid #E2E8F0', borderRadius: 14, padding: '22px 24px', borderTop: '5px solid ' + c }}>
        <Box style={{ flexDirection: 'row', alignItems: 'center', gap: 14 }}>
          <Text style={{ fontSize: 34 }}>{ic}</Text>
          <Text style={{ fontSize: 24, fontWeight: 'bold', color: c }}>{t}</Text>
        </Box>
        <Text style={{ fontSize: 15.5, color: '#64748B', marginTop: 12, lineHeight: 1.7 }}>{d}</Text>
      </Box>
    ))}
  </Box>

  <Box style={{ height: 60, padding: '0 64px', flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', borderTop: '1px solid #E2E8F0', position: 'absolute', bottom: 0, left: 0, right: 0 }}>
    <Text style={{ fontSize: 14, color: '#64748B' }}>Regent 系统介绍 · 树米科技</Text>
    <Text style={{ fontSize: 14, color: '#64748B' }}>15 / 20</Text>
  </Box>
</Slide>

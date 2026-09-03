<Slide style={{ width: '1280px', height: '720px', background: '#FFFFFF', fontFamily: "'PingFang SC','Microsoft YaHei','Helvetica Neue',sans-serif", overflow: 'hidden' }}>
  <Box style={{ height: 120, padding: '28px 64px 0 64px', flexDirection: 'row', justifyContent: 'space-between', alignItems: 'flex-start' }}>
    <Box>
      <Text style={{ fontSize: 16, color: '#64748B', letterSpacing: 1 }}>第三章 · 框架演变</Text>
      <Text style={{ fontSize: 34, fontWeight: 'bold', color: '#1E293B', marginTop: 6 }}>演变洞察：复杂度涨，交付力跌</Text>
    </Box>
    <Text style={{ fontSize: 16, color: '#3B82F6', fontWeight: 'bold', paddingTop: 10 }}>12</Text>
  </Box>

  <Box style={{ height: 540, padding: '10px 64px 0 64px', flexDirection: 'row', gap: 40 }}>
    <Box style={{ width: 360, justifyContent: 'center', gap: 18 }}>
      <Text style={{ fontSize: 20, color: '#64748B', lineHeight: 1.6 }}>最刺眼的一个数字：</Text>
      <Text style={{ fontSize: 96, fontWeight: 'bold', color: '#EF4444', lineHeight: 1 }}>32<Text style={{ fontSize: 34, color: '#64748B' }}> 天</Text></Text>
      <Text style={{ fontSize: 19, color: '#1E293B', lineHeight: 1.7 }}>
        连续 <span style={{ color: '#EF4444', fontWeight: 'bold' }}>32 天</span> 没有一项用户可见的进展，<br />而框架文档却在持续变厚。
      </Text>
      <Box style={{ flexDirection: 'row', alignItems: 'center', gap: 10, marginTop: 6 }}>
        <FAIcon name='arrow-down' style={{ fill: '#EF4444', width: 26, height: 26 }} />
        <Text style={{ fontSize: 19, color: '#EF4444', fontWeight: 'bold' }}>交付力，反而更弱了</Text>
      </Box>
    </Box>

    <Box style={{ flex: 1, background: '#F1F5F9', borderRadius: 18, padding: '20px 24px', justifyContent: 'center' }}>
      <Chart
        style={{ width: '100%', height: 420 }}
        chartType='barChart'
        barDirection='column'
        grouping='clustered'
        title='各阶段：架构复杂度 vs 可交付能力'
        titleColor='#1E293B'
        showLegend={true}
        showDataLabels={true}
        legendColor='#64748B'
        axisColor='#94A3B8'
        dataLabelColor='#1E293B'
        colors={['#3B82F6', '#EF4444']}
        background='#F1F5F9'
        data={[
          ['阶段', '架构复杂度', '可交付能力'],
          ['P0 内核', 20, 80],
          ['P1 闭环', 45, 70],
          ['P2 治理', 70, 55],
          ['P3 测量', 85, 45],
          ['P4 末期', 96, 28],
        ]}
      />
    </Box>
  </Box>

  <Box style={{ height: 60, padding: '0 64px', flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', borderTop: '1px solid #E2E8F0' }}>
    <Text style={{ fontSize: 14, color: '#64748B' }}>Regent 失败案例复盘</Text>
    <Text style={{ fontSize: 14, color: '#64748B' }}>12 / 20</Text>
  </Box>
</Slide>

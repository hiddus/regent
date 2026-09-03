<Slide style={{ width: '1280px', height: '720px', background: '#FFFFFF', fontFamily: "'PingFang SC','Microsoft YaHei','Helvetica Neue',sans-serif", overflow: 'hidden' }}>
  <Box style={{ height: 120, padding: '28px 64px 0 64px', flexDirection: 'row', justifyContent: 'space-between', alignItems: 'flex-start' }}>
    <Box>
      <Text style={{ fontSize: 16, color: '#64748B', letterSpacing: 1 }}>第二章 · 各阶段框架</Text>
      <Text style={{ fontSize: 34, fontWeight: 'bold', color: '#1E293B', marginTop: 6 }}>P1 运营闭环 + 验收矩阵</Text>
    </Box>
    <Text style={{ fontSize: 16, color: '#3B82F6', fontWeight: 'bold', paddingTop: 10 }}>08</Text>
  </Box>

  <Box style={{ height: 200, padding: '6px 64px 0 64px', flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 10 }}>
    {['目标','计划','执行','自校验','交付'].map((t, i) => (
      <Box key={i} style={{ flexDirection: 'row', alignItems: 'center', gap: 10 }}>
        <Box style={{ background: 'linear-gradient(135deg,#3B82F6,#06B6D4)', borderRadius: 12, padding: '18px 22px', minWidth: 96, alignItems: 'center' }}>
          <Text style={{ fontSize: 20, fontWeight: 'bold', color: '#FFFFFF' }}>{t}</Text>
        </Box>
        {i < 4 && <FAIcon name='arrow-right' style={{ fill: '#94A3B8', width: 26, height: 26 }} />}
      </Box>
    ))}
  </Box>

  <Box style={{ height: 340, padding: '6px 64px 0 64px' }}>
    <Table
      style={{ width: '100%', height: '100%' }}
      defaultTextStyle={{ fontSize: 17, textAlign: 'left', color: '#1E293B' }}
      defaultCellStyle={{
        border: { left: { width: 1, color: '#E2E8F0' }, right: { width: 1, color: '#E2E8F0' }, top: { width: 1, color: '#E2E8F0' }, bottom: { width: 1, color: '#E2E8F0' } },
        padding: 13,
      }}
      cells={[
        [
          { text: '验收维度', textStyle: { bold: true, color: '#FFFFFF', fontSize: 18 }, cellStyle: { background: { color: '#3B82F6' } } },
          { text: '验收标准', textStyle: { bold: true, color: '#FFFFFF', fontSize: 18 }, cellStyle: { background: { color: '#3B82F6' } } },
          { text: '实际状态', textStyle: { bold: true, color: '#FFFFFF', fontSize: 18 }, cellStyle: { background: { color: '#3B82F6' } } },
        ],
        ['单 Agent 闭环', '端到端跑通至少一个目标', { text: '未达成', textStyle: { color: '#EF4444', bold: true } }],
        ['失败可终态', '卡死任务能标记 FAILED', { text: '8 例卡死', textStyle: { color: '#EF4444', bold: true } }],
        ['事件有消费者', '状态变更被下游消费', { text: '事件无消费者', textStyle: { color: '#EF4444', bold: true } }],
        ['交付前自校验', 'SelfReview 在交付门生效', { text: '部分生效', textStyle: { color: '#64748B' } }],
      ]}
    />
  </Box>

  <Box style={{ height: 60, padding: '0 64px', flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', borderTop: '1px solid #E2E8F0' }}>
    <Text style={{ fontSize: 14, color: '#64748B' }}>Regent 失败案例复盘</Text>
    <Text style={{ fontSize: 14, color: '#64748B' }}>08 / 20</Text>
  </Box>
</Slide>

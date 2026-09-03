<Slide style={{ width: '1280px', height: '720px', background: '#FFFFFF', fontFamily: "'PingFang SC','Microsoft YaHei','Helvetica Neue',sans-serif", overflow: 'hidden' }}>
  <Box style={{ height: 120, padding: '28px 64px 0 64px', flexDirection: 'row', justifyContent: 'space-between', alignItems: 'flex-start' }}>
    <Box>
      <Text style={{ fontSize: 16, color: '#64748B', letterSpacing: 1 }}>第五章 · 结论</Text>
      <Text style={{ fontSize: 34, fontWeight: 'bold', color: '#1E293B', marginTop: 6 }}>多 Agent 到底该怎么搭</Text>
    </Box>
    <Text style={{ fontSize: 16, color: '#3B82F6', fontWeight: 'bold', paddingTop: 10 }}>19</Text>
  </Box>

  <Box style={{ height: 540, padding: '8px 64px 0 64px', flexDirection: 'row', gap: 36 }}>
    <Box style={{ flex: 1, background: '#EFF6FF', borderRadius: 18, padding: '28px 26px' }}>
      <Box style={{ flexDirection: 'row', alignItems: 'center', gap: 12, marginBottom: 16 }}>
        <FAIcon name='info-circle' style={{ fill: '#3B82F6', width: 30, height: 30 }} />
        <Text style={{ fontSize: 23, fontWeight: 'bold', color: '#1E293B' }}>原则</Text>
      </Box>
      <Box style={{ gap: 16 }}>
        <Box style={{ flexDirection: 'row', alignItems: 'flex-start', gap: 12 }}>
          <Text style={{ fontSize: 20, fontWeight: 'bold', color: '#3B82F6' }}>·</Text>
          <Text style={{ fontSize: 18, color: '#475569', lineHeight: 1.6 }}>沙箱内多 Agent 可试；扩大生产默认/现实权限才需证据</Text>
        </Box>
        <Box style={{ flexDirection: 'row', alignItems: 'flex-start', gap: 12 }}>
          <Text style={{ fontSize: 20, fontWeight: 'bold', color: '#3B82F6' }}>·</Text>
          <Text style={{ fontSize: 18, color: '#475569', lineHeight: 1.6 }}>触发：上下文 / 能力 / 吞吐真撞墙，且做过实验</Text>
        </Box>
        <Box style={{ flexDirection: 'row', alignItems: 'flex-start', gap: 12 }}>
          <Text style={{ fontSize: 20, fontWeight: 'bold', color: '#3B82F6' }}>·</Text>
          <Text style={{ fontSize: 18, color: '#475569', lineHeight: 1.6 }}>按能力边界拆，不按文件功能切</Text>
        </Box>
      </Box>
    </Box>

    <Box style={{ flex: 1, background: '#F1F5F9', borderRadius: 18, padding: '28px 26px' }}>
      <Box style={{ flexDirection: 'row', alignItems: 'center', gap: 12, marginBottom: 16 }}>
        <FAIcon name='cog' style={{ fill: '#06B6D4', width: 30, height: 30 }} />
        <Text style={{ fontSize: 23, fontWeight: 'bold', color: '#1E293B' }}>协议与做法</Text>
      </Box>
      <Box style={{ gap: 16 }}>
        <Box style={{ flexDirection: 'row', alignItems: 'flex-start', gap: 12 }}>
          <Text style={{ fontSize: 20, fontWeight: 'bold', color: '#06B6D4' }}>·</Text>
          <Text style={{ fontSize: 18, color: '#475569', lineHeight: 1.6 }}>明确消息 / 交接契约，失败码要真被消费</Text>
        </Box>
        <Box style={{ flexDirection: 'row', alignItems: 'flex-start', gap: 12 }}>
          <Text style={{ fontSize: 20, fontWeight: 'bold', color: '#06B6D4' }}>·</Text>
          <Text style={{ fontSize: 18, color: '#475569', lineHeight: 1.6 }}>orchestrator 职责单一、可测，别堆 4400 行</Text>
        </Box>
        <Box style={{ flexDirection: 'row', alignItems: 'flex-start', gap: 12 }}>
          <Text style={{ fontSize: 20, fontWeight: 'bold', color: '#06B6D4' }}>·</Text>
          <Text style={{ fontSize: 18, color: '#475569', lineHeight: 1.6 }}>先有可对照基线，再决定是否扩大生产权限</Text>
        </Box>
      </Box>
    </Box>
  </Box>

  <Box style={{ height: 60, padding: '0 64px', flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', borderTop: '1px solid #E2E8F0' }}>
    <Text style={{ fontSize: 14, color: '#64748B' }}>Regent 失败案例复盘</Text>
    <Text style={{ fontSize: 14, color: '#64748B' }}>19 / 20</Text>
  </Box>
</Slide>

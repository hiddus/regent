<Slide style={{ width: '1280px', height: '720px', background: '#0F172A', fontFamily: "'PingFang SC','Microsoft YaHei','Helvetica Neue',sans-serif", overflow: 'hidden' }}>
  <Box style={{ position: 'absolute', top: -140, right: -120, width: 540, height: 540, borderRadius: 270, background: 'linear-gradient(135deg,#3B82F6 0%,#06B6D4 100%)', opacity: 0.18 }} />
  <Box style={{ position: 'absolute', bottom: -180, left: -110, width: 460, height: 460, borderRadius: 230, background: 'linear-gradient(135deg,#3B82F6 0%,#06B6D4 100%)', opacity: 0.12 }} />

  <Box style={{ position: 'absolute', top: 48, left: 64, right: 64, flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' }}>
    <Text style={{ fontSize: 18, color: '#94A3B8', letterSpacing: 2 }}>内部技术复盘 · 2026</Text>
    <Text style={{ fontSize: 18, color: '#94A3B8' }}>Agent 架构学习课堂</Text>
  </Box>

  <Box style={{ position: 'absolute', top: 210, left: 64, right: 64 }}>
    <Text style={{ fontSize: 22, color: '#06B6D4', fontWeight: 'bold', letterSpacing: 4, marginBottom: 18 }}>REGENT FAILURE POSTMORTEM</Text>
    <Text style={{ fontSize: 76, fontWeight: 'bold', color: '#FFFFFF', lineHeight: 1.15 }}>
      Regent <span style={{ color: '#3B82F6' }}>失败案例</span>复盘
    </Text>
    <Box style={{ width: 130, height: 6, background: 'linear-gradient(90deg,#3B82F6,#06B6D4)', marginTop: 30, marginBottom: 30, borderRadius: 3 }} />
    <Text style={{ fontSize: 26, color: '#CBD5E1', lineHeight: 1.7 }}>
      定义没问题，需求没问题 —— 但我们一直在周边敲边鼓，<br />
      <span style={{ color: '#FFFFFF', fontWeight: 'bold' }}>核心的单 Agent 闭环，始终没打通。</span>
    </Text>
  </Box>

  <Box style={{ position: 'absolute', bottom: 36, left: 64, right: 64, flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' }}>
    <Text style={{ fontSize: 14, color: '#64748B' }}>Regent 失败案例复盘</Text>
    <Text style={{ fontSize: 14, color: '#64748B' }}>01 / 20</Text>
  </Box>
</Slide>

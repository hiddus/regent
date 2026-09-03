<Slide style={{ width: '1280px', height: '720px', background: '#FFFFFF', fontFamily: "'PingFang SC','Microsoft YaHei','Helvetica Neue',sans-serif", overflow: 'hidden' }}>
  <Box style={{ height: 120, padding: '28px 64px 0 64px', flexDirection: 'row', justifyContent: 'space-between', alignItems: 'flex-start' }}>
    <Box>
      <Text style={{ fontSize: 16, color: '#64748B', letterSpacing: 1 }}>第三章 · 核心能力突破</Text>
      <Text style={{ fontSize: 34, fontWeight: 'bold', color: '#16335B', marginTop: 6 }}>自进化 · 受监管的自我改进</Text>
    </Box>
    <Text style={{ fontSize: 16, color: '#2E6DB4', fontWeight: 'bold', paddingTop: 10 }}>10</Text>
  </Box>

  <Box style={{ padding: '16px 64px 0 64px', flexDirection: 'row', gap: 36 }}>
    <Box style={{ flex: 1.1, background: '#F5F8FC', borderRadius: 14, padding: '20px 24px', border: '1px solid #E2E8F0' }}>
      <Text style={{ fontSize: 17, fontWeight: 'bold', color: '#16335B', marginBottom: 12 }}>系统越用越聪明，且全程受监管</Text>
      {[
        ['1', '自检缺口', '识别自身不足与可改进点'],
        ['2', '提出改进', '在隔离环境生成候选方案'],
        ['3', '隔离验证', '独立编译、测试，不动生产'],
        ['4', '独立评审', '第三方评审，不得削弱治理'],
        ['5', '人工审批', '由人决定是否采纳，绝不自作主张'],
      ].map(([n, t, d], i) => (
        <Box key={n} style={{ flexDirection: 'row', gap: 14, alignItems: 'center', padding: '9px 0' }}>
          <Box style={{ flex: '0 0 32px', height: 32, borderRadius: 16, background: 'linear-gradient(135deg,#16335B,#2E6DB4)', alignItems: 'center', justifyContent: 'center' }}>
            <Text style={{ fontSize: 15, fontWeight: 'bold', color: '#FFFFFF' }}>{n}</Text>
          </Box>
          <Box style={{ flex: 1 }}>
            <Text style={{ fontSize: 17, fontWeight: 'bold', color: '#1F2A37' }}>{t}</Text>
            <Text style={{ fontSize: 14, color: '#64748B' }}>{d}</Text>
          </Box>
          {i < 4 && <Text style={{ fontSize: 18, color: '#94A3B8' }}>↓</Text>}
        </Box>
      ))}
    </Box>
    <Box style={{ flex: 0.9, gap: 16 }}>
      <Box style={{ background: '#16335B', borderRadius: 14, padding: '20px 22px' }}>
        <Text style={{ fontSize: 22, fontWeight: 'bold', color: '#F4B400' }}>越用越聪明</Text>
        <Text style={{ fontSize: 15.5, color: '#CBD5E1', marginTop: 8, lineHeight: 1.7 }}>持续的自我改进机制，让系统能力随运营不断沉淀、持续进化。</Text>
      </Box>
      <Box style={{ background: '#FDECEF', borderRadius: 14, padding: '20px 22px', border: '1px solid #F4C2C9' }}>
        <Text style={{ fontSize: 20, fontWeight: 'bold', color: '#D7263D' }}>绝不擅自改生产</Text>
        <Text style={{ fontSize: 15.5, color: '#7A2530', marginTop: 8, lineHeight: 1.7 }}>候选只停留在隔离副本；必须人工批准，才进入另行授权的实现步骤。</Text>
      </Box>
      <Box style={{ background: '#E8F0FA', borderRadius: 14, padding: '20px 22px' }}>
        <Text style={{ fontSize: 20, fontWeight: 'bold', color: '#2E6DB4' }}>治理只增不减</Text>
        <Text style={{ fontSize: 15.5, color: '#1F2A37', marginTop: 8, lineHeight: 1.7 }}>评审明确禁止削弱权限、审计、安全与测试——升级不降级。</Text>
      </Box>
    </Box>
  </Box>

  <Box style={{ height: 60, padding: '0 64px', flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', borderTop: '1px solid #E2E8F0', position: 'absolute', bottom: 0, left: 0, right: 0 }}>
    <Text style={{ fontSize: 14, color: '#64748B' }}>Regent 系统介绍 · 树米科技</Text>
    <Text style={{ fontSize: 14, color: '#64748B' }}>10 / 20</Text>
  </Box>
</Slide>

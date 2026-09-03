<Slide style={{ width: '1280px', height: '720px', background: '#FFFFFF', fontFamily: "'PingFang SC','Microsoft YaHei','Helvetica Neue',sans-serif", overflow: 'hidden' }}>
  <Box style={{ height: 120, padding: '28px 64px 0 64px', flexDirection: 'row', justifyContent: 'space-between', alignItems: 'flex-start' }}>
    <Box>
      <Text style={{ fontSize: 16, color: '#64748B', letterSpacing: 1 }}>第六章 · 合作邀约</Text>
      <Text style={{ fontSize: 34, fontWeight: 'bold', color: '#16335B', marginTop: 6 }}>为什么交给树米</Text>
    </Box>
    <Text style={{ fontSize: 16, color: '#2E6DB4', fontWeight: 'bold', paddingTop: 10 }}>19</Text>
  </Box>

  <Box style={{ padding: '14px 64px 0 64px', flexDirection: 'row', gap: 28 }}>
    <Box style={{ flex: 1.1, gap: 12 }}>
      <Text style={{ fontSize: 17, fontWeight: 'bold', color: '#16335B', marginBottom: 4 }}>树米的承接优势</Text>
      {[
        ['自主可控底座', '自研 Regent 目标操作系统，不绑定外部黑箱，源码与数据自主可控。'],
        ['强治理能力', '边界内自治、全程审计、失败即止损，政务与关键场景敢用。'],
        ['已落地验证', '内核已通过严格自动化验收，自进化与 Hive 能力已工程化。'],
        ['可持续运营交付', '不“交完即止”，而是长期运营、持续迭代、越用越好。'],
      ].map(([t, d], i) => (
        <Box key={i} style={{ flexDirection: 'row', gap: 12, alignItems: 'flex-start', background: '#F5F8FC', borderRadius: 12, padding: '13px 18px' }}>
          <Box style={{ flex: '0 0 28px', height: 28, borderRadius: 8, background: '#16335B', alignItems: 'center', justifyContent: 'center' }}>
            <Text style={{ fontSize: 15, fontWeight: 'bold', color: '#FFFFFF' }}>{'✓'}</Text>
          </Box>
          <Box>
            <Text style={{ fontSize: 18, fontWeight: 'bold', color: '#1F2A37' }}>{t}</Text>
            <Text style={{ fontSize: 14.5, color: '#64748B', marginTop: 3, lineHeight: 1.55 }}>{d}</Text>
          </Box>
        </Box>
      ))}
    </Box>
    <Box style={{ flex: 0.9, background: 'linear-gradient(135deg,#16335B,#2E6DB4)', borderRadius: 16, padding: '26px 24px', justifyContent: 'center' }}>
      <Text style={{ fontSize: 15, color: '#9DB2CC', fontWeight: 'bold', letterSpacing: 1 }}>诚 挚 请 求</Text>
      <Text style={{ fontSize: 23, color: '#FFFFFF', fontWeight: 'bold', marginTop: 14, lineHeight: 1.6 }}>恳请领导将</Text>
      <Text style={{ fontSize: 23, color: '#F4B400', fontWeight: 'bold', marginTop: 6, lineHeight: 1.6 }}>智慧城市 · 智慧文旅 · 智慧场馆 · 智能工厂</Text>
      <Text style={{ fontSize: 23, color: '#FFFFFF', fontWeight: 'bold', marginTop: 6, lineHeight: 1.6 }}>等持久运营项目交给树米</Text>
      <Text style={{ fontSize: 17, color: '#CBD5E1', marginTop: 18, lineHeight: 1.7 }}>以 Regent 为底座，与省市共建可复制、可推广的标杆应用，让 AI 真正服务于高质量发展大局。</Text>
    </Box>
  </Box>

  <Box style={{ height: 60, padding: '0 64px', flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', borderTop: '1px solid #E2E8F0', position: 'absolute', bottom: 0, left: 0, right: 0 }}>
    <Text style={{ fontSize: 14, color: '#64748B' }}>Regent 系统介绍 · 树米科技</Text>
    <Text style={{ fontSize: 14, color: '#64748B' }}>19 / 20</Text>
  </Box>
</Slide>

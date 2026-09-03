<Slide style={{ width: '1280px', height: '720px', background: '#FFFFFF', fontFamily: "'PingFang SC','Microsoft YaHei','Helvetica Neue',sans-serif", overflow: 'hidden' }}>
  <Box style={{ height: 120, padding: '28px 64px 0 64px', flexDirection: 'row', justifyContent: 'space-between', alignItems: 'flex-start' }}>
    <Box>
      <Text style={{ fontSize: 16, color: '#64748B', letterSpacing: 1 }}>第四章 · 安全与治理</Text>
      <Text style={{ fontSize: 34, fontWeight: 'bold', color: '#16335B', marginTop: 6 }}>安全可信合规 · 六道防线</Text>
    </Box>
    <Text style={{ fontSize: 16, color: '#2E6DB4', fontWeight: 'bold', paddingTop: 10 }}>13</Text>
  </Box>

  <Box style={{ padding: '16px 64px 0 64px', flexDirection: 'row', flexWrap: 'wrap', gap: 16 }}>
    {[
      ['🔐', '边界可控', '高风险动作（发布、外部影响）必须人工审批，不能“自己说了算”。'],
      ['🗝', '凭据不落地', '系统不接触明文密钥，权限按需、按时下发，杜绝泄漏。'],
      ['🧾', '全程可审计', '每一步可追溯、可回放，谁在何时做了什么，责任清晰。'],
      ['🛡', '数据不泄露', '外部内容默认不可信，敏感信息最小化采集与保留。'],
      ['🛑', '失败即止损', '重复副作用 / 未对账 / 安全违规，系统立即停，不将错就错。'],
      ['✅', '独立验证', '用真实结果验收，不用“内部自测”糊弄过关。'],
    ].map(([ic, t, d], i) => (
      <Box key={i} style={{ width: 'calc(50% - 8px)', background: '#F5F8FC', border: '1px solid #E2E8F0', borderRadius: 14, padding: '18px 20px', flexDirection: 'row', gap: 14, alignItems: 'flex-start' }}>
        <Text style={{ fontSize: 30 }}>{ic}</Text>
        <Box>
          <Text style={{ fontSize: 19, fontWeight: 'bold', color: '#16335B' }}>{t}</Text>
          <Text style={{ fontSize: 14.5, color: '#64748B', marginTop: 6, lineHeight: 1.6 }}>{d}</Text>
        </Box>
      </Box>
    ))}
  </Box>

  <Box style={{ margin: '18px 64px 0 64px', background: '#16335B', borderRadius: 14, padding: '16px 26px', flexDirection: 'row', gap: 16, alignItems: 'center' }}>
    <Text style={{ fontSize: 16, color: '#9DB2CC', fontWeight: 'bold' }}>设计哲学</Text>
    <Text style={{ fontSize: 17, color: '#FFFFFF', lineHeight: 1.5 }}>把<span style={{ color: '#F4B400', fontWeight: 'bold' }}>「可控、可信」</span>刻进系统骨子里，而不是事后补救——这是政务与关键场景敢用的前提。</Text>
  </Box>

  <Box style={{ height: 60, padding: '0 64px', flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', borderTop: '1px solid #E2E8F0', position: 'absolute', bottom: 0, left: 0, right: 0 }}>
    <Text style={{ fontSize: 14, color: '#64748B' }}>Regent 系统介绍 · 树米科技</Text>
    <Text style={{ fontSize: 14, color: '#64748B' }}>13 / 20</Text>
  </Box>
</Slide>

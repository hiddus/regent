<Slide style={{ width: '1280px', height: '720px', background: '#FFFFFF', fontFamily: "'PingFang SC','Microsoft YaHei','Helvetica Neue',sans-serif", overflow: 'hidden' }}>
  <Box style={{ height: 120, padding: '28px 64px 0 64px', flexDirection: 'row', justifyContent: 'space-between', alignItems: 'flex-start' }}>
    <Box>
      <Text style={{ fontSize: 16, color: '#64748B', letterSpacing: 1 }}>第一章 · 项目目标</Text>
      <Text style={{ fontSize: 34, fontWeight: 'bold', color: '#16335B', marginTop: 6 }}>项目目标 · 我们要做成什么</Text>
    </Box>
    <Text style={{ fontSize: 16, color: '#2E6DB4', fontWeight: 'bold', paddingTop: 10 }}>05</Text>
  </Box>

  <Box style={{ padding: '16px 64px 0 64px', flexDirection: 'row', gap: 18 }}>
    {[
      ['①', '升级 AI 的能力', '把 AI 从“会聊天的工具”升级为“能干事的目标执行系统”——一句话目标，输出可运行、可验证的真实应用。'],
      ['②', '建一套生产体系', '自主可控、安全合规的 AI 应用生产体系：边界内自治、全程留痕、可审计、可接管。'],
      ['③', '让能力可沉淀', '缺什么补什么，不重复造轮子；用得越多越聪明，能力跨场景复用。'],
    ].map(([n, t, d], i) => (
      <Box key={i} style={{ flex: 1, background: '#F5F8FC', border: '1px solid #E2E8F0', borderRadius: 14, padding: '24px 22px' }}>
        <Box style={{ width: 46, height: 46, borderRadius: 12, background: '#16335B', alignItems: 'center', justifyContent: 'center' }}>
          <Text style={{ fontSize: 22, fontWeight: 'bold', color: '#FFFFFF' }}>{n}</Text>
        </Box>
        <Text style={{ fontSize: 21, fontWeight: 'bold', color: '#16335B', marginTop: 14 }}>{t}</Text>
        <Text style={{ fontSize: 15.5, color: '#64748B', marginTop: 10, lineHeight: 1.7 }}>{d}</Text>
      </Box>
    ))}
  </Box>

  <Box style={{ margin: '22px 64px 0 64px', background: 'linear-gradient(135deg,#16335B,#2E6DB4)', borderRadius: 14, padding: '20px 26px' }}>
    <Text style={{ fontSize: 17, color: '#9DB2CC', fontWeight: 'bold', marginBottom: 6 }}>战略定位</Text>
    <Text style={{ fontSize: 19, color: '#FFFFFF', lineHeight: 1.6 }}>Regent 不是又一个聊天机器人，而是管理 AI 团队在约束下自治干活的<span style={{ color: '#F4B400', fontWeight: 'bold' }}>「目标操作系统」</span>——尤其适合智慧城市、文旅、场馆、工厂等需要<span style={{ color: '#F4B400', fontWeight: 'bold' }}>长期持久运营经营</span>的项目。</Text>
  </Box>

  <Box style={{ height: 60, padding: '0 64px', flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', borderTop: '1px solid #E2E8F0', position: 'absolute', bottom: 0, left: 0, right: 0 }}>
    <Text style={{ fontSize: 14, color: '#64748B' }}>Regent 系统介绍 · 树米科技</Text>
    <Text style={{ fontSize: 14, color: '#64748B' }}>05 / 20</Text>
  </Box>
</Slide>

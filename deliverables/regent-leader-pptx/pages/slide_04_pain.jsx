<Slide style={{ width: '1280px', height: '720px', background: '#FFFFFF', fontFamily: "'PingFang SC','Microsoft YaHei','Helvetica Neue',sans-serif", overflow: 'hidden' }}>
  <Box style={{ height: 120, padding: '28px 64px 0 64px', flexDirection: 'row', justifyContent: 'space-between', alignItems: 'flex-start' }}>
    <Box>
      <Text style={{ fontSize: 16, color: '#64748B', letterSpacing: 1 }}>第一章 · 项目目标</Text>
      <Text style={{ fontSize: 34, fontWeight: 'bold', color: '#16335B', marginTop: 6 }}>为什么需要 Regent</Text>
    </Box>
    <Text style={{ fontSize: 16, color: '#2E6DB4', fontWeight: 'bold', paddingTop: 10 }}>04</Text>
  </Box>

  <Text style={{ padding: '6px 64px 0 64px', fontSize: 19, color: '#1F2A37' }}>AI 要真正服务发展，落地仍是最大难题。当前普遍面临四道坎：</Text>

  <Box style={{ padding: '16px 64px 0 64px', flexDirection: 'row', gap: 18 }}>
    {[
      ['⏳', '从想法到可用，太慢太贵', '“做个应用”仍靠堆人、堆时间，周期长、门槛高，基层与中小企业用不起。'],
      ['❓', '会聊不会干，难验证', '多数 AI 只停留在对话，生成的东西不可信、不可审计，关键场景不敢用。'],
      ['🔓', '怕失控、怕担责', '安全边界不清、责任难追溯、数据易泄露——政务与关键领域最忌“黑箱”。'],
      ['🔁', '重复造轮子', '各部门、各企业各自摸索，能力无法沉淀复用，资源大量浪费。'],
    ].map(([ic, t, d], i) => (
      <Box key={i} style={{ flex: 1, background: '#F5F8FC', border: '1px solid #E2E8F0', borderRadius: 14, padding: '22px 20px' }}>
        <Text style={{ fontSize: 34 }}>{ic}</Text>
        <Text style={{ fontSize: 20, fontWeight: 'bold', color: '#16335B', marginTop: 12 }}>{t}</Text>
        <Text style={{ fontSize: 15.5, color: '#64748B', marginTop: 10, lineHeight: 1.65 }}>{d}</Text>
      </Box>
    ))}
  </Box>

  <Box style={{ margin: '20px 64px 0 64px', background: '#16335B', borderRadius: 14, padding: '18px 26px', flexDirection: 'row', gap: 16, alignItems: 'center' }}>
    <Text style={{ fontSize: 16, color: '#9DB2CC', fontWeight: 'bold' }}>核心问题</Text>
    <Text style={{ fontSize: 18, color: '#FFFFFF', lineHeight: 1.5 }}>缺一套<span style={{ color: '#F4B400', fontWeight: 'bold' }}>「让 AI 真正干事、且始终在掌控之中」</span>的系统。这正是 Regent 要解决的。</Text>
  </Box>

  <Box style={{ height: 60, padding: '0 64px', flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', borderTop: '1px solid #E2E8F0', position: 'absolute', bottom: 0, left: 0, right: 0 }}>
    <Text style={{ fontSize: 14, color: '#64748B' }}>Regent 系统介绍 · 树米科技</Text>
    <Text style={{ fontSize: 14, color: '#64748B' }}>04 / 20</Text>
  </Box>
</Slide>

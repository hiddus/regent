<Slide style={{ width: '1280px', height: '720px', background: '#FFFFFF', fontFamily: "'PingFang SC','Microsoft YaHei','Helvetica Neue',sans-serif", overflow: 'hidden' }}>
  <Box style={{ height: 120, padding: '28px 64px 0 64px', flexDirection: 'row', justifyContent: 'space-between', alignItems: 'flex-start' }}>
    <Box>
      <Text style={{ fontSize: 16, color: '#64748B', letterSpacing: 1 }}>第一章 · 项目目标</Text>
      <Text style={{ fontSize: 34, fontWeight: 'bold', color: '#1E293B', marginTop: 6 }}>Regent 到底是什么</Text>
    </Box>
    <Text style={{ fontSize: 16, color: '#3B82F6', fontWeight: 'bold', paddingTop: 10 }}>04</Text>
  </Box>

  <Box style={{ height: 540, padding: '10px 64px 0 64px', flexDirection: 'row', gap: 40 }}>
    <Box style={{ flex: 1.1, background: 'linear-gradient(135deg,#3B82F6 0%,#06B6D4 100%)', borderRadius: 18, padding: '36px 32px', justifyContent: 'center' }}>
      <Text style={{ fontSize: 18, color: '#E0F2FE', fontWeight: 'bold', letterSpacing: 1 }}>它的定义</Text>
      <Text style={{ fontSize: 30, fontWeight: 'bold', color: '#FFFFFF', lineHeight: 1.5, marginTop: 18 }}>
        Regent 是一个<br />自主目标执行内核
      </Text>
      <Box style={{ width: 70, height: 4, background: '#FFFFFF', opacity: 0.6, marginTop: 22, marginBottom: 22, borderRadius: 2 }} />
      <Text style={{ fontSize: 19, color: '#F1F5F9', lineHeight: 1.8 }}>
        把一句话目标，通过受管理的执行循环，变成可独立运行的应用。
      </Text>
      <Box style={{ flexDirection: 'row', alignItems: 'center', gap: 12, marginTop: 26 }}>
        <FAIcon name='arrow-right' style={{ fill: '#FFFFFF', width: 26, height: 26 }} />
        <Text style={{ fontSize: 19, color: '#FFFFFF', fontWeight: 'bold' }}>目标 → 循环 → 可运行应用</Text>
      </Box>
    </Box>

    <Box style={{ flex: 1, justifyContent: 'center', gap: 18 }}>
      <Text style={{ fontSize: 18, color: '#64748B', fontWeight: 'bold', letterSpacing: 1 }}>它「不是」什么</Text>
      <Box style={{ flexDirection: 'row', alignItems: 'center', gap: 14 }}>
        <FAIcon name='times-circle' style={{ fill: '#EF4444', width: 24, height: 24 }} />
        <Text style={{ fontSize: 20, color: '#1E293B' }}>不是聊天机器人</Text>
      </Box>
      <Box style={{ flexDirection: 'row', alignItems: 'center', gap: 14 }}>
        <FAIcon name='times-circle' style={{ fill: '#EF4444', width: 24, height: 24 }} />
        <Text style={{ fontSize: 20, color: '#1E293B' }}>不是工作流调度器</Text>
      </Box>
      <Box style={{ flexDirection: 'row', alignItems: 'center', gap: 14 }}>
        <FAIcon name='times-circle' style={{ fill: '#EF4444', width: 24, height: 24 }} />
        <Text style={{ fontSize: 20, color: '#1E293B' }}>不是又一层 Prompt 包装</Text>
      </Box>
      <Box style={{ flexDirection: 'row', alignItems: 'center', gap: 14, marginTop: 6 }}>
        <FAIcon name='check-circle' style={{ fill: '#3B82F6', width: 24, height: 24 }} />
        <Text style={{ fontSize: 20, color: '#1E293B', fontWeight: 'bold' }}>而是能「跑通一件事」的 Agent</Text>
      </Box>
    </Box>
  </Box>

  <Box style={{ height: 60, padding: '0 64px', flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', borderTop: '1px solid #E2E8F0' }}>
    <Text style={{ fontSize: 14, color: '#64748B' }}>Regent 失败案例复盘</Text>
    <Text style={{ fontSize: 14, color: '#64748B' }}>04 / 20</Text>
  </Box>
</Slide>

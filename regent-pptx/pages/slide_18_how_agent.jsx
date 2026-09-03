<Slide style={{ width: '1280px', height: '720px', background: '#FFFFFF', fontFamily: "'PingFang SC','Microsoft YaHei','Helvetica Neue',sans-serif", overflow: 'hidden' }}>
  <Box style={{ height: 120, padding: '28px 64px 0 64px', flexDirection: 'row', justifyContent: 'space-between', alignItems: 'flex-start' }}>
    <Box>
      <Text style={{ fontSize: 16, color: '#64748B', letterSpacing: 1 }}>第五章 · 结论</Text>
      <Text style={{ fontSize: 34, fontWeight: 'bold', color: '#1E293B', marginTop: 6 }}>Agent 到底该怎么搭</Text>
    </Box>
    <Text style={{ fontSize: 16, color: '#3B82F6', fontWeight: 'bold', paddingTop: 10 }}>18</Text>
  </Box>

  <Box style={{ height: 540, padding: '6px 64px 0 64px', flexDirection: 'row', gap: 40 }}>
    <Box style={{ width: 260, background: 'linear-gradient(135deg,#3B82F6 0%,#06B6D4 100%)', borderRadius: 18, padding: '30px 26px', justifyContent: 'center' }}>
      <Text style={{ fontSize: 80, fontWeight: 'bold', color: '#FFFFFF', lineHeight: 1 }}>7</Text>
      <Text style={{ fontSize: 22, fontWeight: 'bold', color: '#FFFFFF', marginTop: 6 }}>步法</Text>
      <Text style={{ fontSize: 17, color: '#E0F2FE', lineHeight: 1.7, marginTop: 16 }}>
        先闭环，<br />再谈高级。
      </Text>
    </Box>

    <Box style={{ flex: 1, justifyContent: 'space-between' }}>
      {[
        ['1', '定义最小闭环', '先圈出「一件能跑通的小事」'],
        ['2', '写出核心循环', 'goal→plan→act→observe→reflect→deliver，先单线程跑通'],
        ['3', '加质量门', '自校验 + 可重跑，不过门不出门'],
        ['4', '上下文工程优先', '把上下文喂好，比加节点更重要'],
        ['5', '稳定后再扩展', '循环稳了，才加并行 / 分支'],
        ['6', '做到可观测', '事件有消费者、状态不悬停、失败能终态'],
        ['7', '用进度衡量', '禁用「架构很美」式自我安慰'],
      ].map((s, i) => (
        <Box key={i} style={{ flexDirection: 'row', alignItems: 'center', gap: 16, background: '#F1F5F9', borderRadius: 10, padding: '11px 18px' }}>
          <Text style={{ fontSize: 24, fontWeight: 'bold', color: '#3B82F6', width: 36 }}>{s[0]}</Text>
          <Text style={{ fontSize: 18, fontWeight: 'bold', color: '#1E293B', width: 150 }}>{s[1]}</Text>
          <Text style={{ fontSize: 16, color: '#64748B', flex: 1 }}>{s[2]}</Text>
        </Box>
      ))}
    </Box>
  </Box>

  <Box style={{ height: 60, padding: '0 64px', flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', borderTop: '1px solid #E2E8F0' }}>
    <Text style={{ fontSize: 14, color: '#64748B' }}>Regent 失败案例复盘</Text>
    <Text style={{ fontSize: 14, color: '#64748B' }}>18 / 20</Text>
  </Box>
</Slide>

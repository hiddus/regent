<Slide style={{ width: '1280px', height: '720px', background: '#FFFFFF', fontFamily: "'PingFang SC','Microsoft YaHei','Helvetica Neue',sans-serif", overflow: 'hidden' }}>
  <Box style={{ height: 120, padding: '30px 64px 0 64px', flexDirection: 'row', justifyContent: 'space-between', alignItems: 'flex-start' }}>
    <Box>
      <Text style={{ fontSize: 16, color: '#64748B', letterSpacing: 1 }}>AGENDA</Text>
      <Text style={{ fontSize: 34, fontWeight: 'bold', color: '#16335B', marginTop: 6 }}>汇报目录</Text>
    </Box>
    <Text style={{ fontSize: 16, color: '#2E6DB4', fontWeight: 'bold', paddingTop: 10 }}>02</Text>
  </Box>

  <Box style={{ padding: '20px 64px 0 64px', flexDirection: 'row', gap: 40 }}>
    <Box style={{ flex: 1, gap: 16 }}>
      {[
        ['01', '项目目标', '我们想做成什么'],
        ['02', '系统定义', 'Regent 到底是什么'],
        ['03', '核心能力突破', '自进化 · Hive 多智能体'],
      ].map(([n, t, d]) => (
        <Box key={n} style={{ flexDirection: 'row', alignItems: 'center', gap: 18, background: '#F5F8FC', borderRadius: 14, padding: '18px 22px', borderLeft: '5px solid #2E6DB4' }}>
          <Text style={{ fontSize: 30, fontWeight: 'bold', color: '#16335B', width: 54 }}>{n}</Text>
          <Box>
            <Text style={{ fontSize: 22, fontWeight: 'bold', color: '#1F2A37' }}>{t}</Text>
            <Text style={{ fontSize: 16, color: '#64748B', marginTop: 2 }}>{d}</Text>
          </Box>
        </Box>
      ))}
    </Box>
    <Box style={{ flex: 1, gap: 16 }}>
      {[
        ['04', '安全与治理', '领导最关心的可控可信'],
        ['05', '落地场景', '树米可承接的持久运营项目'],
        ['06', '合作邀约', '请领导把项目交给树米'],
      ].map(([n, t, d]) => (
        <Box key={n} style={{ flexDirection: 'row', alignItems: 'center', gap: 18, background: '#F5F8FC', borderRadius: 14, padding: '18px 22px', borderLeft: '5px solid #D7263D' }}>
          <Text style={{ fontSize: 30, fontWeight: 'bold', color: '#16335B', width: 54 }}>{n}</Text>
          <Box>
            <Text style={{ fontSize: 22, fontWeight: 'bold', color: '#1F2A37' }}>{t}</Text>
            <Text style={{ fontSize: 16, color: '#64748B', marginTop: 2 }}>{d}</Text>
          </Box>
        </Box>
      ))}
    </Box>
  </Box>

  <Box style={{ height: 60, padding: '0 64px', flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', borderTop: '1px solid #E2E8F0', position: 'absolute', bottom: 0, left: 0, right: 0 }}>
    <Text style={{ fontSize: 14, color: '#64748B' }}>Regent 系统介绍 · 树米科技</Text>
    <Text style={{ fontSize: 14, color: '#64748B' }}>02 / 20</Text>
  </Box>
</Slide>

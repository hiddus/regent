<Slide style={{ width: '1280px', height: '720px', background: '#0F172A', fontFamily: "'PingFang SC','Microsoft YaHei','Helvetica Neue',sans-serif", overflow: 'hidden' }}>
  <Box style={{ position: 'absolute', top: -140, right: -120, width: 520, height: 520, borderRadius: 260, background: 'linear-gradient(135deg,#3B82F6 0%,#06B6D4 100%)', opacity: 0.14 }} />

  <Box style={{ position: 'absolute', top: 56, left: 64, right: 64, flexDirection: 'row', justifyContent: 'space-between', alignItems: 'flex-start' }}>
    <Box>
      <Text style={{ fontSize: 16, color: '#94A3B8', letterSpacing: 1 }}>第四章 · 失败原因</Text>
      <Text style={{ fontSize: 34, fontWeight: 'bold', color: '#FFFFFF', marginTop: 6 }}>证据墙：项目确实失败了</Text>
    </Box>
    <Text style={{ fontSize: 16, color: '#F87171', fontWeight: 'bold', paddingTop: 10 }}>14</Text>
  </Box>

  <Box style={{ position: 'absolute', top: 170, left: 64, right: 64, flexDirection: 'row', justifyContent: 'space-between', gap: 28 }}>
    <Box style={{ flex: 1, background: '#1E293B', borderRadius: 18, padding: '34px 28px', alignItems: 'center' }}>
      <Text style={{ fontSize: 88, fontWeight: 'bold', color: '#EF4444', lineHeight: 1 }}>32</Text>
      <Text style={{ fontSize: 22, color: '#CBD5E1', marginTop: 8 }}>天无用户进展</Text>
    </Box>
    <Box style={{ flex: 1, background: '#1E293B', borderRadius: 18, padding: '34px 28px', alignItems: 'center' }}>
      <Text style={{ fontSize: 88, fontWeight: 'bold', color: '#EF4444', lineHeight: 1 }}>21</Text>
      <Text style={{ fontSize: 22, color: '#CBD5E1', marginTop: 8 }}>个 PENDING 运行</Text>
    </Box>
    <Box style={{ flex: 1, background: '#1E293B', borderRadius: 18, padding: '34px 28px', alignItems: 'center' }}>
      <Text style={{ fontSize: 88, fontWeight: 'bold', color: '#EF4444', lineHeight: 1 }}>8</Text>
      <Text style={{ fontSize: 22, color: '#CBD5E1', marginTop: 8 }}>例卡死无法终态</Text>
    </Box>
    <Box style={{ flex: 1, background: '#1E293B', borderRadius: 18, padding: '34px 28px', alignItems: 'center' }}>
      <Text style={{ fontSize: 88, fontWeight: 'bold', color: '#EF4444', lineHeight: 1 }}>0</Text>
      <Text style={{ fontSize: 22, color: '#CBD5E1', marginTop: 8 }}>失败码被引用</Text>
    </Box>
  </Box>

  <Box style={{ position: 'absolute', bottom: 120, left: 64, right: 64, alignItems: 'center' }}>
    <Text style={{ fontSize: 22, color: '#94A3B8', textAlign: 'center' }}>
      这些不是推测，是日志、状态机与代码核查给出的硬事实。
    </Text>
  </Box>

  <Box style={{ position: 'absolute', bottom: 36, left: 64, right: 64, flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' }}>
    <Text style={{ fontSize: 14, color: '#64748B' }}>Regent 失败案例复盘</Text>
    <Text style={{ fontSize: 14, color: '#64748B' }}>14 / 20</Text>
  </Box>
</Slide>

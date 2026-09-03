<Slide style={{ width: '1280px', height: '720px', background: '#FFFFFF', fontFamily: "'PingFang SC','Microsoft YaHei','Helvetica Neue',sans-serif", overflow: 'hidden' }}>
  <Box style={{ height: 120, padding: '28px 64px 0 64px', flexDirection: 'row', justifyContent: 'space-between', alignItems: 'flex-start' }}>
    <Box>
      <Text style={{ fontSize: 16, color: '#64748B', letterSpacing: 1 }}>第五章 · 落地场景</Text>
      <Text style={{ fontSize: 34, fontWeight: 'bold', color: '#16335B', marginTop: 6 }}>智慧文旅 · 讲好本地故事</Text>
    </Box>
    <Text style={{ fontSize: 16, color: '#2E6DB4', fontWeight: 'bold', paddingTop: 10 }}>17</Text>
  </Box>

  <Box style={{ padding: '16px 64px 0 64px', flexDirection: 'row', gap: 36, alignItems: 'center' }}>
    <Box style={{ flex: '0 0 240px', height: 360, borderRadius: 16, background: 'linear-gradient(135deg,#2E6DB4,#1F6F5C)', alignItems: 'center', justifyContent: 'center' }}>
      <Text style={{ fontSize: 96 }}>🏞</Text>
      <Text style={{ fontSize: 22, color: '#FFFFFF', fontWeight: 'bold', marginTop: 10 }}>智慧文旅</Text>
    </Box>
    <Box style={{ flex: 1, gap: 14 }}>
      {[
        ['智能导览', '个性化行程与讲解，用 AI 讲好本地文化与景点故事。'],
        ['客流与舆情监测', '实时掌握景区客流、舆情与投诉，及时疏导与回应。'],
        ['内容营销', '批量生成小红书/短视频脚本与卖点文案，助农助旅、拓宽销路。'],
        ['持续运营', '沉淀游客画像与偏好，迭代产品与活动，提升复游与消费。'],
      ].map(([t, d], i) => (
        <Box key={i} style={{ flexDirection: 'row', gap: 14, alignItems: 'flex-start', background: '#F5F8FC', borderRadius: 12, padding: '14px 18px', borderLeft: '5px solid #2E6DB4' }}>
          <Text style={{ fontSize: 20 }}>{'①②③④'[i]}</Text>
          <Box>
            <Text style={{ fontSize: 19, fontWeight: 'bold', color: '#1F2A37' }}>{t}</Text>
            <Text style={{ fontSize: 15, color: '#64748B', marginTop: 4, lineHeight: 1.6 }}>{d}</Text>
          </Box>
        </Box>
      ))}
      <Text style={{ fontSize: 15, color: '#D7263D', fontWeight: 'bold', marginTop: 2 }}>关键点：运营带动流量与消费，是文旅长期增值的抓手。</Text>
    </Box>
  </Box>

  <Box style={{ height: 60, padding: '0 64px', flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', borderTop: '1px solid #E2E8F0', position: 'absolute', bottom: 0, left: 0, right: 0 }}>
    <Text style={{ fontSize: 14, color: '#64748B' }}>Regent 系统介绍 · 树米科技</Text>
    <Text style={{ fontSize: 14, color: '#64748B' }}>17 / 20</Text>
  </Box>
</Slide>

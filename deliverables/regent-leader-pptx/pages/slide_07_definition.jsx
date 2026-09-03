<Slide style={{ width: '1280px', height: '720px', background: '#FFFFFF', fontFamily: "'PingFang SC','Microsoft YaHei','Helvetica Neue',sans-serif", overflow: 'hidden' }}>
  <Box style={{ height: 120, padding: '28px 64px 0 64px', flexDirection: 'row', justifyContent: 'space-between', alignItems: 'flex-start' }}>
    <Box>
      <Text style={{ fontSize: 16, color: '#64748B', letterSpacing: 1 }}>第二章 · 系统定义</Text>
      <Text style={{ fontSize: 34, fontWeight: 'bold', color: '#16335B', marginTop: 6 }}>系统定义 · Regent 到底是什么</Text>
    </Box>
    <Text style={{ fontSize: 16, color: '#2E6DB4', fontWeight: 'bold', paddingTop: 10 }}>07</Text>
  </Box>

  <Box style={{ padding: '14px 64px 0 64px', flexDirection: 'row', gap: 36 }}>
    <Box style={{ flex: 1.05 }}>
      <Text style={{ fontSize: 18, color: '#1F2A37', lineHeight: 1.8 }}>Regent 接收人的自然语言目标，在明确的<span style={{ color: '#16335B', fontWeight: 'bold' }}>约束、资源、授权和治理边界</span>内，自主解释目标、发现并补齐能力，形成最合适的人机组织，创建并运营可独立运行的应用，并依据<span style={{ color: '#16335B', fontWeight: 'bold' }}>可验证的外部结果</span>持续调整，直至目标达成或被叫停。</Text>
      <Box style={{ marginTop: 20, background: '#16335B', borderRadius: 12, padding: '18px 22px' }}>
        <Text style={{ fontSize: 15, color: '#9DB2CC', fontWeight: 'bold', marginBottom: 6 }}>产品身份</Text>
        <Text style={{ fontSize: 17, color: '#FFFFFF', lineHeight: 1.65 }}>不是“更强的单一 AI”，而是<span style={{ color: '#F4B400', fontWeight: 'bold' }}>管理 AI 组织在约束与治理下自治运行的目标操作系统</span>。</Text>
      </Box>
    </Box>
    <Box style={{ flex: 0.95, background: '#F5F8FC', borderRadius: 14, padding: '18px 22px', border: '1px solid #E2E8F0' }}>
      <Text style={{ fontSize: 17, fontWeight: 'bold', color: '#16335B', marginBottom: 8 }}>九个恒定属性</Text>
      {[
        ['1', '目标定方向不定路径：经营目标给方向，不锁死唯一路线'],
        ['2', '探索默认开放：沙箱/原型/试验无需先证明必然有效'],
        ['3', '在实践中进化：缩短提出—尝试—观察—学习循环'],
        ['4', '团队是生命组织：成员与拓扑可自行形成与重组'],
        ['5', '证据用于学习：不足则继续探索，不作探索许可证'],
        ['6', '资源自治：以总池与机会成本管理，非逐任务审批'],
        ['7', '边界落在现实影响：资金/生产/敏感数据/法律责任才门禁'],
        ['8', '自由但不脱离现实：重要行动可追溯，人类保留接管权'],
        ['9', '长期连续开放演化：阶段结束不抹掉学习与能力积累'],
      ].map(([n, t]) => (
        <Box key={n} style={{ flexDirection: 'row', gap: 12, alignItems: 'flex-start', padding: '5px 0', borderBottom: '1px dashed #DCE5F0' }}>
          <Box style={{ flex: '0 0 26px', height: 26, borderRadius: 7, background: '#2E6DB4', alignItems: 'center', justifyContent: 'center' }}>
            <Text style={{ fontSize: 14, fontWeight: 'bold', color: '#FFFFFF' }}>{n}</Text>
          </Box>
          <Text style={{ fontSize: 14, color: '#1F2A37', lineHeight: 1.45 }}>{t}</Text>
        </Box>
      ))}
      <Text style={{ fontSize: 12, color: '#64748B', marginTop: 10, lineHeight: 1.5 }}>规范源：docs/definitions/REGENT-DEFINITION-3.0.txt（取代 1.0/2.0；勿再引用 1.0「明确终止」）</Text>
    </Box>
  </Box>

  <Box style={{ height: 60, padding: '0 64px', flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', borderTop: '1px solid #E2E8F0', position: 'absolute', bottom: 0, left: 0, right: 0 }}>
    <Text style={{ fontSize: 14, color: '#64748B' }}>Regent 系统介绍 · 树米科技</Text>
    <Text style={{ fontSize: 14, color: '#64748B' }}>07 / 20</Text>
  </Box>
</Slide>

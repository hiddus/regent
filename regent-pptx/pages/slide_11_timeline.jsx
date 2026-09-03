<Slide style={{ width: '1280px', height: '720px', background: '#FFFFFF', fontFamily: "'PingFang SC','Microsoft YaHei','Helvetica Neue',sans-serif", overflow: 'hidden' }}>
  <Box style={{ height: 120, padding: '28px 64px 0 64px', flexDirection: 'row', justifyContent: 'space-between', alignItems: 'flex-start' }}>
    <Box>
      <Text style={{ fontSize: 16, color: '#64748B', letterSpacing: 1 }}>第三章 · 框架演变</Text>
      <Text style={{ fontSize: 34, fontWeight: 'bold', color: '#1E293B', marginTop: 6 }}>框架演变时间线</Text>
    </Box>
    <Text style={{ fontSize: 16, color: '#3B82F6', fontWeight: 'bold', paddingTop: 10 }}>11</Text>
  </Box>

  <Box style={{ height: 540, padding: '0 64px', alignItems: 'center', justifyContent: 'center' }}>
    <svg width={1152} height={460} viewBox='0 0 1152 460'>
      <line x1={100} y1={210} x2={1060} y2={210} stroke='#CBD5E1' strokeWidth={4} />
      <line x1={100} y1={210} x2={1060} y2={210} stroke='url(#g)' strokeWidth={4} opacity={0} />

      <circle cx={100} cy={210} r={16} fill='#3B82F6' />
      <circle cx={340} cy={210} r={16} fill='#06B6D4' />
      <circle cx={580} cy={210} r={16} fill='#3B82F6' />
      <circle cx={820} cy={210} r={16} fill='#06B6D4' />
      <circle cx={1060} cy={210} r={18} fill='#EF4444' />

      <text x={100} y={150} textAnchor='middle' fontFamily="'PingFang SC','Microsoft YaHei',sans-serif" fontSize={20} fontWeight='bold' fill='#1E293B'>P0 内核</text>
      <text x={340} y={150} textAnchor='middle' fontFamily="'PingFang SC','Microsoft YaHei',sans-serif" fontSize={20} fontWeight='bold' fill='#1E293B'>P1 闭环</text>
      <text x={580} y={150} textAnchor='middle' fontFamily="'PingFang SC','Microsoft YaHei',sans-serif" fontSize={20} fontWeight='bold' fill='#1E293B'>P2 治理</text>
      <text x={820} y={150} textAnchor='middle' fontFamily="'PingFang SC','Microsoft YaHei',sans-serif" fontSize={20} fontWeight='bold' fill='#1E293B'>P3 测量</text>
      <text x={1060} y={150} textAnchor='middle' fontFamily="'PingFang SC','Microsoft YaHei',sans-serif" fontSize={20} fontWeight='bold' fill='#EF4444'>P4 末期</text>

      <text x={100} y={268} textAnchor='middle' fontFamily="'PingFang SC','Microsoft YaHei',sans-serif" fontSize={16} fill='#64748B'>主循环定义</text>
      <text x={100} y={290} textAnchor='middle' fontFamily="'PingFang SC','Microsoft YaHei',sans-serif" fontSize={16} fill='#64748B'>但从未闭合</text>

      <text x={340} y={268} textAnchor='middle' fontFamily="'PingFang SC','Microsoft YaHei',sans-serif" fontSize={16} fill='#64748B'>运营 + 验收</text>
      <text x={340} y={290} textAnchor='middle' fontFamily="'PingFang SC','Microsoft YaHei',sans-serif" fontSize={16} fill='#64748B'>流程仍空转</text>

      <text x={580} y={268} textAnchor='middle' fontFamily="'PingFang SC','Microsoft YaHei',sans-serif" fontSize={16} fill='#64748B'>拆角色、定契约</text>
      <text x={580} y={290} textAnchor='middle' fontFamily="'PingFang SC','Microsoft YaHei',sans-serif" fontSize={16} fill='#64748B'>9 码 0 引用</text>

      <text x={820} y={268} textAnchor='middle' fontFamily="'PingFang SC','Microsoft YaHei',sans-serif" fontSize={16} fill='#64748B'>评估协议</text>
      <text x={820} y={290} textAnchor='middle' fontFamily="'PingFang SC','Microsoft YaHei',sans-serif" fontSize={16} fill='#64748B'>基线未稳就扩</text>

      <text x={1060} y={268} textAnchor='middle' fontFamily="'PingFang SC','Microsoft YaHei',sans-serif" fontSize={16} fill='#EF4444'>编排 4400 行</text>
      <text x={1060} y={290} textAnchor='middle' fontFamily="'PingFang SC','Microsoft YaHei',sans-serif" fontSize={16} fill='#EF4444'>卡死 8 例</text>

      <text x={580} y={370} textAnchor='middle' fontFamily="'PingFang SC','Microsoft YaHei',sans-serif" fontSize={22} fontWeight='bold' fill='#1E293B'>复杂度一路向上，可交付能力一路向下</text>
      <text x={580} y={402} textAnchor='middle' fontFamily="'PingFang SC','Microsoft YaHei',sans-serif" fontSize={16} fill='#64748B'>每一层「框架」都在加，但主循环始终没被补上</text>
    </svg>
  </Box>

  <Box style={{ height: 60, padding: '0 64px', flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', borderTop: '1px solid #E2E8F0' }}>
    <Text style={{ fontSize: 14, color: '#64748B' }}>Regent 失败案例复盘</Text>
    <Text style={{ fontSize: 14, color: '#64748B' }}>11 / 20</Text>
  </Box>
</Slide>

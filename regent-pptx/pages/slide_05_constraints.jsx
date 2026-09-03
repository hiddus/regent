<Slide style={{ width: '1280px', height: '720px', background: '#FFFFFF', fontFamily: "'PingFang SC','Microsoft YaHei','Helvetica Neue',sans-serif", overflow: 'hidden' }}>
  <Box style={{ height: 120, padding: '28px 64px 0 64px', flexDirection: 'row', justifyContent: 'space-between', alignItems: 'flex-start' }}>
    <Box>
      <Text style={{ fontSize: 16, color: '#64748B', letterSpacing: 1 }}>第一章 · 项目目标</Text>
      <Text style={{ fontSize: 34, fontWeight: 'bold', color: '#1E293B', marginTop: 6 }}>目标的硬约束</Text>
    </Box>
    <Text style={{ fontSize: 16, color: '#3B82F6', fontWeight: 'bold', paddingTop: 10 }}>05</Text>
  </Box>

  <Box style={{ height: 540, padding: '8px 64px 0 64px', flexDirection: 'row', gap: 40 }}>
    <Box style={{ width: 320, justifyContent: 'center' }}>
      <Text style={{ fontSize: 22, fontWeight: 'bold', color: '#1E293B', lineHeight: 1.5 }}>
        这些约束写在 PRD 的「不可变原则」里
      </Text>
      <Text style={{ fontSize: 18, color: '#64748B', lineHeight: 1.8, marginTop: 18 }}>
        它们本身完全正确 —— 问题从来不是「定得不对」，而是「后面没被遵守」。
      </Text>
      <Box style={{ flexDirection: 'row', alignItems: 'center', gap: 12, marginTop: 22 }}>
        <FAIcon name='info-circle' style={{ fill: '#3B82F6', width: 28, height: 28 }} />
        <Text style={{ fontSize: 18, color: '#3B82F6', fontWeight: 'bold' }}>定义优秀，不等于执行到位</Text>
      </Box>
    </Box>

    <Box style={{ flex: 1 }}>
      <Table
        style={{ width: '100%', height: '100%' }}
        defaultTextStyle={{ fontSize: 17, textAlign: 'left', color: '#1E293B' }}
        defaultCellStyle={{
          border: { left: { width: 1, color: '#E2E8F0' }, right: { width: 1, color: '#E2E8F0' }, top: { width: 1, color: '#E2E8F0' }, bottom: { width: 1, color: '#E2E8F0' } },
          padding: 14,
        }}
        cells={[
          [
            { text: '硬约束', textStyle: { bold: true, color: '#FFFFFF', fontSize: 18 }, cellStyle: { background: { color: '#3B82F6' } } },
            { text: '它的真实含义', textStyle: { bold: true, color: '#FFFFFF', fontSize: 18 }, cellStyle: { background: { color: '#3B82F6' } } },
          ],
          [
            { text: '生产权限门禁', textStyle: { bold: true, color: '#1E293B' } },
            { text: '沙箱探索开放；扩大现实生产权限才需对照证据' },
          ],
          [
            { text: '实验驱动', textStyle: { bold: true, color: '#1E293B' } },
            { text: '晋级生产默认 / 扩大现实权限前要有对照实验' },
          ],
          [
            { text: '闭环优先', textStyle: { bold: true, color: '#1E293B' } },
            { text: '目标到交付必须端到端跑通一次' },
          ],
          [
            { text: '失败可终态', textStyle: { bold: true, color: '#1E293B' } },
            { text: '状态机不能悬停，失败要明确终态' },
          ],
          [
            { text: '协议真用', textStyle: { bold: true, color: '#1E293B' } },
            { text: '失败码 / 交接契约要被代码消费' },
          ],
        ]}
      />
    </Box>
  </Box>

  <Box style={{ height: 60, padding: '0 64px', flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', borderTop: '1px solid #E2E8F0' }}>
    <Text style={{ fontSize: 14, color: '#64748B' }}>Regent 失败案例复盘</Text>
    <Text style={{ fontSize: 14, color: '#64748B' }}>05 / 20</Text>
  </Box>
</Slide>

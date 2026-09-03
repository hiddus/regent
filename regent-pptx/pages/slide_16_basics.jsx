<Slide style={{ width: '1280px', height: '720px', background: '#FFFFFF', fontFamily: "'PingFang SC','Microsoft YaHei','Helvetica Neue',sans-serif", overflow: 'hidden' }}>
  <Box style={{ height: 120, padding: '28px 64px 0 64px', flexDirection: 'row', justifyContent: 'space-between', alignItems: 'flex-start' }}>
    <Box>
      <Text style={{ fontSize: 16, color: '#64748B', letterSpacing: 1 }}>第四章 · 失败原因</Text>
      <Text style={{ fontSize: 34, fontWeight: 'bold', color: '#1E293B', marginTop: 6 }}>根因 → 漏掉的基本功</Text>
    </Box>
    <Text style={{ fontSize: 16, color: '#3B82F6', fontWeight: 'bold', paddingTop: 10 }}>16</Text>
  </Box>

  <Box style={{ height: 540, padding: '8px 64px 0 64px', flexDirection: 'row', gap: 40 }}>
    <Box style={{ width: 320, justifyContent: 'center' }}>
      <Text style={{ fontSize: 22, fontWeight: 'bold', color: '#1E293B', lineHeight: 1.5 }}>
        每一个根因，<br />都对应一项被跳过的基本功
      </Text>
      <Text style={{ fontSize: 18, color: '#64748B', lineHeight: 1.8, marginTop: 18 }}>
        失败的不是「不懂高级架构」，<br />而是<span style={{ color: '#3B82F6', fontWeight: 'bold' }}>地基没打就盖楼</span>。
      </Text>
      <Box style={{ flexDirection: 'row', alignItems: 'center', gap: 12, marginTop: 22 }}>
        <FAIcon name='check-circle' style={{ fill: '#3B82F6', width: 28, height: 28 }} />
        <Text style={{ fontSize: 18, color: '#3B82F6', fontWeight: 'bold' }}>先补基本功，再谈进阶</Text>
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
            { text: '根因', textStyle: { bold: true, color: '#FFFFFF', fontSize: 18 }, cellStyle: { background: { color: '#3B82F6' } } },
            { text: '漏掉的基本功', textStyle: { bold: true, color: '#FFFFFF', fontSize: 18 }, cellStyle: { background: { color: '#3B82F6' } } },
          ],
          [{ text: 'A 主循环未闭合', textStyle: { bold: true } }, { text: '先让单 Agent 把一件事跑通（闭环优先）' }],
          [{ text: 'B 控制流当能力', textStyle: { bold: true } }, { text: '能力 = 跑通的产物，不是调度器' }],
          [{ text: 'C 工程卫生缺失', textStyle: { bold: true } }, { text: '地基：可追踪、可校验、零魔法字符串' }],
          [{ text: 'D 优先级倒置', textStyle: { bold: true } }, { text: '顺序：先稳交付闭环 → 再扩现实生产权限' }],
          [{ text: 'E 元工作替代', textStyle: { bold: true } }, { text: '交付 > 元工作，用进度说话' }],
        ]}
      />
    </Box>
  </Box>

  <Box style={{ height: 60, padding: '0 64px', flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', borderTop: '1px solid #E2E8F0' }}>
    <Text style={{ fontSize: 14, color: '#64748B' }}>Regent 失败案例复盘</Text>
    <Text style={{ fontSize: 14, color: '#64748B' }}>16 / 20</Text>
  </Box>
</Slide>

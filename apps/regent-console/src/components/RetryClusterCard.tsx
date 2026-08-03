import { useState } from 'react'
import type { Message } from '../lib/types'
import type { RetryCluster } from '../lib/retryClusters'
import { summarizeGapReasons } from '../lib/retryClusters'
import { MarkdownBody } from './MarkdownBody'

interface RetryClusterCardProps {
  cluster: RetryCluster
}

function roundLabel(m: Message): string {
  const t = m.message_type
  if (t === 'GENERATION_ATTEMPT_FAILED') return '生成未通过'
  if (t === 'DELIVERY_GAP_CAPABILITY_ESCALATED') return '交付缺口 · 升级重试'
  if (t === 'PROJECT_AGENT_SESSION_RESUMED') return '同 Session 续跑'
  return t || '过程'
}

/** One card for N identical retry / escalation bubbles. */
export function RetryClusterCard({ cluster }: RetryClusterCardProps) {
  const [open, setOpen] = useState(false)
  const reasons = summarizeGapReasons(cluster.latest)
  const n = cluster.attemptCount

  return (
    <article className="message assistant message-noise retry-cluster">
      <div className="avatar" aria-hidden>R</div>
      <div className="body">
        <div className="meta">
          <span>Regent</span>
          <span className="meta-chip">过程</span>
          <span className="meta-chip retry-count">重试 ×{n}</span>
        </div>
        <div className="retry-cluster-card">
          <p className="retry-cluster-title">
            交付仍未通过，系统已按相同原因自动重试 {n} 次
          </p>
          {reasons ? (
            <p className="retry-cluster-reasons" title={reasons}>
              共同原因：{reasons}
            </p>
          ) : null}
          <p className="retry-cluster-hint">
            这不是界面刷屏错误 — Core 每轮会写入审计消息；相同失败已合并显示。
          </p>
          <button
            type="button"
            className="md-collapse-toggle"
            onClick={() => setOpen(v => !v)}
          >
            {open ? '收起各次记录' : `查看 ${cluster.messages.length} 条原始记录`}
          </button>
          {open && (
            <ul className="retry-cluster-list">
              {cluster.messages.map((m, idx) => (
                <li key={m.id}>
                  <div className="retry-cluster-item-head">
                    <span>#{idx + 1} {roundLabel(m)}</span>
                    {m.created_at ? (
                      <time dateTime={m.created_at}>
                        {new Date(m.created_at).toLocaleTimeString('zh-CN', {
                          hour: '2-digit',
                          minute: '2-digit',
                          second: '2-digit',
                        })}
                      </time>
                    ) : null}
                  </div>
                  <MarkdownBody collapseAt={220} collapsible>
                    {m.content}
                  </MarkdownBody>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </article>
  )
}

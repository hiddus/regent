import { useState, useRef, useEffect } from 'react'

interface ComposerProps {
  onSend: (text: string) => void
  onUpload: (file: File) => void
  disabled?: boolean
  busy?: boolean
  queuedCount?: number
  goalStatus?: string | null
}

export function Composer({
  onSend,
  onUpload,
  disabled = false,
  busy = false,
  queuedCount = 0,
  goalStatus,
}: ComposerProps) {
  const [text, setText] = useState('')
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const fileRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto'
      textareaRef.current.style.height = Math.min(textareaRef.current.scrollHeight, 200) + 'px'
    }
  }, [text])

  const handleSend = () => {
    const trimmed = text.trim()
    if (!trimmed || disabled) return
    onSend(trimmed)
    setText('')
  }

  const sendWithIntent = (intent: string) => {
    const trimmed = text.trim()
    if (!trimmed || disabled) return
    onSend(`[${intent}] ${trimmed}`)
    setText('')
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

  return (
    <div className="composer-wrap">
      <div className="composer-intents" aria-label="消息意图">
        <button type="button" disabled={disabled || !text.trim()} onClick={() => sendWithIntent('补充信息')}>补充</button>
        <button type="button" disabled={disabled || !text.trim()} onClick={() => sendWithIntent('纠正方向')}>纠错</button>
        <button type="button" disabled={disabled || !text.trim()} onClick={() => sendWithIntent('询问进度')}>问进度</button>
        <button type="button" disabled={disabled || !text.trim()} onClick={() => sendWithIntent('继续执行')}>继续</button>
        {(busy || queuedCount > 0) && (
          <span className="composer-queue-status">
            {queuedCount > 0 ? `已排队 ${queuedCount} 条` : 'Regent 正在处理，可继续输入'}
          </span>
        )}
      </div>
      <div className="composer">
        <textarea
          ref={textareaRef}
          value={text}
          onChange={e => setText(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={
            goalStatus === 'WAITING_HUMAN'
              ? '补充方向即批准并继续…'
              : '描述目标，或指导 Regent 调整方向...'
          }
          rows={1}
          disabled={disabled}
        />
        <div className="actions">
          <div className="actions-left">
            <button
              className="upload-btn"
              onClick={() => fileRef.current?.click()}
              title="上传文件"
            >
              +
            </button>
            <input
              ref={fileRef}
              type="file"
              hidden
              onChange={e => {
                const f = e.target.files?.[0]
                if (f) onUpload(f)
                e.target.value = ''
              }}
            />
          </div>
          <button className="send" onClick={handleSend} disabled={disabled || !text.trim()}>
            ↑
          </button>
        </div>
      </div>
    </div>
  )
}

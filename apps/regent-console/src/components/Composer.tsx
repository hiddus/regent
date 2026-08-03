import { useState, useRef, useEffect } from 'react'
import { api } from '../lib/api'

interface ComposerProps {
  onSend: (text: string) => void
  onUpload: (file: File) => void
  disabled: boolean
  userHint: string
  userHintError: boolean
  coreHint: string
  coreHintError: boolean
  goalStatus?: string | null
  goalId?: string | null
}

export function Composer({
  onSend,
  onUpload,
  disabled,
  userHint,
  userHintError,
  coreHint,
  coreHintError,
  goalStatus,
  goalId,
}: ComposerProps) {
  const [text, setText] = useState('')
  const [sideOpen, setSideOpen] = useState(false)
  const [sideQ, setSideQ] = useState('')
  const [sideBusy, setSideBusy] = useState(false)
  const [sideAnswer, setSideAnswer] = useState<string | null>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const fileRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto'
      textareaRef.current.style.height = Math.min(textareaRef.current.scrollHeight, 200) + 'px'
    }
  }, [text])

  useEffect(() => {
    setSideAnswer(null)
    setSideQ('')
    setSideOpen(false)
  }, [goalId])

  const handleSend = () => {
    const trimmed = text.trim()
    if (!trimmed || disabled) return
    onSend(trimmed)
    setText('')
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

  const askSide = async () => {
    if (!goalId || !sideQ.trim()) return
    setSideBusy(true)
    try {
      const r = await api.sideQuestion(goalId, sideQ.trim())
      setSideAnswer(String(r.text || ''))
      setSideQ('')
    } catch (err) {
      setSideAnswer((err as Error).message)
    } finally {
      setSideBusy(false)
    }
  }

  return (
    <div className="composer-wrap">
      {sideOpen && goalId && (
        <div className="side-question-panel">
          <div className="side-question-row">
            <input
              value={sideQ}
              onChange={e => setSideQ(e.target.value)}
              placeholder="侧问（不改计划）"
              disabled={sideBusy}
              onKeyDown={e => {
                if (e.key === 'Enter') {
                  e.preventDefault()
                  void askSide()
                }
              }}
            />
            <button type="button" disabled={sideBusy || !sideQ.trim()} onClick={() => void askSide()}>
              快问
            </button>
            <button type="button" className="side-close" onClick={() => setSideOpen(false)}>
              收起
            </button>
          </div>
          {sideAnswer ? <p className="hint side-answer">{sideAnswer}</p> : null}
        </div>
      )}
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
            {goalId && (
              <button
                type="button"
                className={`side-ask-btn ${sideOpen ? 'active' : ''}`}
                title="侧问（不改计划）"
                onClick={() => setSideOpen(v => !v)}
              >
                快问
              </button>
            )}
            <div className="hint-stack">
              {userHint && (
                <span className={`hint user-hint ${userHintError ? 'error' : ''}`}>{userHint}</span>
              )}
              {coreHint && (
                <span className={`hint core-hint ${coreHintError ? 'error' : ''}`}>{coreHint}</span>
              )}
            </div>
          </div>
          <button className="send" onClick={handleSend} disabled={disabled || !text.trim()}>
            ↑
          </button>
        </div>
      </div>
    </div>
  )
}

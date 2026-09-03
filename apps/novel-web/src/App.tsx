import { useEffect, useRef, useState } from 'react'
import { api, connectSSE, logout as clearToken } from './api'

type Direction = {card_id:string;title:string;protagonist_desire:string;core_conflict:string;genre_promise:string;pacing:string;differentiator:string}
type Onboarding = {status:string;questions:{question_id:string;prompt:string;options:string[];default_assumption:string}[];directions:Direction[];assumptions:string[]}
type Work = {work_id:string;title:string;genre:string;state:string;latest_chapter_no:number;projection?:{stage_label:string}}
type Progress = {chapter_no:number;state:string;current_step?:string;steps:Record<string,string>}
type Chapter = {title:string;content:string;word_count:number;ai_disclosure:string}

const STEP_LABELS: Record<string,string> = {
  ASSEMBLE:'整理世界与目标', PERFORM:'角色各自行动', DIRECT:'导演编排场景',
  WEAVE:'写成章节', REVIEW:'审校并修订', CANON:'写入事实档案',
}
const STEP_ORDER = ['ASSEMBLE','PERFORM','DIRECT','WEAVE','REVIEW','CANON']
const TERMINAL = new Set(['CANONIZED','TERMINAL_FAILED','CANCELLED'])

export default function App() {
  const [works, setWorks] = useState<Work[]>([])
  const [workId, setWorkId] = useState('')
  const [intent, setIntent] = useState('')
  const [onboarding, setOnboarding] = useState<Onboarding | null>(null)
  const [answers, setAnswers] = useState<Record<string,string>>({})
  const [progress, setProgress] = useState<Progress | null>(null)
  const [chapter, setChapter] = useState<Chapter | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [streaming, setStreaming] = useState(false)
  const sseActive = useRef(false)
  const sseWorkRef = useRef('')

  // --- Init: load token ---
  useEffect(() => { void api('/me').catch(() => {}) }, [])

  // --- URL routing: read on mount ---
  useEffect(() => {
    const m = window.location.pathname.match(/\/w\/(.+)/)
    if (m) {
      const wid = decodeURIComponent(m[1])
      setWorkId(wid)
      void loadWorks().then(() => void loadProgress(wid))
    } else {
      void loadWorks()
    }
    const onPop = () => {
      const pm = window.location.pathname.match(/\/w\/(.+)/)
      const next = pm ? decodeURIComponent(pm[1]) : ''
      setWorkId(prev => {
        if (prev === next) return prev
        if (next) void loadProgress(next)
        else { setOnboarding(null); setProgress(null); setChapter(null) }
        return next
      })
    }
    window.addEventListener('popstate', onPop)
    return () => window.removeEventListener('popstate', onPop)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  function pushUrl(wid: string) {
    const url = wid ? `/w/${encodeURIComponent(wid)}` : '/'
    if (window.location.pathname !== url) window.history.pushState(null, '', url)
  }
  function goHome() {
    setWorkId(''); setOnboarding(null); setProgress(null); setChapter(null); setError('')
    window.history.pushState(null, '', '/')
  }

  // --- SSE: real-time progress ---
  useEffect(() => {
    if (!workId || sseWorkRef.current === workId && sseActive.current) return
    if (onboarding || chapter) return
    sseActive.current = true
    sseWorkRef.current = workId
    setStreaming(true)

    const abort = connectSSE(
      `/works/${workId}/events/stream`,
      (msg) => {
        if (msg.event === 'step_succeeded' && msg.data) {
          try {
            const d = JSON.parse(msg.data)
            const step = d.data?.step as string | undefined
            if (step) setProgress(p => p ? {...p, steps:{...p.steps, [step]:'SUCCEEDED'}} : p)
          } catch { /* ignore parse errors */ }
        } else if (msg.event === 'chapter_done' && msg.data) {
          try {
            const d = JSON.parse(msg.data)
            const no = d.chapter_no as number
            void api<Chapter>(`/works/${workId}/chapters/${no}`).then(setChapter)
            void loadWorks()
            setProgress(p => p ? {...p, state:'CANONIZED'} : p)
          } catch { /* ignore */ }
        } else if (msg.event === 'stream_error') {
          setError('生成过程遇到问题，请检查进度')
        }
      },
      {
        onError: (e) => { setStreaming(false); setError(`连接中断: ${e.message}`) },
        onRetry: () => setStreaming(true),
      },
    )
    return () => { sseActive.current = false; abort() }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workId, !!onboarding, !!chapter])

  // --- Data helpers ---
  async function loadWorks() {
    try { return await api<Work[]>('/works').then(w => { setWorks(w); return w }) }
    catch { return [] }
  }

  async function loadProgress(wid: string) {
    try {
      const run = await api<Progress>(`/works/${wid}/runs`)
      setProgress(run)
      if (run.state === 'CANONIZED') {
        const ch = await api<Chapter>(`/works/${wid}/chapters/${run.chapter_no}`)
        setChapter(ch)
      }
    } catch { /* no run yet */ }
  }

  // --- Actions ---
  function logout() {
    clearToken(); goHome(); setWorks([])
  }

  async function create() {
    if (!intent.trim()) return
    setBusy(true); setError('')
    try {
      const r = await api<{work_id:string;onboarding:Onboarding}>('/works', {
        method:'POST', body: JSON.stringify({raw_intent: intent, client_nonce: crypto.randomUUID()}),
      })
      setWorkId(r.work_id); pushUrl(r.work_id)
      setOnboarding(r.onboarding); setProgress(null); setChapter(null)
      await loadWorks()
    } catch(e) { setError((e as Error).message) } finally { setBusy(false) }
  }

  async function clarify() {
    setBusy(true); setError('')
    try { setOnboarding(await api<Onboarding>(`/works/${workId}/clarify`, {
      method:'POST', body: JSON.stringify({answers, accept_defaults: true}),
    })) }
    catch(e) { setError((e as Error).message) } finally { setBusy(false) }
  }

  async function choose(cardId: string) {
    setBusy(true); setError('')
    try {
      await api(`/works/${workId}/directions`, {
        method:'POST', body: JSON.stringify({card_id: cardId, client_nonce: crypto.randomUUID()}),
      })
      const run = await api<Progress>(`/works/${workId}/runs`, {
        method:'POST', headers:{'Idempotency-Key': crypto.randomUUID()},
      })
      setProgress(run); setOnboarding(null); setChapter(null)
      await loadWorks()
    } catch(e) { setError((e as Error).message) } finally { setBusy(false) }
  }

  async function openWork(id: string) {
    setWorkId(id); pushUrl(id)
    setOnboarding(null); setChapter(null); setError(''); setProgress(null)
    await loadProgress(id)
  }

  // --- Render ---
  return <div className="shell">
    <header role="banner">
      <a className="brand" href="/" onClick={e=>{e.preventDefault();goHome()}}>造境<span>小说导演</span></a>
      <div className="status">
        <i className={streaming ? 'live' : ''}/>
        {streaming ? '实时连接中' : 'Agent loop 在线'}
        {works.length > 0 && <button className="logout" onClick={logout} aria-label="登出">登出</button>}
      </div>
    </header>
    <main>
      <aside role="navigation" aria-label="作品列表">
        <button className="new" onClick={()=>{goHome();setIntent('')}}>＋ 新故事</button>
        <p className="eyebrow">我的故事</p>
        {works.length === 0 && <p className="muted small">还没有故事，开始第一个吧</p>}
        {works.map(w => (
          <button key={w.work_id} className={`work ${workId===w.work_id?'active':''}`}
            onClick={()=>openWork(w.work_id)} aria-current={workId===w.work_id?'page':undefined}>
            <b>{w.title}</b>
            <small>{w.projection?.stage_label || w.state}</small>
          </button>
        ))}
      </aside>

      <section className="stage" role="main">
        {error && <div className="error" role="alert">
          {error}
          <button onClick={()=>setError('')} aria-label="关闭">×</button>
          <button className="retry" onClick={()=>{setError('');if(workId)void loadProgress(workId)}}>重试</button>
        </div>}

        {/* --- Hero: new story --- */}
        {!workId && <div className="hero">
          <p className="eyebrow">从一句话，到一个持续生长的世界</p>
          <h1>你负责想象，<br/><em>角色负责活起来。</em></h1>
          <p className="lead">告诉我你想写什么。我们先确认方向，再让角色在各自知道的世界里行动，由导演循环编排、写作和修订。</p>
          <div className="composer">
            <textarea value={intent} onChange={e=>setIntent(e.target.value)}
              placeholder="例如：一个能听见旧物记忆的修表匠，发现父亲失踪前修过的最后一块表正在倒着走……"
              aria-label="故事目标" />
            <button disabled={busy||!intent.trim()} onClick={create}>{busy?'正在理解…':'开始构思 →'}</button>
          </div>
          <div className="principles"><span>一次澄清后继续</span><span>角色信息彼此隔离</span><span>每章自动审校修订</span></div>
        </div>}

        {/* --- Onboarding: clarify --- */}
        {onboarding?.questions.length ? <div className="panel">
          <p className="eyebrow">只确认这一次</p>
          <h2>让故事的第一步更准</h2>
          <p className="muted">不想细选也没关系，我们会采用标出的默认方向继续。</p>
          {onboarding.questions.map(q => (
            <fieldset key={q.question_id}>
              <legend>{q.prompt}</legend>
              <div className="chips" role="radiogroup" aria-label={q.prompt}>
                {q.options.map(o => (
                  <button key={o} className={answers[q.question_id]===o?'selected':''}
                    onClick={()=>setAnswers({...answers,[q.question_id]:o})}
                    role="radio" aria-checked={answers[q.question_id]===o}>{o}</button>
                ))}
              </div>
            </fieldset>
          ))}
          <button className="primary" disabled={busy} onClick={clarify}>生成故事方向</button>
        </div> : null}

        {/* --- Onboarding: direction cards --- */}
        {onboarding && !onboarding.questions.length && <div className="panel wide">
          <p className="eyebrow">选择你最想追下去的方向</p>
          <h2>三种不同的故事承诺</h2>
          <div className="cards">
            {onboarding.directions.map((d,i) => (
              <article key={d.card_id}>
                <span>0{i+1}</span><h3>{d.title}</h3><p>{d.differentiator}</p>
                <dl>
                  <dt>主角想要</dt><dd>{d.protagonist_desire}</dd>
                  <dt>核心阻力</dt><dd>{d.core_conflict}</dd>
                  <dt>阅读节奏</dt><dd>{d.pacing}</dd>
                </dl>
                <button disabled={busy} onClick={()=>choose(d.card_id)}>就写这个方向</button>
              </article>
            ))}
          </div>
        </div>}

        {/* --- Progress --- */}
        {progress && !chapter && <div className="panel progress">
          <p className="eyebrow">第 {progress.chapter_no} 章{streaming && <span className="live-dot"> · 实时</span>}</p>
          <h2>{progress.state==='TERMINAL_FAILED'?'这一章需要处理':'角色正在把故事向前推'}</h2>
          <p className="muted">你可以关掉页面，服务器会继续。每一步都已保存，失败不会从头重来。</p>
          <div className="steps" role="list" aria-label="生成步骤">
            {STEP_ORDER.map((s,i) => {
              const st = progress.steps[s]?.toLowerCase() || ''
              return <div className={st} key={s} role="listitem" aria-label={`${STEP_LABELS[s]}: ${st||'等待中'}`}>
                <span>{progress.steps[s]==='SUCCEEDED'?'✓':i+1}</span>
                <p><b>{STEP_LABELS[s]}</b>
                <small>{progress.steps[s]==='SUCCEEDED'?'已完成':progress.steps[s]==='RUNNING'?'进行中':'等待中'}</small></p>
              </div>
            })}
          </div>
        </div>}

        {/* --- Chapter reader --- */}
        {chapter && <article className="reader">
          <div className="readerHead">
            <div>
              <p className="eyebrow">第 {progress?.chapter_no} 章 · {chapter.word_count} 字</p>
              <h1>{chapter.title}</h1>
            </div>
            <button onClick={()=>navigator.clipboard.writeText(chapter.content)} aria-label="复制正文">复制正文</button>
          </div>
          <div className="prose">{chapter.content.split('\n').map((p,i) => <p key={i}>{p}</p>)}</div>
          <footer>{chapter.ai_disclosure}</footer>
        </article>}
      </section>
    </main>
  </div>
}

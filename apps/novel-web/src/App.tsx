import { useEffect, useRef, useState } from 'react'
import { api, connectSSE, logout as clearToken } from './api'

type Direction = {card_id:string;title:string;protagonist_desire:string;core_conflict:string;genre_promise:string;pacing:string;differentiator:string}
type Onboarding = {status:string;questions:{question_id:string;prompt:string;options:string[];default_assumption:string}[];directions:Direction[];assumptions:string[]}
type Work = {work_id:string;title:string;genre:string;state:string;latest_chapter_no:number;projection?:{stage_label:string}}
type Progress = {chapter_no:number;state:string;current_step?:string;steps:Record<string,string>}
type Chapter = {title:string;content:string;word_count:number;ai_disclosure:string}
type CostData = {currency:string;consumed_minor:number;released_minor:number;by_chapter:{chapter_no:number;amount_minor:number}[];by_step:{step:string;amount_minor:number}[]}
type Share = {share_id:string;work_id:string;share_url:string;scope:string;noindex:boolean;revoked_at:string|null;expires_at:string|null}
type SharedWork = {title:string;genre:string;ai_disclosure:string;expires_at:string;chapters:{chapter_no:number;title:string;content:string;word_count:number}[]}

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
  const [cost, setCost] = useState<CostData | null>(null)
  const [shares, setShares] = useState<Share[]>([])
  const [panel, setPanel] = useState<'cost'|'share'|'export'|'fact'|null>(null)
  const [shareLabel, setShareLabel] = useState('')
  const [shareUrl, setShareUrl] = useState('')
  const [factStatement, setFactStatement] = useState('')
  const [factResult, setFactResult] = useState('')
  const [exportNotice, setExportNotice] = useState<{notice_version:string;title:string;body:string;satisfied_at:string|null}|null>(null)
  const [exportResult, setExportResult] = useState<{download_url:string;format:string}|null>(null)
  const [sharedWork, setSharedWork] = useState<SharedWork|null>(null)

  // --- Init: load token ---
  useEffect(() => {
    const share = window.location.pathname.match(/^\/read\/([^/]+)/)
    if (share) {
      void fetch(`/v1/novel/public/shares/${encodeURIComponent(share[1])}`)
        .then(async r => { if (!r.ok) throw new Error('分享已失效或不存在'); return r.json() })
        .then(setSharedWork).catch(e => setError(e.message))
      return
    }
    void api('/me').catch(() => {})
  }, [])

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
    setPanel(null); setCost(null); setShares([]); setShareUrl(''); setExportNotice(null); setExportResult(null); setFactResult('')
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
        if (msg.event === 'chapter.step_succeeded' && msg.data) {
          try {
            const d = JSON.parse(msg.data)
            const step = d.data?.step as string | undefined
            if (step) setProgress(p => p ? {...p, steps:{...p.steps, [step]:'SUCCEEDED'}} : p)
          } catch { /* ignore parse errors */ }
        } else if (msg.event === 'chapter.done' && msg.data) {
          try {
            const d = JSON.parse(msg.data)
            const no = (d.chapter_no ?? d.data?.chapter_no) as number
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

  useEffect(() => {
    if (!workId || !progress || TERMINAL.has(progress.state) || onboarding || chapter) return
    const timer = window.setInterval(() => { void loadProgress(workId) }, 4000)
    return () => window.clearInterval(timer)
  }, [workId, progress?.state, !!onboarding, !!chapter])

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

  // --- Cost / Share / Export / Fact ---
  async function loadCost() {
    if (!workId) return
    try { setCost(await api<CostData>(`/works/${workId}/costs`)) }
    catch(e) { setError((e as Error).message) }
  }
  async function createShare() {
    if (!workId) return
    setBusy(true); setError('')
    try {
      const s = await api<Share>(`/works/${workId}/shares`, {
        method:'POST', body: JSON.stringify({invitee_label: shareLabel || '朋友', scope:'FULL'}),
      })
      // Use window.location.origin so the share URL is always externally accessible
      const token = s.share_url.split('/read/')[1] || ''
      const readableUrl = token ? `${window.location.origin}/read/${token}` : s.share_url
      setShareUrl(readableUrl); setShares(prev => [...prev, {...s, share_url: readableUrl}])
    } catch(e) { setError((e as Error).message) } finally { setBusy(false) }
  }
  async function revokeShare(sid: string) {
    try {
      await api(`/works/${workId}/shares/${sid}`, {method:'DELETE'})
      setShares(prev => prev.map(s => s.share_id===sid ? {...s, revoked_at: new Date().toISOString()} : s))
    } catch(e) { setError((e as Error).message) }
  }
  async function checkExportNotice() {
    if (!workId) return
    try {
      const n = await api<{notice_version:string;title:string;body:string;satisfied_at:string|null}>(`/works/${workId}/export-notice`)
      setExportNotice(n)
    } catch(e) { setError((e as Error).message) }
  }
  async function acknowledgeAndExport() {
    if (!workId || !exportNotice) return
    setBusy(true); setError('')
    try {
      if (!exportNotice.satisfied_at) {
        await api(`/works/${workId}/export-notice/acknowledge`, {
          method:'POST', body: JSON.stringify({notice_version: exportNotice.notice_version}),
        })
      }
      const exp = await api<{export_id:string;download_url:string;format:string}>(`/works/${workId}/exports`, {
        method:'POST', body: JSON.stringify({format:'txt'}),
      })
      setExportResult({download_url: exp.download_url, format: exp.format})
    } catch(e) { setError((e as Error).message) } finally { setBusy(false) }
  }
  async function reportFact() {
    if (!workId || !factStatement.trim()) return
    setBusy(true); setError('')
    try {
      const r = await api<{accepted:boolean;message:string;ticket_id:string}>(`/works/${workId}/facts/report`, {
        method:'POST', body: JSON.stringify({statement: factStatement, chapter_no: progress?.chapter_no, client_nonce: crypto.randomUUID()}),
      })
      setFactResult(r.accepted ? `已记录 (#${r.ticket_id.slice(0,8)})：${r.message}` : `未受理：${r.message}`)
      setFactStatement('')
    } catch(e) { setError((e as Error).message) } finally { setBusy(false) }
  }
  function openPanel(p: 'cost'|'share'|'export'|'fact') {
    setPanel(p)
    if (p === 'cost') void loadCost()
    if (p === 'export') void checkExportNotice()
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

  async function nextChapter() {
    if (!workId) return
    setBusy(true); setError('')
    try {
      const run = await api<Progress>(`/works/${workId}/runs`, {
        method:'POST', headers:{'Idempotency-Key':crypto.randomUUID()},
      })
      setChapter(null); setProgress(run)
    } catch(e) { setError((e as Error).message) } finally { setBusy(false) }
  }

  if (window.location.pathname.startsWith('/read/')) return <div className="sharedPage">
    {error && <div className="panel"><h2>无法打开分享</h2><p className="muted">{error}</p></div>}
    {!error && !sharedWork && <div className="panel"><p className="muted">正在打开故事…</p></div>}
    {sharedWork && <article className="sharedReader">
      <header><p className="eyebrow">{sharedWork.genre || '分享阅读'}</p><h1>{sharedWork.title}</h1></header>
      {sharedWork.chapters.map(ch => <section key={ch.chapter_no}>
        <p className="eyebrow">第 {ch.chapter_no} 章 · {ch.word_count} 字</p><h2>{ch.title}</h2>
        <div className="prose">{ch.content.split('\n').map((p,i)=><p key={i}>{p}</p>)}</div>
      </section>)}
      <footer>{sharedWork.ai_disclosure}</footer>
    </article>}
  </div>

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
          <div className="toolbar">
            <button className="nextChapter" disabled={busy} onClick={nextChapter}>{busy?'正在开始…':'续写下一章 →'}</button>
            <button onClick={()=>openPanel('cost')}>成本明细</button>
            <button onClick={()=>openPanel('share')}>定向分享</button>
            <button onClick={()=>openPanel('export')}>导出作品</button>
            <button onClick={()=>openPanel('fact')}>事实报错</button>
          </div>
          <div className="prose">{chapter.content.split('\n').map((p,i) => <p key={i}>{p}</p>)}</div>
          <footer>{chapter.ai_disclosure}</footer>
        </article>}

        {/* --- Cost panel (FR-19) --- */}
        {panel === 'cost' && workId && <div className="panel sub">
          <div className="panelTop"><h2>成本明细</h2><button className="x" onClick={()=>setPanel(null)}>×</button></div>
          {cost ? <div>
            <p className="big">{((cost.consumed_minor) / 100).toFixed(2)} <small>{cost.currency}</small></p>
            {cost.by_chapter.length > 0 && <div><p className="eyebrow">按章节</p>
              {cost.by_chapter.map(c => <div className="costRow" key={c.chapter_no}><span>第 {c.chapter_no} 章</span><span>{(c.amount_minor/100).toFixed(2)}</span></div>)}
            </div>}
            {cost.by_step.length > 0 && <div><p className="eyebrow">按步骤</p>
              {cost.by_step.map(s => <div className="costRow" key={s.step}><span>{STEP_LABELS[s.step]||s.step}</span><span>{(s.amount_minor/100).toFixed(2)}</span></div>)}
            </div>}
          </div> : <p className="muted">加载中…</p>}
        </div>}

        {/* --- Share panel (FR-17) --- */}
        {panel === 'share' && workId && <div className="panel sub">
          <div className="panelTop"><h2>定向分享</h2><button className="x" onClick={()=>setPanel(null)}>×</button></div>
          <p className="muted">生成一个链接发给朋友，仅被邀请者可读，不会被搜索引擎收录。</p>
          <div className="shareForm">
            <input placeholder="对方称呼（可选）" value={shareLabel} onChange={e=>setShareLabel(e.target.value)} aria-label="邀请对象"/>
            <button disabled={busy} onClick={createShare}>{busy?'生成中…':'生成链接'}</button>
          </div>
          {shareUrl && <div className="shareResult"><p>分享链接已生成：</p><code onClick={()=>navigator.clipboard.writeText(shareUrl)} title="点击复制">{shareUrl}</code></div>}
          {shares.filter(s=>!s.revoked_at).length > 0 && <div><p className="eyebrow">有效分享</p>
            {shares.filter(s=>!s.revoked_at).map(s => <div className="shareRow" key={s.share_id}><code className="sm">{s.share_url.slice(0,50)}…</code><button onClick={()=>revokeShare(s.share_id)}>撤回</button></div>)}
          </div>}
        </div>}

        {/* --- Export panel (FR-16/23) --- */}
        {panel === 'export' && workId && <div className="panel sub">
          <div className="panelTop"><h2>导出作品</h2><button className="x" onClick={()=>setPanel(null)}>×</button></div>
          {exportNotice && !exportNotice.satisfied_at && <div className="exportNotice"><p><b>{exportNotice.title}</b></p><p className="muted">{exportNotice.body}</p>
            <button className="primary" disabled={busy} onClick={acknowledgeAndExport}>{busy?'处理中…':'知悉并导出'}</button></div>}
          {exportNotice?.satisfied_at && !exportResult && <div><p className="muted">已确认导出告知。</p><button className="primary" disabled={busy} onClick={acknowledgeAndExport}>{busy?'导出中…':'导出 TXT'}</button></div>}
          {exportResult && <div className="shareResult"><p>导出成功：</p><a href={exportResult.download_url} download className="primary dl">下载 {exportResult.format.toUpperCase()}</a></div>}
          {!exportNotice && <p className="muted">加载中…</p>}
        </div>}

        {/* --- Fact report panel (FR-11) --- */}
        {panel === 'fact' && workId && <div className="panel sub">
          <div className="panelTop"><h2>事实报错</h2><button className="x" onClick={()=>setPanel(null)}>×</button></div>
          <p className="muted">发现前后矛盾或与设定不符的事实？请描述问题，系统会在后续章节修正。</p>
          <textarea className="factInput" value={factStatement} onChange={e=>setFactStatement(e.target.value)} placeholder="例如：第1章说修表匠的店铺在城东，但第3章变成了城西…" aria-label="事实错误描述"/>
          <button className="primary" disabled={busy||!factStatement.trim()} onClick={reportFact}>{busy?'提交中…':'提交报错'}</button>
          {factResult && <p className="factResult">{factResult}</p>}
        </div>}
      </section>
    </main>
  </div>
}

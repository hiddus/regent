import { useEffect, useState } from 'react'
import { api } from './api'

type Direction = {card_id:string,title:string,protagonist_desire:string,core_conflict:string,genre_promise:string,pacing:string,differentiator:string}
type Onboarding = {status:string;questions:{question_id:string;prompt:string;options:string[];default_assumption:string}[];directions:Direction[];assumptions:string[]}
type Work = {work_id:string;title:string;genre:string;state:string;latest_chapter_no:number;projection?:{stage_label:string}}
type Progress = {chapter_no:number;state:string;current_step?:string;steps:Record<string,string>}
type Chapter = {title:string;content:string;word_count:number;ai_disclosure:string}

const labels:Record<string,string> = {ASSEMBLE:'整理世界与目标',PERFORM:'角色各自行动',DIRECT:'导演编排场景',WEAVE:'写成章节',REVIEW:'审校并修订',CANON:'写入事实档案'}

export default function App() {
  const [works,setWorks] = useState<Work[]>([])
  const [workId,setWorkId] = useState('')
  const [intent,setIntent] = useState('')
  const [onboarding,setOnboarding] = useState<Onboarding|null>(null)
  const [answers,setAnswers] = useState<Record<string,string>>({})
  const [progress,setProgress] = useState<Progress|null>(null)
  const [chapter,setChapter] = useState<Chapter|null>(null)
  const [busy,setBusy] = useState(false)
  const [error,setError] = useState('')

  const loadWorks = () => api<Work[]>('/works').then(setWorks).catch(e=>setError(e.message))
  useEffect(()=>{ void loadWorks() },[])
  useEffect(()=>{
    if (!workId || !progress || ['CANONIZED','TERMINAL_FAILED','CANCELLED'].includes(progress.state)) return
    const timer = window.setInterval(async()=>{
      try {
        const next = await api<Progress>(`/works/${workId}/runs`)
        setProgress(next)
        if (next.state === 'CANONIZED') {
          setChapter(await api<Chapter>(`/works/${workId}/chapters/${next.chapter_no}`))
          loadWorks()
        }
      } catch (e) { setError((e as Error).message) }
    },2500)
    return ()=>clearInterval(timer)
  },[workId,progress?.state])

  async function create() {
    if (!intent.trim()) return
    setBusy(true); setError('')
    try {
      const result = await api<{work_id:string;onboarding:Onboarding}>('/works',{method:'POST',body:JSON.stringify({raw_intent:intent,client_nonce:crypto.randomUUID()})})
      setWorkId(result.work_id); setOnboarding(result.onboarding); await loadWorks()
    } catch(e) { setError((e as Error).message) } finally { setBusy(false) }
  }
  async function clarify() {
    setBusy(true); setError('')
    try { setOnboarding(await api<Onboarding>(`/works/${workId}/clarify`,{method:'POST',body:JSON.stringify({answers,accept_defaults:true})})) }
    catch(e){setError((e as Error).message)} finally{setBusy(false)}
  }
  async function choose(cardId:string) {
    setBusy(true); setError('')
    try {
      await api(`/works/${workId}/directions`,{method:'POST',body:JSON.stringify({card_id:cardId,client_nonce:crypto.randomUUID()})})
      const run = await api<Progress>(`/works/${workId}/runs`,{method:'POST',headers:{'Idempotency-Key':crypto.randomUUID()}})
      setProgress(run); setOnboarding(null); await loadWorks()
    } catch(e){setError((e as Error).message)} finally{setBusy(false)}
  }
  async function openWork(id:string) {
    setWorkId(id); setOnboarding(null); setChapter(null); setError('')
    try {
      const run = await api<Progress>(`/works/${id}/runs`); setProgress(run)
      if(run.state==='CANONIZED') setChapter(await api<Chapter>(`/works/${id}/chapters/${run.chapter_no}`))
    } catch(e){setError((e as Error).message)}
  }

  const ordered = ['ASSEMBLE','PERFORM','DIRECT','WEAVE','REVIEW','CANON']
  return <div className="shell">
    <header><a className="brand" href="/">造境<span>小说导演</span></a><div className="status"><i/> Agent loop 在线</div></header>
    <main>
      <aside>
        <button className="new" onClick={()=>{setWorkId('');setOnboarding(null);setProgress(null);setChapter(null)}}>＋ 新故事</button>
        <p className="eyebrow">我的故事</p>
        {works.map(w=><button key={w.work_id} className={`work ${workId===w.work_id?'active':''}`} onClick={()=>openWork(w.work_id)}><b>{w.title}</b><small>{w.projection?.stage_label || w.state}</small></button>)}
      </aside>
      <section className="stage">
        {error && <div className="error">{error}<button onClick={()=>setError('')}>×</button></div>}
        {!workId && <div className="hero">
          <p className="eyebrow">从一句话，到一个持续生长的世界</p>
          <h1>你负责想象，<br/><em>角色负责活起来。</em></h1>
          <p className="lead">告诉我你想写什么。我们先确认方向，再让角色在各自知道的世界里行动，由导演循环编排、写作和修订。</p>
          <div className="composer"><textarea value={intent} onChange={e=>setIntent(e.target.value)} placeholder="例如：一个能听见旧物记忆的修表匠，发现父亲失踪前修过的最后一块表正在倒着走……"/><button disabled={busy||!intent.trim()} onClick={create}>{busy?'正在理解…':'开始构思 →'}</button></div>
          <div className="principles"><span>一次澄清后继续</span><span>角色信息彼此隔离</span><span>每章自动审校修订</span></div>
        </div>}
        {onboarding?.questions.length ? <div className="panel"><p className="eyebrow">只确认这一次</p><h2>让故事的第一步更准</h2><p className="muted">不想细选也没关系，我们会采用标出的默认方向继续。</p>{onboarding.questions.map(q=><fieldset key={q.question_id}><legend>{q.prompt}</legend><div className="chips">{q.options.map(o=><button className={answers[q.question_id]===o?'selected':''} onClick={()=>setAnswers({...answers,[q.question_id]:o})}>{o}</button>)}</div></fieldset>)}<button className="primary" disabled={busy} onClick={clarify}>生成故事方向</button></div>:null}
        {onboarding && !onboarding.questions.length && <div className="panel wide"><p className="eyebrow">选择你最想追下去的方向</p><h2>三种不同的故事承诺</h2><div className="cards">{onboarding.directions.map((d,i)=><article key={d.card_id}><span>0{i+1}</span><h3>{d.title}</h3><p>{d.differentiator}</p><dl><dt>主角想要</dt><dd>{d.protagonist_desire}</dd><dt>核心阻力</dt><dd>{d.core_conflict}</dd><dt>阅读节奏</dt><dd>{d.pacing}</dd></dl><button disabled={busy} onClick={()=>choose(d.card_id)}>就写这个方向</button></article>)}</div></div>}
        {progress && !chapter && <div className="panel progress"><p className="eyebrow">第 {progress.chapter_no} 章</p><h2>{progress.state==='TERMINAL_FAILED'?'这一章需要处理':'角色正在把故事向前推'}</h2><p className="muted">你可以关掉页面，服务器会继续。每一步都已保存，失败不会从头重来。</p><div className="steps">{ordered.map((s,i)=><div className={progress.steps[s]?.toLowerCase()||''} key={s}><span>{progress.steps[s]==='SUCCEEDED'?'✓':i+1}</span><p><b>{labels[s]}</b><small>{progress.steps[s]==='RUNNING'?'进行中':progress.steps[s]==='SUCCEEDED'?'已完成':'等待中'}</small></p></div>)}</div></div>}
        {chapter && <article className="reader"><div className="readerHead"><div><p className="eyebrow">第 {progress?.chapter_no} 章 · {chapter.word_count} 字</p><h1>{chapter.title}</h1></div><button onClick={()=>navigator.clipboard.writeText(chapter.content)}>复制正文</button></div><div className="prose">{chapter.content.split('\n').map((p,i)=><p key={i}>{p}</p>)}</div><footer>{chapter.ai_disclosure}</footer></article>}
      </section>
    </main>
  </div>
}

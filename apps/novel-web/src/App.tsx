import { useEffect, useRef, useState, type UIEvent } from 'react'
import { api, connectSSE, logout as clearToken } from './api'
import { Icon, Sheet, Segmented } from './ui'

function uuid(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') return crypto.randomUUID()
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, c => {
    const r = Math.random() * 16 | 0
    return (c === 'x' ? r : (r & 0x3 | 0x8)).toString(16)
  })
}

type Direction = {card_id:string;title:string;protagonist_desire:string;core_conflict:string;genre_promise:string;pacing:string;differentiator:string}
type Onboarding = {status:string;questions:{question_id:string;prompt:string;options:string[];default_assumption:string}[];directions:Direction[];assumptions:string[]}
type Work = {work_id:string;title:string;genre:string;state:string;latest_chapter_no:number;projection?:{stage_label:string}}
type Progress = {chapter_no:number;state:string;current_step?:string;steps:Record<string,string>;auto_advance?:boolean;awaiting_input?:boolean;scene_no?:number;scene_count?:number;completed_scenes?:number}
type Chapter = {title:string;content:string;word_count:number;ai_disclosure:string}
type ChapterSummary = {chapter_no:number;title:string;word_count:number;state:string;attempt:number;version_count:number}
type ChapterVersion = {attempt:number;title:string;word_count:number;state:string;created_at:string|null}
type Character = {name:string;identity:string;drive:string;voice:string;traits:string[]}
type ArcNode = {arc_no:number;title:string;arc_type:string;chapter_range_start:number;chapter_range_end:number;core_conflict:string;resolution_type:string}
type Volume = {volume_no:number;title:string;cultivation_realm:string;start_chapter_no:number;end_chapter_no:number;state:string;summary:object[];arcs:ArcNode[]}
type CostData = {currency:string;consumed_minor:number;released_minor:number;by_chapter:{chapter_no:number;amount_minor:number}[];by_step:{step:string;amount_minor:number}[]}
type Share = {share_id:string;work_id:string;share_url:string;scope:string;noindex:boolean;revoked_at:string|null;expires_at:string|null}
type SharedWork = {title:string;genre:string;ai_disclosure:string;expires_at:string;chapters:{chapter_no:number;title:string;content:string;word_count:number}[]}
type AgentMessage = {id:string;step:string;label:string;status:'thinking'|'done';summary?:string;title?:string;content?:string;wordCount?:number;performances?:{persona:string;immediate_goal:string;actions:string[];emotional_shift:string}[];sceneGoal?:string;beats?:string[];endingHook?:string;passed?:boolean;issuesCount?:number;timestamp:number}
type QualityReport = {passed:boolean;issues:string[];revised:boolean;score?:number}
type Decision = {decision_id:string;work_id:string;chapter_no:number;state:string;trigger_summary:string;why_human:string;options:{option_id:string;label:string;near_term_consequence:string;reversibility:string}[];default_option_id:string|null;deadline:string|null;impact_level:string;impact_horizon_chapters:number;confirm_nonce:string;version:number}
type ModerationCase = {case_id:string;work_id:string;chapter_no:number|null;target_type:string;decision:string;reason_code:string|null;detail:string;appealed_at:string|null;resolved_at:string|null}
type CriticalPathNode = {node_id:string;ordinal:number;title:string;promise:string;preconditions:string[];consequences:string[];requires_human?:boolean;locked?:boolean;volume_no?:number;arc_no?:number}
type PathImpact = {affected_chapters:number[];frozen_conflict:boolean;frozen_through_chapter:number;eta_minutes_min:number|null;eta_minutes_max:number|null;cost_ceiling_minor:number|null;currency:string}
type SheetKind = null|'catalog'|'account'|'reader'|'cost'|'share'|'export'|'fact'|'inbox'|'moderation'
type ThemeKind = 'day'|'sepia'|'night'
type CatalogTab = 'chapters'|'outline'|'chars'|'volumes'

const STEP_LABELS: Record<string,string> = {
  ASSEMBLE:'整理世界与目标', PERFORM:'角色各自行动', DIRECT:'导演编排场景',
  PRODUCE:'导演指导场景创作', WEAVE:'写成章节', REVIEW:'检查章节一致性', CANON:'写入事实档案',
}
const STEP_ORDER = ['ASSEMBLE','PERFORM','DIRECT','WEAVE','REVIEW','CANON']
const DIRECTOR_STEP_ORDER = ['ASSEMBLE','DIRECT','PRODUCE','REVIEW','CANON']
const TERMINAL = new Set(['CANONIZED','TERMINAL_FAILED','CANCELLED'])
// FR-15 / FR-23：AI 显著标识。后端返回完整披露文案，缺失时用最短标识兜底。
const AI_FALLBACK = '本篇内容由 AI 参与生成'

export default function App() {
  const [works, setWorks] = useState<Work[]>([])
  const [workId, setWorkId] = useState('')
  const [intent, setIntent] = useState('')
  const [onboarding, setOnboarding] = useState<Onboarding | null>(null)
  const [answers, setAnswers] = useState<Record<string,string>>({})
  const [progress, setProgress] = useState<Progress | null>(null)
  const [chapter, setChapter] = useState<Chapter | null>(null)
  const [busy, setBusy] = useState(false)
  const [choosingDirection, setChoosingDirection] = useState(false)
  const [error, setError] = useState('')
  const [streaming, setStreaming] = useState(false)
  const sseActive = useRef(false)
  const sseWorkRef = useRef('')
  const [cost, setCost] = useState<CostData | null>(null)
  const [shares, setShares] = useState<Share[]>([])
  const [sheet, setSheet] = useState<SheetKind>(null)
  const [shareLabel, setShareLabel] = useState('')
  const [shareUrl, setShareUrl] = useState('')
  const [factStatement, setFactStatement] = useState('')
  const [factResult, setFactResult] = useState('')
  // C-01：完结/暂停作品报错后，重演要等用户明确恢复才执行——把动作交到用户手上
  const [factPendingResume, setFactPendingResume] = useState('')
  const [exportNotice, setExportNotice] = useState<{notice_version:string;title:string;body:string;satisfied_at:string|null}|null>(null)
  const [exportResult, setExportResult] = useState<{download_url:string;format:string}|null>(null)
  const [sharedWork, setSharedWork] = useState<SharedWork|null>(null)
  const [volumes, setVolumes] = useState<Volume[]>([])
  const [viewChapterNo, setViewChapterNo] = useState(0)
  const [volumeNotice, setVolumeNotice] = useState('')
  const [messages, setMessages] = useState<AgentMessage[]>([])
  const [typingText, setTypingText] = useState('')
  const typingRef = useRef<number | null>(null)
  // 人在回路检查点状态
  const [awaitingInput, setAwaitingInput] = useState(false)
  const [guidanceText, setGuidanceText] = useState('')
  const [autoAdvance, setAutoAdvance] = useState(false)
  const [checkpointStep, setCheckpointStep] = useState<string>('')
  const [allChapters, setAllChapters] = useState<ChapterSummary[]>([])
  const [characters, setCharacters] = useState<Character[]>([])
  const [criticalPath, setCriticalPath] = useState<{version?:number;nodes:CriticalPathNode[]}>({version:1,nodes:[]})
  const [chapterVersions, setChapterVersions] = useState<ChapterVersion[]>([])
  const [viewingAttempt, setViewingAttempt] = useState<number | null>(null)
  const [sidebarTab, setSidebarTab] = useState<'chapters'|'outline'|'chars'>('chapters')
  const [composerText, setComposerText] = useState('')
  const [composerBusy, setComposerBusy] = useState(false)
  const [qualityReport, setQualityReport] = useState<QualityReport|null>(null)
  const [qualityOpen, setQualityOpen] = useState(true)
  const [qualityTags, setQualityTags] = useState<string[]>([])
  const [qualityFeedback, setQualityFeedback] = useState('')
  const [feedbackSent, setFeedbackSent] = useState(false)
  const [feedbackMessage, setFeedbackMessage] = useState('')
  // --- 待裁决收件箱（FR-10/FR-12）与审核（FR-25）---
  const [decisions, setDecisions] = useState<Decision[]>([])
  const [focusDecision, setFocusDecision] = useState<Decision|null>(null)
  const [modCases, setModCases] = useState<ModerationCase[]>([])
  const [modReason, setModReason] = useState('other')
  const [modDetail, setModDetail] = useState('')
  const [modNotice, setModNotice] = useState('')
  // --- 关键路径编辑（FR-04/FR-05）---
  const [pathImpact, setPathImpact] = useState<PathImpact|null>(null)
  const [pathBusy, setPathBusy] = useState(false)
  const [pathNotice, setPathNotice] = useState('')
  const [paused, setPaused] = useState(false)
  // --- H5 外壳：底栏 Tab、弹层、阅读偏好 ---
  const [tab, setTab] = useState<'shelf'|'work'>('shelf')
  const [catalogTab, setCatalogTab] = useState<CatalogTab>('chapters')
  const [immersive, setImmersive] = useState(false)
  const [readPct, setReadPct] = useState(0)
  const [readerFs, setReaderFs] = useState(() => Number(localStorage.getItem('novel-reader-fs')) || 17)
  const [readerLh, setReaderLh] = useState(() => Number(localStorage.getItem('novel-reader-lh')) || 1.95)
  const [theme, setTheme] = useState<ThemeKind>(() => (localStorage.getItem('novel-theme') as ThemeKind) || 'day')
  const screenRef = useRef<HTMLDivElement | null>(null)
  const lastScrollY = useRef(0)

  // 阅读偏好持久化 + 主题色同步
  useEffect(() => {
    document.documentElement.dataset.theme = theme
    localStorage.setItem('novel-theme', theme)
    const meta = document.querySelector('meta[name="theme-color"]')
    if (meta) meta.setAttribute('content', theme === 'night' ? '#131418' : theme === 'sepia' ? '#f2e7d3' : '#f3efe6')
  }, [theme])
  useEffect(() => {
    document.documentElement.style.setProperty('--reader-fs', `${readerFs}px`)
    localStorage.setItem('novel-reader-fs', String(readerFs))
  }, [readerFs])
  useEffect(() => {
    document.documentElement.style.setProperty('--reader-lh', String(readerLh))
    localStorage.setItem('novel-reader-lh', String(readerLh))
  }, [readerLh])

  // --- 待裁决深链：从通知/邮件进入时直达该裁决 ---
  useEffect(() => {
    if (!new URLSearchParams(window.location.search).get('decision')) return
    void loadInbox().then(() => { if (workId) setTab('work') })
  }, [workId])

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

  // --- Load volumes + metadata when work is selected ---
  useEffect(() => {
    if (workId) {
      void loadVolumes()
      void loadMetaData()
    } else {
      setVolumes([]); setVolumeNotice('')
      setAllChapters([]); setCharacters([]); setCriticalPath({nodes:[]})
      setChapterVersions([]); setViewingAttempt(null)
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workId])

  // --- URL routing: read on mount ---
  useEffect(() => {
    const m = window.location.pathname.match(/\/w\/(.+)/)
    if (m) {
      const wid = decodeURIComponent(m[1])
      setWorkId(wid); setTab('work')
      void loadWorks().then(() => void loadProgress(wid))
    } else {
      setTab('shelf')
      void loadWorks()
    }
    const onPop = () => {
      const pm = window.location.pathname.match(/\/w\/(.+)/)
      const next = pm ? decodeURIComponent(pm[1]) : ''
      setTab(next ? 'work' : 'shelf')
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
    setSheet(null); setCost(null); setShares([]); setShareUrl(''); setExportNotice(null); setExportResult(null); setFactResult('')
    setTab('shelf'); setImmersive(false); setViewChapterNo(0)
    window.history.pushState(null, '', '/')
  }
  /** 回到书架（保留当前作品上下文，便于随时切回创作） */
  function goShelf() {
    setTab('shelf'); setSheet(null); setImmersive(false)
  }
  /** 滚动：阅读进度 + 向下滚动自动收起工具栏 */
  function handleScroll(e: UIEvent<HTMLDivElement>) {
    const el = e.currentTarget
    const max = el.scrollHeight - el.clientHeight
    setReadPct(max > 8 ? Math.min(100, Math.round((el.scrollTop / max) * 100)) : 0)
    if (!chapter) return
    const y = el.scrollTop
    if (y > lastScrollY.current + 8 && y > 90) setImmersive(true)
    else if (y < lastScrollY.current - 8) setImmersive(false)
    lastScrollY.current = y
  }
  function openCatalog(t: CatalogTab) {
    setCatalogTab(t); setSheet('catalog')
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
        if (msg.event === 'chapter.step_started' && msg.data) {
          try {
            const d = JSON.parse(msg.data)
            const step = d.data?.step as string | undefined
            if (step) {
              setMessages(prev => {
                if (prev.some(m => m.step === step)) return prev
                return [...prev, {id:`${d.data?.chapter_no ?? progress?.chapter_no}-${step}`, step, label:STEP_LABELS[step]||step, status:'thinking', timestamp:Date.now()}]
              })
            }
          } catch { /* ignore */ }
        } else if (msg.event === 'chapter.step_output' && msg.data) {
          try {
            const d = JSON.parse(msg.data)
            const step = d.data?.step as string | undefined
            if (!step) return
            const update: Partial<AgentMessage> = {status:'done', timestamp:Date.now()}
            if (step === 'ASSEMBLE') update.summary = d.data?.summary || ''
            else if (step === 'PERFORM') update.performances = d.data?.performances || []
            else if (step === 'DIRECT') { update.summary = d.data?.summary || ''; update.sceneGoal = d.data?.scene_goal || ''; update.beats = d.data?.beats || []; update.endingHook = d.data?.ending_hook || '' }
            else if (step === 'PRODUCE') { update.summary = d.data?.summary || ''; update.title = d.data?.title || ''; update.wordCount = d.data?.word_count || 0 }
            else if (step === 'WEAVE') {
              update.title = d.data?.title || ''; update.content = d.data?.content || ''; update.wordCount = d.data?.word_count || 0
              // 启动打字机动画
              const fullText = d.data?.content || ''
              setTypingText('')
              if (typingRef.current) clearInterval(typingRef.current)
              let idx = 0
              typingRef.current = window.setInterval(() => {
                idx += 3
                if (idx >= fullText.length) { setTypingText(fullText); if (typingRef.current) clearInterval(typingRef.current); typingRef.current = null }
                else setTypingText(fullText.slice(0, idx))
              }, 16)
            }
            else if (step === 'REVIEW') {
              update.passed = d.data?.passed; update.issuesCount = d.data?.issues_count || 0
              const issues = [
                ...(d.data?.continuity_issues || []),
                ...(d.data?.leakage_issues || []),
                ...(d.data?.prose_issues || []),
                ...(d.data?.issues || []),
              ].filter((v: unknown): v is string => typeof v === 'string' && !!v.trim())
              setQualityReport({passed: !!d.data?.passed, issues, revised: !!d.data?.revised, score: d.data?.score})
            }
            else if (step === 'CANON') update.summary = '事实档案已更新'
            setMessages(prev => prev.map(m => m.step === step && m.status === 'thinking' ? {...m, ...update} : m))
          } catch { /* ignore */ }
        } else if (msg.event === 'chapter.scene_progress' && msg.data) {
          try {
            const d = JSON.parse(msg.data)
            setProgress(p => p ? {...p, scene_no:d.data?.scene_no, completed_scenes:d.data?.completed_scenes} : p)
            setMessages(prev => prev.map(m => m.step === 'PRODUCE'
              ? {...m, summary:`正在创作第 ${d.data?.scene_no || 1} 场，已完成 ${d.data?.completed_scenes || 0} 场`}
              : m))
          } catch { /* ignore malformed progress hints */ }
        } else if (msg.event === 'chapter.step_succeeded' && msg.data) {
          try {
            const d = JSON.parse(msg.data)
            const step = d.data?.step as string | undefined
            if (step) setProgress(p => p ? {...p, steps:{...p.steps, [step]:'SUCCEEDED'}} : p)
          } catch { /* ignore parse errors */ }
        } else if (msg.event === 'chapter.done' && msg.data) {
          try {
            const d = JSON.parse(msg.data)
            const no = (d.chapter_no ?? d.data?.chapter_no) as number
            void api<Chapter>(`/works/${workId}/chapters/${no}`).then(ch => { setChapter(ch); setViewChapterNo(no) })
            void loadWorks()
            setProgress(p => p ? {...p, state:'CANONIZED'} : p)
            void loadMetaData()
            void loadChapterVersions(no)
          } catch { /* ignore */ }
        } else if (msg.event === 'volume.expanded' && msg.data) {
          try {
            const d = JSON.parse(msg.data)
            const volNo = d.data?.volume_no as number
            setVolumeNotice(`第 ${volNo} 卷已展开`)
            void loadVolumes()
          } catch { /* ignore */ }
        } else if (msg.event === 'chapter.checkpoint_reached' && msg.data) {
          try {
            const d = JSON.parse(msg.data)
            setAwaitingInput(true)
            setCheckpointStep(d.data?.step || '')
            setProgress(p => p ? {...p, awaiting_input: true} : p)
          } catch { /* ignore */ }
        } else if (msg.event === 'chapter.guidance_submitted' && msg.data) {
          try {
            setAwaitingInput(false)
            setGuidanceText('')
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
    return () => { sseActive.current = false; abort(); if (typingRef.current) { clearInterval(typingRef.current); typingRef.current = null } }
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
      setAwaitingInput(!!run.awaiting_input)
      setAutoAdvance(!!run.auto_advance)
      if (run.state === 'CANONIZED') {
        const ch = await api<Chapter>(`/works/${wid}/chapters/${run.chapter_no}`)
        setChapter(ch); setViewChapterNo(run.chapter_no)
      } else {
        setChapter(null); setViewChapterNo(0)
      }
    } catch { /* no run yet */ }
  }

  async function loadVolumes() {
    if (!workId) return
    try { setVolumes(await api<Volume[]>(`/works/${workId}/volumes`)) } catch { /* no volumes yet */ }
  }

  async function loadMetaData() {
    if (!workId) return
    try {
      const [chs, chars, cp] = await Promise.all([
        api<ChapterSummary[]>(`/works/${workId}/chapters`).catch(() => []),
        api<Character[]>(`/works/${workId}/characters`).catch(() => []),
        api<{nodes:[]}>((`/works/${workId}/critical-path`)).catch(() => ({nodes:[]})),
      ])
      setAllChapters(chs)
      setCharacters(chars)
      setCriticalPath(cp)
    } catch { /* ignore */ }
  }

  async function loadChapterVersions(chapterNo: number) {
    if (!workId) return
    try {
      const vs = await api<ChapterVersion[]>(`/works/${workId}/chapters/${chapterNo}/versions`)
      setChapterVersions(vs)
    } catch { setChapterVersions([]) }
  }

  async function openChapter(chapterNo: number, attempt: number | null = null) {
    if (!workId || chapterNo < 1) return
    try {
      const url = attempt ? `/works/${workId}/chapters/${chapterNo}?attempt=${attempt}` : `/works/${workId}/chapters/${chapterNo}`
      const ch = await api<Chapter>(url)
      setChapter(ch)
      setViewingAttempt(attempt)
      setViewChapterNo(chapterNo)
      setTab('work'); setSheet(null); setImmersive(false); setQualityReport(null); setFeedbackSent(false)
      screenRef.current?.scrollTo({ top: 0 })
      lastScrollY.current = 0
      void loadChapterVersions(chapterNo)
    } catch { setError(`无法打开第 ${chapterNo} 章`) }
  }

  async function regenerateChapter(chapterNo: number) {
    if (!workId) return
    setBusy(true); setError('')
    try {
      await api(`/works/${workId}/chapters/${chapterNo}/regenerate`, { method: 'POST' })
      // Reset to generation view
      setChapter(null); setViewingAttempt(null); setChapterVersions([]); setFeedbackSent(false)
      setViewChapterNo(0); setTab('work'); setImmersive(false); setSheet(null)
      await loadProgress(workId)
      await loadMetaData()
    } catch(e) { setError((e as Error).message) } finally { setBusy(false) }
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
      const r = await api<{accepted:boolean;message:string;ticket_id:string;available_actions?:string[]}>(`/works/${workId}/facts/report`, {
        method:'POST', body: JSON.stringify({statement: factStatement, chapter_no: viewChapterNo || progress?.chapter_no, client_nonce: uuid()})
      })
      setFactResult(r.accepted ? `已记录 (#${r.ticket_id.slice(0,8)})：${r.message}` : `未受理：${r.message}`)
      setFactPendingResume(r.accepted && (r.available_actions || []).includes('resume_then_replay') ? r.ticket_id : '')
      setFactStatement('')
    } catch(e) { setError((e as Error).message) } finally { setBusy(false) }
  }

  // C-01 闭环：完结/暂停作品在用户明确恢复后，已排队的局部重演才会真正执行
  async function resumeForCorrection() {
    if (!workId || !factPendingResume) return
    setBusy(true); setError('')
    try {
      await api(`/works/${workId}/resume-correction`, {
        method:'POST', body: JSON.stringify({ticket_id: factPendingResume}),
      })
      setFactResult('已恢复创作，系统会执行这次纠错的局部重演。')
      setFactPendingResume('')
    } catch(e) { setError((e as Error).message) } finally { setBusy(false) }
  }

  async function submitQualityFeedback() {
    if (!workId || !progress) return
    const details = [...qualityTags, qualityFeedback.trim()].filter(Boolean).join('；')
    if (!details) return
    setBusy(true); setError('')
    try {
      const targetChapter = viewChapterNo || progress.chapter_no
      const statement = `第 ${targetChapter} 章读者反馈：${details}`
      const isFact = qualityTags.some(tag => tag === '前后有矛盾')
      const result = await api<{accepted:boolean;message:string}>(`/works/${workId}/facts/report`, {
        method:'POST', body:JSON.stringify({statement, chapter_no:targetChapter, kind:isFact?'FACT':'TASTE', client_nonce:uuid()}),
      })
      setFeedbackSent(true)
      setFeedbackMessage(result.message)
    } catch(e) { setError((e as Error).message) } finally { setBusy(false) }
  }
  function openSheet(kind: Exclude<SheetKind, null>) {
    setSheet(kind)
    if (kind === 'cost') void loadCost()
    if (kind === 'export') void checkExportNotice()
    if (kind === 'inbox') void loadInbox()
    if (kind === 'moderation') void loadModeration()
  }

  // --- 待裁决收件箱（FR-10 / FR-12）---
  async function loadInbox() {
    setError('')
    try {
      const list = await api<Decision[]>('/decisions')
      setDecisions(list)
      const fromUrl = new URLSearchParams(window.location.search).get('decision')
      if (fromUrl) setFocusDecision(list.find(d => d.decision_id === fromUrl) || null)
    } catch(e) { setError((e as Error).message) }
  }
  async function resolveDecision(d: Decision, optionId: string|null, acceptDefault = false) {
    setBusy(true); setError('')
    try {
      await api(`/works/${d.work_id}/decisions/${d.decision_id}/resolve`, {
        method:'POST',
        body: JSON.stringify({
          option_id: optionId, accept_default: acceptDefault,
          confirm_nonce: d.confirm_nonce, client_nonce: uuid(),
        }),
      })
      // 裁决深链用过后清掉，避免刷新时重复聚焦已解决项
      if (new URLSearchParams(window.location.search).get('decision') === d.decision_id) {
        const url = new URL(window.location.href); url.searchParams.delete('decision')
        window.history.replaceState({}, '', url.toString())
      }
      setFocusDecision(null)
      await loadInbox()
      if (d.work_id === workId) void loadProgress(d.work_id)
    } catch(e) { setError((e as Error).message) } finally { setBusy(false) }
  }

  // --- 审核与投诉（FR-25）---
  async function loadModeration() {
    if (!workId) return
    setError('')
    try { setModCases(await api<ModerationCase[]>(`/works/${workId}/moderation`)) }
    catch(e) { setError((e as Error).message) }
  }
  async function reportModeration() {
    if (!workId) return
    setBusy(true); setModNotice('')
    try {
      await api(`/works/${workId}/moderation/report`, {
        method:'POST',
        body: JSON.stringify({
          chapter_no: viewChapterNo || progress?.chapter_no || null,
          reason_code: modReason, detail: modDetail, client_nonce: uuid(),
        }),
      })
      setModNotice('投诉已记录，进入待处理（不会产生自动判定）')
      setModDetail('')
      await loadModeration()
    } catch(e) { setError((e as Error).message) } finally { setBusy(false) }
  }
  async function appealModeration(caseId: string) {
    if (!workId) return
    setBusy(true)
    try {
      await api(`/works/${workId}/moderation/${caseId}/appeal?reason=${encodeURIComponent('用户申诉：认为判定有误')}`, {method:'POST'})
      await loadModeration()
    } catch(e) { setError((e as Error).message) } finally { setBusy(false) }
  }

  // --- 关键路径编辑（FR-04 / FR-05）：改之前先看到代价 ---
  function pathNodes() { return criticalPath.nodes || [] }
  function reorderNodes(from: number, to: number) {
    const nodes = [...pathNodes()]
    if (to < 0 || to >= nodes.length) return
    const [moved] = nodes.splice(from, 1)
    nodes.splice(to, 0, moved)
    setCriticalPath({nodes: nodes.map((n, i) => ({...n, ordinal: i + 1}))})
    setPathNotice('顺序已调整，保存前请先查看影响')
    setPathImpact(null)
  }
  function removeNode(index: number) {
    const nodes = [...pathNodes()]
    if (nodes[index]?.locked) { setPathNotice('已锁定节点不能删除'); return }
    if (nodes.length <= 10) { setPathNotice('关键路径至少保留 10 个节点'); return }
    nodes.splice(index, 1)
    setCriticalPath({nodes: nodes.map((n, i) => ({...n, ordinal: i + 1}))})
    setPathNotice('节点已移除，保存前请先查看影响')
    setPathImpact(null)
  }
  function toggleLock(index: number) {
    const nodes = [...pathNodes()]
    nodes[index] = {...nodes[index], locked: !nodes[index].locked}
    setCriticalPath({nodes})
    setPathNotice(nodes[index].locked ? '节点已锁定' : '节点已解锁')
    setPathImpact(null)
  }
  function insertNodeAfter(index: number) {
    const nodes = [...pathNodes()]
    if (nodes.length >= 20) { setPathNotice('关键路径最多 20 个节点'); return }
    nodes.splice(index + 1, 0, {
      node_id: `n${Date.now()}`, ordinal: index + 2, title: '新节点',
      promise: '', preconditions: [], consequences: [], requires_human: false, locked: false,
    })
    setCriticalPath({nodes: nodes.map((n, i) => ({...n, ordinal: i + 1}))})
    setPathNotice('已插入节点，保存前请先查看影响')
    setPathImpact(null)
  }
  async function previewPath() {
    if (!workId) return
    setPathBusy(true); setError('')
    try {
      setPathImpact(await api<PathImpact>(`/works/${workId}/critical-path/preview`, {
        method:'POST',
        body: JSON.stringify({nodes: pathNodes(), dependency_edges: [], expected_version: criticalPath.version ?? 1}),
      }))
    } catch(e) { setError((e as Error).message) } finally { setPathBusy(false) }
  }
  async function savePath() {
    if (!workId) return
    setPathBusy(true); setError('')
    try {
      await api(`/works/${workId}/critical-path`, {
        method:'PUT',
        headers: {'If-Match': String(criticalPath.version ?? 1)},
        body: JSON.stringify({nodes: pathNodes(), dependency_edges: [], expected_version: criticalPath.version ?? 1}),
      })
      setPathNotice('关键路径已保存')
      setPathImpact(null)
      setCriticalPath(await api<typeof criticalPath>(`/works/${workId}/critical-path`))
    } catch(e) {
      const msg = (e as Error).message
      setPathNotice(msg.includes('409') || msg.includes('version') ? '路径已被别处修改，已重新载入最新版' : msg)
      if (workId) setCriticalPath(await api<typeof criticalPath>(`/works/${workId}/critical-path`).catch(() => criticalPath))
    } finally { setPathBusy(false) }
  }

  // --- 暂停与恢复（FR-06）---
  async function togglePause() {
    if (!workId) return
    setBusy(true); setError('')
    try {
      await api(`/works/${workId}/${paused ? 'resume' : 'pause'}`, {method:'POST'})
      setPaused(!paused)
    } catch(e) { setError((e as Error).message) } finally { setBusy(false) }
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
        method:'POST', body: JSON.stringify({raw_intent: intent, client_nonce: uuid()})
      })
      setWorkId(r.work_id); pushUrl(r.work_id)
      setOnboarding(r.onboarding); setProgress(null); setChapter(null); setViewChapterNo(0)
      setTab('work'); setIntent('')
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
    setChoosingDirection(true); setBusy(true); setError('')
    try {
      await api(`/works/${workId}/directions`, {
        method:'POST', body: JSON.stringify({card_id: cardId, client_nonce: uuid()})
      })
      const run = await api<Progress>(`/works/${workId}/runs`, {
        method:'POST', headers:{'Idempotency-Key': uuid()},
      })
      setProgress(run); setOnboarding(null); setChapter(null); setMessages([]); setTypingText('')
      setViewChapterNo(0); setTab('work'); setImmersive(false)
      if (typingRef.current) { clearInterval(typingRef.current); typingRef.current = null }
      setChoosingDirection(false)
      await loadWorks()
    } catch(e) { setError((e as Error).message); setChoosingDirection(false) } finally { setBusy(false) }
  }

  async function openWork(id: string) {
    setWorkId(id); pushUrl(id)
    setOnboarding(null); setChapter(null); setError(''); setProgress(null); setQualityReport(null); setFeedbackSent(false)
    setViewChapterNo(0); setTab('work'); setImmersive(false); setSheet(null)
    await loadProgress(id)
  }

  async function nextChapter() {
    if (!workId) return
    setBusy(true); setError('')
    try {
      const run = await api<Progress>(`/works/${workId}/runs`, {
        method:'POST', headers:{'Idempotency-Key':uuid()},
      })
      setChapter(null); setProgress(run); setMessages([]); setTypingText('')
      setViewChapterNo(0); setTab('work'); setImmersive(false); setSheet(null); setQualityReport(null)
      if (typingRef.current) { clearInterval(typingRef.current); typingRef.current = null }
    } catch(e) { setError((e as Error).message) } finally { setBusy(false) }
  }

  if (window.location.pathname.startsWith('/read/')) return <div className="sharedPage">
    {error && <div className="panel"><h2>无法打开分享</h2><p className="muted">{error}</p></div>}
    {!error && !sharedWork && <div className="panel"><p className="muted">正在打开故事…</p></div>}
    {sharedWork && <article className="sharedReader">
      <header><p className="eyebrow">{sharedWork.genre || '分享阅读'}</p><h1>{sharedWork.title}</h1>
        <p className="ai-badge" role="note">{sharedWork.ai_disclosure || AI_FALLBACK}</p></header>
      {sharedWork.chapters.map(ch => <section key={ch.chapter_no}>
        <p className="eyebrow">第 {ch.chapter_no} 章 · {ch.word_count} 字</p><h2>{ch.title}</h2>
        <div className="prose">{ch.content.split('\n').map((p,i)=><p key={i}>{p}</p>)}</div>
      </section>)}
      <footer>{sharedWork.ai_disclosure}</footer>
    </article>}
  </div>

  // --- Render ---
  const onShelf = !workId || tab === 'shelf'
  const reading = !!chapter
  const readingNo = viewChapterNo || progress?.chapter_no || 0
  const workTitle = works.find(w => w.work_id === workId)?.title || ''
  const stepState = (s: string) => progress?.steps[s] === 'SUCCEEDED' ? 'ok' : progress?.current_step === s ? 'on' : ''

  // 人在回路：提交反馈
  async function submitGuidance(feedback: string, approve: boolean) {
    if (!workId || !progress) return
    setBusy(true); setError('')
    try {
      await api(`/works/${workId}/runs/${progress.chapter_no}/guidance`, {
        method: 'POST',
        body: JSON.stringify({ feedback, approve })
      })
      setAwaitingInput(false)
      setGuidanceText('')
    } catch(e) { setError((e as Error).message) } finally { setBusy(false) }
  }

  // 人在回路：切换自动模式
  async function toggleAutoAdvance() {
    if (!workId || !progress) return
    const newVal = !autoAdvance
    try {
      await api(`/works/${workId}/runs/${progress.chapter_no}/auto-advance`, {
        method: 'PUT',
        body: JSON.stringify({ enabled: newVal })
      })
      setAutoAdvance(newVal)
      // 如果开启自动模式且当前在等待，自动恢复
      if (newVal && awaitingInput) {
        setAwaitingInput(false)
      }
    } catch(e) { setError((e as Error).message) }
  }

  async function handleComposerSend(text: string) {
    const trimmed = text.trim()
    if (!trimmed || composerBusy) return
    setComposerBusy(true)
    try {
      if (trimmed === '续写下一章' || trimmed.startsWith('续写')) {
        await nextChapter()
      } else if (trimmed === '章节目录') {
        openCatalog('chapters')
      } else if (trimmed === '卷目录') {
        openCatalog('volumes')
      } else if (trimmed === '复制正文' && chapter) {
        await navigator.clipboard.writeText(chapter.content)
      } else {
        await api(`/works/${workId}/facts/report`, {
          method:'POST', body:JSON.stringify({statement:trimmed, chapter_no:progress?.chapter_no, client_nonce:uuid()}),
        })
      }
    } finally {
      setComposerBusy(false)
      setComposerText('')
    }
  }

  const quickActions = !onShelf && !reading && !!progress ? ['续写下一章', '章节目录', '卷目录'] : []

  return <div className={`app${immersive && reading ? ' immersive' : ''}`}>
    {/* === 顶栏 === */}
    <header className="appbar">
      {onShelf
        ? <span className="spacer" />
        : <button className="iconbtn" onClick={goShelf} aria-label="返回书架"><Icon name="back" size={20} /></button>}
      <div className="appbar-mid">
        <span className="appbar-title">
          {onShelf ? '造境' : reading ? chapter.title : onboarding ? (workTitle || '新故事') : `第 ${progress?.chapter_no || 1} 章`}
        </span>
        <span className="appbar-sub">
          {onShelf
            ? <>书架 · {works.length} 部作品</>
            : <><i className={`live-dot${streaming ? ' on' : ''}`} />
                {streaming ? '实时生成中' : reading ? `${chapter.word_count} 字 · 已读 ${readPct}%` : awaitingInput ? '等待你的确认' : '在线'}</>}
        </span>
      </div>
      {!onShelf
        ? <button className="iconbtn" onClick={() => openSheet('account')} aria-label="更多"><Icon name="more" size={20} /></button>
        : <span className="spacer" />}
    </header>

    {/* === 主滚动区 === */}
    <div className="screen" ref={screenRef} onScroll={handleScroll}>
      {/* 书架：新建 + 作品列表 */}
      {onShelf && <>
        <div className="shelf-hero">
          <div className="shelf-brand">造境</div>
          <h1>从一句话，到一个持续生长的世界</h1>
          <p>先确认方向，再让角色在各自知道的世界里行动。</p>
        </div>
        <div className="composer-card">
          <textarea value={intent} onChange={e => setIntent(e.target.value)}
            placeholder="例如：一个能听见旧物记忆的修表匠，发现父亲失踪前修过的最后一块表正在倒着走……"
            aria-label="故事目标" />
          <div className="row">
            <span className="hint">{intent.trim() ? `${intent.trim().length} 字` : '一句话即可开始'}</span>
            <button className="go" disabled={busy || !intent.trim()} onClick={create}>{busy ? '正在理解…' : '开始构思'}</button>
          </div>
        </div>
        <div className="principles"><span>一次澄清后继续</span><span>角色信息彼此隔离</span><span>每章自动审校修订</span></div>
        <div className="shelf-head"><p className="eyebrow">我的书架</p><span className="small muted">{works.length} 部</span></div>
        {works.length === 0
          ? <p className="empty-hint">还没有故事。<br />写下第一个念头，我们来把它养成一部长篇。</p>
          : <div className="worklist">{works.map(w => (
              <button key={w.work_id} className={`workcard${workId === w.work_id ? ' active' : ''}`}
                onClick={() => openWork(w.work_id)} aria-current={workId === w.work_id ? 'page' : undefined}>
                <span className="cover">{(w.title || '境').slice(0, 1)}</span>
                <span className="info">
                  <b>{w.title}</b>
                  <span className="state">{w.projection?.stage_label || w.state}{w.latest_chapter_no ? ` · 第 ${w.latest_chapter_no} 章` : ''}</span>
                </span>
                <Icon name="chevronRight" size={16} />
              </button>
            ))}</div>}
      </>}

      {/* 引导：一次澄清 */}
      {!onShelf && onboarding?.questions.length ? <div className="msg-block clarify-block">
        <p className="eyebrow">只确认这一次</p>
        <h2>让故事的第一步更准</h2>
        <p className="muted">不想细选也没关系，我们会采用默认方向继续。</p>
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

      {/* 引导：方向卡横向轮播 */}
      {!onShelf && onboarding && !onboarding.questions.length && <div className="msg-block direction-block">
        {choosingDirection && <div className="overlay-spinner">
          <div className="spinner" />
          <p>正在生成故事大纲…</p>
          <small>模型正在为你的故事设计定制化大纲，约需 1-2 分钟</small>
        </div>}
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
        <p className="carousel-hint">左右滑动查看更多方向</p>
      </div>}

      {/* 阅读态：正文 + 版本 + 质量卡 */}
      {!onShelf && !onboarding && reading && <div className="reader-shell" onClick={() => { if (immersive) setImmersive(false) }}>
        <article className="readerProse">
          <h1>{chapter.title}</h1>
          <p className="ai-badge" role="note">{AI_FALLBACK}</p>
          <p className="ch-meta">第 {readingNo} 章 · {chapter.word_count} 字{viewingAttempt ? ` · 第 ${viewingAttempt} 稿` : ''}</p>
          {chapter.content.split('\n').filter(Boolean).map((p, i) => <p key={i}>{p}</p>)}
          <footer>{chapter.ai_disclosure}</footer>
        </article>
        {chapterVersions.length > 1 && <div className="versionBar">
          {chapterVersions.map(v => (
            <button key={v.attempt} className={`ver-tag ${viewingAttempt === v.attempt ? 'active' : ''}`}
              onClick={() => openChapter(readingNo, v.attempt)}>
              v{v.attempt}{v.attempt === chapterVersions[chapterVersions.length-1].attempt ? ' · 当前稿' : ''}
            </button>
          ))}
        </div>}
        <aside className="quality-card" aria-label="本章质量与反馈">
            <button className="quality-heading" onClick={()=>setQualityOpen(v=>!v)} aria-expanded={qualityOpen}>
              <span className={`quality-mark ${qualityReport?.passed === false ? 'needs-work' : ''}`}>{qualityReport?.passed === false ? '!' : '✓'}</span>
              <span><b>{qualityReport?.passed === false ? '本章仍有改进空间' : '本章已完成质量检查'}</b><small>{qualityReport?.revised ? '已根据检查结果自动修订' : '你可以继续阅读，也可以提出修改意见'}</small></span>
              <span className="quality-chevron">{qualityOpen ? '收起' : '查看'}</span>
            </button>
            {qualityOpen && <div className="quality-body">
              {typeof qualityReport?.score === 'number' && <div className="quality-score"><strong>{Math.round(qualityReport.score)}</strong><span>综合质量</span></div>}
              {qualityReport?.issues.length ? <div className="quality-issues">
                <p>检查发现并处理</p>
                <ul>{qualityReport.issues.map((issue,i)=><li key={i}>{issue}</li>)}</ul>
              </div> : <p className="quality-note">已检查情节衔接、人物行为、设定一致性和文字重复。详细结果会在可用时显示。</p>}
              {!feedbackSent ? <>
                <p className="quality-prompt">哪里还不够好？可多选，修改意见会用于后续创作。</p>
                <div className="quality-tags">
                  {['情节没推进','人物不真实','前后有矛盾','内容重复','节奏不合适','文风不喜欢'].map(tag=><button key={tag}
                    className={qualityTags.includes(tag)?'selected':''}
                    onClick={()=>setQualityTags(v=>v.includes(tag)?v.filter(x=>x!==tag):[...v,tag])}>{tag}</button>)}
                </div>
                <textarea value={qualityFeedback} onChange={e=>setQualityFeedback(e.target.value)} rows={2}
                  placeholder="补充具体位置或你希望的改法（可选）" />
                <div className="quality-actions">
                  <button disabled={busy || (!qualityTags.length && !qualityFeedback.trim())} onClick={submitQualityFeedback}>记录修改意见</button>
                  {qualityTags.includes('前后有矛盾') && <button className="rewrite" disabled={busy || (!qualityTags.length && !qualityFeedback.trim())}
                    onClick={submitQualityFeedback}>提交并核对矛盾</button>}
                </div>
              </> : <div className="feedback-success"><b>{qualityTags.includes('前后有矛盾') ? '问题已进入核对' : '反馈已收到'}</b><span>{feedbackMessage}</span>{qualityTags.includes('前后有矛盾') && <button disabled={busy} onClick={()=>regenerateChapter(readingNo)}>先重写一版</button>}</div>}
            </div>}
        </aside>
      </div>}

      {/* 创作态：Agent 生成流 */}
      {!onShelf && !onboarding && !reading && <>
        {volumeNotice && <button className="notice-bar" onClick={() => openCatalog('volumes')}>{volumeNotice} · 点击查看</button>}
        <div className="stream-head">
          <span className="eyebrow">{progress ? '正在创作' : '等待开工'}</span>
          <b>第 {progress?.chapter_no || 1} 章 · {workTitle || '故事'}</b>
        </div>
        <div className="step-progress" aria-hidden="true">
          {(progress?.steps && 'PRODUCE' in progress.steps ? DIRECTOR_STEP_ORDER : STEP_ORDER).map(s => <i key={s} className={stepState(s)} />)}
        </div>
        {!!progress?.scene_count && <p role="status" aria-live="polite">
          导演正在创作第 {progress.scene_no} 场 · 已完成 {progress.completed_scenes || 0}/{progress.scene_count} 场
        </p>}
        {!progress && <div className="empty-hint">
          方向已确定，随时可以让 Agent 动工。
          <button className="primary" style={{marginTop:14}} disabled={busy} onClick={nextChapter}>开始第一章</button>
        </div>}

        {/* Agent 步骤流 */}
        {progress && <div className="stream">
          {messages.length === 0 && <article className="message pending">
            <div className="avatar">境</div>
            <div className="body">
              <div className="meta">造境 Agent · 启动中</div>
              <div className="content"><div className="typingDots"><span/><span/><span/></div></div>
            </div>
          </article>}
          {messages.map(m => (
            <article key={m.id} className={`message assistant ${m.status}`}>
              <div className="avatar">{m.status === 'thinking' ? '◉' : '✓'}</div>
              <div className="body">
                <div className="meta">{m.step === 'REVIEW' ? '质量检查' : m.step === 'WEAVE' ? '章节创作' : '故事筹备'}{m.status === 'thinking' && <span className="msg-status">进行中</span>}</div>
                {m.status === 'done' && <div className="content">
                  {m.step === 'ASSEMBLE' && m.summary && <p>正在衔接前文与本章目标。</p>}
                  {(m.step === 'DIRECT' || m.step === 'PRODUCE') && m.summary && <p>{m.summary}</p>}
                  {m.step === 'PERFORM' && <p className="muted" style={{fontSize:12}}>正在推演人物在当前处境下的选择。</p>}
                  {m.step === 'DIRECT' && m.sceneGoal && <div className="directPlan">
                    <p className="directGoal">本章的推进与人物选择已经安排好，正在写成正文。</p>
                  </div>}
                  {m.step === 'WEAVE' && m.title && <div className="weaveSummary">
                    <p><b>{m.title}</b> · {m.wordCount || 0} 字</p>
                  </div>}
                  {m.step === 'REVIEW' && <p className={m.passed ? 'pass' : 'fail'}>{m.passed ? '审校通过' : `发现 ${m.issuesCount || 0} 个问题，已修订`}</p>}
                  {m.step === 'CANON' && <p>本章关键设定与人物变化已记住。</p>}
                </div>}
              </div>
            </article>
          ))}
          {/* Pending send */}
          {composerBusy && <article className="message assistant pending">
            <div className="avatar">◉</div>
            <div className="body">
              <div className="meta">造境 Agent · 处理中</div>
              <div className="content"><div className="typingDots"><span/><span/><span/></div></div>
            </div>
          </article>}
        </div>}

        {/* 人在回路：检查点卡片 */}
        {awaitingInput && <div className="checkpoint-card">
        <div className="checkpoint-header">
          <span className="checkpoint-icon">⏸</span>
          <span>{checkpointStep === 'DIRECT' ? '场景计划已完成，请审查' : '初稿已完成，请审查'}</span>
        </div>
        <div className="checkpoint-actions">
          <button className="checkpoint-approve" disabled={busy} onClick={() => submitGuidance('', true)}>
            ✓ 满意，继续
          </button>
          <div className="checkpoint-feedback">
            <textarea
              value={guidanceText}
              onChange={e => setGuidanceText(e.target.value)}
              placeholder="给 Agent 反馈：哪里需要改？例如：节奏太快、角色行为不合理、场景描述不够详细…"
              rows={2}
              disabled={busy}
            />
            <button className="checkpoint-submit" disabled={busy || !guidanceText.trim()} onClick={() => submitGuidance(guidanceText, false)}>
              提交反馈并重写
            </button>
          </div>
        </div>
        <label className="auto-toggle">
          <input type="checkbox" checked={autoAdvance} onChange={toggleAutoAdvance} disabled={busy} />
          <span>自动模式（跳过审查，直接继续）</span>
        </label>
        {/* FR-06：暂停与恢复——暂停后 worker 释放，恢复从检查点继续，不重复调用 */}
        {progress && !TERMINAL.has(progress.state) && <div className="pause-row">
          <button disabled={busy} onClick={togglePause}>{paused ? '继续创作' : '暂停创作'}</button>
          <small className="muted">{paused ? '已暂停，进度保留在最后一次检查点' : '暂停后不会丢失已完成的部分'}</small>
        </div>}
        </div>}
      </>}
    </div>

    {/* === 阅读态底栏 === */}
    {reading && !onShelf && <div className="readerbar">
      <button className="rb-btn" onClick={() => openCatalog('chapters')}><Icon name="list" size={19} /><span>目录</span></button>
      <button className="rb-btn" disabled={busy || readingNo <= 1} onClick={() => openChapter(readingNo - 1)}><Icon name="back" size={19} /><span>上一章</span></button>
      <button className="rb-btn" onClick={() => setSheet('reader')}><Icon name="type" size={19} /><span>设置</span></button>
      <button className="rb-btn" disabled={busy} onClick={() => regenerateChapter(readingNo)}><Icon name="refresh" size={19} /><span>重写</span></button>
      <button className="rb-btn primary" disabled={busy} onClick={nextChapter}><Icon name="pen" size={19} /><span>续写</span></button>
    </div>}

    {/* === 创作态输入条 === */}
    {!onShelf && !reading && <div className="composer-wrap">
        {quickActions.length > 0 && <div className="composer-intents">
          {quickActions.map(a => (
            <button key={a} type="button" disabled={composerBusy} onClick={() => handleComposerSend(a)}>{a}</button>
          ))}
          {streaming && <span className="composer-queue-status">Agent 正在生成，可继续输入</span>}
        </div>}
        <div className="composer">
          <textarea
            value={composerText}
            onChange={e => setComposerText(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleComposerSend(composerText) } }}
            placeholder={(progress || chapter) ? '指导 Agent：调整方向、纠错、续写…' : '描述你的故事想法…'}
            rows={1}
            disabled={composerBusy}
          />
          <button className="send" onClick={() => handleComposerSend(composerText)} disabled={composerBusy || !composerText.trim()} aria-label="发送"><Icon name="arrowUp" size={19} /></button>
        </div>
      </div>}

    {/* === 底部 Tab === */}
    {!reading && <nav className="tabbar" role="navigation" aria-label="主导航">
      <button className={`tab${onShelf ? ' active' : ''}`} onClick={goShelf}><Icon name="shelf" size={20} /><span>书架</span></button>
      <button className={`tab${!onShelf ? ' active' : ''}`} disabled={!workId} onClick={() => { setTab('work'); setSheet(null); setImmersive(false) }}><Icon name="pen" size={20} /><span>创作</span></button>
      <button className={`tab${sheet === 'catalog' ? ' active' : ''}`} disabled={!workId} onClick={() => openCatalog('chapters')}><Icon name="list" size={20} /><span>目录</span></button>
      <button className={`tab${sheet === 'account' ? ' active' : ''}`} onClick={() => openSheet('account')}><Icon name="user" size={20} /><span>我的</span></button>
    </nav>}

    {/* 阅读进度 */}
    {reading && readPct > 0 && <div className="read-progress" style={{ width: `${readPct}%` }} />}

    {/* === Toast === */}
    {error && <div className="error-bar" role="alert">
      <span>{error}</span>
      <button onClick={() => setError('')} aria-label="关闭">×</button>
    </div>}

    {/* === 底部弹层：作品目录 === */}
    {sheet === 'catalog' && <Sheet title="作品目录" onClose={() => setSheet(null)}>
      <Segmented<CatalogTab> value={catalogTab} onChange={setCatalogTab} options={[
        { key: 'chapters', label: `章节${allChapters.length ? ` ${allChapters.length}` : ''}` },
        { key: 'outline', label: '大纲' },
        { key: 'chars', label: `角色${characters.length ? ` ${characters.length}` : ''}` },
        { key: 'volumes', label: '卷' },
      ]} />
      {catalogTab === 'chapters' && (allChapters.length === 0
        ? <p className="empty-hint">尚无章节</p>
        : allChapters.map(c => (
          <button key={c.chapter_no} className={`metaItem${readingNo === c.chapter_no ? ' current' : ''}`}
            disabled={c.state !== 'CANONIZED'} onClick={() => openChapter(c.chapter_no)}>
            <span className="metaChNo">第 {c.chapter_no} 章</span>
            <span className="metaTitle">{c.title || '（未命名）'}</span>
            {c.version_count > 1 && <span className="metaVer">v{c.attempt}</span>}
            {c.state !== 'CANONIZED' && <span className="metaState">{c.state === 'RUNNING' ? '生成中' : c.state}</span>}
          </button>
        )))}
      {catalogTab === 'outline' && (criticalPath.nodes.length === 0
        ? <p className="empty-hint">尚无大纲节点</p>
        : <>
          {criticalPath.nodes.map((n, i) => (
            <div key={n.node_id} className="metaItem outline-node">
              <span className="metaOrd">{n.ordinal}</span>
              <span className="metaTitle">{n.title}{n.locked ? ' · 已锁定' : ''}</span>
              <span className="nodeActions">
                <button aria-label="上移" disabled={i === 0 || pathBusy} onClick={() => reorderNodes(i, i - 1)}>↑</button>
                <button aria-label="下移" disabled={i === criticalPath.nodes.length - 1 || pathBusy} onClick={() => reorderNodes(i, i + 1)}>↓</button>
                <button aria-label="在后面插入" disabled={pathBusy} onClick={() => insertNodeAfter(i)}>＋</button>
                <button aria-label="锁定或解锁" disabled={pathBusy} onClick={() => toggleLock(i)}>{n.locked ? '解锁' : '锁定'}</button>
                <button aria-label="删除" disabled={pathBusy || !!n.locked} onClick={() => removeNode(i)}>删除</button>
              </span>
            </div>
          ))}
          <div className="pathActions">
            <button disabled={pathBusy} onClick={previewPath}>查看影响</button>
            <button className="primary" disabled={pathBusy} onClick={savePath}>{pathBusy ? '保存中…' : '保存关键路径'}</button>
          </div>
          {pathNotice && <p className="muted small">{pathNotice}</p>}
          {pathImpact && <div className="impactCard">
            <b>这次改动的代价</b>
            <p>受影响章节：{pathImpact.affected_chapters.length ? pathImpact.affected_chapters.join('、') : '无'}</p>
            {pathImpact.frozen_conflict && <p className="warn">会改动已冻结内容（第 {pathImpact.frozen_through_chapter} 章及以前）</p>}
            {pathImpact.eta_minutes_max ? <p>预计耗时：{pathImpact.eta_minutes_min}–{pathImpact.eta_minutes_max} 分钟</p> : null}
            {pathImpact.cost_ceiling_minor !== null && <p>费用上限：{(pathImpact.cost_ceiling_minor / 100).toFixed(2)} {pathImpact.currency}</p>}
          </div>}
        </>)}
      {catalogTab === 'chars' && (characters.length === 0
        ? <p className="empty-hint">尚无角色档案</p>
        : characters.map(c => (
          <div key={c.name} className="metaItem char-card">
            <b>{c.name}</b>
            {c.identity && <small>{c.identity}</small>}
            {c.drive && <small>驱动：{c.drive}</small>}
            {(c.traits || []).length > 0 && <div className="traits">{(c.traits || []).map(t => <span key={t}>{t}</span>)}</div>}
          </div>
        )))}
      {catalogTab === 'volumes' && (volumes.length === 0
        ? <p className="empty-hint">尚无卷目录，故事推进到一定篇幅后会自动展开</p>
        : <div className="volumeList">
          {volumes.map(v => {
            const inThisVol = readingNo >= v.start_chapter_no && readingNo <= v.end_chapter_no
            return <div key={v.volume_no} className={`volumeItem ${v.state === 'ACTIVE' ? 'active' : ''} ${inThisVol ? 'current' : ''}`}>
              <div className="volumeHeader">
                <b>{v.title}</b>
                <span className="volumeState">{v.state === 'ACTIVE' ? '进行中' : v.state === 'COMPLETED' ? '已完结' : '待展开'}</span>
              </div>
              {v.cultivation_realm && <p className="muted small">境界：{v.cultivation_realm}</p>}
              <p className="small muted">第 {v.start_chapter_no}–{v.end_chapter_no} 章</p>
              {v.arcs.length > 0 && <div className="arcList">
                {v.arcs.map(a => <div key={a.arc_no} className="arcItem">
                  <span>弧段 {a.arc_no}</span><span className="muted">{a.title}</span>
                  <span className="small muted">第 {a.chapter_range_start}–{a.chapter_range_end} 章</span>
                </div>)}
              </div>}
            </div>
          })}
        </div>)}
    </Sheet>}

    {/* === 底部弹层：我的 === */}
    {sheet === 'account' && <Sheet title="我的" onClose={() => setSheet(null)}>
      <div className="menu-list">
        {workId && <>
          <button className="menu-item" onClick={() => openSheet('cost')}>
            <span className="mi-icon"><Icon name="coin" size={17} /></span>
            <span className="mi-body"><b>成本明细</b><small>本作已消耗的费用与 Token</small></span>
            <Icon name="chevronRight" size={16} />
          </button>
          <button className="menu-item" onClick={() => openSheet('share')}>
            <span className="mi-icon"><Icon name="share" size={17} /></span>
            <span className="mi-body"><b>定向分享</b><small>生成仅被邀请者可读的链接</small></span>
            <Icon name="chevronRight" size={16} />
          </button>
          <button className="menu-item" onClick={() => openSheet('export')}>
            <span className="mi-icon"><Icon name="download" size={17} /></span>
            <span className="mi-body"><b>导出作品</b><small>下载 TXT 全文</small></span>
            <Icon name="chevronRight" size={16} />
          </button>
          <button className="menu-item" onClick={() => openSheet('fact')}>
            <span className="mi-icon"><Icon name="flag" size={17} /></span>
            <span className="mi-body"><b>事实报错</b><small>指出前后矛盾，后续章节会修正</small></span>
            <Icon name="chevronRight" size={16} />
          </button>
          <button className="menu-item" onClick={() => openSheet('inbox')}>
            <span className="mi-icon"><Icon name="flag" size={17} /></span>
            <span className="mi-body"><b>待裁决</b><small>{decisions.length ? `${decisions.length} 项等你决定` : '当前没有待你决定的节点'}</small></span>
            <Icon name="chevronRight" size={16} />
          </button>
          <button className="menu-item" onClick={() => openSheet('moderation')}>
            <span className="mi-icon"><Icon name="flag" size={17} /></span>
            <span className="mi-body"><b>投诉与申诉</b><small>提交投诉或对判定提出申诉</small></span>
            <Icon name="chevronRight" size={16} />
          </button>
          {reading && <button className="menu-item" onClick={() => { void navigator.clipboard.writeText(chapter.content) }}>
            <span className="mi-icon"><Icon name="pen" size={17} /></span>
            <span className="mi-body"><b>复制本章正文</b><small>{chapter.word_count} 字</small></span>
          </button>}
        </>}
        <button className="menu-item" onClick={() => openSheet('reader')}>
          <span className="mi-icon"><Icon name="sliders" size={17} /></span>
          <span className="mi-body"><b>阅读设置</b><small>字号 {readerFs}px · {theme === 'day' ? '日间' : theme === 'sepia' ? '护眼' : '夜间'}</small></span>
          <Icon name="chevronRight" size={16} />
        </button>
        <button className="menu-item" onClick={goShelf}>
          <span className="mi-icon"><Icon name="shelf" size={17} /></span>
          <span className="mi-body"><b>回到书架</b><small>切换或新建作品</small></span>
        </button>
        {works.length > 0 && <button className="menu-item danger" onClick={logout}>
          <span className="mi-icon"><Icon name="logout" size={17} /></span>
          <span className="mi-body"><b>登出</b><small>清除本机创作会话</small></span>
        </button>}
      </div>
    </Sheet>}

    {/* === 底部弹层：待裁决收件箱（FR-10 / FR-12）=== */}
    {sheet === 'inbox' && <Sheet title="待你决定" onClose={() => { setSheet(null); setFocusDecision(null) }}>
      {decisions.length === 0 && <p className="empty-hint">当前没有待你决定的节点。</p>}
      {decisions.map(d => {
        const focused = focusDecision?.decision_id === d.decision_id
        const workTitle = works.find(w => w.work_id === d.work_id)?.title || '作品'
        return <div key={d.decision_id} className={`decision-card${focused ? ' focused' : ''}`}>
          <p className="eyebrow">{workTitle} · 第 {d.chapter_no} 章 · 影响 {d.impact_horizon_chapters} 章</p>
          <b>{d.trigger_summary}</b>
          <p className="muted small">为什么需要你决定：{d.why_human}</p>
          {d.deadline && <p className="muted small">截止时间：{new Date(d.deadline).toLocaleString()}</p>}
          {focused && <div className="decision-options">
            {d.options.map(o => (
              <button key={o.option_id} disabled={busy} onClick={() => resolveDecision(d, o.option_id)}>
                <b>{o.label}</b>
                <small>{o.near_term_consequence}</small>
                <small className="rev">{o.reversibility === 'IRREVERSIBLE' ? '不可撤销' : o.reversibility === 'COSTLY' ? '撤销代价较高' : '可撤销'}</small>
                {d.default_option_id === o.option_id && <small className="rev">默认选项</small>}
              </button>
            ))}
            {d.default_option_id && <button className="primary" disabled={busy}
              onClick={() => resolveDecision(d, null, true)}>按默认策略继续（{new Date(d.deadline || Date.now()).toLocaleDateString()} 前未决定则自动采用）</button>}
          </div>}
          {!focused && <button onClick={() => setFocusDecision(d)}>查看选项并决定</button>}
        </div>
      })}
    </Sheet>}

    {/* === 底部弹层：投诉与申诉（FR-25）=== */}
    {sheet === 'moderation' && <Sheet title="投诉与申诉" onClose={() => setSheet(null)}>
      <p className="muted small">投诉会进入待处理队列，由人工给出结论；系统不会自动判定内容是否违规。</p>
      <div className="setting-row">
        <span className="setting-label">原因</span>
        <select value={modReason} onChange={e => setModReason(e.target.value)} aria-label="投诉原因">
          <option value="illegal">违法违规</option>
          <option value="porn">色情</option>
          <option value="violence">暴力</option>
          <option value="political">敏感政治</option>
          <option value="infringement">侵权</option>
          <option value="privacy">隐私</option>
          <option value="other">其他</option>
        </select>
      </div>
      <textarea value={modDetail} onChange={e => setModDetail(e.target.value)} rows={3}
        placeholder="说明具体位置或情况（可选）" />
      <button className="primary" disabled={busy} onClick={reportModeration}>提交投诉</button>
      {modNotice && <p className="muted small">{modNotice}</p>}
      {modCases.length > 0 && <>
        <p className="eyebrow" style={{marginTop:16}}>已有记录</p>
        {modCases.map(c => <div key={c.case_id} className="decision-card">
          <b>{c.reason_code || '未分类'}{c.chapter_no ? ` · 第 ${c.chapter_no} 章` : ''}</b>
          <p className="muted small">状态：{c.decision}{c.appealed_at ? '（已申诉）' : ''}{c.resolved_at ? '（已结案）' : '（待处理）'}</p>
          {!c.appealed_at && !c.resolved_at && <button disabled={busy} onClick={() => appealModeration(c.case_id)}>申诉</button>}
        </div>)}
      </>}
    </Sheet>}

    {/* === 底部弹层：阅读设置 === */}
    {sheet === 'reader' && <Sheet title="阅读设置" onClose={() => setSheet(null)}>
      <div className="setting-row">
        <span className="setting-label"><Icon name="type" size={17} />字号</span>
        <div className="stepper">
          <button onClick={() => setReaderFs(v => Math.max(14, v - 1))} disabled={readerFs <= 14} aria-label="减小字号">A−</button>
          <span className="val">{readerFs}px</span>
          <button onClick={() => setReaderFs(v => Math.min(26, v + 1))} disabled={readerFs >= 26} aria-label="增大字号">A＋</button>
        </div>
      </div>
      <div className="setting-row">
        <span className="setting-label"><Icon name="list" size={17} />行距</span>
        <div className="lh-row">
          {([[1.6,'紧凑'],[1.85,'标准'],[2.05,'宽松'],[2.3,'很宽']] as const).map(([v,label]) => (
            <button key={v} className={readerLh === v ? 'active' : ''} onClick={() => setReaderLh(v)}>{label}</button>
          ))}
        </div>
      </div>
      <div className="setting-row">
        <span className="setting-label"><Icon name="sliders" size={17} />主题</span>
        <div className="theme-row">
          {([['day','日间'],['sepia','护眼'],['night','夜间']] as const).map(([k,label]) => (
            <button key={k} className={theme === k ? 'active' : ''} onClick={() => setTheme(k)}>{label}</button>
          ))}
        </div>
      </div>
      <p className="quality-note" style={{marginTop:14}}>向下滚动会自动收起顶栏与底栏；向上滚动或轻点正文即可唤回。</p>
    </Sheet>}

    {sheet === 'cost' && workId && <Sheet title="成本明细" onClose={() => setSheet(null)}>
      {cost ? <div>
        <p className="big">{((cost.consumed_minor) / 100).toFixed(2)} <small>{cost.currency}</small></p>
        {cost.by_chapter.length > 0 && <div><p className="eyebrow">按章节</p>
          {cost.by_chapter.map(c => <div className="costRow" key={c.chapter_no}><span>第 {c.chapter_no} 章</span><span>{(c.amount_minor/100).toFixed(2)}</span></div>)}
        </div>}
        {cost.by_step.length > 0 && <div style={{marginTop:16}}><p className="eyebrow">按步骤</p>
          {cost.by_step.map(s => <div className="costRow" key={s.step}><span>{STEP_LABELS[s.step]||s.step}</span><span>{(s.amount_minor/100).toFixed(2)}</span></div>)}
        </div>}
      </div> : <p className="muted">加载中…</p>}
    </Sheet>}

    {sheet === 'share' && workId && <Sheet title="定向分享" onClose={() => setSheet(null)}>
      <p className="muted small" style={{lineHeight:1.7}}>生成一个链接发给朋友，仅被邀请者可读，不会被搜索引擎收录。</p>
      <div className="shareForm">
        <input placeholder="对方称呼（可选）" value={shareLabel} onChange={e=>setShareLabel(e.target.value)} aria-label="邀请对象"/>
        <button disabled={busy} onClick={createShare}>{busy?'生成中…':'生成链接'}</button>
      </div>
      {shareUrl && <div className="shareResult"><p>分享链接已生成（点击复制）：</p><code onClick={()=>navigator.clipboard.writeText(shareUrl)}>{shareUrl}</code></div>}
      {shares.filter(s=>!s.revoked_at).length > 0 && <div style={{marginTop:14}}><p className="eyebrow">有效分享</p>
        {shares.filter(s=>!s.revoked_at).map(s => <div className="shareRow" key={s.share_id}><code className="sm">{s.share_url.slice(0,44)}…</code><button onClick={()=>revokeShare(s.share_id)}>撤回</button></div>)}
      </div>}
    </Sheet>}

    {sheet === 'export' && workId && <Sheet title="导出作品" onClose={() => setSheet(null)}>
      {exportNotice && !exportNotice.satisfied_at && <div className="exportNotice"><p><b>{exportNotice.title}</b></p><p className="muted">{exportNotice.body}</p>
        <button className="primary" disabled={busy} onClick={acknowledgeAndExport}>{busy?'处理中…':'知悉并导出'}</button></div>}
      {exportNotice?.satisfied_at && !exportResult && <div><p className="muted small">已确认导出告知。</p><button className="primary" style={{marginTop:12}} disabled={busy} onClick={acknowledgeAndExport}>{busy?'导出中…':'导出 TXT'}</button></div>}
      {exportResult && <div className="shareResult"><p>导出成功：</p><a href={exportResult.download_url} download className="primary dl">下载 {exportResult.format.toUpperCase()}</a></div>}
      {!exportNotice && <p className="muted">加载中…</p>}
    </Sheet>}

    {sheet === 'fact' && workId && <Sheet title="事实报错" onClose={() => setSheet(null)}>
      <p className="muted small" style={{lineHeight:1.7}}>发现前后矛盾或与设定不符的事实？请描述问题，系统会在后续章节修正。</p>
      <textarea className="factInput" value={factStatement} onChange={e=>setFactStatement(e.target.value)} placeholder="例如：第1章说修表匠的店铺在城东，但第3章变成了城西…" aria-label="事实错误描述"/>
      <button className="primary" disabled={busy||!factStatement.trim()} onClick={reportFact}>{busy?'提交中…':'提交报错'}</button>
      {factResult && <p className="factResult">{factResult}</p>}
      {factPendingResume && <button className="primary" disabled={busy} onClick={resumeForCorrection}>{busy?'恢复中…':'恢复创作并执行重演'}</button>}
    </Sheet>}
  </div>
}

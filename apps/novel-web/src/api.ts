const KEY = 'novel-maker-token'

async function token() {
  const saved = localStorage.getItem(KEY)
  if (saved) return saved
  const response = await fetch('/v1/novel/auth/session', {method: 'POST'})
  if (!response.ok) throw new Error('无法建立创作会话')
  const body = await response.json()
  localStorage.setItem(KEY, body.token)
  return body.token as string
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const auth = await token()
  const response = await fetch(`/v1/novel${path}`, {
    ...init,
    headers: {'Content-Type':'application/json', Authorization:`Bearer ${auth}`, ...(init.headers || {})},
  })
  if (!response.ok) {
    const detail = await response.json().catch(() => ({}))
    throw new Error(detail?.error?.message || detail?.message || `请求失败 (${response.status})`)
  }
  return response.status === 204 ? undefined as T : response.json()
}

export function logout() {
  localStorage.removeItem(KEY)
}

/**
 * fetch-based SSE with auth header (EventSource doesn't support custom headers).
 * Returns an abort function. The handler receives {id?, event?, data?}.
 */
export function connectSSE(
  path: string,
  handler: (msg: {id?: string; event?: string; data?: string}) => void,
  opts?: {onError?: (e: Error) => void; onRetry?: () => void},
): () => void {
  const ac = new AbortController()
  let lastSeq = ''
  let retryMs = 1000

  ;(async () => {
    while (!ac.signal.aborted) {
      try {
        const auth = localStorage.getItem(KEY) || ''
        const params = new URLSearchParams()
        if (lastSeq) params.set('after_seq', lastSeq)
        const qs = params.toString()
        const res = await fetch(`/v1/novel${path}${qs ? '?' + qs : ''}`, {
          headers: {Authorization: `Bearer ${auth}`, Accept: 'text/event-stream'},
          signal: ac.signal,
        })
        if (!res.ok || !res.body) { opts?.onError?.(new Error(`SSE ${res.status}`)); return }
        retryMs = 1000
        opts?.onRetry?.()
        const reader = res.body.getReader()
        const dec = new TextDecoder()
        let buf = ''
        while (!ac.signal.aborted) {
          const {done, value} = await reader.read()
          if (done) break
          buf += dec.decode(value, {stream: true})
          const parts = buf.split('\n\n')
          buf = parts.pop() || ''
          for (const part of parts) {
            if (!part.trim()) continue
            let id: string | undefined, event: string | undefined, data = ''
            for (const line of part.split('\n')) {
              if (line.startsWith('id: ')) id = line.slice(4)
              else if (line.startsWith('event: ')) event = line.slice(7)
              else if (line.startsWith('data: ')) data += (data ? '\n' : '') + line.slice(6)
            }
            if (id) lastSeq = id
            if (data || event) handler({id, event, data})
          }
        }
      } catch (e) {
        if ((e as Error).name === 'AbortError') return
        opts?.onError?.(e as Error)
      }
      if (ac.signal.aborted) return
      await new Promise(r => setTimeout(r, retryMs))
      retryMs = Math.min(retryMs * 2, 30000)
    }
  })()

  return () => ac.abort()
}

const KEY = 'novel-maker-token'

async function readJson(response: Response): Promise<any> {
  const text = await response.text()
  const trimmed = text.trim()
  if (!trimmed) return {}
  if (trimmed.startsWith('<') || trimmed.startsWith('<!')) {
    throw new Error(
      `服务返回了网页而不是接口数据（HTTP ${response.status}）。请硬刷新后重试；若刚更新过站点，先清除本站数据再打开。`,
    )
  }
  try {
    return JSON.parse(trimmed)
  } catch {
    throw new Error(`接口响应无法解析（HTTP ${response.status}）：${trimmed.slice(0, 80)}`)
  }
}

async function mintToken(): Promise<string> {
  const response = await fetch('/v1/novel/auth/session', {method: 'POST'})
  if (!response.ok) {
    throw new Error(`无法建立创作会话（HTTP ${response.status}）`)
  }
  const body = await readJson(response)
  const tok = String(body.token || '')
  if (!tok) throw new Error('创作会话未返回 token')
  localStorage.setItem(KEY, tok)
  return tok
}

async function token(forceNew = false): Promise<string> {
  if (!forceNew) {
    const saved = localStorage.getItem(KEY)
    if (saved) return saved
  } else {
    localStorage.removeItem(KEY)
  }
  return mintToken()
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const attempt = async (auth: string) => {
    const response = await fetch(`/v1/novel${path}`, {
      ...init,
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${auth}`,
        ...(init.headers || {}),
      },
    })
    return response
  }

  let auth = await token()
  let response = await attempt(auth)

  // 部署重启后旧 token 常失效：清掉并重签一次，避免整页刷「未登录」假错。
  if (response.status === 401) {
    auth = await token(true)
    response = await attempt(auth)
  }

  if (!response.ok) {
    const detail = await readJson(response).catch(() => ({} as any))
    throw new Error(
      detail?.error?.message || detail?.message || `请求失败 (${response.status})`,
    )
  }
  if (response.status === 204) return undefined as T
  return (await readJson(response)) as T
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
        let auth = localStorage.getItem(KEY) || ''
        if (!auth) {
          try { auth = await mintToken() } catch (e) {
            opts?.onError?.(e as Error)
            return
          }
        }
        const params = new URLSearchParams()
        if (lastSeq) params.set('after_seq', lastSeq)
        const qs = params.toString()
        let res = await fetch(`/v1/novel${path}${qs ? '?' + qs : ''}`, {
          headers: {Authorization: `Bearer ${auth}`, Accept: 'text/event-stream'},
          signal: ac.signal,
        })
        if (res.status === 401) {
          auth = await mintToken()
          res = await fetch(`/v1/novel${path}${qs ? '?' + qs : ''}`, {
            headers: {Authorization: `Bearer ${auth}`, Accept: 'text/event-stream'},
            signal: ac.signal,
          })
        }
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

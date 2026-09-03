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

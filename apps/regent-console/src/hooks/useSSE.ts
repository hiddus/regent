import { useEffect, useRef, useCallback } from 'react'

interface UseSSEOptions {
  onEvent?: (type: string, data: Record<string, unknown>) => void
  onError?: (error: Event) => void
  onConnectionChange?: (state: 'connecting' | 'connected' | 'reconnecting') => void
  /** Reconnect delay in ms (default 3000) */
  reconnectDelay?: number
}

export function useSSE(url: string | null, options: UseSSEOptions) {
  const esRef = useRef<EventSource | null>(null)
  const optionsRef = useRef(options)
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const reconnectCountRef = useRef(0)
  optionsRef.current = options

  const disconnect = useCallback(() => {
    if (reconnectTimerRef.current) {
      clearTimeout(reconnectTimerRef.current)
      reconnectTimerRef.current = null
    }
    if (esRef.current) {
      esRef.current.close()
      esRef.current = null
    }
  }, [])

  const connect = useCallback(() => {
    if (!url) return
    disconnect()
    optionsRef.current.onConnectionChange?.(
      reconnectCountRef.current > 0 ? 'reconnecting' : 'connecting',
    )

    const es = new EventSource(url)
    esRef.current = es

    es.onopen = () => {
      reconnectCountRef.current = 0
      optionsRef.current.onConnectionChange?.('connected')
    }

    es.onmessage = (event) => {
      try {
        const parsed = JSON.parse(event.data) as { type: string; data: Record<string, unknown> }
        optionsRef.current.onEvent?.(parsed.type, parsed.data)
      } catch {
        // ignore parse errors
      }
    }

    es.onerror = (e) => {
      optionsRef.current.onError?.(e)
      optionsRef.current.onConnectionChange?.('reconnecting')
      // Close and schedule reconnect with exponential backoff
      es.close()
      esRef.current = null

      const delay = Math.min(
        (options.reconnectDelay ?? 3000) * Math.pow(1.5, reconnectCountRef.current),
        30000,
      )
      reconnectCountRef.current += 1
      reconnectTimerRef.current = setTimeout(() => {
        connect()
      }, delay)
    }
  }, [url, disconnect, options.reconnectDelay])

  useEffect(() => {
    if (!url) {
      optionsRef.current.onConnectionChange?.('connecting')
      disconnect()
      return
    }
    connect()
    return () => {
      disconnect()
    }
  }, [connect, disconnect, url])
}

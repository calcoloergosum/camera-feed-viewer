import { useEffect, useRef, useState } from 'react'
import { pushFrameWithLimit } from '../lib/overlaySync'
import { parseOverlayFrame, type OverlayFrame } from '../types/overlay'

type ConnectionState = 'idle' | 'connecting' | 'open' | 'closed' | 'error'

const BACKEND_BASE_URL =
  (import.meta.env.VITE_BACKEND_BASE_URL as string | undefined)?.replace(/\/$/, '') ||
  'http://127.0.0.1:8000'

const METADATA_WS_URL = `${BACKEND_BASE_URL.replace(/^http/, 'ws')}/ws/metadata`

const RETRY_BASE_MS = 500
const RETRY_MAX_MS = 6000

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null

interface UseOverlayFeedOptions {
  enabled: boolean
}

interface OverlayFeedState {
  frames: OverlayFrame[]
  connectionState: ConnectionState
  receivedFrames: number
  droppedFrames: number
}

export const useOverlayFeed = ({
  enabled,
}: UseOverlayFeedOptions): OverlayFeedState => {
  const [frames, setFrames] = useState<OverlayFrame[]>([])
  const [connectionState, setConnectionState] = useState<ConnectionState>('idle')
  const [receivedFrames, setReceivedFrames] = useState(0)
  const [droppedFrames, setDroppedFrames] = useState(0)

  const serverToLocalOffsetMsRef = useRef<number | null>(null)

  useEffect(() => {
    if (!enabled) {
      return
    }

    let disposed = false
    let socket: WebSocket | null = null
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null
    let reconnectAttempt = 0

    const cleanup = () => {
      if (reconnectTimer) {
        clearTimeout(reconnectTimer)
        reconnectTimer = null
      }

      if (socket) {
        socket.close()
        socket = null
      }
    }

    const ingestFrame = (rawMessage: string) => {
      let parsedValue: unknown

      try {
        parsedValue = JSON.parse(rawMessage)
      } catch {
        setDroppedFrames((count) => count + 1)
        return
      }

      const frame = parseOverlayFrame(parsedValue)
      if (!frame) {
        setDroppedFrames((count) => count + 1)
        return
      }

      const localEpochNow = Date.now()
      const sampleOffset = localEpochNow - frame.timestampMs
      const currentOffset = serverToLocalOffsetMsRef.current
      if (currentOffset === null || Math.abs(sampleOffset - currentOffset) > 5000) {
        serverToLocalOffsetMsRef.current = sampleOffset
      } else {
        serverToLocalOffsetMsRef.current = currentOffset * 0.9 + sampleOffset * 0.1
      }

      let referenceTimestamp = frame.timestampMs
      if (isRecord(parsedValue) && typeof parsedValue.serverTimestampMs === 'number') {
        referenceTimestamp = parsedValue.serverTimestampMs
      }

      const normalizedFrame: OverlayFrame = {
        ...frame,
        // Stage D aligns backend epoch timestamps into local epoch timeline using an offset estimator.
        timestampMs: referenceTimestamp + (serverToLocalOffsetMsRef.current ?? sampleOffset),
      }

      setFrames((current) => pushFrameWithLimit(current, normalizedFrame))
      setReceivedFrames((count) => count + 1)
    }

    const scheduleReconnect = () => {
      if (disposed) {
        return
      }

      const delay = Math.min(
        RETRY_MAX_MS,
        RETRY_BASE_MS * 2 ** Math.min(6, reconnectAttempt),
      )
      reconnectAttempt += 1

      reconnectTimer = setTimeout(() => {
        void connectWebSocket()
      }, delay)
    }

    const connectWebSocket = async () => {
      if (disposed) {
        return
      }

      setFrames([])
      setConnectionState('connecting')
      setReceivedFrames(0)
      setDroppedFrames(0)
      socket = new WebSocket(METADATA_WS_URL)

      socket.onopen = () => {
        if (disposed) {
          return
        }

        reconnectAttempt = 0
        setConnectionState('open')
      }

      socket.onerror = () => {
        if (!disposed) {
          setConnectionState('error')
        }
      }

      socket.onclose = () => {
        if (disposed) {
          return
        }

        setConnectionState('closed')
        scheduleReconnect()
      }

      socket.onmessage = (messageEvent) => {
        if (typeof messageEvent.data === 'string') {
          ingestFrame(messageEvent.data)
          return
        }

        setDroppedFrames((count) => count + 1)
      }
    }

    void connectWebSocket().catch(() => {
      if (!disposed) {
        setConnectionState('error')
      }
    })

    return () => {
      disposed = true
      cleanup()
    }
  }, [enabled])

  return {
    frames: enabled ? frames : [],
    connectionState: enabled ? connectionState : 'idle',
    receivedFrames: enabled ? receivedFrames : 0,
    droppedFrames: enabled ? droppedFrames : 0,
  }
}
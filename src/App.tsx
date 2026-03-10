import { useEffect, useMemo, useRef, useState } from 'react'
import './App.css'
import { OverlayCanvas } from './components/OverlayCanvas'
import { useOverlayFeed } from './hooks/useOverlayFeed.ts'
import { selectSynchronizedFrame } from './lib/overlaySync'

const DEFAULT_VIDEO_SIZE = {
  width: 1280,
  height: 720,
}

type FeedStatus = 'idle' | 'connecting' | 'ready' | 'error'

const BACKEND_BASE_URL =
  (import.meta.env.VITE_BACKEND_BASE_URL as string | undefined)?.replace(/\/$/, '') ||
  'http://127.0.0.1:8000'

const HEALTH_TIMEOUT_MS = 2000
const HEALTH_POLL_MS = 2500
const RETRY_BASE_MS = 700
const RETRY_MAX_MS = 10000

const parseNumberWithDefault = (value: string | undefined, fallback: number) => {
  if (!value) {
    return fallback
  }

  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : fallback
}

const SYNC_TOLERANCE_MS = Math.max(
  20,
  parseNumberWithDefault(import.meta.env.VITE_SYNC_TOLERANCE_MS as string | undefined, 220),
)

const SYNC_MAX_AGE_MS = Math.max(
  SYNC_TOLERANCE_MS,
  parseNumberWithDefault(import.meta.env.VITE_SYNC_MAX_AGE_MS as string | undefined, 2500),
)

const formatLastSeen = (timestampMs: number | null) => {
  if (timestampMs === null) {
    return 'N/A'
  }

  return new Date(timestampMs).toLocaleTimeString()
}

function App() {
  const [feedEnabled, setFeedEnabled] = useState(true)
  const [feedStatus, setFeedStatus] = useState<FeedStatus>('connecting')
  const [feedError, setFeedError] = useState<string | null>(null)
  const [feedNonce, setFeedNonce] = useState(0)
  const [reconnectAttempt, setReconnectAttempt] = useState(0)
  const [nextRetryAtMs, setNextRetryAtMs] = useState<number | null>(null)
  const [lastFrameAtMs, setLastFrameAtMs] = useState<number | null>(null)
  const [connectionCycle, setConnectionCycle] = useState(0)
  const [videoSize, setVideoSize] = useState(DEFAULT_VIDEO_SIZE)
  const [videoTimeMs, setVideoTimeMs] = useState(Date.now())
  const [overlayFps, setOverlayFps] = useState(0)
  const [retryCountdownSeconds, setRetryCountdownSeconds] = useState<number | null>(null)

  const imageRef = useRef<HTMLImageElement | null>(null)

  useEffect(() => {
    if (!feedEnabled) {
      return
    }

    let cancelled = false
    let reconnectTimer: number | null = null
    let healthPollTimer: number | null = null
    let attempt = 0

    const fetchHealth = async () => {
      const controller = new AbortController()
      const timeoutId = window.setTimeout(() => {
        controller.abort()
      }, HEALTH_TIMEOUT_MS)

      try {
        const response = await fetch(`${BACKEND_BASE_URL}/health`, {
          cache: 'no-store',
          signal: controller.signal,
        })

        window.clearTimeout(timeoutId)

        if (!response.ok) {
          throw new Error(`Backend health check failed: HTTP ${response.status}`)
        }

        return true
      } catch {
        window.clearTimeout(timeoutId)
        return false
      }
    }

    const clearTimers = () => {
      if (reconnectTimer !== null) {
        window.clearTimeout(reconnectTimer)
        reconnectTimer = null
      }

      if (healthPollTimer !== null) {
        window.clearInterval(healthPollTimer)
        healthPollTimer = null
      }
    }

    const startHealthPolling = () => {
      clearTimers()
      healthPollTimer = window.setInterval(() => {
        void (async () => {
          const ok = await fetchHealth()
          if (cancelled) {
            return
          }

          if (ok) {
            setLastFrameAtMs(Date.now())
            return
          }

          clearTimers()
          scheduleReconnect('Backend became unreachable. Reconnecting...')
        })()
      }, HEALTH_POLL_MS)
    }

    const scheduleReconnect = (message: string) => {
      if (cancelled) {
        return
      }

      const cappedPower = Math.min(attempt, 6)
      const backoffDelay = Math.min(RETRY_MAX_MS, RETRY_BASE_MS * 2 ** cappedPower)
      const jitter = Math.floor(Math.random() * 250)
      const retryDelay = backoffDelay + jitter
      const nextAttempt = attempt + 1

      setFeedStatus('error')
      setFeedError(message)
      setReconnectAttempt(nextAttempt)
      setNextRetryAtMs(Date.now() + retryDelay)

      reconnectTimer = window.setTimeout(() => {
        attempt = nextAttempt
        void startConnection()
      }, retryDelay)
    }

    const startConnection = async () => {
      if (cancelled) {
        return
      }

      setFeedStatus('connecting')
      setFeedError(null)
      setNextRetryAtMs(null)

      const ok = await fetchHealth()
      if (ok) {
        if (cancelled) {
          return
        }

        setFeedNonce((current) => current + 1)
        setFeedStatus('ready')
        setFeedError(null)
        setReconnectAttempt(0)
        setNextRetryAtMs(null)
        setLastFrameAtMs(Date.now())
        startHealthPolling()
        return
      }

      scheduleReconnect(
        `Unable to connect to backend at ${BACKEND_BASE_URL}. Reconnecting automatically...`,
      )
    }

    void startConnection()

    return () => {
      cancelled = true
      clearTimers()
    }
  }, [feedEnabled, connectionCycle])

  useEffect(() => {
    if (!feedEnabled || nextRetryAtMs === null) {
      setRetryCountdownSeconds(null)
      return
    }

    const updateCountdown = () => {
      const remainingMs = nextRetryAtMs - Date.now()
      if (remainingMs <= 0) {
        setRetryCountdownSeconds(0)
        return
      }

      setRetryCountdownSeconds(Math.ceil(remainingMs / 1000))
    }

    updateCountdown()
    const intervalId = window.setInterval(updateCountdown, 250)

    return () => {
      window.clearInterval(intervalId)
    }
  }, [feedEnabled, nextRetryAtMs])

  const effectiveFeedStatus: FeedStatus = feedEnabled ? feedStatus : 'idle'
  const effectiveFeedError = feedEnabled ? feedError : null
  const effectiveReconnectAttempt = feedEnabled ? reconnectAttempt : 0
  const effectiveRetryCountdown = feedEnabled ? retryCountdownSeconds : null

  const streamUrl = feedEnabled
    ? `${BACKEND_BASE_URL}/stream.mjpeg?fps=30&nonce=${feedNonce}`
    : undefined

  useEffect(() => {
    let frameId = 0

    const tick = () => {
      setVideoTimeMs(Date.now())

      frameId = requestAnimationFrame(tick)
    }

    frameId = requestAnimationFrame(tick)
    return () => {
      cancelAnimationFrame(frameId)
    }
  }, [])

  const {
    frames,
    connectionState,
    receivedFrames,
    droppedFrames,
  } = useOverlayFeed({
    enabled: feedEnabled && effectiveFeedStatus === 'ready',
  })

  const synchronizedFrame = useMemo(
    () => selectSynchronizedFrame(frames, videoTimeMs, SYNC_TOLERANCE_MS, SYNC_MAX_AGE_MS),
    [frames, videoTimeMs],
  )

  const overlayLagMs = useMemo(() => {
    if (!synchronizedFrame) {
      return null
    }
    return Math.max(0, videoTimeMs - synchronizedFrame.timestampMs)
  }, [synchronizedFrame, videoTimeMs])

  const handleMetadataLoaded = () => {
    const element = imageRef.current
    if (!element || !element.naturalWidth || !element.naturalHeight) {
      return
    }

    setVideoSize((current) => {
      if (
        current.width === element.naturalWidth &&
        current.height === element.naturalHeight
      ) {
        return current
      }

      return {
        width: element.naturalWidth,
        height: element.naturalHeight,
      }
    })

    setLastFrameAtMs(Date.now())
  }

  const handleFeedError = () => {
    if (!feedEnabled) {
      return
    }

    setFeedStatus('error')
    setFeedError(
      `Stream decode failed. Reconnecting to ${BACKEND_BASE_URL}...`,
    )
    setConnectionCycle((current) => current + 1)
  }

  const handleRetryNow = () => {
    setConnectionCycle((current) => current + 1)
  }

  return (
    <main className="app-shell">
      <section className="stage-wrap">
        <div className="stage" role="img" aria-label="live camera with overlays">
          <img
            ref={imageRef}
            className="camera-video"
            src={streamUrl}
            alt="Backend camera stream"
            onLoad={handleMetadataLoaded}
            onError={handleFeedError}
          />
          <OverlayCanvas
            frame={synchronizedFrame}
            videoWidth={videoSize.width}
            videoHeight={videoSize.height}
            onFpsSample={setOverlayFps}
          />
          {feedEnabled && effectiveFeedStatus === 'error' && (
            <div className="stage-fallback" role="status" aria-live="polite">
              <h2>Feed unavailable</h2>
              <p>Automatic reconnect is active.</p>
              <button type="button" className="retry-btn" onClick={handleRetryNow}>
                Retry now
              </button>
            </div>
          )}
        </div>
      </section>

      <aside className="hud-panels" aria-label="overlay controls">
        <details className="hud-panel" aria-label="feed controls and status">
          <summary title="Feed controls" aria-label="Feed controls">
            <svg
              className="hud-icon-svg"
              viewBox="0 0 24 24"
              fill="none"
              xmlns="http://www.w3.org/2000/svg"
              aria-hidden="true"
            >
              <path
                d="M4 8H20M4 16H20M8 5V11M16 13V19"
                stroke="currentColor"
                strokeWidth="1.8"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
              <circle cx="8" cy="12" r="2.2" fill="currentColor" />
              <circle cx="16" cy="12" r="2.2" fill="currentColor" />
            </svg>
          </summary>

          <div className="hud-panel-body">
            <h2>Feed Controls</h2>
            <section className="control-strip" aria-label="camera and feed controls">
              <button
                type="button"
                className="action-btn"
                onClick={() => setFeedEnabled((current) => !current)}
              >
                {feedEnabled ? 'Stop feed' : 'Start feed'}
              </button>
              <div className="status-pill">
                Backend feed: <strong>{effectiveFeedStatus}</strong>
              </div>
              <div className="status-pill">
                Metadata: <strong>{connectionState}</strong>
              </div>
              <div className="status-pill">
                Reconnect attempts: <strong>{effectiveReconnectAttempt}</strong>
              </div>
            </section>

            {effectiveFeedError && <p className="error-banner">{effectiveFeedError}</p>}
          </div>
        </details>

        <details className="hud-panel" aria-label="testing diagnostics">
          <summary title="Testing diagnostics" aria-label="Testing diagnostics">
            <svg
              className="hud-icon-svg"
              viewBox="0 0 24 24"
              fill="none"
              xmlns="http://www.w3.org/2000/svg"
              aria-hidden="true"
            >
              <path
                d="M5 19V11M11 19V7M17 19V14"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
              />
              <path
                d="M4 19H20"
                stroke="currentColor"
                strokeWidth="1.8"
                strokeLinecap="round"
              />
            </svg>
          </summary>

          <div className="hud-panel-body">
            <h2>Testing Diagnostics</h2>
            <section className="diagnostics-grid" aria-label="connection diagnostics">
              <article>
                <h3>Backend URL</h3>
                <p>{BACKEND_BASE_URL}</p>
              </article>
              <article>
                <h3>Last successful frame</h3>
                <p>{formatLastSeen(lastFrameAtMs)}</p>
              </article>
              <article>
                <h3>Next retry in</h3>
                <p>{effectiveRetryCountdown === null ? 'N/A' : `${effectiveRetryCountdown}s`}</p>
              </article>
              <article>
                <h3>Sync window</h3>
                <p>{`+-${SYNC_TOLERANCE_MS.toFixed(0)} ms (max age ${SYNC_MAX_AGE_MS.toFixed(0)} ms)`}</p>
              </article>
            </section>

            <section className="metrics-grid" aria-label="stream metrics">
              <article>
                <h3>Overlay render fps</h3>
                <p>{overlayFps.toFixed(1)}</p>
              </article>
              <article>
                <h3>Metadata frames received</h3>
                <p>{receivedFrames}</p>
              </article>
              <article>
                <h3>Metadata frames dropped</h3>
                <p>{droppedFrames}</p>
              </article>
              <article>
                <h3>Sync lag</h3>
                <p>{overlayLagMs === null ? 'N/A' : `${overlayLagMs.toFixed(0)} ms`}</p>
              </article>
            </section>
          </div>
        </details>
      </aside>
    </main>
  )
}

export default App

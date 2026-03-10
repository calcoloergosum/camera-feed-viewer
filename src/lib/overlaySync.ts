import type { OverlayFrame } from '../types/overlay'

export const MAX_BUFFERED_FRAMES = 180

export const pushFrameWithLimit = (
  previous: OverlayFrame[],
  nextFrame: OverlayFrame,
  maxFrames = MAX_BUFFERED_FRAMES,
) => {
  const next = [...previous, nextFrame]
  if (next.length <= maxFrames) {
    return next
  }

  return next.slice(next.length - maxFrames)
}

export const selectSynchronizedFrame = (
  frames: OverlayFrame[],
  videoTimeMs: number,
  toleranceMs: number,
  maxAgeMs: number,
): OverlayFrame | null => {
  if (frames.length === 0 || !Number.isFinite(videoTimeMs)) {
    return null
  }

  let best: OverlayFrame | null = null
  let bestDistance = Number.POSITIVE_INFINITY

  for (const frame of frames) {
    const ageMs = videoTimeMs - frame.timestampMs
    if (ageMs > maxAgeMs) {
      continue
    }

    const distance = Math.abs(frame.timestampMs - videoTimeMs)
    if (distance > toleranceMs) {
      continue
    }

    if (distance < bestDistance) {
      best = frame
      bestDistance = distance
      continue
    }

    if (distance === bestDistance && best && frame.timestampMs > best.timestampMs) {
      best = frame
    }
  }

  return best
}
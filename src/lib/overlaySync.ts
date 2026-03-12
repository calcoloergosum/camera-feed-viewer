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

  void toleranceMs

  let latest: OverlayFrame | null = null

  for (const frame of frames) {
    const ageMs = videoTimeMs - frame.timestampMs
    if (ageMs > maxAgeMs) {
      continue
    }

    if (!latest || frame.timestampMs > latest.timestampMs) {
      latest = frame
    }
  }

  return latest
}
import type { OverlayFrame } from '../types/overlay'

const SOURCE_WIDTH = 1280
const SOURCE_HEIGHT = 720

const clamp = (value: number, min: number, max: number) =>
  Math.min(max, Math.max(min, value))

export const createDemoOverlayFrame = (timestampMs: number): OverlayFrame => {
  const t = timestampMs / 1000
  const centerX = SOURCE_WIDTH * (0.5 + Math.sin(t * 0.6) * 0.25)
  const centerY = SOURCE_HEIGHT * (0.45 + Math.cos(t * 0.9) * 0.18)

  const boxWidth = 190
  const boxHeight = 130
  const boxX = clamp(centerX - boxWidth / 2, 0, SOURCE_WIDTH - boxWidth)
  const boxY = clamp(centerY - boxHeight / 2, 0, SOURCE_HEIGHT - boxHeight)

  const sweep = (Math.sin(t * 0.75) + 1) / 2
  const polyLeft = 140 + sweep * 160
  const polyTop = 160 + Math.cos(t * 0.4) * 40

  return {
    timestampMs,
    sourceWidth: SOURCE_WIDTH,
    sourceHeight: SOURCE_HEIGHT,
    items: [
      {
        kind: 'box',
        id: 'tracked-target',
        x: boxX,
        y: boxY,
        width: boxWidth,
        height: boxHeight,
        label: 'Target A',
        color: '#2ec4b6',
      },
      {
        kind: 'polygon',
        id: 'attention-zone',
        points: [
          [polyLeft, polyTop],
          [polyLeft + 300, polyTop - 45],
          [polyLeft + 410, polyTop + 125],
          [polyLeft + 120, polyTop + 220],
        ],
        label: 'Zone 2',
        color: '#ff9f1c',
      },
      {
        kind: 'label',
        id: 'status',
        x: 32,
        y: 42,
        text: `metadata tick ${Math.floor(t * 2)}`,
        color: '#e8f1ff',
      },
    ],
  }
}
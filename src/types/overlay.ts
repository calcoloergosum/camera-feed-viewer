export type OverlayItem = OverlayBox | OverlayPolygon | OverlayLabel

interface OverlayBase {
  id: string
  color?: string
}

export interface OverlayBox extends OverlayBase {
  kind: 'box'
  x: number
  y: number
  width: number
  height: number
  label?: string
}

export interface OverlayPolygon extends OverlayBase {
  kind: 'polygon'
  points: [number, number][]
  label?: string
}

export interface OverlayLabel extends OverlayBase {
  kind: 'label'
  x: number
  y: number
  text: string
}

export interface OverlayFrame {
  timestampMs: number
  sourceWidth: number
  sourceHeight: number
  items: OverlayItem[]
}

const isObject = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null

const isFiniteNumber = (value: unknown): value is number =>
  typeof value === 'number' && Number.isFinite(value)

const parsePoint = (value: unknown): [number, number] | null => {
  if (!Array.isArray(value) || value.length !== 2) {
    return null
  }

  const [x, y] = value
  if (!isFiniteNumber(x) || !isFiniteNumber(y)) {
    return null
  }

  return [x, y]
}

const parseItem = (value: unknown): OverlayItem | null => {
  if (!isObject(value) || typeof value.kind !== 'string') {
    return null
  }

  const id = typeof value.id === 'string' ? value.id : crypto.randomUUID()
  const color = typeof value.color === 'string' ? value.color : undefined

  if (value.kind === 'box') {
    if (
      !isFiniteNumber(value.x) ||
      !isFiniteNumber(value.y) ||
      !isFiniteNumber(value.width) ||
      !isFiniteNumber(value.height)
    ) {
      return null
    }

    return {
      kind: 'box',
      id,
      x: value.x,
      y: value.y,
      width: value.width,
      height: value.height,
      label: typeof value.label === 'string' ? value.label : undefined,
      color,
    }
  }

  if (value.kind === 'polygon') {
    if (!Array.isArray(value.points)) {
      return null
    }

    const points = value.points.map(parsePoint)
    if (points.some((point) => point === null)) {
      return null
    }

    return {
      kind: 'polygon',
      id,
      points: points as [number, number][],
      label: typeof value.label === 'string' ? value.label : undefined,
      color,
    }
  }

  if (value.kind === 'label') {
    if (
      !isFiniteNumber(value.x) ||
      !isFiniteNumber(value.y) ||
      typeof value.text !== 'string'
    ) {
      return null
    }

    return {
      kind: 'label',
      id,
      x: value.x,
      y: value.y,
      text: value.text,
      color,
    }
  }

  return null
}

export const parseOverlayFrame = (value: unknown): OverlayFrame | null => {
  if (!isObject(value) || !Array.isArray(value.items)) {
    return null
  }

  if (
    !isFiniteNumber(value.timestampMs) ||
    !isFiniteNumber(value.sourceWidth) ||
    !isFiniteNumber(value.sourceHeight)
  ) {
    return null
  }

  const items = value.items.map(parseItem)
  if (items.some((item) => item === null)) {
    return null
  }

  return {
    timestampMs: value.timestampMs,
    sourceWidth: value.sourceWidth,
    sourceHeight: value.sourceHeight,
    items: items as OverlayItem[],
  }
}
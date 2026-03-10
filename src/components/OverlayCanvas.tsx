import { useEffect, useRef } from 'react'
import {
  Application,
  Container,
  Graphics,
  Text,
  TextStyle,
} from 'pixi.js'
import type { OverlayFrame, OverlayItem } from '../types/overlay'

interface OverlayCanvasProps {
  frame: OverlayFrame | null
  videoWidth: number
  videoHeight: number
  onFpsSample?: (fps: number) => void
}

interface Bounds {
  x: number
  y: number
  width: number
  height: number
}

const fitContain = (
  viewportWidth: number,
  viewportHeight: number,
  sourceWidth: number,
  sourceHeight: number,
): Bounds => {
  const viewportRatio = viewportWidth / viewportHeight
  const sourceRatio = sourceWidth / sourceHeight

  if (sourceRatio > viewportRatio) {
    const width = viewportWidth
    const height = width / sourceRatio
    return {
      x: 0,
      y: (viewportHeight - height) / 2,
      width,
      height,
    }
  }

  const height = viewportHeight
  const width = height * sourceRatio
  return {
    x: (viewportWidth - width) / 2,
    y: 0,
    width,
    height,
  }
}

const parseColor = (value: string | undefined, fallback = 0x2ec4b6) => {
  if (!value || !value.startsWith('#')) {
    return fallback
  }

  const candidate = value.slice(1)
  if (candidate.length !== 3 && candidate.length !== 6) {
    return fallback
  }

  const hex = candidate.length === 3
    ? candidate
      .split('')
      .map((char) => `${char}${char}`)
      .join('')
    : candidate

  const parsed = Number.parseInt(hex, 16)
  return Number.isNaN(parsed) ? fallback : parsed
}

const drawLabel = (
  labelLayer: Container,
  text: string,
  x: number,
  y: number,
  color: number,
) => {
  const label = new Text({
    text,
    style: new TextStyle({
      fontFamily: 'Menlo, Monaco, Consolas, monospace',
      fontSize: 14,
      fill: color,
      stroke: {
        color: 0x000000,
        width: 3,
      },
    }),
  })
  label.x = x
  label.y = y
  labelLayer.addChild(label)
}

const drawItem = (
  graphics: Graphics,
  labelLayer: Container,
  item: OverlayItem,
  frame: OverlayFrame,
  viewport: Bounds,
) => {
  const color = parseColor(item.color)
  const projectX = (x: number) => viewport.x + (x / frame.sourceWidth) * viewport.width
  const projectY = (y: number) => viewport.y + (y / frame.sourceHeight) * viewport.height

  if (item.kind === 'box') {
    const x = projectX(item.x)
    const y = projectY(item.y)
    const width = (item.width / frame.sourceWidth) * viewport.width
    const height = (item.height / frame.sourceHeight) * viewport.height

    graphics.rect(x, y, width, height).fill({ color, alpha: 0.12 }).stroke({
      color,
      width: 2,
      alpha: 0.95,
    })

    if (item.label) {
      drawLabel(labelLayer, item.label, x + 4, Math.max(viewport.y, y - 20), color)
    }

    return
  }

  if (item.kind === 'polygon') {
    const points = item.points
      .map(([x, y]) => [projectX(x), projectY(y)] as const)
      .flatMap(([x, y]) => [x, y])

    graphics.poly(points, true).fill({ color, alpha: 0.1 }).stroke({
      color,
      width: 2,
      alpha: 0.95,
    })

    if (item.label && item.points.length > 0) {
      const [lx, ly] = item.points[0]
      drawLabel(labelLayer, item.label, projectX(lx) + 4, projectY(ly) - 20, color)
    }

    return
  }

  if (item.kind === 'label') {
    drawLabel(labelLayer, item.text, projectX(item.x), projectY(item.y), color)
  }
}

export const OverlayCanvas = ({
  frame,
  videoWidth,
  videoHeight,
  onFpsSample,
}: OverlayCanvasProps) => {
  const hostRef = useRef<HTMLDivElement | null>(null)
  const appRef = useRef<Application | null>(null)
  const graphicsRef = useRef<Graphics | null>(null)
  const labelsRef = useRef<Container | null>(null)

  useEffect(() => {
    const host = hostRef.current
    if (!host) {
      return
    }

    let cancelled = false
    let fpsSampleAt = 0

    const app = new Application()
    const setup = async () => {
      await app.init({
        backgroundAlpha: 0,
        antialias: true,
        resizeTo: host,
      })

      if (cancelled) {
        app.destroy()
        return
      }

      host.appendChild(app.canvas)

      const graphics = new Graphics()
      const labels = new Container()
      app.stage.addChild(graphics)
      app.stage.addChild(labels)

      appRef.current = app
      graphicsRef.current = graphics
      labelsRef.current = labels

      app.ticker.add(() => {
        if (!onFpsSample) {
          return
        }

        const now = performance.now()
        if (now - fpsSampleAt < 700) {
          return
        }

        fpsSampleAt = now
        onFpsSample(app.ticker.FPS)
      })
    }

    void setup()

    return () => {
      cancelled = true
      appRef.current = null
      graphicsRef.current = null
      labelsRef.current = null
      app.destroy()
    }
  }, [onFpsSample])

  useEffect(() => {
    const app = appRef.current
    const graphics = graphicsRef.current
    const labelLayer = labelsRef.current

    if (!app || !graphics || !labelLayer) {
      return
    }

    graphics.clear()

    const labels = labelLayer.removeChildren()
    for (const label of labels) {
      label.destroy()
    }

    if (!frame) {
      return
    }

    const viewport = fitContain(
      app.screen.width,
      app.screen.height,
      videoWidth,
      videoHeight,
    )

    for (const item of frame.items) {
      drawItem(graphics, labelLayer, item, frame, viewport)
    }
  }, [frame, videoWidth, videoHeight])

  return <div className="overlay-canvas" ref={hostRef} aria-hidden />
}
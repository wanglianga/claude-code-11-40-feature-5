/**
 * Pure geometry helpers for the disclosure map.
 *
 * Kept free of Vue so the projection can be checked directly against real API
 * payloads (see `frontend/scripts/check-map.mjs`): only the geometry the API
 * releases is ever measured or drawn.
 */

export type Ring = { points: Array<[number, number]> }

export type Shape = {
  properties: Record<string, any>
  rings: Ring[]
}

export interface ViewBox {
  x: number
  y: number
  w: number
  h: number
}

export function polygonsOf(geometry: any): number[][][][] {
  if (!geometry || !geometry.type) return []
  if (geometry.type === 'Polygon') return [geometry.coordinates]
  if (geometry.type === 'MultiPolygon') return geometry.coordinates
  return []
}

export function computeBounds(features: Array<{ geometry: any }>) {
  let minX = Infinity
  let minY = Infinity
  let maxX = -Infinity
  let maxY = -Infinity
  for (const feature of features) {
    for (const polygon of polygonsOf(feature.geometry)) {
      for (const ring of polygon) {
        for (const coordinate of ring) {
          const [x, y] = coordinate
          if (x < minX) minX = x
          if (x > maxX) maxX = x
          if (y < minY) minY = y
          if (y > maxY) maxY = y
        }
      }
    }
  }
  if (!Number.isFinite(minX)) return { minX: 0, minY: 0, maxX: 1, maxY: 1 }
  const padX = (maxX - minX) * 0.04 || 0.001
  const padY = (maxY - minY) * 0.04 || 0.001
  return { minX: minX - padX, minY: minY - padY, maxX: maxX + padX, maxY: maxY + padY }
}

export function computeViewBox(
  bounds: { minX: number; minY: number; maxX: number; maxY: number },
  width: number,
  height: number,
): ViewBox {
  const { minX, minY, maxX, maxY } = bounds
  const spanX = maxX - minX
  const spanY = maxY - minY
  const aspect = width / height
  let viewWidth = spanX
  let viewHeight = spanY
  if (spanX / spanY < aspect) viewWidth = spanY * aspect
  else viewHeight = spanX / aspect
  return {
    x: minX - (viewWidth - spanX) / 2,
    y: minY - (viewHeight - spanY) / 2,
    w: viewWidth,
    h: viewHeight,
  }
}

export function toShapes(features: Array<{ geometry: any; properties: Record<string, any> }>): Shape[] {
  return features.map((feature) => ({
    properties: feature.properties,
    rings: polygonsOf(feature.geometry).flatMap((polygon) =>
      polygon.map((ring) => ({
        points: ring.map(([x, y]) => [Number(x), Number(y)] as [number, number]),
      })),
    ),
  }))
}

/** SVG path data in viewBox space; latitude is flipped so north points up. */
export function buildPaths(shapes: Shape[], view: ViewBox, width: number, height: number) {
  const sx = width / view.w
  const sy = height / view.h
  return shapes.map((shape) => ({
    properties: shape.properties,
    d: shape.rings
      .map((ring) =>
        ring.points
          .map(
            ([px, py], index) =>
              `${index === 0 ? 'M' : 'L'}${((px - view.x) * sx).toFixed(2)} ${(
                (view.y + view.h - py) *
                sy
              ).toFixed(2)}`,
          )
          .join(' ') + 'Z',
      )
      .join(' '),
  }))
}

export function colorFor(properties: Record<string, any>): { fill: string; stroke: string } {
  const level = properties.sensitivity_level
  if (level === 'critical') return { fill: '#b64a38', stroke: '#8a2f21' }
  if (level === 'high') return { fill: '#c97c3f', stroke: '#9e5a24' }
  if (level === 'medium') return { fill: '#5f8d6d', stroke: '#3f6f52' }
  return { fill: '#6d8a7f', stroke: '#4c6a5e' }
}

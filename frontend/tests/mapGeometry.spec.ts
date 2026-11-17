import { describe, expect, it } from 'vitest'
import {
  buildPaths,
  colorFor,
  computeBounds,
  computeViewBox,
  polygonsOf,
  toShapes,
} from '../src/mapGeometry'

const square = (x: number, y: number, size: number) => ({
  type: 'Polygon',
  coordinates: [
    [
      [x, y],
      [x + size, y],
      [x + size, y + size],
      [x, y + size],
      [x, y],
    ],
  ],
})

function feature(geometry: any, properties: Record<string, any>) {
  return { type: 'Feature', geometry, properties }
}

describe('map geometry', () => {
  it('reads polygons and multipolygons', () => {
    expect(polygonsOf(square(0, 0, 1))).toHaveLength(1)
    const multi = {
      type: 'MultiPolygon',
      coordinates: [square(0, 0, 1).coordinates, square(2, 2, 1).coordinates],
    }
    const polygons = polygonsOf(multi)
    expect(polygons).toHaveLength(2)
    expect(polygons[0][0][0]).toEqual([0, 0])
    expect(polygonsOf(null)).toEqual([])
  })

  it('computes a padded bounding box and view box', () => {
    const bounds = computeBounds([feature(square(103.6, 31.0, 0.2), {})])
    expect(bounds.minX).toBeLessThan(103.6)
    expect(bounds.maxX).toBeGreaterThan(103.8)
    const view = computeViewBox(bounds, 760, 420)
    expect(view.w / view.h).toBeCloseTo(760 / 420, 6)
    expect(view.x).toBeLessThanOrEqual(bounds.minX)
  })

  it('builds path data that starts with a move command and flips latitude', () => {
    const features = [feature(square(103.6, 31.0, 0.2), { region_code: 'R-1' })]
    const view = computeViewBox(computeBounds(features), 760, 420)
    const paths = buildPaths(toShapes(features), view, 760, 420)
    expect(paths).toHaveLength(1)
    expect(paths[0].d.startsWith('M')).toBe(true)
    expect(paths[0].d.endsWith('Z')).toBe(true)
    // All rendered y values must sit inside the viewport after the flip.
    const ys = [...paths[0].d.matchAll(/[ML][\d.-]+ ([\d.-]+)/g)].map((m) => Number(m[1]))
    expect(Math.min(...ys)).toBeGreaterThanOrEqual(-0.5)
    expect(Math.max(...ys)).toBeLessThanOrEqual(420.5)
  })

  it('keeps only numeric coordinates after normalising API payloads', () => {
    const features = [feature(square(103.6, 31.0, 0.2), {})]
    for (const shape of toShapes(features)) {
      for (const ring of shape.rings) {
        for (const point of ring.points) {
          expect(typeof point[0]).toBe('number')
          expect(typeof point[1]).toBe('number')
        }
      }
    }
  })

  it('colours by sensitivity level and defaults to low', () => {
    expect(colorFor({ sensitivity_level: 'critical' }).fill).toBe('#b64a38')
    expect(colorFor({ sensitivity_level: 'high' }).fill).toBe('#c97c3f')
    expect(colorFor({}).fill).toBe('#6d8a7f')
  })

  it('handles an empty feature list without throwing', () => {
    const view = computeViewBox(computeBounds([]), 760, 420)
    expect(Number.isFinite(view.w)).toBe(true)
    expect(buildPaths([], view, 760, 420)).toEqual([])
  })
})

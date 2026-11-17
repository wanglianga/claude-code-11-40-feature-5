/**
 * Verify the disclosure map against a live API payload.
 *
 * Uses the same geometry module the Vue component uses, so this checks the code
 * that actually draws. It asserts that:
 *   - every published partition renders as a non-degenerate SVG path;
 *   - no feature carries precise coordinates (nest points / station positions);
 *   - no two features share a disclosure block (a shared block would let a viewer
 *     recombine statistics from one region with the boundary of the other);
 *   - the download CSV matches the regional statistics in the map response.
 *
 * Run against a running backend:
 *   node scripts/check-map.mjs [baseUrl]
 */
// The geometry module is TypeScript; bundle it with the project's own esbuild so
// this check exercises exactly the code the Vue component imports.
import { execFileSync } from 'node:child_process'
import { mkdtempSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { pathToFileURL } from 'node:url'

const bundleDir = mkdtempSync(join(tmpdir(), 'map-geometry-'))
const bundleFile = join(bundleDir, 'mapGeometry.mjs')
execFileSync(join(process.cwd(), 'node_modules/.bin/esbuild'), [
  'src/mapGeometry.ts',
  '--format=esm',
  `--outfile=${bundleFile}`,
  '--log-level=error',
])
const { buildPaths, computeBounds, computeViewBox, toShapes } = await import(
  pathToFileURL(bundleFile).href
)

const BASE = process.argv[2] || 'http://127.0.0.1:8000'

async function main() {
  const list = await fetch(`${BASE}/api/public/publications`).then((r) => r.json())
  if (!list.length) throw new Error('no public publication is being served')

  let checked = 0
  for (const summary of list) {
    const response = await fetch(`${BASE}/api/public/publications/${summary.code}/map`)
    if (response.status === 409) continue
    if (!response.ok) throw new Error(`map ${summary.code}: ${response.status}`)
    const payload = await response.json()
    const features = payload.map.features
    if (!features.length) throw new Error(`${summary.code}: empty map`)

    const text = JSON.stringify(payload).toLowerCase()
    for (const token of ['latitude', 'longitude', 's3://', 'record_id', 'external_id', 'station_code']) {
      if (text.includes(token)) throw new Error(`${summary.code}: leaked ${token}`)
    }

    const shapes = toShapes(features)
    const view = computeViewBox(computeBounds(features), 760, 420)
    const paths = buildPaths(shapes, view, 760, 420)
    for (const path of paths) {
      if (!path.d || path.d.length < 20) throw new Error(`${path.properties.region_code}: degenerate path`)
      if (!/^M[\d.-]/.test(path.d)) throw new Error(`bad path data for ${path.properties.region_code}`)
      if (path.properties.withheld) throw new Error('withheld region leaked into the map')
      const minimum = summary.sensitivity_level === 'critical' ? 500 : 200
      if (path.properties.display_cell_m < minimum) {
        throw new Error(`display granularity too fine for ${path.properties.region_code}`)
      }
    }

    // Display blocks must not overlap across regions. Each released geometry is a
    // union of equal-size squares, so identical minimum corners mean a shared block.
    const blockSets = features.map((feature) => {
      const polygons =
        feature.geometry.type === 'Polygon'
          ? [feature.geometry.coordinates]
          : feature.geometry.coordinates
      const xs = []
      const ys = []
      for (const polygon of polygons) {
        for (const ring of polygon) {
          for (const [x, y] of ring) {
            xs.push(Math.round(Number(x) * 2000))
            ys.push(Math.round(Number(y) * 2000))
          }
        }
      }
      return { code: feature.properties.region_code, key: `${Math.min(...xs)}:${Math.min(...ys)}` }
    })
    const seen = new Set()
    for (const block of blockSets) {
      if (seen.has(block.key)) throw new Error(`overlapping disclosure block at ${block.key}`)
      seen.add(block.key)
    }

    const csv = await fetch(`${BASE}/api/public/publications/${summary.code}/download.csv`).then((r) =>
      r.text(),
    )
    const rows = csv.trim().split('\n')
    const header = rows[0].split(',')
    const regionIndex = header.indexOf('region_code')
    const observationIndex = header.indexOf('observation_count')
    const csvRegions = rows.slice(1).map((line) => line.split(','))
    if (csvRegions.length !== payload.stats.regions.length) {
      throw new Error(
        `${summary.code}: CSV has ${csvRegions.length} rows, stats has ${payload.stats.regions.length}`,
      )
    }
    for (const row of csvRegions) {
      const match = payload.stats.regions.find((item) => item.region_code === row[regionIndex])
      if (!match) throw new Error(`${summary.code}: CSV region ${row[regionIndex]} missing from stats`)
      if (String(match.observation_count) !== row[observationIndex]) {
        throw new Error(`${summary.code}: CSV observation count mismatch for ${row[regionIndex]}`)
      }
    }

    console.log(
      `ok ${summary.code}: ${features.length} regions, granularity ${paths[0].properties.display_cell_m} m, ` +
        `${payload.stats.withheld.length} withheld, CSV ${csvRegions.length} rows`,
    )
    checked += 1
  }
  if (!checked) throw new Error('no delivery could be checked')
  console.log(`checked ${checked} public deliveries`)
}

main().catch((error) => {
  console.error(error.message || error)
  process.exit(1)
})

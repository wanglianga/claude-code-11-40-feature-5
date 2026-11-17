import { describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import PublicMapView from '../src/views/PublicMapView.vue'
import type { SpatialMapPayload, SpatialPublication } from '../src/types'

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

const publication: SpatialPublication = {
  id: 1,
  code: 'PUB-TEST',
  species_code: 'LOPHOPHORUS',
  scope_code: 'PUBLIC',
  season_code: '2026-SPRING',
  status: 'published',
  survey_run_id: 1,
  sensitivity_level: 'high',
  sensitivity_version: 1,
  habitat_version: 1,
  suitability_version: 1,
  scope_version: 1,
  region_count: 2,
  published_region_count: 1,
  checks_passed: true,
  stop_reason: '',
  stale_reason: '',
  note: '',
  created_by: 'yq.li',
  created_at: null,
  published_at: null,
}

const payload: SpatialMapPayload = {
  publication: {
    code: 'PUB-TEST',
    species_code: 'LOPHOPHORUS',
    species_name: '绿尾虹雉',
    season_code: '2026-SPRING',
    scope_code: 'PUBLIC',
    scope_name: '公众',
    sensitivity_level: 'high',
    sensitivity_version: 1,
    habitat_version: 1,
    suitability_version: 1,
    published_region_count: 1,
    withheld_region_count: 1,
    unattributed_observations: 3,
    status: 'published',
  },
  map: {
    type: 'FeatureCollection',
    features: [
      {
        type: 'Feature',
        geometry: square(103.6, 31.0, 0.01),
        properties: {
          region_code: 'R-1',
          extent_key: 'EX-1',
          display_cell_m: 500,
          display_area_ha: 25,
          candidate_habitat_ha: 12.5,
          observation_count: 7,
          present_count: 2,
          effective_hours: 48,
          route_length_km: 1.2,
          effort_known: true,
          occurrence_rate: 0.2857,
          sensitivity_level: 'high',
          withheld: false,
        },
      },
    ],
    disclosure: '仅显示合并后的发布分区',
  },
  stats: {
    regions: [
      {
        region_code: 'R-1',
        candidate_habitat_ha: 12.5,
        observation_count: 7,
        present_count: 2,
        effective_hours: 48,
        route_length_km: 1.2,
        effort_known: true,
        occurrence_rate: 0.2857,
        display_cell_m: 500,
      },
    ],
    withheld: [{ region_code: 'R-2', reason_codes: ['REGION_TOO_NARROW'], candidate_habitat_ha: 0.49 }],
  },
}

describe('PublicMapView', () => {
  it('reads only public endpoints, renders the map and links the CSV download', async () => {
    const calls: string[] = []
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: string | URL) => {
        const url = String(input)
        calls.push(url)
        const body = url.endsWith('/public/publications')
          ? [publication]
          : payload
        return {
          ok: true,
          status: 200,
          json: async () => body,
          text: async () => JSON.stringify(body),
        }
      }),
    )

    const wrapper = mount(PublicMapView)
    await flushPromises()
    await flushPromises()

    expect(calls.some((url) => url.includes('/api/public/publications'))).toBe(true)
    expect(calls.every((url) => url.includes('/api/public/'))).toBe(true)
    expect(wrapper.findAll('.spatial-map path')).toHaveLength(1)
    expect(wrapper.text()).toContain('未归属观察 3 条')
    const link = wrapper.get('a[download]')
    expect(link.attributes('href')).toBe('/api/public/publications/PUB-TEST/download.csv')
    expect(wrapper.text()).toContain('REGION_TOO_NARROW')
  })

  it('shows the straddle-unattributed count and withholds geometry', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: string | URL) => {
        const url = String(input)
        const body = url.endsWith('/public/publications') ? [publication] : payload
        return { ok: true, status: 200, json: async () => body, text: async () => JSON.stringify(body) }
      }),
    )
    const wrapper = mount(PublicMapView)
    await flushPromises()
    await flushPromises()
    expect(wrapper.findAll('.withheld-row')).toHaveLength(1)
    expect(wrapper.html()).not.toContain('latitude')
    expect(wrapper.html()).not.toContain('station_code')
  })
})

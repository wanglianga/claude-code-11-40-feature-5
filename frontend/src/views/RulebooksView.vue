<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api } from '../api'
import type { ProbabilityModel, RulebookVersion } from '../types'
import { fmtDateTime, fmtPct } from '../format'

const rulebooks = ref<RulebookVersion[]>([])
const models = ref<ProbabilityModel[]>([])
const selectedRulebook = ref<RulebookVersion | null>(null)
const loading = ref(true)
const error = ref('')

onMounted(async () => {
  try {
    rulebooks.value = await api.get<RulebookVersion[]>('/rulebooks')
    models.value = await api.get<ProbabilityModel[]>('/models')
    selectedRulebook.value =
      rulebooks.value.find((r) => r.status === 'active') ?? rulebooks.value[0] ?? null
  } catch (err) {
    error.value = err instanceof Error ? err.message : '加载失败'
  } finally {
    loading.value = false
  }
})

function requirementRows(req: Record<string, any>): Array<{ label: string; key: string; value: string }> {
  const rows: Array<{ label: string; key: string; value: string }> = []
  const map: Record<string, [string, Record<string, (v: any) => string>]> = {
    device: [
      '设备可用性',
      {
        target_available_hours: (v) => `${v} h`,
        min_available_hours: (v) => `${v} h`,
        target_available_ratio: (v) => fmtPct(v),
        min_available_ratio: (v) => fmtPct(v),
        max_heartbeat_gap_hours: (v) => `${v} h`,
        min_gap_hours: (v) => `${v} h`,
      },
    ],
    time: [
      '时间可信度',
      { tolerance_seconds: (v) => `${v} s`, late_delivery_hours: (v) => `${v} h` },
    ],
    space: [
      '空间覆盖',
      {
        target_route_coverage_ratio: (v) => fmtPct(v),
        min_route_coverage_ratio: (v) => fmtPct(v),
        edge_penetration_m: (v) => `${v} m`,
        min_in_buffer_ratio: (v) => fmtPct(v),
      },
    ],
    reading: [
      '素材判读',
      {
        min_confidence: (v) => v.toFixed(2),
        max_pending_ratio: (v) => fmtPct(v),
        min_available_observations: (v) => `${v} 条`,
      },
    ],
  }
  for (const [section, [label, formatters]] of Object.entries(map)) {
    const block = req[section] ?? {}
    for (const [key, fmt] of Object.entries(formatters)) {
      if (block[key] !== undefined) {
        rows.push({ label, key, value: fmt(block[key]) })
      }
    }
  }
  return rows
}
</script>

<template>
  <div>
    <div class="page-head">
      <div>
        <div class="eyebrow">Rulebook & Model</div>
        <h1>样区规则册与概率模型</h1>
        <p class="page-sub">
          规则册维护站点缓冲、路线段、海拔带、季节窗口与目标物种的最低调查要求；概率模型记录校准集、
          参数与阈值复核成本。草案版本的规则不会用于已签发证书。
        </p>
      </div>
    </div>

    <div v-if="loading" class="loading">加载中…</div>
    <div v-else-if="error" class="callout danger">{{ error }}</div>

    <template v-else>
      <div class="grid grid-2-1">
        <div class="card">
          <div class="card-head">
            <h2>规则册版本</h2>
            <select
              class="select"
              style="width: 260px"
              :value="selectedRulebook?.id"
              @change="
                (e) =>
                  (selectedRulebook = rulebooks.find(
                    (r) => r.id === Number((e.target as HTMLSelectElement).value),
                  ) ?? null)
              "
            >
              <option v-for="r in rulebooks" :key="r.id" :value="r.id">
                {{ r.rulebook_code }} v{{ r.version_number }} · {{ r.label }}
              </option>
            </select>
          </div>

          <template v-if="selectedRulebook">
            <div class="flex wrap" style="margin-bottom: 10px">
              <span class="badge" :class="selectedRulebook.status === 'active' ? 'valid' : 'neutral'">
                {{ selectedRulebook.status === 'active' ? '启用中' : '草案 · 未启用' }}
              </span>
              <span class="chip mono">{{ selectedRulebook.season_start }} → {{ selectedRulebook.season_end }}</span>
              <span class="chip">{{ selectedRulebook.season_code }}</span>
            </div>

            <div class="tiny muted" style="margin-bottom: 6px">目标物种</div>
            <div class="pill-row" style="margin-bottom: 12px">
              <span v-for="s in selectedRulebook.target_species" :key="s.code" class="chip">
                {{ s.name }} · 最低 {{ s.min_detections }} 次
              </span>
            </div>

            <div class="table-wrap">
              <table>
                <thead>
                  <tr><th>门槛</th><th>要求项</th><th>阈值</th></tr>
                </thead>
                <tbody>
                  <tr v-for="row in requirementRows(selectedRulebook.requirements)" :key="row.label + row.key">
                    <td>{{ row.label }}</td>
                    <td class="mono tiny">{{ row.key }}</td>
                    <td class="num">{{ row.value }}</td>
                  </tr>
                </tbody>
              </table>
            </div>

            <div class="divider"></div>
            <div class="tiny muted" style="margin-bottom: 6px">路线段（{{ selectedRulebook.route_segments.length }}）</div>
            <div class="stack-sm">
              <div v-for="seg in selectedRulebook.route_segments" :key="seg.code" class="seg-card">
                <div class="between">
                  <span class="mono tiny">{{ seg.code }}</span>
                  <span class="chip tiny">{{ seg.elevation_band_m?.[0] }}–{{ seg.elevation_band_m?.[1] }} m</span>
                </div>
                <div class="small">{{ seg.name }}</div>
                <div class="tiny muted">
                  适用站点：{{ seg.station_codes?.join('、') || '全部' }}
                  <template v-if="seg.valid_from"> · 自 {{ seg.valid_from }}</template>
                  <template v-if="seg.valid_to"> · 至 {{ seg.valid_to }}</template>
                </div>
              </div>
            </div>

            <div class="divider"></div>
            <div class="tiny muted" style="margin-bottom: 6px">海拔带</div>
            <div class="pill-row">
              <span v-for="band in selectedRulebook.elevation_bands" :key="band.code" class="chip">
                {{ band.name }} {{ band.min_m }}–{{ band.max_m }} m
              </span>
            </div>
            <div v-if="selectedRulebook.notes" class="callout info" style="margin-top: 12px">
              {{ selectedRulebook.notes }}
            </div>
          </template>
        </div>

        <div class="stack">
          <div class="card">
            <div class="card-head"><h2>概率模型</h2></div>
            <div v-for="model in models" :key="model.id" class="model-card">
              <div class="between">
                <span class="mono small">{{ model.code }} v{{ model.version }}</span>
                <span class="badge" :class="model.status === 'active' ? 'valid' : 'pending'">
                  {{ model.status === 'active' ? '启用' : model.status }}
                </span>
              </div>
              <div class="small" style="margin-top: 2px">{{ model.name }}</div>
              <div class="tiny muted">
                阈值 {{ model.threshold.toFixed(2) }} · 已产生 {{ model.estimate_count ?? 0 }} 条估计
              </div>
              <div class="divider" style="margin: 8px 0"></div>
              <div class="tiny muted" style="margin-bottom: 4px">校准集</div>
              <div class="pill-row">
                <span v-for="cal in model.calibration_sets" :key="cal.label" class="chip">
                  {{ cal.label }}
                </span>
              </div>
              <div class="tiny muted" style="margin-top: 8px">
                阈值复核：{{ model.threshold_review?.cadence }} · 上次 {{ model.threshold_review?.last_review }}
                · 下次 {{ model.threshold_review?.next_review }}
              </div>
              <div class="callout warn" style="margin-top: 8px">{{ model.notes }}</div>
            </div>
          </div>

          <div class="card">
            <div class="card-head"><h2>模型特征权重</h2></div>
            <div v-for="model in models" :key="'w-' + model.id">
              <div class="tiny muted" style="margin-bottom: 6px">pivot {{ model.parameters.pivot }} · slope {{ model.parameters.slope }}</div>
              <div
                v-for="(weight, name) in model.parameters.weights"
                :key="name"
                class="weight-row"
              >
                <span class="mono tiny">{{ name }}</span>
                <div class="weight-bar"><div :style="{ width: Math.min(100, Number(weight) * 500) + '%' }"></div></div>
                <span class="mono tiny">{{ Number(weight).toFixed(2) }}</span>
              </div>
              <div class="tiny muted" style="margin-top: 8px">
                门控特征：{{ model.parameters.gating_features?.join('、') }}（不满足则覆盖值为 0）
              </div>
            </div>
          </div>
        </div>
      </div>
    </template>
  </div>
</template>

<style scoped>
.seg-card { border: 1px solid var(--line); border-radius: 8px; padding: 8px 10px; }
.model-card { border: 1px solid var(--line); border-radius: 9px; padding: 10px 12px; margin-bottom: 10px; }
.weight-row { display: grid; grid-template-columns: 150px 1fr 40px; gap: 8px; align-items: center; padding: 2px 0; }
.weight-bar { height: 6px; background: var(--surface-2); border-radius: 3px; overflow: hidden; }
.weight-bar div { height: 100%; background: var(--pine-mid); }
</style>

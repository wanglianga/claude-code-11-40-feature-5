<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api'
import type { Overview } from '../types'
import StatusBadge from '../components/StatusBadge.vue'
import {
  actionLabel,
  fmtDate,
  fmtDateTime,
  fmtPct,
  GATE_LABEL,
  shortDate,
  statusClass,
  statusLabel,
} from '../format'

const router = useRouter()
const data = ref<Overview | null>(null)
const loading = ref(true)
const error = ref('')
const selectedStation = ref<number | null>(null)
const gateFilter = ref<string>('')

const statusOrder = ['valid', 'restricted', 'invalid', 'pending']

async function load() {
  loading.value = true
  error.value = ''
  try {
    const runs = await api.get<Array<{ id: number; run_code: string; status: string }>>('/runs')
    const active = runs.find((r) => r.run_code === 'RUN-2026-SPRING-01') ?? runs[0]
    if (!active) throw new Error('尚无调查运行')
    data.value = await api.get<Overview>(`/runs/${active.id}/overview`)
  } catch (err) {
    error.value = err instanceof Error ? err.message : '加载失败'
  } finally {
    loading.value = false
  }
}

onMounted(load)

const matrixStations = computed(() => {
  if (!data.value) return []
  const rows = data.value.stations
  return selectedStation.value ? rows.filter((s) => s.id === selectedStation.value) : rows
})

const dates = computed(() => data.value?.dates ?? [])

const statusTotal = computed(() => {
  if (!data.value) return 0
  return statusOrder.reduce((sum, key) => sum + (data.value!.status_counts[key] ?? 0), 0)
})

const gateSummary = computed(() => {
  if (!data.value) return []
  return (['device', 'time', 'space', 'reading'] as const).map((gate) => {
    const counts = data.value!.gate_counts[gate] ?? {}
    return {
      gate,
      label: GATE_LABEL[gate],
      pass: counts.pass ?? 0,
      restricted: counts.restricted ?? 0,
      pending: counts.pending ?? 0,
      fail: counts.fail ?? 0,
    }
  })
})

const attentionUnits = computed(() => {
  if (!data.value) return []
  const rows: Array<{
    unit_id: number
    station_code: string
    station_name: string
    date: string
    status: string
    review: string
    reason: string
    gate: string
  }> = []
  for (const station of data.value.stations) {
    for (const unit of station.units) {
      const flags = Object.entries(unit.gate_status || {}).filter(
        ([, value]) => value === 'pending' || value === 'fail',
      )
      if (!flags.length && unit.review_status !== 'under_review') continue
      if (gateFilter.value && !flags.some(([gate]) => gate === gateFilter.value)) continue
      rows.push({
        unit_id: unit.unit_id,
        station_code: station.code,
        station_name: station.name,
        date: unit.date,
        status: unit.cert_status,
        review: unit.review_status,
        reason: unit.open_conflicts ? `${unit.open_conflicts} 项判读冲突未决` : '',
        gate: flags.map(([gate]) => GATE_LABEL[gate]).join('、') || '复审中',
      })
    }
  }
  return rows
})

function cellClass(unit: Record<string, any>): string {
  if (unit.review_status === 'under_review') return 'review'
  return unit.cert_status || 'pending'
}

function openUnit(unitId: number) {
  router.push({ name: 'unit', params: { id: unitId } })
}
</script>

<template>
  <div>
    <div class="page-head">
      <div>
        <div class="eyebrow">Season Overview</div>
        <h1>站点覆盖总览</h1>
        <p class="page-sub">
          每个调查单元对应一个站点—样区—日期。色块表示当前证书状态；点击进入单元查看门槛证据、
          规则证书与概率估计。证书状态不产生任何派工、预约或执法动作。
        </p>
      </div>
      <div class="toolbar" v-if="data">
        <span class="chip">{{ data.run.run_code }}</span>
        <span class="chip mono">{{ data.run.season_start }} → {{ data.run.season_end }}</span>
        <button class="btn sm" @click="load">刷新</button>
      </div>
    </div>

    <div v-if="loading" class="loading">正在组装覆盖视图…</div>
    <div v-else-if="error" class="callout danger">{{ error }}</div>

    <template v-else-if="data">
      <div class="grid grid-4" style="margin-bottom: 14px">
        <div class="card tight">
          <div class="stat">
            <div class="stat-value">{{ data.unit_count }}</div>
            <div class="stat-label">调查单元（{{ data.station_count }} 站点 × {{ data.date_count }} 调查日）</div>
          </div>
        </div>
        <div class="card tight">
          <div class="stat">
            <div class="stat-value" style="color: var(--pine)">{{ data.status_counts.valid ?? 0 }}</div>
            <div class="stat-label">有效调查</div>
            <div class="stat-hint">可支持正式未发现结论</div>
          </div>
        </div>
        <div class="card tight">
          <div class="stat">
            <div class="stat-value" style="color: var(--amber)">
              {{ (data.status_counts.restricted ?? 0) + (data.status_counts.invalid ?? 0) }}
            </div>
            <div class="stat-label">受限 / 未有效</div>
            <div class="stat-hint">不简化为物种未出现</div>
          </div>
        </div>
        <div class="card tight">
          <div class="stat">
            <div class="stat-value" style="color: var(--slate)">
              {{ (data.status_counts.pending ?? 0) }}
            </div>
            <div class="stat-label">待核 · {{ data.open_conflicts }} 项冲突未决</div>
            <div class="stat-hint">{{ data.active_trend_references }} 条趋势引用</div>
          </div>
        </div>
      </div>

      <div class="grid grid-2-1" style="margin-bottom: 14px">
        <div class="card">
          <div class="card-head">
            <h2>站点 × 调查日 覆盖矩阵</h2>
            <div class="toolbar">
              <select v-model="selectedStation" class="select" style="width: 180px">
                <option :value="null">全部站点</option>
                <option v-for="s in data.stations" :key="s.id" :value="s.id">
                  {{ s.code }} · {{ s.name }}
                </option>
              </select>
            </div>
          </div>

          <div
            class="matrix"
            :style="{
              gridTemplateColumns: `150px repeat(${dates.length}, minmax(0, 1fr))`,
            }"
          >
            <div></div>
            <div v-for="d in dates" :key="d" class="matrix-head">{{ shortDate(d) }}</div>
            <template v-for="station in matrixStations" :key="station.id">
              <div class="matrix-label">
                <div>{{ station.code }}</div>
                <div class="tiny muted">{{ station.zone?.name }}</div>
              </div>
              <div
                v-for="d in dates"
                :key="station.id + d"
                class="matrix-cell"
                :class="[
                  cellClass(station.units.find((u) => u.date === d) || { cert_status: 'pending' }),
                ]"
                :title="`${station.code} ${d}`"
                @click="
                  (() => {
                    const unit = station.units.find((u) => u.date === d)
                    if (unit) openUnit(unit.unit_id)
                  })()
                "
              >
                {{
                  (() => {
                    const unit = station.units.find((u) => u.date === d)
                    if (!unit) return '—'
                    if (unit.review_status === 'under_review') return '复审'
                    return (
                      { valid: '有效', restricted: '受限', invalid: '无效', pending: '待核' }[
                        unit.cert_status as string
                      ] ?? '待核'
                    )
                  })()
                }}
              </div>
            </template>
          </div>

          <div class="legend" style="margin-top: 12px">
            <span><i class="swatch" style="background: #cfe3d5"></i>有效调查</span>
            <span><i class="swatch" style="background: #f2ddb9"></i>受限调查</span>
            <span><i class="swatch" style="background: #ecc9c1"></i>未有效调查</span>
            <span><i class="swatch" style="background: #d9e0ec"></i>待核</span>
            <span><i class="swatch" style="background: #ded0ec"></i>复审中</span>
          </div>
        </div>

        <div class="card">
          <div class="card-head"><h2>门槛分布</h2></div>
          <div class="stack">
            <div v-for="row in gateSummary" :key="row.gate" class="stack-sm">
              <div class="between">
                <span style="font-size: 12.5px; font-weight: 500">{{ row.label }}</span>
                <span class="tiny muted">
                  通过 {{ row.pass }} · 受限 {{ row.restricted }} · 待核 {{ row.pending }} · 未满足 {{ row.fail }}
                </span>
              </div>
              <div class="bar">
                <span
                  v-for="(seg, idx) in [
                    { n: row.pass, c: '#a9c9b5' },
                    { n: row.restricted, c: '#dfc190' },
                    { n: row.pending, c: '#bfcadd' },
                    { n: row.fail, c: '#dcaa9f' },
                  ]"
                  :key="idx"
                  :style="{
                    width: (seg.n / Math.max(1, data.unit_count)) * 100 + '%',
                    background: seg.c,
                  }"
                ></span>
              </div>
            </div>

            <div class="divider"></div>
            <div class="stack-sm">
              <div class="between">
                <span class="tiny muted">证书状态合计</span>
                <span class="tiny mono">{{ statusTotal }} 单元</span>
              </div>
              <div class="pill-row">
                <StatusBadge
                  v-for="key in statusOrder"
                  :key="key"
                  :status="key"
                  :label="`${statusLabel(key)} ${data.status_counts[key] ?? 0}`"
                />
              </div>
            </div>
            <div v-if="data.model" class="callout info" style="margin-top: 4px">
              <div class="callout-title">概率模型 {{ data.model.code }} v{{ data.model.version }}</div>
              阈值 {{ data.model.threshold.toFixed(2) }}；仅用于趋势参考，正式未发现结论只使用规则证书。
            </div>
          </div>
        </div>
      </div>

      <div class="grid grid-2-1">
        <div class="card">
          <div class="card-head">
            <h2>需关注的调查单元</h2>
            <select v-model="gateFilter" class="select" style="width: 150px">
              <option value="">全部门槛</option>
              <option v-for="(label, key) in GATE_LABEL" :key="key" :value="key">{{ label }}</option>
            </select>
          </div>
          <div v-if="!attentionUnits.length" class="empty">当前筛选下没有待核或未满足的单元。</div>
          <div v-else class="table-wrap" style="max-height: 340px; overflow-y: auto">
            <table>
              <thead>
                <tr>
                  <th>站点</th>
                  <th>调查日</th>
                  <th>证书</th>
                  <th>触发门槛</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="row in attentionUnits" :key="row.unit_id">
                  <td>
                    <div>{{ row.station_code }}</div>
                    <div class="tiny muted">{{ row.station_name }}</div>
                  </td>
                  <td class="num nowrap">{{ fmtDate(row.date) }}</td>
                  <td>
                    <StatusBadge
                      :status="row.status"
                      :review-status="row.review"
                      :label="statusLabel(row.status, row.review)"
                    />
                  </td>
                  <td>
                    <span class="small">{{ row.gate }}</span>
                    <div v-if="row.reason" class="tiny muted">{{ row.reason }}</div>
                  </td>
                  <td class="right">
                    <button class="btn ghost sm" @click="openUnit(row.unit_id)">查看</button>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>

        <div class="card">
          <div class="card-head"><h2>最近数据活动</h2></div>
          <div class="timeline">
            <div v-for="event in data.recent_activity.slice(0, 8)" :key="event.id" class="tl-item">
              <div class="tl-dot"></div>
              <div class="tl-body">
                <div>{{ actionLabel(event.action) }}</div>
                <div class="tl-meta">
                  {{ event.actor }} · {{ fmtDateTime(event.created_at) }}
                </div>
              </div>
            </div>
          </div>
          <div class="divider"></div>
          <div class="kv">
            <dt>规则册</dt>
            <dd>{{ data.rulebook?.rulebook_code }} v{{ data.rulebook?.version_number }} · {{ data.rulebook?.label }}</dd>
            <dt>目标物种</dt>
            <dd>{{ data.rulebook?.target_species.map((s) => s.name).join('、') }}</dd>
            <dt>运行状态</dt>
            <dd>{{ data.run.status === 'active' ? '当前运行' : '已被替代' }} · {{ fmtDateTime(data.run.created_at) }}</dd>
          </div>
        </div>
      </div>
    </template>
  </div>
</template>

<style scoped>
.bar {
  display: flex;
  height: 7px;
  border-radius: 4px;
  overflow: hidden;
  background: var(--surface-2);
}
.bar span {
  display: block;
  height: 100%;
}
</style>

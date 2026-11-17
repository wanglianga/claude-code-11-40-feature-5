<script setup lang="ts">
/**
 * Cross-season comparison on shared extents only.
 *
 * Two seasons are compared region-by-region only where their retained base units
 * match (a shared extent_key). Areas without a shared extent are shown separately:
 * two regions of different area are never differenced as if they were one trend.
 */
import { computed, onMounted, ref } from 'vue'
import { api, getToken } from '../api'
import type { SpatialComparison, SpatialPublication } from '../types'

const publications = ref<SpatialPublication[]>([])
const firstId = ref<number | null>(null)
const secondId = ref<number | null>(null)
const comparison = ref<SpatialComparison | null>(null)
const loading = ref(true)
const error = ref('')

async function loadList() {
  loading.value = true
  try {
    publications.value = await api.get<SpatialPublication[]>('/spatial/publications')
    const comparable = publications.value.filter((row) =>
      ['published', 'superseded'].includes(row.status),
    )
    const spring = comparable.find((row) => row.season_code === '2026-SPRING')
    const autumn = comparable.find((row) => row.season_code === '2026-AUTUMN')
    firstId.value = spring?.id ?? comparable[0]?.id ?? null
    secondId.value = autumn?.id ?? comparable[1]?.id ?? null
  } catch (err) {
    error.value = err instanceof Error ? err.message : '加载失败'
  } finally {
    loading.value = false
  }
}

async function compare() {
  if (!firstId.value || !secondId.value) return
  error.value = ''
  comparison.value = null
  try {
    comparison.value = await api.get<SpatialComparison>(
      `/spatial/compare?first=${firstId.value}&second=${secondId.value}`,
    )
  } catch (err) {
    error.value = err instanceof Error ? err.message : '比较失败'
  }
}

onMounted(async () => {
  await loadList()
  if (firstId.value && secondId.value) await compare()
})

const comparable = computed(() => comparison.value?.comparable_rows ?? [])
const onlyFirst = computed(() => comparison.value?.only_first ?? [])
const onlySecond = computed(() => comparison.value?.only_second ?? [])

function pct(value: number | null | undefined): string {
  if (value === null || value === undefined) return '—'
  return `${(value * 100).toFixed(1)}%`
}

function sign(value: number): string {
  return value > 0 ? `+${value}` : `${value}`
}

const loggedIn = computed(() => Boolean(getToken()))
</script>

<template>
  <div>
    <div class="page-head">
      <div>
        <div class="eyebrow">Shared Extent Comparison</div>
        <h1>共同范围比较</h1>
        <p class="page-sub">
          只对两期共用同一批保留基础单元（相同 extent_key）的发布分区计算涨跌；无法建立共同范围的区域分开列示，
          不把两个面积不同的区域直接计算涨跌。
        </p>
      </div>
    </div>

    <div v-if="!loggedIn" class="callout warn">
      比较属于内部分析视图，请先 <router-link to="/login">登录</router-link>。
    </div>
    <div v-else-if="loading" class="loading">正在读取发布记录…</div>
    <div v-else-if="error" class="callout danger">{{ error }}</div>

    <template v-else>
      <div class="card" style="margin-bottom: 14px">
        <div class="flex wrap">
          <div class="field">
            <label>第一期</label>
            <select v-model="firstId" class="select" style="width: 320px">
              <option v-for="row in publications" :key="row.id" :value="row.id" :disabled="row.id === secondId">
                {{ row.season_code }} · {{ row.code }} · {{ row.status }}
              </option>
            </select>
          </div>
          <div class="field">
            <label>第二期</label>
            <select v-model="secondId" class="select" style="width: 320px">
              <option v-for="row in publications" :key="row.id" :value="row.id" :disabled="row.id === firstId">
                {{ row.season_code }} · {{ row.code }} · {{ row.status }}
              </option>
            </select>
          </div>
          <button class="btn primary" style="align-self: flex-end" @click="compare">比较</button>
        </div>
        <p v-if="comparison" class="tiny muted" style="margin-top: 10px">{{ comparison.rule }}</p>
      </div>

      <div v-if="comparison">
        <div class="grid grid-3" style="margin-bottom: 14px">
          <div class="card tight">
            <div class="stat">
              <div class="stat-value" style="color: var(--pine)">{{ comparison.shared_extent_count }}</div>
              <div class="stat-label">可比较的共同范围</div>
              <div class="stat-hint">{{ comparison.first.season_code }} ↔ {{ comparison.second.season_code }}</div>
            </div>
          </div>
          <div class="card tight">
            <div class="stat">
              <div class="stat-value" style="color: var(--amber)">{{ onlyFirst.length + onlySecond.length }}</div>
              <div class="stat-label">无共同范围、分开展示</div>
              <div class="stat-hint">不直接计算涨跌</div>
            </div>
          </div>
          <div class="card tight">
            <div class="stat">
              <div class="stat-value">{{ comparison.first.display_cell_m ?? '—' }} / {{ comparison.second.display_cell_m ?? '—' }} m</div>
              <div class="stat-label">两期公开粒度</div>
              <div class="stat-hint">敏感级别变化会改变粒度</div>
            </div>
          </div>
        </div>

        <div class="card" style="margin-bottom: 14px">
          <div class="card-head">
            <h2>共同范围逐块比较</h2>
            <span class="card-note">仅共同 extent_key 计算差值</span>
          </div>
          <div class="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>extent_key</th><th>分区（前 / 后）</th>
                  <th class="num">生境 (ha)</th>
                  <th class="num">观察（前 → 后）</th>
                  <th class="num">出现（前 → 后）</th>
                  <th class="num">有效时长 (h)</th>
                  <th class="num">出现率（前 → 后）</th>
                  <th class="num">观察变化</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="row in comparable" :key="row.extent_key">
                  <td class="mono">{{ row.extent_key }}</td>
                  <td class="mono tiny">{{ row.region_code_first }} / {{ row.region_code_second }}</td>
                  <td class="num">{{ row.candidate_habitat_ha[0].toFixed(2) }}</td>
                  <td class="num">{{ row.observation_count[0] }} → {{ row.observation_count[1] }}</td>
                  <td class="num">{{ row.present_count[0] }} → {{ row.present_count[1] }}</td>
                  <td class="num">{{ row.effective_hours[0].toFixed(1) }} / {{ row.effective_hours[1].toFixed(1) }}</td>
                  <td class="num">{{ pct(row.occurrence_rate[0]) }} → {{ pct(row.occurrence_rate[1]) }}</td>
                  <td class="num" :style="{ color: row.delta_observations >= 0 ? 'var(--pine)' : 'var(--rust)' }">
                    {{ sign(row.delta_observations) }}
                  </td>
                </tr>
                <tr v-if="!comparable.length">
                  <td colspan="8" class="empty">两期没有共同范围，不能直接计算涨跌</td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>

        <div class="grid grid-2">
          <div class="card">
            <div class="card-head"><h2>仅第一期存在</h2></div>
            <div class="table-wrap">
              <table>
                <thead><tr><th>分区</th><th class="num">生境 (ha)</th><th class="num">观察</th><th class="num">时长 (h)</th></tr></thead>
                <tbody>
                  <tr v-for="row in onlyFirst" :key="row.extent_key">
                    <td class="mono">{{ row.region_code }}<div class="tiny muted">{{ row.extent_key }}</div></td>
                    <td class="num">{{ row.candidate_habitat_ha.toFixed(2) }}</td>
                    <td class="num">{{ row.observation_count }}</td>
                    <td class="num">{{ row.effective_hours.toFixed(1) }}</td>
                  </tr>
                  <tr v-if="!onlyFirst.length"><td colspan="4" class="empty">无</td></tr>
                </tbody>
              </table>
            </div>
          </div>
          <div class="card">
            <div class="card-head"><h2>仅第二期存在</h2></div>
            <div class="table-wrap">
              <table>
                <thead><tr><th>分区</th><th class="num">生境 (ha)</th><th class="num">观察</th><th class="num">时长 (h)</th></tr></thead>
                <tbody>
                  <tr v-for="row in onlySecond" :key="row.extent_key">
                    <td class="mono">{{ row.region_code }}<div class="tiny muted">{{ row.extent_key }}</div></td>
                    <td class="num">{{ row.candidate_habitat_ha.toFixed(2) }}</td>
                    <td class="num">{{ row.observation_count }}</td>
                    <td class="num">{{ row.effective_hours.toFixed(1) }}</td>
                  </tr>
                  <tr v-if="!onlySecond.length"><td colspan="4" class="empty">无</td></tr>
                </tbody>
              </table>
            </div>
          </div>
        </div>
      </div>
    </template>
  </div>
</template>

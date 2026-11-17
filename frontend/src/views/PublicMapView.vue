<script setup lang="ts">
/**
 * Public spatial delivery view.
 *
 * Reached without a login: it can only read /api/public/* endpoints, which expose
 * merged disclosure partitions, regional statistics and a download — never precise
 * coordinates, raw tracks or station identifiers. This page is the "拿到地图和数据
 * 文件的接收方" view; it is deliberately separate from the internal verification view.
 */
import { computed, onMounted, ref, watch } from 'vue'
import { api } from '../api'
import SpatialMap from '../components/SpatialMap.vue'
import type { SpatialMapPayload, SpatialPublication } from '../types'

const publications = ref<SpatialPublication[]>([])
const selectedCode = ref<string>('')
const payload = ref<SpatialMapPayload | null>(null)
const selectedRegion = ref<string | null>(null)
const loading = ref(true)
const error = ref('')
const downloadUrl = ref('')

async function loadList() {
  loading.value = true
  error.value = ''
  try {
    publications.value = await api.get<SpatialPublication[]>('/public/publications')
    if (!selectedCode.value && publications.value.length) {
      selectedCode.value = publications.value[0].code
    }
  } catch (err) {
    error.value = err instanceof Error ? err.message : '加载失败'
  } finally {
    loading.value = false
  }
}

async function loadPayload() {
  if (!selectedCode.value) return
  error.value = ''
  payload.value = null
  selectedRegion.value = null
  try {
    payload.value = await api.get<SpatialMapPayload>(
      `/public/publications/${selectedCode.value}/map`,
    )
    downloadUrl.value = `/api/public/publications/${selectedCode.value}/download.csv`
  } catch (err) {
    error.value = err instanceof Error ? err.message : '该发布当前不可用'
  }
}

onMounted(loadList)
watch(selectedCode, loadPayload)

const currentPublication = computed(
  () => publications.value.find((row) => row.code === selectedCode.value) || null,
)

const regionRows = computed(() => payload.value?.stats.regions ?? [])

const activeRow = computed(() =>
  selectedRegion.value
    ? regionRows.value.find((row) => row.region_code === selectedRegion.value) ?? null
    : null,
)

const totals = computed(() => {
  const rows = regionRows.value
  return {
    regions: rows.length,
    habitatHa: rows.reduce((sum, row) => sum + (row.candidate_habitat_ha ?? 0), 0),
    observations: rows.reduce((sum, row) => sum + (row.observation_count ?? 0), 0),
    hours: rows.reduce((sum, row) => sum + (row.effective_hours ?? 0), 0),
    routeKm: rows.reduce((sum, row) => sum + (row.route_length_km ?? 0), 0),
  }
})

function fmt(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined) return '—'
  return value.toFixed(digits)
}
</script>

<template>
  <div v-if="loading" class="loading">正在读取公开交付…</div>

  <div v-else class="public-shell">
    <header class="public-head">
      <div>
        <div class="eyebrow">Public Survey Data</div>
        <h1>年度物种调查 · 公开分布数据</h1>
        <p class="page-sub">
          面向合作研究机构与公众的分布概览。地图只显示合并后的发布分区，统计只按分区汇总；
          内部核查入口另行登录，公开查询无法用细粒度条件缩小到发布边界以内。
        </p>
      </div>
      <div class="toolbar">
        <a class="btn" href="#/login">内部核查登录</a>
      </div>
    </header>

    <div class="callout info" style="margin-bottom: 14px">
      本服务只交付调查数据，不生成巡护路线、捕捉建议或保护行动；区域边界由公开生境与适生条件计算，
      不代表巢点位置。撤回链接不会被当作旧信息已经消除。
    </div>

    <div v-if="error" class="callout danger">{{ error }}</div>

    <template v-else>
      <div class="card" style="margin-bottom: 14px">
        <div class="between wrap">
          <div class="flex wrap">
            <label class="small muted">发布批次</label>
            <select v-model="selectedCode" class="select" style="width: 340px">
              <option v-for="row in publications" :key="row.code" :value="row.code">
                {{ row.season_code }} · {{ row.species_code }} · {{ row.scope_code }} · 敏感级别
                {{ row.sensitivity_level }}
              </option>
            </select>
          </div>
          <div class="toolbar" v-if="payload">
            <a class="btn primary sm" :href="downloadUrl" download>下载调查数据 CSV</a>
          </div>
        </div>
        <div v-if="payload" class="flex wrap" style="margin-top: 10px">
          <span class="chip">发布 {{ payload.publication.code }}</span>
          <span class="chip">物种 {{ payload.publication.species_name }}</span>
          <span class="chip">敏感级别 {{ payload.publication.sensitivity_level }}</span>
          <span class="chip">生境 v{{ payload.publication.habitat_version }}</span>
          <span class="chip">适生条件 v{{ payload.publication.suitability_version }}</span>
          <span class="chip">未归属观察 {{ payload.publication.unattributed_observations }} 条</span>
        </div>
      </div>

      <div class="grid grid-4" style="margin-bottom: 14px" v-if="payload">
        <div class="card tight">
          <div class="stat">
            <div class="stat-value">{{ totals.regions }}</div>
            <div class="stat-label">发布分区</div>
            <div class="stat-hint">{{ payload.publication.withheld_region_count }} 个区域未公开</div>
          </div>
        </div>
        <div class="card tight">
          <div class="stat">
            <div class="stat-value">{{ fmt(totals.habitatHa) }}</div>
            <div class="stat-label">可用生境（ha）</div>
            <div class="stat-hint">按公开生境与适生条件计算</div>
          </div>
        </div>
        <div class="card tight">
          <div class="stat">
            <div class="stat-value">{{ totals.observations }}</div>
            <div class="stat-label">归入观察</div>
            <div class="stat-hint">跨分区定位误差不计入</div>
          </div>
        </div>
        <div class="card tight">
          <div class="stat">
            <div class="stat-value">{{ fmt(totals.hours) }}</div>
            <div class="stat-label">有效调查时长（h）</div>
            <div class="stat-hint">重叠运行区间已合并</div>
          </div>
        </div>
      </div>

      <div class="grid grid-2-1" v-if="payload">
        <div class="card">
          <div class="card-head">
            <h2>发布分区地图</h2>
            <span class="card-note">颜色深浅表示敏感级别；点击分区查看统计</span>
          </div>
          <SpatialMap
            :features="payload.map.features"
            :selected="selectedRegion"
            :height="430"
            @select="(code) => (selectedRegion = code)"
          />
          <p class="tiny muted" style="margin-top: 8px">{{ payload.map.disclosure }}</p>
        </div>

        <div class="card">
          <div class="card-head">
            <h2>{{ activeRow ? activeRow.region_code : '分区统计' }}</h2>
            <span v-if="!activeRow" class="card-note">点击左侧分区</span>
          </div>

          <div v-if="activeRow" class="stack-sm">
            <div class="metric-row"><span>可用生境</span><strong>{{ fmt(activeRow.candidate_habitat_ha, 2) }} ha</strong></div>
            <div class="metric-row"><span>发布粒度</span><strong>{{ fmt(activeRow.display_cell_m, 0) }} m</strong></div>
            <div class="metric-row"><span>观察次数</span><strong>{{ activeRow.observation_count }}</strong></div>
            <div class="metric-row"><span>目标物种出现</span><strong>{{ activeRow.present_count }}</strong></div>
            <div class="metric-row"><span>调查时长</span><strong>{{ fmt(activeRow.effective_hours, 1) }} h</strong></div>
            <div class="metric-row"><span>路线覆盖</span><strong>{{ fmt(activeRow.route_length_km, 2) }} km</strong></div>
            <div class="metric-row">
              <span>出现率（出现 / 归入观察）</span>
              <strong>{{ activeRow.occurrence_rate === null ? '—' : (activeRow.occurrence_rate * 100).toFixed(1) + '%' }}</strong>
            </div>
            <div class="metric-row">
              <span>调查强度已知</span>
              <strong>{{ activeRow.effort_known ? '是' : '否（不发布为未出现）' }}</strong>
            </div>
          </div>
          <div v-else class="empty">选择一个分区查看汇总统计</div>

          <div v-if="payload.stats.withheld.length" style="margin-top: 14px">
            <div class="tiny muted" style="margin-bottom: 6px">未公开区域（仅计数，不显示边界）</div>
            <div v-for="row in payload.stats.withheld" :key="row.region_code" class="withheld-row">
              <span class="mono">{{ row.region_code }}</span>
              <span class="tiny">{{ row.reason_codes.join(' · ') }}</span>
              <span class="tiny muted">{{ fmt(row.candidate_habitat_ha, 2) }} ha</span>
            </div>
          </div>
        </div>
      </div>

      <div class="card" style="margin-top: 14px" v-if="payload">
        <div class="card-head">
          <h2>分区调查统计</h2>
          <span class="card-note">同一批数据即下载文件内容</span>
        </div>
        <div class="table-wrap">
          <table>
            <thead>
              <tr>
                <th>发布分区</th>
                <th class="num">粒度 (m)</th>
                <th class="num">可用生境 (ha)</th>
                <th class="num">观察</th>
                <th class="num">出现</th>
                <th class="num">有效时长 (h)</th>
                <th class="num">路线 (km)</th>
                <th class="num">强度已知</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="row in regionRows" :key="row.region_code">
                <td class="mono">{{ row.region_code }}</td>
                <td class="num">{{ fmt(row.display_cell_m, 0) }}</td>
                <td class="num">{{ fmt(row.candidate_habitat_ha, 2) }}</td>
                <td class="num">{{ row.observation_count }}</td>
                <td class="num">{{ row.present_count }}</td>
                <td class="num">{{ fmt(row.effective_hours, 1) }}</td>
                <td class="num">{{ fmt(row.route_length_km, 2) }}</td>
                <td class="num">{{ row.effort_known ? '是' : '否' }}</td>
              </tr>
              <tr v-if="!regionRows.length">
                <td colspan="8" class="empty">该批次当前没有可公开分区</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      <div v-if="!payload && !error" class="empty">尚无公开交付</div>
    </template>
  </div>
</template>

<style scoped>
.public-shell {
  max-width: 1320px;
  margin: 0 auto;
  padding: 26px 30px 60px;
}
.public-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-end;
  gap: 18px;
  margin-bottom: 16px;
}
.metric-row {
  display: flex;
  justify-content: space-between;
  gap: 10px;
  font-size: 12.5px;
  padding: 5px 0;
  border-bottom: 1px dashed var(--line);
}
.metric-row:last-child {
  border-bottom: none;
}
.withheld-row {
  display: grid;
  grid-template-columns: 1fr 1.4fr auto;
  gap: 8px;
  font-size: 11.5px;
  padding: 4px 0;
  border-bottom: 1px dashed var(--line);
}
</style>

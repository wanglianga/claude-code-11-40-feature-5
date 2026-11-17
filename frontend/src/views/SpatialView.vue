<script setup lang="ts">
/**
 * Internal publication workspace.
 *
 * Everything here stays behind authentication: unmerged geometry, retained base
 * cells, withheld reasons, per-item reconciliation and the released-material
 * ledger. Raising sensitivity or revising habitat marks earlier deliveries stale;
 * the generator only reads partitions whose checks passed.
 */
import { computed, onMounted, ref, watch } from 'vue'
import { api } from '../api'
import SpatialMap from '../components/SpatialMap.vue'
import { authState } from '../session'
import type {
  SpatialMapPayload,
  SpatialMaterial,
  SpatialPublication,
  SpatialPublicationDetail,
  SpatialRegion,
} from '../types'

const publications = ref<SpatialPublication[]>([])
const selectedId = ref<number | null>(null)
const detail = ref<SpatialPublicationDetail | null>(null)
const publicPreview = ref<SpatialMapPayload | null>(null)
const materials = ref<SpatialMaterial[]>([])
const selectedRegion = ref<string | null>(null)
const loading = ref(true)
const busy = ref(false)
const error = ref('')
const message = ref('')

async function loadAll() {
  loading.value = true
  error.value = ''
  try {
    publications.value = await api.get<SpatialPublication[]>('/spatial/publications')
    materials.value = await api.get<SpatialMaterial[]>('/spatial/materials')
    if (!selectedId.value && publications.value.length) {
      selectedId.value = publications.value[0].id
    }
  } catch (err) {
    error.value = err instanceof Error ? err.message : '加载失败'
  } finally {
    loading.value = false
  }
}

async function loadDetail() {
  if (!selectedId.value) return
  selectedRegion.value = null
  publicPreview.value = null
  try {
    detail.value = await api.get<SpatialPublicationDetail>(
      `/spatial/publications/${selectedId.value}`,
    )
    if (detail.value.publication.checks_passed) {
      try {
        publicPreview.value = await api.get<SpatialMapPayload>(
          `/public/publications/${detail.value.publication.code}/map`,
        )
      } catch {
        publicPreview.value = null
      }
    }
  } catch (err) {
    error.value = err instanceof Error ? err.message : '加载失败'
  }
}

onMounted(loadAll)
watch(selectedId, loadDetail)

const publishedRegions = computed(
  () => detail.value?.regions.filter((region) => region.status === 'published') ?? [],
)
const withheldRegions = computed(
  () => detail.value?.regions.filter((region) => region.status === 'withheld') ?? [],
)

const activeRegion = computed<SpatialRegion | null>(() =>
  selectedRegion.value
    ? detail.value?.regions.find((region) => region.region_code === selectedRegion.value) ?? null
    : null,
)

const failedChecks = computed(
  () => detail.value?.checks.filter((check) => check.status === 'fail') ?? [],
)

const mapFeatures = computed(() => {
  if (!detail.value) return []
  const features = publicPreview.value?.map.features ?? []
  if (features.length) return features
  return publishedRegions.value.map((region) => ({
    geometry: region.display_geometry,
    properties: {
      region_code: region.region_code,
      candidate_habitat_ha: Number((region.candidate_habitat_m2 / 1e4).toFixed(2)),
      sensitivity_level: region.sensitivity_level,
      region,
    },
  }))
})

const materialForSelection = computed(() =>
  materials.value.filter((row) => row.publication_code === detail.value?.publication.code),
)

async function publishCurrent() {
  if (!detail.value) return
  busy.value = true
  error.value = ''
  message.value = ''
  try {
    const response = await api.post<{ materials: SpatialMaterial[] }>(
      `/spatial/publications/${detail.value.publication.id}/publish`,
    )
    message.value = `已生成交付：${response.materials.map((row) => row.material_code).join('、')}`
    await loadAll()
    await loadDetail()
  } catch (err) {
    error.value = err instanceof Error ? err.message : '生成交付失败'
  } finally {
    busy.value = false
  }
}

async function activateDataset(dataset: 'habitat' | 'sensitivity' | 'suitability', version: number) {
  busy.value = true
  error.value = ''
  message.value = ''
  try {
    const response = await api.post<{
      dataset: string
      active_version: number
      stale_publications: string[]
    }>(`/spatial/datasets/${dataset}/activate`, { version })
    message.value =
      `已激活 ${dataset} v${response.active_version}；` +
      (response.stale_publications.length
        ? `以下发布被标记过期，需重新检查：${response.stale_publications.join('、')}`
        : '没有发布受影响。')
    await loadAll()
    await loadDetail()
  } catch (err) {
    error.value = err instanceof Error ? err.message : '激活失败'
  } finally {
    busy.value = false
  }
}

async function withdrawMaterial(code: string) {
  if (!window.confirm(`撤回 ${code}？撤回仅停止提供，已公开范围仍留在账本中。`)) return
  busy.value = true
  try {
    await api.post(`/spatial/materials/${code}/withdraw`, { reason: '内部工作台主动撤回' })
    message.value = `${code} 已撤回；账本保留其范围，后续发布仍需覆盖。`
    await loadAll()
  } catch (err) {
    error.value = err instanceof Error ? err.message : '撤回失败'
  } finally {
    busy.value = false
  }
}

function fmt(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined) return '—'
  return value.toFixed(digits)
}

function statusBadgeClass(status: string): string {
  if (status === 'published') return 'valid'
  if (status === 'stale') return 'restricted'
  if (status === 'stopped') return 'invalid'
  if (status === 'superseded') return 'pending'
  return 'neutral'
}

const canWrite = computed(() => authState.isWriter.value)
const sensitivitySummary = computed(() => detail.value?.publication.sensitivity_level ?? '—')
</script>

<template>
  <div>
    <div class="page-head">
      <div>
        <div class="eyebrow">Spatial Publication</div>
        <h1>空间泛化与发布工作台</h1>
        <p class="page-sub">
          按接收范围与敏感级别配置最低空间尺度与候选生境面积；候选范围只由公开生境与适生条件计算。
          相邻基础网格合并后保留每块的基础单元，未达标区域不公开并记录原因；交付生成器只读取已通过检查的分区与统计。
        </p>
      </div>
      <div class="toolbar">
        <router-link class="btn sm" to="/public-map">查看公开视图</router-link>
        <button class="btn sm" @click="loadAll">刷新</button>
      </div>
    </div>

    <div v-if="loading" class="loading">正在读取发布记录…</div>
    <div v-else-if="error" class="callout danger">{{ error }}</div>
    <div v-if="message" class="callout pine">{{ message }}</div>

    <template v-else>
      <div class="card" style="margin-bottom: 14px">
        <div class="between wrap">
          <div class="flex wrap">
            <label class="small muted">发布批次</label>
            <select v-model="selectedId" class="select" style="width: 360px">
              <option v-for="row in publications" :key="row.id" :value="row.id">
                {{ row.code }} · {{ row.season_code }} · {{ row.status }}
              </option>
            </select>
          </div>
          <div class="toolbar" v-if="detail">
            <button
              class="btn primary sm"
              :disabled="busy || !canWrite || detail.publication.status === 'stale'"
              @click="publishCurrent"
            >
              生成 / 重新生成交付
            </button>
            <button class="btn sm" :disabled="busy || !canWrite" @click="activateDataset('sensitivity', 2)">
              敏感级别 → critical v2
            </button>
            <button class="btn sm" :disabled="busy || !canWrite" @click="activateDataset('habitat', 2)">
              生境 → v2
            </button>
            <button class="btn sm" :disabled="busy || !canWrite" @click="activateDataset('suitability', 2)">
              适生条件 → v2
            </button>
          </div>
        </div>
        <div v-if="detail" class="flex wrap" style="margin-top: 10px">
          <span class="badge" :class="statusBadgeClass(detail.publication.status)">
            <span class="dot"></span>{{ detail.publication.status }}
          </span>
          <span class="chip">敏感级别 {{ sensitivitySummary }}</span>
          <span class="chip">敏感级别 v{{ detail.publication.sensitivity_version }}</span>
          <span class="chip">生境 v{{ detail.publication.habitat_version }}</span>
          <span class="chip">适生条件 v{{ detail.publication.suitability_version }}</span>
          <span class="chip">{{ publishedRegions.length }} 个已公开 / {{ withheldRegions.length }} 个未公开</span>
          <span v-if="detail.publication.stale_reason" class="chip" style="color: var(--rust)">
            {{ detail.publication.stale_reason }}
          </span>
        </div>
      </div>

      <div class="grid grid-2-1" v-if="detail">
        <div class="card">
          <div class="card-head">
            <h2>发布分区（公开粒度）</h2>
            <span class="card-note">点击查看单块分区的计数与排除</span>
          </div>
          <SpatialMap
            :features="mapFeatures"
            :selected="selectedRegion"
            :height="430"
            @select="(code) => (selectedRegion = code)"
          />
          <div v-if="withheldRegions.length" style="margin-top: 12px">
            <div class="tiny muted" style="margin-bottom: 6px">未公开区域及原因（内部视图）</div>
            <div v-for="region in withheldRegions" :key="region.region_code" class="withheld-block">
              <div class="between">
                <span class="mono">{{ region.region_code }} · {{ region.family }}</span>
                <span class="tiny muted">
                  {{ fmt(region.candidate_habitat_m2 / 10000, 2) }} ha · 宽 {{ fmt(region.width_m, 0) }} m ·
                  {{ region.base_cell_count }} 个基础单元
                </span>
              </div>
              <ul class="reason-list">
                <li v-for="reason in region.reasons" :key="reason.code">
                  <span class="mono tiny">{{ reason.code }}</span> {{ reason.detail }}
                </li>
              </ul>
            </div>
          </div>
        </div>

        <div class="card">
          <div class="card-head">
            <h2>{{ activeRegion ? activeRegion.region_code : '分区明细' }}</h2>
            <span v-if="!activeRegion" class="card-note">点击左侧分区</span>
          </div>
          <div v-if="activeRegion" class="stack-sm">
            <div class="metric-row"><span>连通关系键</span><strong class="mono">{{ activeRegion.family }}</strong></div>
            <div class="metric-row"><span>候选生境面积</span><strong>{{ fmt(activeRegion.candidate_habitat_m2 / 10000, 2) }} ha</strong></div>
            <div class="metric-row"><span>区域最窄宽度</span><strong>{{ fmt(activeRegion.width_m, 0) }} m</strong></div>
            <div class="metric-row"><span>基础单元</span><strong>{{ activeRegion.base_cell_count }}</strong></div>
            <div class="metric-row"><span>公开粒度</span><strong>{{ fmt(activeRegion.display_cell_m, 0) }} m</strong></div>
            <div class="metric-row"><span>观察 / 出现</span><strong>{{ activeRegion.observation_count }} / {{ activeRegion.present_count }}</strong></div>
            <div class="metric-row"><span>转载排重素材</span><strong>{{ activeRegion.duplicate_materials }}</strong></div>
            <div class="metric-row"><span>有效调查时长</span><strong>{{ fmt(activeRegion.effective_hours, 1) }} h</strong></div>
            <div class="metric-row"><span>合并时段块</span><strong>{{ activeRegion.merged_effort_windows }}</strong></div>
            <div class="metric-row"><span>路线覆盖</span><strong>{{ fmt(activeRegion.route_length_m / 1000, 2) }} km</strong></div>
            <div class="metric-row"><span>强度未知单元</span><strong>{{ activeRegion.effort_unknown_units }}</strong></div>
            <div class="metric-row"><span>发布阈值</span><strong>≥ {{ fmt(activeRegion.threshold_snapshot.min_candidate_habitat_m2, 0) }} m² / {{ fmt(activeRegion.threshold_snapshot.min_region_width_m, 0) }} m</strong></div>
          </div>
          <div v-else class="empty">选择一个分区查看明细</div>
        </div>
      </div>

      <div class="card" style="margin-top: 14px" v-if="detail">
        <div class="card-head">
          <h2>发布检查（逐项对账）</h2>
          <span class="card-note">只有全部通过的发布才允许生成交付</span>
        </div>
        <div class="table-wrap">
          <table>
            <thead>
              <tr><th>检查</th><th>状态</th><th>说明</th></tr>
            </thead>
            <tbody>
              <tr v-for="check in detail.checks" :key="check.check_code">
                <td class="mono">{{ check.check_code }}</td>
                <td>
                  <span class="badge" :class="check.status === 'pass' ? 'pass' : 'fail'">
                    <span class="dot"></span>{{ check.status === 'pass' ? '通过' : '未通过' }}
                  </span>
                </td>
                <td class="small">{{ check.detail }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <div v-if="failedChecks.length" class="callout danger" style="margin-top: 10px">
          {{ failedChecks.map((check) => check.check_code).join('、') }} 未通过，该发布不能生成交付。
        </div>
      </div>

      <div class="card" style="margin-top: 14px" v-if="detail">
        <div class="card-head">
          <h2>对外提供过的材料账本</h2>
          <span class="card-note">撤回链接不等于消除旧信息；范围仍用于后续覆盖检查</span>
        </div>
        <div class="table-wrap">
          <table>
            <thead>
              <tr>
                <th>材料</th><th>类型</th><th>状态</th><th class="num">范围块</th><th>说明</th><th></th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="row in materialForSelection" :key="row.material_code">
                <td class="mono">{{ row.material_code }}</td>
                <td>{{ row.kind === 'map' ? '地图' : '数据文件' }}</td>
                <td>
                  <span class="badge" :class="row.status === 'active' ? 'valid' : row.status === 'withdrawn' ? 'pending' : 'restricted'">
                    <span class="dot"></span>{{ row.status }}
                  </span>
                </td>
                <td class="num">{{ row.regions.length }}</td>
                <td class="tiny">{{ row.note }}</td>
                <td>
                  <button
                    v-if="row.status === 'active' && canWrite"
                    class="btn sm danger"
                    :disabled="busy"
                    @click="withdrawMaterial(row.material_code)"
                  >
                    撤回
                  </button>
                </td>
              </tr>
              <tr v-if="!materialForSelection.length">
                <td colspan="6" class="empty">该发布尚未生成对外材料</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </template>
  </div>
</template>

<style scoped>
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
.withheld-block {
  border: 1px solid var(--line);
  border-left: 3px solid var(--rust);
  border-radius: 8px;
  padding: 8px 10px;
  margin-bottom: 8px;
  background: #fffdfb;
}
.reason-list {
  margin: 6px 0 0;
  padding-left: 16px;
  font-size: 11.5px;
  color: var(--ink-soft);
}
</style>

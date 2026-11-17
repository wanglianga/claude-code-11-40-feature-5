<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { api } from '../api'
import type { Overview, UnitDetail } from '../types'
import StatusBadge from '../components/StatusBadge.vue'
import { fmtDateTime, statusLabel } from '../format'

interface ChainStep {
  step: string
  label: string
  items: any[]
  current_id?: number
  determinations?: any[]
}

const route = useRoute()
const units = ref<Array<{ id: number; station_code: string; station_name: string; unit_date: string; cert_status: string; review_status: string }>>([])
const selectedUnit = ref<number | null>(null)
const detail = ref<UnitDetail | null>(null)
const chain = ref<ChainStep[]>([])
const loading = ref(true)
const error = ref('')
const expandedRecord = ref<number | null>(null)

onMounted(async () => {
  try {
    const runs = await api.get<Array<{ id: number; run_code: string }>>('/runs')
    const active = runs.find((r) => r.run_code === 'RUN-2026-SPRING-01') ?? runs[0]
    const overview = await api.get<Overview>(`/runs/${active.id}/overview`)
    const flat: typeof units.value = []
    for (const station of overview.stations) {
      for (const unit of station.units) {
        flat.push({
          id: unit.unit_id,
          station_code: station.code,
          station_name: station.name,
          unit_date: unit.date,
          cert_status: unit.cert_status,
          review_status: unit.review_status,
        })
      }
    }
    units.value = flat
    const fromQuery = Number(route.query.unit)
    selectedUnit.value = fromQuery || flat[0]?.id || null
    await loadChain()
  } catch (err) {
    error.value = err instanceof Error ? err.message : '加载失败'
  } finally {
    loading.value = false
  }
})

async function loadChain() {
  if (!selectedUnit.value) return
  error.value = ''
  try {
    const [unitDetail, trace] = await Promise.all([
      api.get<UnitDetail>(`/units/${selectedUnit.value}`),
      api.get<{ chain: ChainStep[] }>(`/units/${selectedUnit.value}/trace`),
    ])
    detail.value = unitDetail
    chain.value = trace.chain
  } catch (err) {
    error.value = err instanceof Error ? err.message : '加载失败'
  }
}

const stepByKey = computed(() => Object.fromEntries(chain.value.map((s) => [s.step, s])))

const rawRecords = computed(() => stepByKey.value.raw_events?.items ?? [])
const gates = computed(() => stepByKey.value.coverage_conditions?.items ?? [])
const versions = computed(() => stepByKey.value.versions?.items ?? [])
const certificates = computed(() => stepByKey.value.certificate?.items ?? [])
const refs = computed(() => stepByKey.value.trend_references?.items ?? [])
const determinations = computed(() => stepByKey.value.raw_events?.determinations ?? [])

function selectUnit() {
  loadChain()
}
</script>

<template>
  <div>
    <div class="page-head">
      <div>
        <div class="eyebrow">Traceability</div>
        <h1>单元回溯链</h1>
        <p class="page-sub">
          从原始事件到覆盖条件、规则/模型/站点版本、证书与趋势引用的完整链条。每一份原始记录保留载荷哈希、
          修订号与来源设备；每次证书签发保留当时的门槛快照。
        </p>
      </div>
      <div class="toolbar">
        <select v-model="selectedUnit" class="select" style="width: 320px" @change="selectUnit">
          <option v-for="u in units" :key="u.id" :value="u.id">
            {{ u.station_code }} · {{ u.unit_date }} · {{ statusLabel(u.cert_status, u.review_status) }}
          </option>
        </select>
      </div>
    </div>

    <div v-if="loading" class="loading">加载中…</div>
    <div v-else-if="error" class="callout danger">{{ error }}</div>

    <template v-else-if="detail">
      <div class="card" style="margin-bottom: 14px">
        <div class="between">
          <div>
            <h2>{{ detail.station.code }} · {{ detail.station.name }}</h2>
            <div class="tiny muted">
              调查日 {{ detail.unit_date }} · 站点版本 v{{ detail.station_version.version_number }}
              · 规则册 {{ detail.rulebook.rulebook_code }} v{{ detail.rulebook.version_number }}
              · 单元 #{{ detail.id }}
            </div>
          </div>
          <div class="flex">
            <StatusBadge :status="detail.certificate?.status" :review-status="detail.certificate?.review_status" />
            <router-link :to="'/units/' + detail.id" class="btn sm">打开单元</router-link>
          </div>
        </div>
      </div>

      <div class="chain">
        <section class="chain-step card">
          <header>
            <span class="step-no">1</span>
            <h2>原始事件（{{ rawRecords.length }}）</h2>
            <span class="tiny muted">哈希与修订版本保留原始载荷</span>
          </header>
          <div class="stack-sm">
            <div v-for="rec in rawRecords" :key="rec.record_id" class="chain-item">
              <div class="between">
                <div class="flex">
                  <span class="chip">{{ rec.record_type }}</span>
                  <span class="mono tiny">{{ rec.external_id }}</span>
                  <span class="chip">r{{ rec.revision }}</span>
                </div>
                <button class="btn ghost sm" @click="expandedRecord = expandedRecord === rec.record_id ? null : rec.record_id">
                  {{ expandedRecord === rec.record_id ? '收起' : '详情' }}
                </button>
              </div>
              <div class="tiny muted">
                事件 {{ fmtDateTime(rec.event_time) }} · 接收 {{ fmtDateTime(rec.received_time) }}
                · {{ rec.source_device }}
                <template v-if="rec.coord_precision_m">· 精度 ±{{ rec.coord_precision_m }} m</template>
              </div>
              <div class="hash">{{ rec.hash }}</div>
              <div v-if="expandedRecord === rec.record_id" class="rec-audit">
                <div
                  v-for="d in determinations.filter((x) => x.material_record_id === rec.record_id)"
                  :key="d.determination_id"
                >
                  判读 #{{ d.determination_id }} · {{ d.species_code }} · {{ d.verdict }} ·
                  {{ Number(d.confidence).toFixed(2) }} · r{{ d.revision }} · {{ d.status }}
                </div>
                <router-link :to="'/records'" class="tiny">在观测档案中查看完整载荷与修订 ⟶</router-link>
              </div>
            </div>
          </div>
        </section>

        <section class="chain-step card">
          <header>
            <span class="step-no">2</span>
            <h2>覆盖条件</h2>
            <span class="tiny muted">门槛结论与证据记录 id</span>
          </header>
          <div class="stack-sm">
            <div v-for="gate in gates" :key="gate.gate" class="chain-item">
              <div class="between">
                <span class="chip">{{ gate.gate }}</span>
                <StatusBadge :status="gate.status" :label="statusLabel(gate.status)" />
              </div>
              <div class="tiny muted">
                证据记录：{{ gate.evidence_ids.length }} 条
                <span class="mono" style="margin-left: 6px">{{ gate.evidence_ids.slice(0, 10).join(', ') }}{{ gate.evidence_ids.length > 10 ? '…' : '' }}</span>
              </div>
            </div>
          </div>
        </section>

        <section class="chain-step card">
          <header>
            <span class="step-no">3</span>
            <h2>规则 / 模型 / 站点版本</h2>
          </header>
          <div class="version-grid">
            <div v-for="item in versions" :key="item.kind" class="version-card">
              <div class="tiny muted">{{ item.kind }}</div>
              <div class="mono small">
                <template v-if="item.kind === 'rulebook'">
                  {{ item.code }} v{{ item.version_number }}
                </template>
                <template v-else-if="item.kind === 'station_version'">
                  站点版本 v{{ item.version_number }} · 缓冲 {{ item.buffer_width_m }} m
                </template>
                <template v-else>
                  {{ item.code }} v{{ item.version }} · 阈值 {{ item.threshold }}
                </template>
              </div>
              <div class="tiny muted">
                <template v-if="item.kind === 'rulebook'">{{ item.label }}</template>
                <template v-else-if="item.kind === 'station_version'">自 {{ item.effective_from }} 生效</template>
                <template v-else>概率模型（趋势参考）</template>
              </div>
            </div>
          </div>
        </section>

        <section class="chain-step card">
          <header>
            <span class="step-no">4</span>
            <h2>证书（{{ certificates.length }} 个版本）</h2>
          </header>
          <div class="stack-sm">
            <div v-for="cert in certificates" :key="cert.id" class="chain-item" :class="{ current: cert.id === stepByKey.certificate?.current_id }">
              <div class="between">
                <div class="flex">
                  <span class="mono tiny">{{ cert.certificate_no }}</span>
                  <span class="chip">v{{ cert.version }}</span>
                  <StatusBadge :status="cert.status" :review-status="cert.review_status" />
                </div>
                <span class="tiny muted">{{ fmtDateTime(cert.issued_at) }}</span>
              </div>
              <div class="tiny muted">
                正式未发现 {{ cert.absence_eligible ? '可用' : '不可用' }} ·
                趋势 {{ cert.trend_eligible ? '可引用' : '暂停' }}
                <template v-if="cert.superseded_by_id"> · 已被后续版本替代</template>
                <template v-else> · 当前版本</template>
              </div>
              <div v-if="cert.review_reason" class="tiny" style="color: var(--rust)">{{ cert.review_reason }}</div>
            </div>
          </div>
        </section>

        <section class="chain-step card">
          <header>
            <span class="step-no">5</span>
            <h2>趋势引用（{{ refs.length }}）</h2>
          </header>
          <div v-if="!refs.length" class="empty">尚无趋势引用。</div>
          <div v-else class="stack-sm">
            <div v-for="ref in refs" :key="ref.id" class="chain-item">
              <div class="between">
                <span class="mono tiny">{{ ref.reference_code }} · {{ ref.purpose_label }}</span>
                <StatusBadge
                  :status="ref.status === 'active' ? 'valid' : ref.status === 'suspended' ? 'pending' : 'invalid'"
                  :label="ref.status === 'active' ? '活动' : ref.status === 'suspended' ? '已暂停' : '已撤回'"
                />
              </div>
              <div class="tiny muted">{{ ref.created_by }} · {{ fmtDateTime(ref.created_at) }}</div>
              <div v-if="ref.note" class="tiny">{{ ref.note }}</div>
              <div v-if="ref.suspended_reason" class="tiny" style="color: var(--rust)">{{ ref.suspended_reason }}</div>
            </div>
          </div>
        </section>
      </div>
    </template>
  </div>
</template>

<style scoped>
.chain { display: flex; flex-direction: column; gap: 12px; }
.chain-step header { display: flex; align-items: center; gap: 10px; margin-bottom: 10px; }
.step-no {
  width: 22px; height: 22px; border-radius: 50%; display: grid; place-items: center;
  background: var(--pine); color: #fff; font-size: 11px; font-weight: 600;
}
.chain-item { border: 1px solid var(--line); border-radius: 8px; padding: 8px 10px; background: #fff; }
.chain-item.current { border-color: var(--pine-mid); background: #f7faf7; }
.rec-audit { margin-top: 6px; border-top: 1px dashed var(--line); padding-top: 6px; font-size: 11px; color: var(--ink-soft); }
.version-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px; }
.version-card { border: 1px solid var(--line); border-radius: 8px; padding: 8px 10px; background: var(--surface-2); }
@media (max-width: 900px) { .version-grid { grid-template-columns: 1fr; } }
</style>

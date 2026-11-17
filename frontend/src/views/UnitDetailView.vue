<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../api'
import { useToast } from '../toast'
import { authState } from '../session'
import type { UnitDetail } from '../types'
import StatusBadge from '../components/StatusBadge.vue'
import ProbabilityBand from '../components/ProbabilityBand.vue'
import {
  actionLabel,
  DIVERGENCE_LABEL,
  fmtDate,
  fmtDateTime,
  fmtNum,
  fmtPct,
  GATE_LABEL,
  RECORD_TYPE_LABEL,
  statusLabel,
  VERDICT_LABEL,
} from '../format'

const route = useRoute()
const router = useRouter()
const { push } = useToast()

const unitId = computed(() => Number(route.params.id))
const data = ref<UnitDetail | null>(null)
const loading = ref(true)
const error = ref('')
const tab = ref<'evidence' | 'gates' | 'certificate' | 'trace' | 'audit'>('gates')
const busy = ref('')
const refNote = ref('')

async function load() {
  loading.value = true
  error.value = ''
  try {
    data.value = await api.get<UnitDetail>(`/units/${unitId.value}`)
  } catch (err) {
    error.value = err instanceof Error ? err.message : '加载失败'
  } finally {
    loading.value = false
  }
}

onMounted(load)

const cert = computed(() => data.value?.certificate ?? null)
const est = computed(() => data.value?.probability ?? null)
const absence = computed(() => cert.value?.conclusions?.absence_analysis ?? null)

const divergence = computed(() => {
  if (!cert.value || !est.value) return null
  const ruleOk = cert.value.absence_eligible && cert.value.review_status === 'normal'
  const probOk = est.value.threshold_pass
  if (ruleOk && probOk) return 'both_support'
  if (!ruleOk && probOk) return 'probability_supports_rule_blocks'
  if (ruleOk && !probOk) return 'rule_supports_probability_below'
  return null
})

const supportingMaterials = computed(() => data.value?.evidence.materials ?? [])

async function addReference(purpose: 'absence' | 'trend') {
  if (!data.value) return
  busy.value = purpose
  try {
    await api.post(`/units/${data.value.id}/trend-references`, {
      purpose,
      note: refNote.value || (purpose === 'absence' ? '规则证书支持正式未发现结论' : '作为趋势输入样本'),
    })
    push(purpose === 'absence' ? '已登记正式未发现结论引用' : '已登记趋势输入引用')
    refNote.value = ''
    await load()
  } catch (err) {
    push(err instanceof Error ? err.message : '登记失败', 'error')
  } finally {
    busy.value = ''
  }
}

async function revokeRef(refId: number) {
  const reason = window.prompt('撤回原因（会写入审计日志）', '研究人员撤回该引用')
  if (!reason) return
  try {
    await api.post(`/trend-references/${refId}/revoke`, { reason })
    push('趋势引用已撤回')
    await load()
  } catch (err) {
    push(err instanceof Error ? err.message : '撤回失败', 'error')
  }
}

async function reissue() {
  if (!data.value) return
  if (!window.confirm('复审完成后重新签发证书？将基于当前判读与覆盖条件生成新版本证书。')) return
  busy.value = 'reissue'
  try {
    await api.post(`/units/${data.value.id}/certificates/reissue`)
    push('已签发新版本证书')
    await load()
  } catch (err) {
    push(err instanceof Error ? err.message : '签发失败', 'error')
  } finally {
    busy.value = ''
  }
}

function metricPairs(gate: Record<string, any>): Array<[string, string]> {
  const m = gate.measured || {}
  const pairs: Array<[string, string]> = []
  const add = (label: string, value: any, formatter?: (v: any) => string) => {
    if (value === null || value === undefined || typeof value === 'object') return
    pairs.push([label, formatter ? formatter(value) : String(value)])
  }
  if (gate.gate === 'device') {
    add('可用时长', m.available_hours, (v) => `${fmtNum(v, 1)} h`)
    add('可用占比', m.available_ratio, (v) => fmtPct(v))
    add('停机已记录', m.explained_downtime_hours, (v) => `${fmtNum(v, 1)} h`)
    add('未解释缺口', m.unexplained_gap_hours, (v) => `${fmtNum(v, 1)} h`)
    add('图像帧', m.frame_count)
    add('心跳', m.heartbeat_count)
  } else if (gate.gate === 'time') {
    add('记录总数', m.records_total)
    add('超出容差', m.untrusted_count)
    add('可验证校时', m.calibrated_count)
    add('迟到送达', m.late_delivery_count)
    add('季节窗口内', m.in_season_ratio, (v) => fmtPct(v))
    add('容差', m.tolerance_seconds, (v) => `${v} s`)
  } else if (gate.gate === 'space') {
    add('路线覆盖', m.route_coverage_ratio, (v) => fmtPct(v))
    add('最深入缓冲', m.penetration_depth_m, (v) => `${fmtNum(v, 0)} m`)
    add('缓冲区宽度', m.buffer_width_m, (v) => `${fmtNum(v, 0)} m`)
    add('定位精度', m.track_precision_m, (v) => `${fmtNum(v, 0)} m`)
    add('素材在缓冲区内', m.in_buffer_ratio, (v) => fmtPct(v))
    add('站点版本', m.station_version, (v) => `v${v}`)
  } else if (gate.gate === 'reading') {
    add('达门槛判读', m.available_observations)
    add('目标出现', m.target_present)
    add('目标未出现', m.target_absent)
    add('待判读', m.pending_count)
    add('待判读占比', m.pending_ratio, (v) => fmtPct(v))
    add('未决冲突', m.open_conflicts)
  }
  return pairs
}

function verdictClass(verdict: string): string {
  return verdict === 'present' ? 'invalid' : verdict === 'absent' ? 'valid' : 'neutral'
}
</script>

<template>
  <div>
    <div v-if="loading" class="loading">正在加载调查单元…</div>
    <div v-else-if="error" class="callout danger">{{ error }}</div>

    <template v-else-if="data">
      <div class="page-head">
        <div>
          <div class="eyebrow">
            <router-link to="/">站点覆盖总览</router-link> /
            {{ data.station.code }} · {{ fmtDate(data.unit_date) }}
          </div>
          <h1>{{ data.station.name }}</h1>
          <p class="page-sub">
            样区 {{ data.station.zone?.name }} · 站点版本 v{{ data.station_version.version_number }}
            （{{ data.station_version.effective_from }} 起） · 单元 #{{ data.id }}
          </p>
        </div>
        <div class="toolbar">
          <button class="btn sm" @click="router.push('/')">返回总览</button>
          <button class="btn sm" @click="router.push('/trace?unit=' + data.id)">回溯链</button>
        </div>
      </div>

      <div class="grid grid-2-1" style="margin-bottom: 14px">
        <div class="card">
          <div class="card-head">
            <h2>
              规则证书
              <StatusBadge :status="cert?.status" :review-status="cert?.review_status" />
            </h2>
            <span class="chip mono">{{ cert?.certificate_no }} · v{{ cert?.version }}</span>
          </div>
          <p style="font-size: 13px; color: var(--ink-soft)">{{ cert?.summary }}</p>

          <div v-if="cert?.review_status === 'under_review'" class="callout danger" style="margin-top: 10px">
            <div class="callout-title">当前证书复审中</div>
            {{ cert.review_reason }} 旧运行保留原证书 v1，可在下方证书历史与回溯链中查看。
            <div style="margin-top: 8px">
              <button v-if="authState.isWriter.value" class="btn sm" :disabled="busy === 'reissue'" @click="reissue">
                复审完成，重新签发
              </button>
            </div>
          </div>

          <div class="divider"></div>

          <div class="grid grid-2" style="gap: 10px">
            <div class="callout" :class="cert?.absence_eligible ? 'pine' : 'warn'">
              <div class="callout-title">
                正式未发现结论 · {{ cert?.absence_eligible ? '可用' : '不可用' }}
              </div>
              <span class="small">{{ absence?.species_note }}</span>
            </div>
            <div class="callout info">
              <div class="callout-title">趋势输入 · {{ cert?.trend_eligible ? '可引用' : '暂停' }}</div>
              <span class="small">
                规则证书用途：{{ cert?.trend_eligible ? '可作为趋势输入' : '当前不可用于趋势引用' }}。
                概率结果另列，不替代规则证书。
              </span>
            </div>
          </div>

          <div v-if="absence" class="stack-sm" style="margin-top: 12px">
            <div class="pill-row">
              <span class="chip">确认出现 {{ absence.confirmed_present }}</span>
              <span class="chip">确认未出现判读 {{ absence.confirmed_absent_readings }}</span>
              <span class="chip">待判读 {{ absence.pending_materials }}</span>
              <span class="chip">未决冲突 {{ absence.open_conflicts }}</span>
            </div>
            <div class="tiny muted">
              以下情况不得简化为物种未出现：{{ absence.never_implies_absence.join('、') }}。
            </div>
          </div>
        </div>

        <div class="card">
          <div class="card-head">
            <h2>概率估计（趋势参考）</h2>
            <span class="chip" v-if="est">{{ est.model_code }} v{{ est.model_version }}</span>
          </div>
          <div v-if="!est" class="empty">该运行未附加概率模型。</div>
          <template v-else>
            <div class="prob-head">
              <span class="prob-value">{{ est.probability.toFixed(2) }}</span>
              <span class="small muted">
                敏感区间 {{ est.interval_low.toFixed(2) }}–{{ est.interval_high.toFixed(2) }}
              </span>
            </div>
            <ProbabilityBand
              :probability="est.probability"
              :low="est.interval_low"
              :high="est.interval_high"
              :threshold="est.threshold"
            />
            <div class="callout warn" style="margin-top: 10px">
              模型推算，不是已确认的实地覆盖。正式未发现结论必须使用规则证书；趋势研究可引用带敏感区间的概率结果。
            </div>

            <div v-if="divergence" class="callout" style="margin-top: 8px" :class="divergence === 'both_support' ? 'pine' : 'info'">
              <div class="callout-title">{{ DIVERGENCE_LABEL[divergence] }}</div>
              <span class="small">
                {{
                  divergence === 'probability_supports_rule_blocks'
                    ? '概率估计支持部分覆盖价值，但规则门槛未满足：正式结论仍以规则证书为准，趋势可参考概率。'
                    : divergence === 'rule_supports_probability_below'
                    ? '规则门槛满足，但概率估计低于阈值：趋势样本需谨慎解释。'
                    : '规则证书与概率估计均支持该单元。'
                }}
              </span>
            </div>

            <div class="divider"></div>
            <div class="stack-sm">
              <div class="tiny muted">敏感区间与场景</div>
              <div class="variant-row" v-for="v in est.variants" :key="v.key">
                <span>{{ v.label }}</span>
                <span class="mono">{{ v.probability.toFixed(3) }}</span>
                <span class="badge" :class="v.passes ? 'pass' : 'fail'">{{ v.passes ? '过阈值' : '未过' }}</span>
              </div>
            </div>
          </template>
        </div>
      </div>

      <div class="tabs">
        <button class="tab" :class="{ active: tab === 'gates' }" @click="tab = 'gates'">门槛判定</button>
        <button class="tab" :class="{ active: tab === 'evidence' }" @click="tab = 'evidence'">
          原始证据（{{ data.evidence.record_count }}）
        </button>
        <button class="tab" :class="{ active: tab === 'certificate' }" @click="tab = 'certificate'">
          证书历史与用途
        </button>
        <button class="tab" :class="{ active: tab === 'audit' }" @click="tab = 'audit'">审计</button>
      </div>

      <template v-if="tab === 'gates'">
        <div class="grid grid-2-1">
          <div class="gate-list">
            <div v-for="gate in data.gates" :key="gate.id" class="gate" :class="gate.status">
              <div class="gate-head">
                <div class="gate-title">
                  {{ GATE_LABEL[gate.gate] || gate.gate }}
                  <StatusBadge :status="gate.status" :label="statusLabel(gate.status)" />
                </div>
                <span class="tiny mono muted">{{ gate.requirement_code }}</span>
              </div>
              <div class="gate-explain">{{ gate.explanation }}</div>
              <div class="gate-metrics">
                <span v-for="[label, value] in metricPairs(gate)" :key="label" class="metric">
                  {{ label }} <b>{{ value }}</b>
                </span>
              </div>

              <div v-if="gate.gate === 'space' && gate.measured.segment_coverages?.length" class="per-segment">
                <div class="tiny muted" style="margin-bottom: 4px">规则册路线段覆盖</div>
                <div v-for="seg in gate.measured.segment_coverages" :key="seg.segment_code + seg.track_record_id" class="seg-row">
                  <span class="mono tiny">{{ seg.segment_code }}</span>
                  <span class="tiny">{{ seg.segment_name }}</span>
                  <span class="mono tiny">{{ fmtPct(seg.coverage) }}</span>
                </div>
              </div>

              <div v-if="gate.gate === 'device' && gate.measured.unexplained_gaps?.length" class="callout warn" style="margin-top: 8px">
                <div class="callout-title">未由停机日志覆盖的帧缺失</div>
                <div v-for="(gap, idx) in gate.measured.unexplained_gaps" :key="idx" class="small">
                  {{ gap.kind }}：{{ fmtDateTime(gap.start) }} → {{ fmtDateTime(gap.end) }}（{{ gap.hours }} h）
                </div>
              </div>

              <div v-if="gate.gate === 'time' && gate.measured.late_delivery_examples?.length" class="callout info" style="margin-top: 8px">
                <div class="callout-title">迟到送达（事件时间不变）</div>
                <div v-for="ex in gate.measured.late_delivery_examples" :key="ex.record_id" class="small mono">
                  {{ ex.external_id }} · 延迟 {{ ex.received_delay_hours }} h
                </div>
              </div>

              <div class="rule-ref">{{ gate.rule_ref }}</div>
            </div>
          </div>

          <div class="stack">
            <div class="card">
              <div class="card-head"><h2>覆盖条件快照</h2></div>
              <dl class="kv">
                <dt>站点版本</dt>
                <dd>v{{ data.station_version.version_number }} · 缓冲 {{ data.station_version.buffer_width_m }} m</dd>
                <dt>版本说明</dt>
                <dd>{{ data.station_version.change_reason || '—' }}</dd>
                <dt>规则册</dt>
                <dd>{{ data.rulebook.rulebook_code }} v{{ data.rulebook.version_number }}</dd>
                <dt>季节窗口</dt>
                <dd class="mono">{{ data.rulebook.season_start }} → {{ data.rulebook.season_end }}</dd>
                <dt>素材 / 判读</dt>
                <dd>{{ data.evidence.record_count }} / {{ data.evidence.determination_count }}</dd>
                <dt>组装时间</dt>
                <dd>{{ fmtDateTime(data.assembled_at) }}</dd>
              </dl>
              <div class="divider"></div>
              <div class="tiny muted" style="margin-bottom: 6px">门槛阈值（来自规则册）</div>
              <div class="stack-sm">
                <div v-for="gate in data.gates" :key="'th-' + gate.id" class="tiny">
                  <span class="mono muted">{{ gate.requirement_code }}</span>
                  <span style="margin-left: 6px">
                    {{ Object.entries(gate.threshold).map(([k, v]) => `${k}=${v}`).join('，') }}
                  </span>
                </div>
              </div>
            </div>

            <div class="card">
              <div class="card-head"><h2>趋势引用</h2></div>
              <div v-if="!data.trend_references.length" class="empty">该单元尚无趋势引用。</div>
              <div v-else class="stack-sm">
                <div v-for="ref in data.trend_references" :key="ref.id" class="ref-row">
                  <div class="between">
                    <span class="mono tiny">{{ ref.reference_code }}</span>
                    <StatusBadge
                      :status="ref.status === 'active' ? 'valid' : ref.status === 'suspended' ? 'pending' : 'invalid'"
                      :label="ref.status === 'active' ? '活动' : ref.status === 'suspended' ? '已暂停' : '已撤回'"
                    />
                  </div>
                  <div class="tiny muted">{{ ref.purpose_label }} · {{ ref.created_by }} · {{ fmtDateTime(ref.created_at) }}</div>
                  <div v-if="ref.note" class="tiny">{{ ref.note }}</div>
                  <div v-if="ref.suspended_reason" class="tiny" style="color: var(--rust)">{{ ref.suspended_reason }}</div>
                  <button
                    v-if="ref.status === 'active' && authState.isWriter.value"
                    class="btn ghost sm"
                    @click="revokeRef(ref.id)"
                  >
                    撤回引用
                  </button>
                </div>
              </div>
            </div>

            <div v-if="authState.isWriter.value" class="card">
              <div class="card-head"><h2>登记引用</h2></div>
              <div class="stack-sm">
                <input v-model="refNote" class="input" placeholder="引用说明（写入快照）" />
                <div class="flex wrap">
                  <button
                    class="btn sm"
                    :disabled="busy !== ''"
                    @click="addReference('absence')"
                  >
                    登记正式未发现引用
                  </button>
                  <button
                    class="btn sm"
                    :disabled="busy !== ''"
                    @click="addReference('trend')"
                  >
                    登记趋势引用
                  </button>
                </div>
                <div class="tiny muted">
                  正式未发现引用要求证书为有效调查；复审中的证书暂停新引用。
                </div>
              </div>
            </div>
          </div>
        </div>
      </template>

      <template v-else-if="tab === 'evidence'">
        <div class="card">
          <div class="card-head">
            <h2>原始事件与判读</h2>
            <span class="tiny muted">每份素材可回查载荷哈希、接收时间、来源设备与判读修订</span>
          </div>
          <div class="table-wrap" style="max-height: 520px; overflow-y: auto">
            <table>
              <thead>
                <tr>
                  <th>类型</th>
                  <th>外部编号</th>
                  <th>事件时间</th>
                  <th>接收时间</th>
                  <th>来源设备</th>
                  <th>坐标 / 精度</th>
                  <th>判读</th>
                  <th>哈希</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="m in supportingMaterials" :key="m.id">
                  <td>
                    <span class="chip">{{ RECORD_TYPE_LABEL[m.record_type] || m.record_type }}</span>
                    <div v-if="m.is_pending" class="tiny" style="color: var(--slate)">待判读</div>
                  </td>
                  <td class="mono tiny">{{ m.external_id }}</td>
                  <td class="nowrap small">{{ fmtDateTime(m.event_time) }}</td>
                  <td class="nowrap small">{{ fmtDateTime(m.received_time) }}</td>
                  <td class="small">{{ m.source_device }}</td>
                  <td class="small nowrap">
                    <template v-if="m.latitude !== null">
                      <span class="mono tiny">{{ m.latitude.toFixed(5) }}, {{ m.longitude?.toFixed(5) }}</span>
                      <div class="tiny muted">±{{ m.coord_precision_m ?? '—' }} m</div>
                    </template>
                    <span v-else class="muted">—</span>
                  </td>
                  <td>
                    <div v-for="d in m.determinations" :key="d.id" class="det-line">
                      <span class="badge" :class="verdictClass(d.verdict)">{{ VERDICT_LABEL[d.verdict] }}</span>
                      <span class="tiny">{{ d.species_code }} · {{ d.confidence.toFixed(2) }}</span>
                      <span v-if="d.status === 'superseded'" class="tiny muted">（已替代）</span>
                      <span v-if="d.revision > 1" class="tiny muted">r{{ d.revision }}</span>
                    </div>
                    <div v-for="c in m.conflicts" :key="c.id" class="tiny" style="color: var(--violet)">
                      冲突未决 #{{ c.id }}
                    </div>
                  </td>
                  <td class="hash">{{ m.payload_hash.slice(0, 12) }}…</td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>

        <div v-if="data.conflicts.length" class="card" style="margin-top: 14px">
          <div class="card-head"><h2>判读冲突</h2></div>
          <div class="stack-sm">
            <div v-for="c in data.conflicts" :key="c.id" class="callout" :class="c.status === 'open' ? 'warn' : 'info'">
              <div class="between">
                <span class="callout-title">{{ c.species_code }} · 素材 #{{ c.material_record_id }}</span>
                <StatusBadge :status="c.status === 'open' ? 'pending' : 'valid'" :label="c.status === 'open' ? '未决' : '已处置'" />
              </div>
              <div class="small">{{ c.detail }}</div>
              <div class="pill-row" style="margin-top: 6px">
                <span v-for="(s, i) in c.sources" :key="i" class="chip">
                  {{ s.source }} · {{ VERDICT_LABEL[s.verdict] || s.verdict }} · {{ Number(s.confidence).toFixed(2) }}
                </span>
              </div>
              <div v-if="c.resolution" class="small" style="margin-top: 6px">处置：{{ c.resolution }}</div>
            </div>
          </div>
        </div>
      </template>

      <template v-else-if="tab === 'certificate'">
        <div class="card">
          <div class="card-head"><h2>证书版本历史</h2></div>
          <div class="stack-sm">
            <div v-for="c in [...data.certificate_history].reverse()" :key="c.id" class="cert-row">
              <div class="between">
                <div class="flex">
                  <StatusBadge :status="c.status" :review-status="c.review_status" />
                  <span class="mono small">{{ c.certificate_no }}</span>
                  <span class="chip">v{{ c.version }}</span>
                </div>
                <span class="tiny muted">{{ fmtDateTime(c.issued_at) }}</span>
              </div>
              <div class="small" style="margin-top: 4px">{{ c.summary }}</div>
              <div class="pill-row" style="margin-top: 6px">
                <span class="chip" :style="c.absence_eligible ? 'color:var(--pine)' : ''">
                  正式未发现 {{ c.absence_eligible ? '可用' : '不可用' }}
                </span>
                <span class="chip" :style="c.trend_eligible ? 'color:var(--pine)' : ''">
                  趋势 {{ c.trend_eligible ? '可引用' : '暂停' }}
                </span>
                <span v-if="c.superseded_by_id" class="chip">已被 v{{ c.version + 1 }} 替代</span>
              </div>
              <div v-if="c.review_reason" class="tiny" style="color: var(--rust); margin-top: 4px">{{ c.review_reason }}</div>
            </div>
          </div>
        </div>
      </template>

      <template v-else-if="tab === 'audit'">
        <div class="card">
          <div class="card-head"><h2>审计记录</h2></div>
          <div v-if="!data.audit.length" class="empty">暂无相关审计事件。</div>
          <div class="timeline" v-else>
            <div v-for="event in data.audit" :key="event.id" class="tl-item">
              <div class="tl-dot"></div>
              <div class="tl-body">
                <div>{{ actionLabel(event.action) }}</div>
                <div class="tl-meta">{{ event.actor }} · {{ fmtDateTime(event.created_at) }}</div>
                <div class="tiny muted mono">{{ JSON.stringify(event.detail) }}</div>
              </div>
            </div>
          </div>
        </div>
      </template>
    </template>
  </div>
</template>

<style scoped>
.per-segment { margin-top: 8px; border-top: 1px dashed var(--line); padding-top: 6px; }
.seg-row { display: grid; grid-template-columns: 120px 1fr auto; gap: 8px; font-size: 11px; padding: 2px 0; }
.det-line { display: flex; gap: 6px; align-items: center; flex-wrap: wrap; }
.ref-row { border: 1px solid var(--line); border-radius: 8px; padding: 8px 10px; }
.cert-row { border: 1px solid var(--line); border-radius: 8px; padding: 10px 12px; background: #fff; }
</style>

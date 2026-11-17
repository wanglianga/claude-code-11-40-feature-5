<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api'
import type { ComparisonRow, Overview, ProbabilityModel } from '../types'
import StatusBadge from '../components/StatusBadge.vue'
import ProbabilityBand from '../components/ProbabilityBand.vue'
import { DIVERGENCE_LABEL, fmtDate, fmtPct, statusLabel } from '../format'

const router = useRouter()
const overview = ref<Overview | null>(null)
const model = ref<ProbabilityModel | null>(null)
const sensitivity = ref<Array<{ threshold: number; delta: number; units_passing: number; delta_from_current: number }>>([])
const rows = ref<ComparisonRow[]>([])
const loading = ref(true)
const error = ref('')
const divergenceFilter = ref('')
const selected = ref<ComparisonRow | null>(null)
const calibrationDelta = ref(0)
const reevaluation = ref<Record<string, any> | null>(null)

async function load() {
  loading.value = true
  try {
    const runs = await api.get<Array<{ id: number; run_code: string }>>('/runs')
    const active = runs.find((r) => r.run_code === 'RUN-2026-SPRING-01') ?? runs[0]
    overview.value = await api.get<Overview>(`/runs/${active.id}/overview`)
    rows.value = overview.value.comparison_rows
    if (overview.value.model) {
      model.value = await api.get<ProbabilityModel>(`/models/${overview.value.model.id}`)
      sensitivity.value = (
        await api.get<{ rows: typeof sensitivity.value }>(
          `/probability/models/${overview.value.model.id}/threshold-sensitivity`,
        )
      ).rows
    }
  } catch (err) {
    error.value = err instanceof Error ? err.message : '加载失败'
  } finally {
    loading.value = false
  }
}

onMounted(load)

const filtered = computed(() => {
  const list = rows.value
  if (divergenceFilter.value === 'divergent') {
    return list.filter((r) => r.divergence !== 'both_support')
  }
  if (divergenceFilter.value) return list.filter((r) => r.divergence === divergenceFilter.value)
  return list
})

const counts = computed(() => ({
  total: rows.value.length,
  both: rows.value.filter((r) => r.divergence === 'both_support').length,
  probOnly: rows.value.filter((r) => r.divergence === 'probability_supports_rule_blocks').length,
  ruleOnly: rows.value.filter((r) => r.divergence === 'rule_supports_probability_below').length,
}))

async function evaluate(row: ComparisonRow) {
  if (!model.value) return
  selected.value = row
  reevaluation.value = null
  try {
    reevaluation.value = await api.post(
      `/probability/models/${model.value.id}/evaluate?unit_id=${row.unit_id}&calibration_delta=${calibrationDelta.value}`,
    )
  } catch (err) {
    error.value = err instanceof Error ? err.message : '估算失败'
  }
}

const maxPassing = computed(() =>
  Math.max(1, ...sensitivity.value.map((r) => r.units_passing)),
)
</script>

<template>
  <div>
    <div class="page-head">
      <div>
        <div class="eyebrow">Rule Certificate · Probability Estimate</div>
        <h1>规则证书 vs 概率估计</h1>
        <p class="page-sub">
          同一站点、同一季节的调查单元并列展示两种判断：逐项规则证书用于正式未发现结论；
          经历史样本校准的检测概率用于趋势研究，附带敏感区间与阈值抽查。概率结果不会被误读为已确认的实地覆盖。
        </p>
      </div>
    </div>

    <div v-if="loading" class="loading">加载中…</div>
    <div v-else-if="error" class="callout danger">{{ error }}</div>

    <template v-else>
      <div class="grid grid-4" style="margin-bottom: 14px">
        <div class="card tight">
          <div class="stat">
            <div class="stat-value">{{ counts.total }}</div>
            <div class="stat-label">可比较单元（有一致或分歧结论）</div>
          </div>
        </div>
        <div class="card tight">
          <div class="stat">
            <div class="stat-value" style="color: var(--pine)">{{ counts.both }}</div>
            <div class="stat-label">两者一致支持</div>
          </div>
        </div>
        <div class="card tight">
          <div class="stat">
            <div class="stat-value" style="color: var(--amber)">{{ counts.probOnly }}</div>
            <div class="stat-label">概率支持 · 规则排除</div>
            <div class="stat-hint">规则门槛未满足，正式结论仍以证书为准</div>
          </div>
        </div>
        <div class="card tight">
          <div class="stat">
            <div class="stat-value" style="color: var(--river)">{{ counts.ruleOnly }}</div>
            <div class="stat-label">规则支持 · 概率偏低</div>
            <div class="stat-hint">概率取决于校准集，需复核</div>
          </div>
        </div>
      </div>

      <div class="grid grid-2-1">
        <div class="card">
          <div class="card-head">
            <h2>并列对照</h2>
            <select v-model="divergenceFilter" class="select" style="width: 200px">
              <option value="">全部结论</option>
              <option value="divergent">只看分歧</option>
              <option value="probability_supports_rule_blocks">概率支持 · 规则排除</option>
              <option value="rule_supports_probability_below">规则支持 · 概率偏低</option>
              <option value="both_support">两者一致支持</option>
            </select>
          </div>

          <div v-if="!filtered.length" class="empty">当前筛选下没有单元。</div>
          <div v-else class="table-wrap" style="max-height: 460px; overflow-y: auto">
            <table>
              <thead>
                <tr>
                  <th>站点 / 日期</th>
                  <th>规则证书</th>
                  <th class="num">概率 (区间)</th>
                  <th>结论</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="row in filtered" :key="row.unit_id" :class="{ 'row-selected': selected?.unit_id === row.unit_id }">
                  <td>
                    <div>{{ row.station_code }} · {{ row.station_name }}</div>
                    <div class="tiny muted mono">{{ fmtDate(row.date) }}</div>
                  </td>
                  <td>
                    <StatusBadge :status="row.rule_status" :label="statusLabel(row.rule_status)" />
                    <div class="tiny muted">
                      {{ row.absence_eligible ? '正式未发现可用' : '正式未发现不可用' }}
                    </div>
                  </td>
                  <td class="num">
                    <span class="mono" style="font-size: 13px">{{ row.probability?.toFixed(2) ?? '—' }}</span>
                    <div class="tiny muted" v-if="row.interval">
                      [{{ row.interval[0].toFixed(2) }}, {{ row.interval[1].toFixed(2) }}]
                    </div>
                  </td>
                  <td>
                    <span class="badge" :class="row.divergence === 'both_support' ? 'valid' : row.divergence === 'probability_supports_rule_blocks' ? 'restricted' : 'pending'">
                      {{ DIVERGENCE_LABEL[row.divergence] }}
                    </span>
                  </td>
                  <td class="right">
                    <button class="btn ghost sm" @click="evaluate(row)">敏感度</button>
                    <button class="btn ghost sm" @click="router.push('/units/' + row.unit_id)">单元</button>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>

        <div class="stack">
          <div class="card">
            <div class="card-head">
              <h2>阈值敏感性</h2>
              <span class="chip" v-if="model">模型阈值 {{ model.threshold.toFixed(2) }}</span>
            </div>
            <div v-if="!sensitivity.length" class="empty">未附加概率模型。</div>
            <template v-else>
              <div class="sens-rows">
                <div v-for="row in sensitivity" :key="row.threshold" class="sens-row" :class="{ current: row.delta_from_current === 0 }">
                  <span class="mono tiny">阈值 {{ row.threshold.toFixed(2) }}</span>
                  <div class="sens-bar">
                    <div :style="{ width: (row.units_passing / maxPassing) * 100 + '%' }"></div>
                  </div>
                  <span class="mono tiny">{{ row.units_passing }} 单元</span>
                </div>
              </div>
              <div class="callout warn" style="margin-top: 10px">
                阈值上浮会减少可用趋势样本；模型校准与阈值复核成本需登记。结论会随校准集变化，引用必须保留模型版本与阈值。
              </div>
              <div v-if="model" class="tiny muted" style="margin-top: 8px">
                校准集：
                <span v-for="cal in model.calibration_sets" :key="cal.label" class="chip" style="margin-right: 4px">
                  {{ cal.label }}
                </span>
              </div>
            </template>
          </div>

          <div class="card">
            <div class="card-head"><h2>单单元敏感度复算</h2></div>
            <div v-if="!selected" class="empty">在左侧选择“敏感度”以复算该单元。</div>
            <template v-else-if="reevaluation">
              <div class="tiny muted">
                {{ selected.station_code }} · {{ selected.date }} · 模型 {{ reevaluation.model_code }} v{{ reevaluation.model_version }}
              </div>
              <div class="prob-head" style="margin-top: 6px">
                <span class="prob-value">{{ Number(reevaluation.probability).toFixed(2) }}</span>
                <span class="small muted">
                  区间 {{ Number(reevaluation.interval_low).toFixed(2) }}–{{ Number(reevaluation.interval_high).toFixed(2) }}
                </span>
              </div>
              <ProbabilityBand
                :probability="Number(reevaluation.probability)"
                :low="Number(reevaluation.interval_low)"
                :high="Number(reevaluation.interval_high)"
                :threshold="Number(reevaluation.threshold)"
              />
              <div class="field" style="margin-top: 10px">
                <label>切换校准集（pivot 偏移）</label>
                <div class="toolbar">
                  <select v-model.number="calibrationDelta" class="select" style="width: 220px">
                    <option :value="0">当前校准集</option>
                    <option
                      v-for="cal in reevaluation.calibration_sets"
                      :key="cal.label"
                      :value="cal.pivot_delta"
                    >
                      {{ cal.label }}（Δ={{ cal.pivot_delta }}）
                    </option>
                  </select>
                  <button class="btn sm" @click="evaluate(selected)">复算</button>
                </div>
              </div>
              <div class="variant-row" v-for="v in reevaluation.variants" :key="v.key">
                <span>{{ v.label }}</span>
                <span class="mono">{{ Number(v.probability).toFixed(3) }}</span>
                <span class="badge" :class="v.passes ? 'pass' : 'fail'">{{ v.passes ? '过阈值' : '未过' }}</span>
              </div>
              <div class="callout info" style="margin-top: 8px">{{ reevaluation.disclaimer }}</div>
            </template>
          </div>
        </div>
      </div>
    </template>
  </div>
</template>

<style scoped>
.row-selected { background: #f4f7f3; }
.sens-rows { display: flex; flex-direction: column; gap: 6px; }
.sens-row {
  display: grid;
  grid-template-columns: 76px 1fr 64px;
  gap: 8px;
  align-items: center;
}
.sens-row.current { font-weight: 600; color: var(--pine); }
.sens-bar { height: 8px; border-radius: 4px; background: var(--surface-2); overflow: hidden; }
.sens-bar div { height: 100%; background: linear-gradient(90deg, #8fb9a3, var(--pine-mid)); }
</style>

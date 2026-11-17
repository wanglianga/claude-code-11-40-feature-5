<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { api } from '../api'
import type { TrendReference } from '../types'
import StatusBadge from '../components/StatusBadge.vue'
import { fmtDateTime, fmtPct } from '../format'

const refs = ref<TrendReference[]>([])
const loading = ref(true)
const error = ref('')
const purposeFilter = ref('')
const statusFilter = ref('')

async function load() {
  loading.value = true
  try {
    const params = new URLSearchParams()
    if (purposeFilter.value) params.set('purpose', purposeFilter.value)
    if (statusFilter.value) params.set('status', statusFilter.value)
    refs.value = await api.get<TrendReference[]>(`/trend-references?${params}`)
  } catch (err) {
    error.value = err instanceof Error ? err.message : '加载失败'
  } finally {
    loading.value = false
  }
}

onMounted(load)

const summary = computed(() => ({
  active: refs.value.filter((r) => r.status === 'active').length,
  suspended: refs.value.filter((r) => r.status === 'suspended').length,
  absence: refs.value.filter((r) => r.purpose === 'absence').length,
  trend: refs.value.filter((r) => r.purpose === 'trend').length,
}))

function statusBadge(status: string): { status: string; label: string } {
  if (status === 'active') return { status: 'valid', label: '活动' }
  if (status === 'suspended') return { status: 'pending', label: '已暂停' }
  return { status: 'invalid', label: '已撤回' }
}
</script>

<template>
  <div>
    <div class="page-head">
      <div>
        <div class="eyebrow">Trend Inputs</div>
        <h1>趋势输入清单</h1>
        <p class="page-sub">
          趋势输入只按证书用途选择单元：正式未发现结论仅使用有效调查证书；趋势研究可引用受限调查单元，
          并同时保留概率估计与敏感区间。复审中的证书暂停新引用，已签发快照不回溯篡改。
        </p>
      </div>
      <div class="toolbar">
        <select v-model="purposeFilter" class="select" style="width: 160px" @change="load">
          <option value="">全部用途</option>
          <option value="absence">正式未发现结论</option>
          <option value="trend">趋势输入</option>
        </select>
        <select v-model="statusFilter" class="select" style="width: 140px" @change="load">
          <option value="">全部状态</option>
          <option value="active">活动</option>
          <option value="suspended">已暂停</option>
          <option value="revoked">已撤回</option>
        </select>
      </div>
    </div>

    <div v-if="error" class="callout danger">{{ error }}</div>
    <div v-if="loading" class="loading">加载中…</div>

    <template v-else>
      <div class="grid grid-4" style="margin-bottom: 14px">
        <div class="card tight"><div class="stat"><div class="stat-value">{{ summary.active }}</div><div class="stat-label">活动引用</div></div></div>
        <div class="card tight"><div class="stat"><div class="stat-value" style="color: var(--slate)">{{ summary.suspended }}</div><div class="stat-label">因复审暂停</div></div></div>
        <div class="card tight"><div class="stat"><div class="stat-value">{{ summary.absence }}</div><div class="stat-label">正式未发现结论引用</div></div></div>
        <div class="card tight"><div class="stat"><div class="stat-value">{{ summary.trend }}</div><div class="stat-label">趋势输入引用</div></div></div>
      </div>

      <div v-if="!refs.length" class="card"><div class="empty">尚无趋势引用。可在调查单元详情页登记。</div></div>
      <div v-else class="card" style="padding: 0">
        <div class="table-wrap">
          <table>
            <thead>
              <tr>
                <th>引用编号</th>
                <th>用途</th>
                <th>站点 / 日期</th>
                <th>引用时证书</th>
                <th class="num">概率</th>
                <th>状态</th>
                <th>登记</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="ref in refs" :key="ref.id">
                <td class="mono tiny">{{ ref.reference_code }}</td>
                <td>
                  <span class="badge" :class="ref.purpose === 'absence' ? 'valid' : 'pending'">
                    {{ ref.purpose_label }}
                  </span>
                </td>
                <td>
                  <div class="small">{{ ref.station_code }} · {{ ref.station_name }}</div>
                  <div class="tiny muted mono">{{ ref.unit_date }}</div>
                </td>
                <td class="small">
                  <div class="mono tiny">{{ ref.snapshot.certificate_no }}</div>
                  <div class="tiny muted">
                    {{ ref.snapshot.certificate_status }} · 复审 {{ ref.snapshot.review_status }}
                  </div>
                </td>
                <td class="num mono small">
                  {{ ref.snapshot.probability?.toFixed?.(3) ?? '—' }}
                  <div v-if="ref.snapshot.interval" class="tiny muted">
                    [{{ Number(ref.snapshot.interval[0]).toFixed(2) }}, {{ Number(ref.snapshot.interval[1]).toFixed(2) }}]
                  </div>
                </td>
                <td>
                  <StatusBadge :status="statusBadge(ref.status).status" :label="statusBadge(ref.status).label" />
                  <div v-if="ref.suspended_reason" class="tiny" style="color: var(--rust); max-width: 220px">
                    {{ ref.suspended_reason }}
                  </div>
                </td>
                <td class="small">
                  <div>{{ ref.created_by }}</div>
                  <div class="tiny muted">{{ fmtDateTime(ref.created_at) }}</div>
                </td>
                <td class="right">
                  <router-link :to="'/units/' + ref.unit_id" class="btn ghost sm">单元</router-link>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      <div class="grid grid-2" style="margin-top: 14px">
        <div class="callout pine">
          <div class="callout-title">用途边界</div>
          正式未发现结论只接受有效调查证书；受限调查证书可进入趋势输入但不支持未发现结论；
          待核与未有效调查证书均不进入趋势输入。
        </div>
        <div class="callout warn">
          <div class="callout-title">模型引用提示</div>
          引用概率结果时必须同时保留模型版本、阈值与校准集；概率估计随校准集变化，
          不构成已确认的实地覆盖。
        </div>
      </div>
    </template>
  </div>
</template>

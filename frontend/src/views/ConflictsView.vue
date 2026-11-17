<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { api } from '../api'
import { useToast } from '../toast'
import { authState } from '../session'
import type { Conflict } from '../types'
import StatusBadge from '../components/StatusBadge.vue'
import { fmtDateTime, VERDICT_LABEL } from '../format'

const { push } = useToast()
const conflicts = ref<Conflict[]>([])
const loading = ref(true)
const error = ref('')
const filter = ref<'open' | 'resolved' | ''>('open')
const resolution = ref<Record<number, string>>({})

async function load() {
  loading.value = true
  try {
    conflicts.value = await api.get<Conflict[]>(
      `/conflicts${filter.value ? `?status=${filter.value}` : ''}`,
    )
  } catch (err) {
    error.value = err instanceof Error ? err.message : '加载失败'
  } finally {
    loading.value = false
  }
}

onMounted(load)

const open = computed(() => conflicts.value.filter((c) => c.status === 'open'))

async function resolve(conflict: Conflict) {
  const text = (resolution.value[conflict.id] || '').trim()
  if (!text) {
    push('请填写处置说明', 'error')
    return
  }
  try {
    await api.post(`/conflicts/${conflict.id}/resolve`, { resolution: text })
    push('冲突已处置，冲突记录保留')
    resolution.value[conflict.id] = ''
    await load()
  } catch (err) {
    push(err instanceof Error ? err.message : '处置失败', 'error')
  }
}
</script>

<template>
  <div>
    <div class="page-head">
      <div>
        <div class="eyebrow">Reading Conflicts</div>
        <h1>判读冲突</h1>
        <p class="page-sub">
          两个来源对同一素材给出互斥判读且均未达置信门槛时，系统保留冲突，不作已发现或未发现结论。
          处置只记录说明，不修改原始判读，也不生成派工或复核任务。
        </p>
      </div>
      <div class="toolbar">
        <select v-model="filter" class="select" style="width: 140px" @change="load">
          <option value="open">未决</option>
          <option value="resolved">已处置</option>
          <option value="">全部</option>
        </select>
      </div>
    </div>

    <div v-if="error" class="callout danger">{{ error }}</div>
    <div v-if="loading" class="loading">加载中…</div>

    <div v-else-if="!conflicts.length" class="card">
      <div class="empty">当前筛选下没有判读冲突。</div>
    </div>

    <div v-else class="stack">
      <div v-for="c in conflicts" :key="c.id" class="card">
        <div class="card-head">
          <h2>
            <span class="mono">{{ c.species_code }}</span>
            <StatusBadge
              :status="c.status === 'open' ? 'pending' : 'valid'"
              :label="c.status === 'open' ? '未决 · 保留冲突' : '已处置'"
            />
          </h2>
          <div class="flex">
            <span class="chip">{{ c.station_code }} · {{ c.unit_date }}</span>
            <router-link :to="'/units/' + c.unit_id" class="btn ghost sm">查看单元</router-link>
          </div>
        </div>

        <div class="small" style="margin-bottom: 10px">{{ c.detail }}</div>
        <div class="grid grid-2" style="gap: 10px">
          <div v-for="s in c.sources" :key="s.determination_id" class="source-card">
            <div class="between">
              <span class="chip">{{ s.source }}</span>
              <span
                class="badge"
                :class="s.verdict === 'present' ? 'invalid' : s.verdict === 'absent' ? 'valid' : 'neutral'"
              >
                {{ VERDICT_LABEL[s.verdict] || s.verdict }}
              </span>
            </div>
            <div class="small" style="margin-top: 4px">判读者：{{ s.analyst || '—' }}</div>
            <div class="tiny muted">置信度 {{ Number(s.confidence).toFixed(2) }} · 判读 #{{ s.determination_id }}</div>
          </div>
        </div>

        <div class="divider"></div>
        <div class="between tiny muted">
          <span>素材 {{ c.material_external_id }} · 检出 {{ fmtDateTime(c.detected_at) }}</span>
        </div>

        <template v-if="c.status === 'open' && authState.isWriter.value">
          <div class="flex" style="margin-top: 10px">
            <input
              v-model="resolution[c.id]"
              class="input grow"
              placeholder="处置说明：例如调取原始 RAW 复核、统一判读口径（不修改原始冲突记录）"
            />
            <button class="btn primary sm" @click="resolve(c)">记录处置</button>
          </div>
        </template>
        <div v-else-if="c.resolution" class="callout pine" style="margin-top: 10px">
          <div class="callout-title">处置记录 · {{ fmtDateTime(c.resolved_at) }}</div>
          {{ c.resolution }}
        </div>
      </div>
    </div>

    <div class="callout info" style="margin-top: 14px">
      未决冲突会使对应调查单元的素材判读门槛保持“待核”，该单元证书不可用于正式未发现结论，
      并暂停新趋势引用。冲突本身作为证据保留，供监管人员解释。
    </div>
  </div>
</template>

<style scoped>
.source-card { border: 1px solid var(--line); border-radius: 8px; padding: 8px 10px; background: #fff; }
</style>

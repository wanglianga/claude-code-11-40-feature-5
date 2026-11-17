<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { api } from '../api'
import { useToast } from '../toast'
import { authState } from '../session'
import type { ArchiveRecord, Station } from '../types'
import {
  fmtDateTime,
  RECORD_TYPE_LABEL,
  VERDICT_LABEL,
} from '../format'

const { push } = useToast()
const items = ref<ArchiveRecord[]>([])
const stations = ref<Station[]>([])
const total = ref(0)
const page = ref(0)
const limit = 40
const typeFilter = ref('')
const stationFilter = ref<number | ''>('')
const search = ref('')
const loading = ref(true)
const error = ref('')
const detail = ref<ArchiveRecord | null>(null)
const editing = ref(false)
const editReason = ref('')
const editPayload = ref('')

async function load() {
  loading.value = true
  error.value = ''
  try {
    const params = new URLSearchParams()
    if (typeFilter.value) params.set('record_type', typeFilter.value)
    if (stationFilter.value) params.set('station_id', String(stationFilter.value))
    if (search.value.trim()) params.set('external_id', search.value.trim())
    params.set('limit', String(limit))
    params.set('offset', String(page.value * limit))
    const res = await api.get<{ total: number; items: ArchiveRecord[] }>(`/records?${params}`)
    items.value = res.items
    total.value = res.total
  } catch (err) {
    error.value = err instanceof Error ? err.message : '加载失败'
  } finally {
    loading.value = false
  }
}

onMounted(async () => {
  stations.value = await api.get<Station[]>('/stations')
  await load()
})

watch([typeFilter, stationFilter], () => {
  page.value = 0
  load()
})

const pageCount = computed(() => Math.max(1, Math.ceil(total.value / limit)))

async function openDetail(id: number) {
  editing.value = false
  try {
    detail.value = await api.get<ArchiveRecord>(`/records/${id}`)
    editPayload.value = JSON.stringify(detail.value.payload, null, 2)
    editReason.value = ''
  } catch (err) {
    push(err instanceof Error ? err.message : '加载失败', 'error')
  }
}

async function submitRevision() {
  if (!detail.value) return
  if (!editReason.value.trim()) {
    push('请填写修订原因', 'error')
    return
  }
  let payload: Record<string, unknown>
  try {
    payload = JSON.parse(editPayload.value)
  } catch {
    push('载荷不是合法 JSON', 'error')
    return
  }
  try {
    const updated = await api.post<ArchiveRecord>(`/records/${detail.value.id}/revisions`, {
      payload,
      reason: editReason.value.trim(),
    })
    detail.value = updated
    editing.value = false
    push(`已保存修订 r${updated.revision}，历史载荷保留`)
    await load()
  } catch (err) {
    push(err instanceof Error ? err.message : '修订失败', 'error')
  }
}

function payloadPreview(record: ArchiveRecord): string {
  const p = record.payload
  if (p.image_ref) return String(p.image_ref)
  if (p.reason) return String(p.reason)
  if (p.observer) return `${p.observer} · ${p.route_code ?? ''}`
  if (p.heartbeat_seq !== undefined) return `心跳 #${p.heartbeat_seq} · 电量 ${p.battery_pct ?? '—'}%`
  if (p.source) return String(p.source)
  if (p.station_code) return `站点元数据 ${p.station_code}`
  return JSON.stringify(p).slice(0, 80)
}
</script>

<template>
  <div>
    <div class="page-head">
      <div>
        <div class="eyebrow">Archive</div>
        <h1>观测档案</h1>
        <p class="page-sub">
          保存相机事件、巡护轨迹、设备心跳、停机日志、物种判读、站点元数据与时间校准记录的原始载荷、
          事件时间、接收时间、来源设备、坐标精度、哈希与修订版本。修订不覆盖历史载荷。
        </p>
      </div>
    </div>

    <div class="card" style="margin-bottom: 14px">
      <div class="toolbar">
        <select v-model="typeFilter" class="select" style="width: 168px">
          <option value="">全部类型</option>
          <option v-for="(label, key) in RECORD_TYPE_LABEL" :key="key" :value="key">{{ label }}</option>
        </select>
        <select v-model="stationFilter" class="select" style="width: 190px">
          <option value="">全部站点</option>
          <option v-for="s in stations" :key="s.id" :value="s.id">{{ s.code }} · {{ s.name }}</option>
        </select>
        <input v-model="search" class="input search" placeholder="按外部编号搜索" @keyup.enter="page = 0; load()" />
        <button class="btn sm" @click="page = 0; load()">查询</button>
        <span class="grow"></span>
        <span class="tiny muted">共 {{ total }} 条</span>
      </div>
    </div>

    <div v-if="error" class="callout danger">{{ error }}</div>
    <div class="card" style="padding: 0">
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>类型</th>
              <th>外部编号</th>
              <th>站点</th>
              <th>事件时间</th>
              <th>接收时间</th>
              <th>来源设备</th>
              <th>坐标精度</th>
              <th>修订</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            <tr v-if="loading"><td colspan="9" class="empty">加载中…</td></tr>
            <tr v-else-if="!items.length"><td colspan="9" class="empty">没有符合条件的记录。</td></tr>
            <tr v-for="record in items" :key="record.id" style="cursor: pointer" @click="openDetail(record.id)">
              <td><span class="chip">{{ record.record_type_label }}</span></td>
              <td class="mono tiny">{{ record.external_id }}</td>
              <td class="small">{{ record.station_code ?? record.station_id }}</td>
              <td class="nowrap small">{{ fmtDateTime(record.event_time) }}</td>
              <td class="nowrap small">{{ fmtDateTime(record.received_time) }}</td>
              <td class="small">{{ record.source_device }}</td>
              <td class="small">{{ record.coord_precision_m ? `±${record.coord_precision_m} m` : '—' }}</td>
              <td><span class="chip">r{{ record.revision }}</span></td>
              <td class="right"><button class="btn ghost sm">查看</button></td>
            </tr>
          </tbody>
        </table>
      </div>
      <div class="between" style="padding: 10px 14px">
        <span class="tiny muted">第 {{ page + 1 }} / {{ pageCount }} 页</span>
        <div class="flex">
          <button class="btn sm" :disabled="page === 0" @click="page--; load()">上一页</button>
          <button class="btn sm" :disabled="page + 1 >= pageCount" @click="page++; load()">下一页</button>
        </div>
      </div>
    </div>

    <div v-if="detail" class="modal-backdrop" @click.self="detail = null">
      <div class="modal">
        <div class="between" style="margin-bottom: 10px">
          <div>
            <div class="eyebrow">{{ detail.record_type_label }} · r{{ detail.revision }}</div>
            <h2 class="mono" style="font-size: 15px">{{ detail.external_id }}</h2>
          </div>
          <button class="btn sm" @click="detail = null">关闭</button>
        </div>

        <dl class="kv">
          <dt>站点</dt><dd>{{ detail.station_code }}（#{{ detail.station_id }}）</dd>
          <dt>事件时间</dt><dd>{{ fmtDateTime(detail.event_time) }}</dd>
          <dt>接收时间</dt><dd>{{ fmtDateTime(detail.received_time) }}</dd>
          <dt>来源设备</dt><dd>{{ detail.source_device }} · {{ detail.device_type }}</dd>
          <dt>坐标</dt>
          <dd>
            <template v-if="detail.latitude !== null">
              {{ detail.latitude }}, {{ detail.longitude }} · 精度 ±{{ detail.coord_precision_m ?? '—' }} m
            </template>
            <template v-else>—</template>
          </dd>
          <dt>载荷哈希</dt><dd class="hash">{{ detail.payload_hash }}</dd>
          <dt>所属单元</dt>
          <dd>
            <span v-for="uid in detail.used_by_units" :key="uid" class="chip" style="margin-right: 4px">
              <router-link :to="'/units/' + uid">单元 #{{ uid }}</router-link>
            </span>
            <span v-if="!detail.used_by_units?.length" class="muted tiny">尚未被任何门槛引用</span>
          </dd>
          <dt>判读</dt>
          <dd>
            <div v-for="d in detail.determinations" :key="d.id" class="tiny">
              {{ d.source }} · {{ VERDICT_LABEL[d.verdict] }} · {{ d.species_code }} · {{ d.confidence }}
              <span v-if="d.status === 'superseded'" class="muted">（已替代）</span>
            </div>
            <span v-if="!detail.determinations?.length" class="muted tiny">无</span>
          </dd>
        </dl>

        <div class="divider"></div>
        <div class="between" style="margin-bottom: 6px">
          <h3>当前载荷</h3>
          <button
            v-if="authState.isWriter.value && detail.record_type !== 'station_metadata'"
            class="btn sm"
            @click="editing = !editing"
          >
            {{ editing ? '取消修订' : '修订载荷' }}
          </button>
        </div>
        <pre v-if="!editing" class="pre">{{ JSON.stringify(detail.payload, null, 2) }}</pre>
        <div v-else class="stack-sm">
          <textarea v-model="editPayload" class="textarea" style="min-height: 160px; font-family: var(--mono); font-size: 11px"></textarea>
          <input v-model="editReason" class="input" placeholder="修订原因（必填，写入修订日志与审计）" />
          <div class="flex">
            <button class="btn primary sm" @click="submitRevision">保存为新修订</button>
            <span class="tiny muted">历史载荷与哈希按修订号保留，不覆盖。</span>
          </div>
        </div>

        <div class="divider"></div>
        <h3 style="margin-bottom: 6px">修订历史</h3>
        <div class="stack-sm">
          <div v-for="rev in [...(detail.revisions || [])].reverse()" :key="rev.id" class="rev-row">
            <div class="between">
              <span class="chip">r{{ rev.revision_number }}</span>
              <span class="tiny muted">{{ rev.changed_by }} · {{ fmtDateTime(rev.created_at) }}</span>
            </div>
            <div class="tiny">{{ rev.change_reason }}</div>
            <div class="hash">{{ rev.payload_hash }}</div>
            <details>
              <summary class="tiny muted" style="cursor: pointer">查看该版本载荷</summary>
              <pre class="pre">{{ JSON.stringify(rev.payload, null, 2) }}</pre>
            </details>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.modal-backdrop {
  position: fixed;
  inset: 0;
  background: rgba(20, 30, 26, 0.42);
  display: grid;
  place-items: center;
  padding: 24px;
  z-index: 40;
}
.modal {
  background: var(--surface);
  border-radius: 12px;
  border: 1px solid var(--line);
  max-width: 760px;
  width: 100%;
  max-height: 86vh;
  overflow-y: auto;
  padding: 20px 22px;
  box-shadow: 0 30px 70px -30px rgba(0, 0, 0, 0.5);
}
.rev-row { border: 1px solid var(--line); border-radius: 8px; padding: 8px 10px; }
</style>

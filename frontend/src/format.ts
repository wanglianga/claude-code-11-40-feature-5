export const STATUS_LABEL: Record<string, string> = {
  valid: '有效调查',
  restricted: '受限调查',
  invalid: '未有效调查',
  pending: '待核',
  under_review: '复审中',
  pass: '通过',
  fail: '未满足',
}

export const GATE_LABEL: Record<string, string> = {
  device: '设备可用性',
  time: '时间可信度',
  space: '空间覆盖',
  reading: '素材判读',
}

export const RECORD_TYPE_LABEL: Record<string, string> = {
  camera_event: '相机事件',
  patrol_track: '巡护轨迹',
  device_heartbeat: '设备心跳',
  downtime_log: '停机日志',
  species_determination: '物种判读',
  station_metadata: '站点元数据',
  time_calibration: '时间校准',
}

export const VERDICT_LABEL: Record<string, string> = {
  present: '出现',
  absent: '未出现',
  uncertain: '不确定',
}

export const DIVERGENCE_LABEL: Record<string, string> = {
  both_support: '两者一致支持',
  probability_supports_rule_blocks: '概率支持 · 规则排除',
  rule_supports_probability_below: '规则支持 · 概率偏低',
}

export function statusClass(status?: string | null, reviewStatus?: string | null): string {
  if (reviewStatus === 'under_review') return 'under_review'
  return status || 'pending'
}

export function statusLabel(status?: string | null, reviewStatus?: string | null): string {
  if (reviewStatus === 'under_review') return '复审中'
  return STATUS_LABEL[status || 'pending'] || status || '—'
}

export function fmtPct(value?: number | null, digits = 0): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—'
  return `${(value * 100).toFixed(digits)}%`
}

export function fmtNum(value?: number | null, digits = 1): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—'
  return value.toFixed(digits)
}

export function fmtDateTime(value?: string | null): string {
  if (!value) return '—'
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return value
  return d.toLocaleString('zh-CN', {
    timeZone: 'Asia/Shanghai',
    hour12: false,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

export function fmtDate(value?: string | null): string {
  if (!value) return '—'
  return value.slice(0, 10)
}

export function fmtTime(value?: string | null): string {
  if (!value) return '—'
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return value
  return d.toLocaleTimeString('zh-CN', {
    timeZone: 'Asia/Shanghai',
    hour12: false,
    hour: '2-digit',
    minute: '2-digit',
  })
}

export function shortDate(value?: string): string {
  if (!value) return '—'
  const parts = value.slice(0, 10).split('-')
  return parts.length === 3 ? `${Number(parts[1])}/${Number(parts[2])}` : value
}

export function actionLabel(action: string): string {
  const map: Record<string, string> = {
    'run.assembled': '组装调查运行',
    'run.superseded': '运行被新版本替代',
    'certificate.issued': '签发证书',
    'certificate.under_review': '证书转入复审',
    'certificate.reissued': '重新签发证书',
    'record.revised': '修订档案记录',
    'determination.revised': '修订物种判读',
    'trend_reference.created': '登记趋势引用',
    'trend_reference.revoked': '撤回趋势引用',
    'reading_conflict.resolved': '处置判读冲突',
  }
  return map[action] || action
}

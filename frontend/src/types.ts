export type Status = 'valid' | 'restricted' | 'invalid' | 'pending' | 'under_review'
export type GateKey = 'device' | 'time' | 'space' | 'reading'

export interface User {
  id: number
  username: string
  display_name: string
  role: 'researcher' | 'auditor' | 'viewer'
}

export interface Gate {
  id: number
  gate: GateKey
  requirement_code: string
  status: 'pass' | 'restricted' | 'fail' | 'pending'
  measured: Record<string, any>
  threshold: Record<string, any>
  explanation: string
  rule_ref: string
  evidence: number[]
}

export interface Certificate {
  id: number
  unit_id: number
  run_id: number
  certificate_no: string
  version: number
  status: Status
  status_label: string
  absence_eligible: boolean
  trend_eligible: boolean
  review_status: 'normal' | 'under_review'
  review_status_label: string
  review_reason: string
  summary: string
  gate_snapshot: Array<Record<string, any>>
  conclusions: {
    absence_analysis?: {
      supported: boolean
      species_note: string
      confirmed_present: number
      confirmed_absent_readings: number
      pending_materials: number
      open_conflicts: number
      never_implies_absence: string[]
    }
    trend?: Record<string, any>
  }
  issued_at: string
  superseded_by_id: number | null
}

export interface EstimateVariant {
  key: string
  label: string
  probability: number
  low: number
  high: number
  passes: boolean
}

export interface ProbabilityEstimate {
  id: number
  unit_id: number
  model_id: number
  model_code: string
  model_name: string
  model_version: string
  model_status: string
  threshold: number
  probability: number
  interval_low: number
  interval_high: number
  threshold_pass: boolean
  conclusion: 'supports_partial' | 'below_threshold'
  variants: EstimateVariant[]
  assumptions: string[]
}

export interface Unit {
  id: number
  run_id: number
  station_id: number
  station_code: string
  station_name: string
  zone_id: number
  unit_date: string
  station_version_id: number
  rulebook_version_id: number
  model_version_id: number | null
  coverage_summary: Record<string, any>
  feature_vector: Record<string, any>
  gate_status: Record<GateKey, string>
  data_issues: Array<{ gate: string; gate_label: string; status: string; explanation: string }>
  assembled_at: string
  certificate: Certificate | null
  probability: ProbabilityEstimate | null
}

export interface TrendReference {
  id: number
  reference_code: string
  purpose: 'absence' | 'trend'
  purpose_label: string
  unit_id: number
  certificate_id: number | null
  status: 'active' | 'suspended' | 'revoked'
  snapshot: Record<string, any>
  created_by: string
  created_at: string
  suspended_reason: string
  suspended_at: string | null
  note: string
  station_code?: string
  station_name?: string
  unit_date?: string
}

export interface Determination {
  id: number
  material_record_id: number
  source: string
  species_code: string
  verdict: 'present' | 'absent' | 'uncertain'
  confidence: number
  analyst: string
  revision: number
  status: 'active' | 'superseded'
  notes: string
}

export interface EvidenceMaterial {
  id: number
  record_type: string
  record_type_label: string
  external_id: string
  event_time: string
  received_time: string
  source_device: string
  latitude: number | null
  longitude: number | null
  coord_precision_m: number | null
  payload: Record<string, any>
  payload_hash: string
  revision: number
  is_pending: boolean
  determinations: Determination[]
  conflicts: Array<{ id: number; sources: Array<Record<string, any>>; status: string }>
}

export interface UnitDetail extends Unit {
  station: StationDetail
  station_version: {
    id: number
    version_number: number
    buffer_width_m: number
    effective_from: string
    effective_to: string | null
    change_reason: string
    boundary: { type: string; coordinates: number[][][] }
  }
  rulebook: RulebookVersion
  gates: Gate[]
  certificate_history: Certificate[]
  conflicts: Array<{
    id: number
    species_code: string
    sources: Array<Record<string, any>>
    detail: string
    status: string
    resolution: string
    material_record_id: number
    detected_at: string
  }>
  trend_references: TrendReference[]
  evidence: {
    materials: EvidenceMaterial[]
    record_count: number
    determination_count: number
  }
  audit: AuditEntry[]
}

export interface Station {
  id: number
  code: string
  name: string
  zone_id: number
  zone: { code: string; name: string } | null
  latitude: number
  longitude: number
  elevation_min_m: number
  elevation_max_m: number
  status: string
  version_count: number
}

export interface StationDetail extends Station {
  versions: Array<{
    id: number
    version_number: number
    boundary: { type: string; coordinates: number[][][] }
    buffer_width_m: number
    centroid: { lat: number; lon: number }
    effective_from: string
    effective_to: string | null
    change_reason: string
  }>
}

export interface RulebookVersion {
  id: number
  rulebook_code: string
  rulebook_name: string
  version_number: number
  label: string
  season_code: string
  season_start: string
  season_end: string
  status: string
  target_species: Array<{ code: string; name: string; min_detections: number }>
  requirements: Record<string, any>
  route_segments: Array<Record<string, any>>
  elevation_bands: Array<Record<string, any>>
  notes: string
}

export interface ProbabilityModel {
  id: number
  code: string
  name: string
  version: string
  status: string
  threshold: number
  calibration_sets: Array<{ label: string; pivot_delta: number; n: number }>
  parameters: Record<string, any>
  threshold_review: Record<string, any>
  notes: string
  estimate_count?: number
}

export interface Run {
  id: number
  run_code: string
  label: string
  season_code: string
  season_start: string
  season_end: string
  status: string
  rulebook_version_id: number
  model_version_id: number | null
  created_by: string
  created_at: string
  unit_count: number
  status_counts: Record<string, number>
  open_conflicts: number
  trend_references: number
}

export interface ComparisonRow {
  unit_id: number
  station_code: string
  station_name: string
  date: string
  rule_status: Status
  absence_eligible: boolean
  probability: number | null
  interval: [number, number] | null
  threshold: number | null
  threshold_pass: boolean | null
  divergence: 'both_support' | 'probability_supports_rule_blocks' | 'rule_supports_probability_below'
}

export interface Overview {
  run: Run
  rulebook: RulebookVersion
  model: {
    id: number
    code: string
    version: string
    threshold: number
    status: string
    calibration_sets: Array<Record<string, any>>
  } | null
  unit_count: number
  station_count: number
  date_count: number
  dates: string[]
  status_counts: Record<string, number>
  gate_counts: Record<GateKey, Record<string, number>>
  stations: Array<Station & { units: Array<Record<string, any>> }>
  comparison_rows: ComparisonRow[]
  open_conflicts: number
  active_trend_references: number
  recent_activity: AuditEntry[]
}

export interface Conflict {
  id: number
  unit_id: number
  run_id: number
  material_record_id: number
  material_external_id: string | null
  station_code: string | null
  station_name: string | null
  unit_date: string | null
  species_code: string
  sources: Array<{
    source: string
    analyst: string
    verdict: string
    confidence: number
    determination_id: number
  }>
  detail: string
  status: 'open' | 'resolved'
  resolution: string
  detected_at: string
  resolved_at: string | null
}

export interface AuditEntry {
  id: number
  entity_type: string
  entity_id: number
  action: string
  actor: string
  detail: Record<string, any>
  created_at: string
}

export interface ArchiveRecord {
  id: number
  record_type: string
  record_type_label: string
  external_id: string
  station_id: number
  station_code: string | null
  event_time: string
  received_time: string
  source_device: string
  device_type: string
  latitude: number | null
  longitude: number | null
  coord_precision_m: number | null
  payload: Record<string, any>
  payload_hash: string
  revision: number
  record_status: string
  revisions?: Array<{
    id: number
    revision_number: number
    payload: Record<string, any>
    payload_hash: string
    received_time: string
    changed_by: string
    change_reason: string
    created_at: string
  }>
  determinations?: Determination[]
  used_by_units?: number[]
}

export interface SpatialRegion {
  id: number
  region_code: string
  extent_key: string
  component_key: string
  family: string
  status: 'published' | 'withheld'
  geometry: { type: string; coordinates: any }
  display_geometry: { type: string; coordinates: any }
  display_cell_m: number
  area_m2: number
  display_area_m2: number
  candidate_habitat_m2: number
  width_m: number
  base_cell_count: number
  candidate_cells: string[]
  base_cells: Array<{ cell_code: string; candidate_habitat_m2: number; observation_count: number; present_count: number }>
  effort_known: boolean
  effort_units: number
  merged_effort_windows: number
  reasons: Array<{ code: string; detail: string }>
  sensitivity_level: string
  threshold_snapshot: Record<string, any>
  observation_count: number
  present_count: number
  duplicate_materials: number
  effective_hours: number
  route_length_m: number
  effort_unknown_units: number
  occurrence_rate: number | null
  effort_blocks: Array<Record<string, any>>
}

export interface SpatialCheck {
  check_code: string
  status: 'pass' | 'fail'
  detail: string
  data: Record<string, any>
}

export interface SpatialPublication {
  id: number
  code: string
  species_code: string
  scope_code: string
  season_code: string
  status: string
  survey_run_id: number | null
  sensitivity_level: string
  sensitivity_version: number
  habitat_version: number
  suitability_version: number
  scope_version: number
  region_count: number
  published_region_count?: number
  withheld_region_count?: number
  checks_passed: boolean
  stop_reason: string
  stale_reason: string
  note: string
  created_by: string
  created_at: string | null
  published_at: string | null
}

export interface SpatialPublicationDetail {
  publication: SpatialPublication
  regions: SpatialRegion[]
  checks: SpatialCheck[]
}

export interface SpatialMapPayload {
  publication: {
    code: string
    species_code: string
    species_name: string
    season_code: string
    scope_code: string
    scope_name: string
    sensitivity_level: string
    sensitivity_version: number
    habitat_version: number
    suitability_version: number
    published_region_count: number
    withheld_region_count: number
    unattributed_observations: number
    status: string
  }
  map: {
    type: 'FeatureCollection'
    features: Array<{ type: 'Feature'; geometry: { type: string; coordinates: any }; properties: Record<string, any> }>
    disclosure: string
  }
  stats: {
    regions: Array<Record<string, any>>
    withheld: Array<{ region_code: string; reason_codes: string[]; candidate_habitat_ha: number }>
  }
}

export interface SpatialComparison {
  first: { code: string; season_code: string; sensitivity_level: string; display_cell_m: number | null }
  second: { code: string; season_code: string; sensitivity_level: string; display_cell_m: number | null }
  shared_extent_count: number
  comparable_rows: Array<{
    extent_key: string
    region_code_first: string
    region_code_second: string
    candidate_habitat_ha: [number, number]
    observation_count: [number, number]
    present_count: [number, number]
    effective_hours: [number, number]
    route_length_km: [number, number]
    occurrence_rate: [number | null, number | null]
    delta_observations: number
  }>
  only_first: Array<Record<string, any>>
  only_second: Array<Record<string, any>>
  rule: string
}

export interface SpatialMaterial {
  material_code: string
  publication_code: string | null
  kind: 'map' | 'download'
  scope_code: string
  species_code: string
  status: string
  sensitivity_level: string
  content_hash: string
  regions: Array<{ region_code: string; extent_key: string; display_cell_m: number }>
  created_at: string | null
  withdrawn_at: string | null
  note: string
}

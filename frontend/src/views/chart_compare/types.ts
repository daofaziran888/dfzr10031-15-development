/** 图表分桶聚合接口的载荷类型，与后端 app/bucketing 输出保持一致。 */

export interface BucketStat {
  key: string
  label: string
  kind: 'finite' | 'underflow' | 'overflow'
  row_count: number
  value_min: number | null
  value_max: number | null
}

export interface MissingStat {
  count: number
  coordinates: string[]
}

export interface BucketingSpec {
  edges: number[]
  include_underflow: boolean
  include_overflow: boolean
  boundary_policy: string
}

export interface ChartSnapshot {
  snapshot_id: string
  chart_name: string
  source_name: string
  coordinate_field: string
  value_field: string
  spec: BucketingSpec
  buckets: BucketStat[]
  missing: MissingStat
  total_rows: number
  input_record_count: number
  bucketed_rows?: number
  input_fingerprint: string
  result_fingerprint: string
  created_at: string
}

export interface DataSource {
  name: string
  label: string
  description: string
  coordinate_field: string
  value_field: string
  record_count: number
  input_fingerprint: string
  records?: Record<string, string | number | null>[]
}

export type Comparability = 'comparable' | 'limited' | 'none'

export interface ComparisonRow {
  key: string
  label: string
  kind: 'finite' | 'underflow' | 'overflow'
  open_ended: boolean
  present_in_base: boolean
  present_in_target: boolean
  directly_comparable: boolean
  comparability: Comparability
  base_count: number | null
  target_count: number | null
  count_delta: number | null
  base_value_range: { min: number; max: number } | null
  target_value_range: { min: number; max: number } | null
  note: string
}

export interface ReconciliationItem {
  snapshot_id: string
  input_record_count: number
  bucketed_rows: number
  missing_rows: number
  accounted_rows: number
  total_rows: number
  balanced: boolean
}

export interface ComparisonReport {
  mode: 'replay' | 'overflow'
  mode_label: string
  base_snapshot_id: string
  target_snapshot_id: string
  spec_signature: BucketingSpec
  rows: ComparisonRow[]
  missing: { snapshot_id: string; missing_count: number; missing_coordinates: string[] }[]
  reconciliation: ReconciliationItem[]
  totals_match: {
    base_total: number
    target_total: number
    difference: number
    equal: boolean
    note: string
  }
  non_comparable_intervals: ComparisonRow[]
  summary: {
    bucket_slots: number
    comparable_buckets: number
    limited_comparable_buckets: number
    non_comparable_buckets: number
    base_total_rows: number
    target_total_rows: number
    target_overflow_rows: number
    base_overflow_rows: number
  }
}

export interface RecomputeResult {
  ok: boolean
  snapshot_id: string
  verification: {
    ok: boolean
    input_fingerprint_match: boolean
    result_fingerprint_match: boolean
    problems: string[]
  }
  rebuilt_result_fingerprint: string
  stored_result_fingerprint: string
}

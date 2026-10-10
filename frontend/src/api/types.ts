// Типы ответов и запросов API (вручную по OpenAPI-схеме бэкенда, /api/v1).
// Время приходит строкой ISO 8601 в UTC.

export type Locale = 'ru' | 'en'
export type IsoDate = string

export interface UserOut {
  id: string
  email: string
  locale: Locale
  timezone: string
  digest_enabled: boolean
  digest_weekday: number
  digest_hour: number
  created_at: IsoDate
}

export interface AuthResponse {
  access_token: string
  token_type: string
  expires_in: number
  user: UserOut
}

export interface PreferencesUpdate {
  locale?: Locale | null
  timezone?: string | null
  digest_enabled?: boolean | null
  digest_weekday?: number | null
  digest_hour?: number | null
}

export interface Page<T> {
  items: T[]
  total: number
  limit: number
  offset: number
}

export type VerificationStatus = 'pending' | 'verified' | 'unverified'
export type VerificationMethod = 'dns' | 'http'

export interface SiteOut {
  id: string
  name: string
  url: string
  verification_status: VerificationStatus
  verification_method: VerificationMethod | null
  verified_at: IsoDate | null
  last_verified_at: IsoDate | null
  created_at: IsoDate
}

export interface VerificationInstruction {
  method: VerificationMethod
  record_name: string
  record_value: string
}

export interface VerificationInfo {
  status: VerificationStatus
  method: VerificationMethod | null
  token: string
  last_verified_at: IsoDate | null
  reverify_days: number
  instructions: VerificationInstruction[]
}

// Профили нагрузки.
export interface ConstantProfile {
  type: 'constant'
  rate: number
  duration_sec: number
}

export interface RampUpProfile {
  type: 'ramp_up'
  start_rate: number
  end_rate: number
  ramp_sec: number
  hold_sec?: number
}

export interface StepProfile {
  type: 'step'
  start_rate: number
  step_rate: number
  step_duration_sec: number
  max_steps: number
  max_error_rate?: number
  max_p95_ms?: number | null
}

export type LoadProfile = ConstantProfile | RampUpProfile | StepProfile
export type LoadProfileType = LoadProfile['type']

export type HttpMethod = 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE' | 'HEAD'
export type BodyType = 'json' | 'form' | 'raw'
export type ExtractSource = 'json' | 'header' | 'cookie' | 'regex'

export interface ExtractRule {
  var: string
  source: ExtractSource
  expr: string
}

export interface StepBody {
  type: BodyType
  content: unknown
  content_type?: string | null
}

export interface RequestStep {
  name: string
  method?: HttpMethod
  path: string
  headers?: Record<string, string>
  body?: StepBody | null
  expected_status?: string[]
  assert_contains?: string | null
  extract?: ExtractRule[]
  think_ms?: number
}

export interface ScenarioIn {
  name: string
  load_profile: LoadProfile
  flow: RequestStep[]
  apdex_t_ms?: number
  slo_p95_ms?: number | null
  slo_max_error_rate?: number | null
}

export interface SecretOut {
  name: string
  value: string
}

export interface ScenarioOut {
  id: string
  site_id: string
  name: string
  load_profile: Record<string, unknown>
  effective_load_profile: Record<string, unknown>
  flow: RequestStep[]
  apdex_t_ms: number
  slo_p95_ms: number | null
  slo_max_error_rate: number | null
  secrets: SecretOut[]
  missing_secrets: string[]
  created_at: IsoDate
  updated_at: IsoDate
}

export interface StepResult {
  name: string
  method: string
  path: string
  ok: boolean
  status: number | null
  error: string | null
  detail: string | null
  duration_ms: number
  timings: Record<string, number>
  response_bytes: number
  truncated: boolean
  extracted: Record<string, string>
}

export interface DryRunOut {
  ok: boolean
  error?: string | null
  duration_ms: number
  steps: StepResult[]
  skipped_steps: string[]
}

export type RunStatus = 'queued' | 'running' | 'finished' | 'failed' | 'cancelled'
export type RunSource = 'manual' | 'schedule' | 'hook'

export interface RunMetricsBrief {
  requests: number
  rps: number
  error_rate: number
  latency_p50_ms: number
  latency_p95_ms: number
  latency_p99_ms: number
  apdex: number | null
  ceiling_rps: number | null
}

export type RegressionMetric = 'p95_ms' | 'error_rate' | 'apdex' | 'ceiling_rps'

export interface RegressionFinding {
  metric: RegressionMetric
  current: number
  baseline: number
  change_pct: number | null
  change_abs: number
  baseline_runs: number
  message_key: string
}

export interface Regression {
  detected: boolean
  baseline: Record<string, number | null>
  baseline_runs: number
  findings: RegressionFinding[]
}

export interface RunOut {
  id: string
  site_id: string
  scenario_id: string | null
  status: RunStatus
  source: RunSource
  label: string | null
  queued_at: IsoDate
  started_at: IsoDate | null
  finished_at: IsoDate | null
  stop_reason: string | null
  error: string | null
  generator_saturated: boolean
  slo_met: boolean | null
  cancel_requested: boolean
  metrics: RunMetricsBrief | null
  regression?: Regression | null
}

export interface LatencySummary {
  min: number
  avg: number
  max: number
  p50: number
  p90: number
  p95: number
  p99: number
}

export interface StatsSummary {
  requests: number
  errors: number
  error_rate: number
  rps: number
  latency_ms: LatencySummary
  timings: Record<string, number>
  apdex: number | null
  status_codes: Record<string, number>
  errors_top: { type: string; count: number }[]
}

export interface StepLoadStep {
  step: number
  target_rate: number
  rps: number
  error_rate: number
  p95_ms: number
  passed: boolean
}

export interface StepLoadResult {
  ceiling_rps: number | null
  reason: string
  ceiling_is_lower_bound: boolean
  steps: StepLoadStep[]
}

export interface RunSummary extends StatsSummary {
  duration_sec: number
  iterations?: {
    total: number
    ok: number
    failed: number
    dropped: number
    aborted: number
    per_sec: number
  }
  bytes?: { sent: number; received: number }
  steps?: (StatsSummary & { name: string })[]
  stop_reason?: string | null
  step_load?: StepLoadResult
}

export interface SeriesPoint {
  t: number
  rps: number
  iterations_per_sec: number
  p95_ms: number
  errors: number
  error_rate: number
}

export interface RunDetailOut extends RunOut {
  params: Record<string, unknown> | null
  summary: RunSummary | null
  series: SeriesPoint[] | null
}

export interface RunCreate {
  scenario_id: string
  label?: string | null
}

export interface ScheduleIn {
  scenario_id: string
  cron: string
  timezone: string
  enabled: boolean
}

export interface ScheduleOut {
  scenario_id: string | null
  cron: string
  timezone: string
  enabled: boolean
  next_run_at: IsoDate | null
  last_run_at: IsoDate | null
  upcoming: IsoDate[]
}

export interface MetricPoint {
  run_id: string
  scenario_id: string | null
  source: RunSource
  label: string | null
  started_at: IsoDate
  rps: number
  error_rate: number
  latency_p50_ms: number
  latency_p95_ms: number
  latency_p99_ms: number
  apdex: number | null
  ceiling_rps: number | null
  slo_met: boolean | null
  generator_saturated: boolean
  regression_detected: boolean
  status_codes: Record<string, number>
  timings: Record<string, number>
}

export interface SiteMetricsOut {
  from: IsoDate
  to: IsoDate
  points: MetricPoint[]
  truncated: boolean
}

export interface SiteSummaryOut {
  last_run: RunOut | null
  baseline: Record<string, number | null> | null
  regression: Regression | null
  ceiling_rps: number | null
  ceiling_is_lower_bound: boolean | null
  slo_met_pct: number | null
  runs_in_period: number
  period_days: number
}

export type ChannelType = 'telegram' | 'email' | 'slack' | 'discord' | 'webhook'
export type ChannelEvent =
  | 'regression'
  | 'slo_breach'
  | 'run_failed'
  | 'site_unverified'
  | 'recovery'
  | 'digest'

export interface ChannelIn {
  type: ChannelType
  name: string
  config: Record<string, string>
  events: ChannelEvent[]
  enabled: boolean
}

export interface ChannelOut {
  id: string
  type: ChannelType
  name: string
  config: Record<string, string>
  events: ChannelEvent[]
  enabled: boolean
  created_at: IsoDate
  signing_secret?: string | null
}

export interface ChannelTestOut {
  ok: boolean
  error: string | null
}

export interface TriggerTokenOut {
  token: string
  example: string
}

export interface AnnotationOut {
  id: string
  kind: 'deploy' | 'comment'
  label: string
  at: IsoDate
  run_id: string | null
}

export type AlertType = 'regression' | 'slo_breach' | 'run_failed' | 'site_unverified'

export interface AlertOut {
  id: string
  type: AlertType
  status: 'open' | 'resolved'
  run_id: string | null
  details: Record<string, unknown> | null
  opened_at: IsoDate
  last_notified_at: IsoDate | null
  resolved_at: IsoDate | null
}

export interface ValidationIssue {
  loc: (string | number)[]
  msg: string
  type: string
}

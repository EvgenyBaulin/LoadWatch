// Таблицы соответствий «значение из API → ключ перевода».
// Ключи обёрнуты в tk(), поэтому `npm run i18n:check` проверяет, что они есть в локалях.

import type {
  AlertType,
  BodyType,
  ChannelEvent,
  ChannelType,
  ExtractSource,
  LoadProfileType,
  RegressionMetric,
  RunSource,
  RunStatus,
  VerificationStatus,
} from '@/api/types'
import { tk } from './index'

export const ERROR_KEYS: Record<string, string> = {
  not_authenticated: tk('errors.not_authenticated'),
  invalid_credentials: tk('errors.invalid_credentials'),
  email_taken: tk('errors.email_taken'),
  registration_closed: tk('errors.registration_closed'),
  invalid_invite_code: tk('errors.invalid_invite_code'),
  rate_limited: tk('errors.rate_limited'),
  not_found: tk('errors.not_found'),
  invalid_site_url: tk('errors.invalid_site_url'),
  target_forbidden: tk('errors.target_forbidden'),
  site_exists: tk('errors.site_exists'),
  verification_failed: tk('errors.verification_failed'),
  scenario_exists: tk('errors.scenario_exists'),
  site_not_verified: tk('errors.site_not_verified'),
  target_unresolvable: tk('errors.target_unresolvable'),
  secrets_unavailable: tk('errors.secrets_unavailable'),
  invalid_schedule: tk('errors.invalid_schedule'),
  run_in_progress: tk('errors.run_in_progress'),
  cooldown_active: tk('errors.cooldown_active'),
  missing_secrets: tk('errors.missing_secrets'),
  run_not_cancellable: tk('errors.run_not_cancellable'),
  invalid_period: tk('errors.invalid_period'),
  invalid_channel: tk('errors.invalid_channel'),
  invalid_trigger_token: tk('errors.invalid_trigger_token'),
  scenario_required: tk('errors.scenario_required'),
  run_has_no_metrics: tk('errors.run_has_no_metrics'),
  validation_error: tk('errors.validation_error'),
  method_not_allowed: tk('errors.method_not_allowed'),
  internal_error: tk('errors.internal_error'),
  network_error: tk('errors.network_error'),
  forbidden: tk('errors.forbidden'),
  payload_too_large: tk('errors.payload_too_large'),
  stale_heartbeat: tk('errors.stale_heartbeat'),
}

export const VERIFICATION_REASON_KEYS: Record<string, string> = {
  token_not_found: tk('errors.verification.token_not_found'),
  target_forbidden: tk('errors.verification.target_forbidden'),
  error: tk('errors.verification.error'),
}

export const VALIDATION_TYPE_KEYS: Record<string, string> = {
  missing: tk('validation.missing'),
  string_too_short: tk('validation.string_too_short'),
  string_too_long: tk('validation.string_too_long'),
  string_pattern_mismatch: tk('validation.string_pattern_mismatch'),
  greater_than: tk('validation.greater_than'),
  greater_than_equal: tk('validation.greater_than_equal'),
  less_than: tk('validation.less_than'),
  less_than_equal: tk('validation.less_than_equal'),
  too_long: tk('validation.too_long'),
  too_short: tk('validation.too_short'),
  int_parsing: tk('validation.number'),
  float_parsing: tk('validation.number'),
  int_type: tk('validation.number'),
  float_type: tk('validation.number'),
  value_error: tk('validation.value_error'),
  literal_error: tk('validation.literal_error'),
  enum: tk('validation.literal_error'),
  json_invalid: tk('validation.json_invalid'),
}

export const RUN_STATUS_KEYS: Record<RunStatus, string> = {
  queued: tk('run.status.queued'),
  running: tk('run.status.running'),
  finished: tk('run.status.finished'),
  failed: tk('run.status.failed'),
  cancelled: tk('run.status.cancelled'),
}

export const RUN_SOURCE_KEYS: Record<RunSource, string> = {
  manual: tk('run.source.manual'),
  schedule: tk('run.source.schedule'),
  hook: tk('run.source.hook'),
}

export const VERIFICATION_STATUS_KEYS: Record<VerificationStatus, string> = {
  pending: tk('sites.status.pending'),
  verified: tk('sites.status.verified'),
  unverified: tk('sites.status.unverified'),
}

export const CHANNEL_TYPE_KEYS: Record<ChannelType, string> = {
  telegram: tk('channels.types.telegram'),
  email: tk('channels.types.email'),
  slack: tk('channels.types.slack'),
  discord: tk('channels.types.discord'),
  webhook: tk('channels.types.webhook'),
}

export const CHANNEL_EVENT_KEYS: Record<ChannelEvent, string> = {
  regression: tk('channels.events.regression'),
  slo_breach: tk('channels.events.slo_breach'),
  run_failed: tk('channels.events.run_failed'),
  site_unverified: tk('channels.events.site_unverified'),
  recovery: tk('channels.events.recovery'),
  digest: tk('channels.events.digest'),
}

export const ALERT_TYPE_KEYS: Record<AlertType, string> = {
  regression: tk('alerts.types.regression'),
  slo_breach: tk('alerts.types.slo_breach'),
  run_failed: tk('alerts.types.run_failed'),
  site_unverified: tk('alerts.types.site_unverified'),
}

export const REGRESSION_METRIC_KEYS: Record<RegressionMetric, string> = {
  p95_ms: tk('regression.p95_ms'),
  error_rate: tk('regression.error_rate'),
  apdex: tk('regression.apdex'),
  ceiling_rps: tk('regression.ceiling_rps'),
}

export const STOP_REASON_KEYS: Record<string, string> = {
  completed: tk('run.stopReason.completed'),
  cancelled: tk('run.stopReason.cancelled'),
  bandwidth_limit: tk('run.stopReason.bandwidth_limit'),
  'profile:threshold_exceeded': tk('run.stopReason.threshold_exceeded'),
  'profile:generator_saturated': tk('run.stopReason.generator_saturated'),
  'profile:max_steps_reached': tk('run.stopReason.max_steps_reached'),
  threshold_exceeded: tk('run.stopReason.threshold_exceeded'),
  generator_saturated: tk('run.stopReason.generator_saturated'),
  max_steps_reached: tk('run.stopReason.max_steps_reached'),
  incomplete: tk('run.stopReason.incomplete'),
}

/** Типы ошибок запросов (из сводки и dry-run). */
export const REQUEST_ERROR_KEYS: Record<string, string> = {
  connect_timeout: tk('requestErrors.connect_timeout'),
  read_timeout: tk('requestErrors.read_timeout'),
  write_timeout: tk('requestErrors.write_timeout'),
  pool_timeout: tk('requestErrors.pool_timeout'),
  tls_error: tk('requestErrors.tls_error'),
  connect_error: tk('requestErrors.connect_error'),
  protocol_error: tk('requestErrors.protocol_error'),
  read_error: tk('requestErrors.read_error'),
  client_error: tk('requestErrors.client_error'),
  body_too_large: tk('requestErrors.body_too_large'),
  missing_secret: tk('requestErrors.missing_secret'),
  missing_variable: tk('requestErrors.missing_variable'),
  invalid_header: tk('requestErrors.invalid_header'),
  assert_failed: tk('requestErrors.assert_failed'),
  extract_failed: tk('requestErrors.extract_failed'),
  target_forbidden: tk('requestErrors.target_forbidden'),
  request_deadline: tk('requestErrors.request_deadline'),
  dry_run_timeout: tk('requestErrors.dry_run_timeout'),
}

export const PROFILE_TYPE_KEYS: Record<LoadProfileType, string> = {
  constant: tk('editor.profile.types.constant'),
  ramp_up: tk('editor.profile.types.ramp_up'),
  step: tk('editor.profile.types.step'),
}

export const BODY_TYPE_KEYS: Record<BodyType | 'none', string> = {
  none: tk('editor.body.types.none'),
  json: tk('editor.body.types.json'),
  form: tk('editor.body.types.form'),
  raw: tk('editor.body.types.raw'),
}

export const EXTRACT_SOURCE_KEYS: Record<ExtractSource, string> = {
  json: tk('editor.extract.sources.json'),
  header: tk('editor.extract.sources.header'),
  cookie: tk('editor.extract.sources.cookie'),
  regex: tk('editor.extract.sources.regex'),
}

/** Подписи полей профиля нагрузки и эффективного профиля. */
export const PROFILE_FIELD_KEYS: Record<string, string> = {
  type: tk('editor.profile.fields.type'),
  rate: tk('editor.profile.fields.rate'),
  duration_sec: tk('editor.profile.fields.duration_sec'),
  start_rate: tk('editor.profile.fields.start_rate'),
  end_rate: tk('editor.profile.fields.end_rate'),
  ramp_sec: tk('editor.profile.fields.ramp_sec'),
  hold_sec: tk('editor.profile.fields.hold_sec'),
  step_rate: tk('editor.profile.fields.step_rate'),
  step_duration_sec: tk('editor.profile.fields.step_duration_sec'),
  max_steps: tk('editor.profile.fields.max_steps'),
  max_error_rate: tk('editor.profile.fields.max_error_rate'),
  max_p95_ms: tk('editor.profile.fields.max_p95_ms'),
}

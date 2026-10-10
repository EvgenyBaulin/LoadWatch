// Модель формы редактора сценария и преобразование в/из формата API.

import type {
  BodyType,
  ExtractRule,
  HttpMethod,
  LoadProfile,
  LoadProfileType,
  RequestStep,
  ScenarioIn,
  ScenarioOut,
} from '@/api/types'

export const MAX_STEPS = 10
export const HTTP_METHODS: HttpMethod[] = ['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD']

export interface KeyValue {
  key: string
  value: string
}

export interface StepForm {
  uid: number
  name: string
  method: HttpMethod
  path: string
  headers: KeyValue[]
  bodyType: BodyType | 'none'
  bodyText: string
  bodyForm: KeyValue[]
  contentType: string
  extract: ExtractRule[]
  expectedStatus: string
  assertContains: string
  thinkMs: number
}

export interface ProfileForm {
  type: LoadProfileType
  rate: number
  duration_sec: number
  start_rate: number
  end_rate: number
  ramp_sec: number
  hold_sec: number
  step_start_rate: number
  step_rate: number
  step_duration_sec: number
  max_steps: number
  max_error_rate_pct: number
  max_p95_ms: number | null
}

export interface ScenarioForm {
  name: string
  profile: ProfileForm
  steps: StepForm[]
  apdexTMs: number
  sloP95Ms: number | null
  /** SLO по ошибкам в процентах (в API — доля 0…1). */
  sloErrorPct: number | null
}

let uidSeq = 0

export function newStep(index: number): StepForm {
  return {
    uid: ++uidSeq,
    name: `step-${index + 1}`,
    method: 'GET',
    path: '/',
    headers: [],
    bodyType: 'none',
    bodyText: '',
    bodyForm: [],
    contentType: '',
    extract: [],
    expectedStatus: '',
    assertContains: '',
    thinkMs: 0,
  }
}

export function defaultProfile(): ProfileForm {
  return {
    type: 'constant',
    rate: 5,
    duration_sec: 60,
    start_rate: 1,
    end_rate: 10,
    ramp_sec: 60,
    hold_sec: 30,
    step_start_rate: 5,
    step_rate: 5,
    step_duration_sec: 30,
    max_steps: 5,
    max_error_rate_pct: 5,
    max_p95_ms: null,
  }
}

export function emptyScenario(): ScenarioForm {
  return {
    name: '',
    profile: defaultProfile(),
    steps: [newStep(0)],
    apdexTMs: 500,
    sloP95Ms: null,
    sloErrorPct: null,
  }
}

function num(value: unknown, fallback: number): number {
  const n = Number(value)
  return Number.isFinite(n) ? n : fallback
}

function toPairs(record: Record<string, string> | undefined | null): KeyValue[] {
  return Object.entries(record ?? {}).map(([key, value]) => ({ key, value: String(value) }))
}

function fromPairs(pairs: KeyValue[]): Record<string, string> {
  const out: Record<string, string> = {}
  for (const { key, value } of pairs) {
    if (key.trim()) out[key.trim()] = value
  }
  return out
}

export function stepFromApi(step: RequestStep): StepForm {
  const body = step.body ?? null
  let bodyText = ''
  let bodyForm: KeyValue[] = []
  if (body?.type === 'json') bodyText = JSON.stringify(body.content, null, 2)
  if (body?.type === 'raw') bodyText = String(body.content ?? '')
  if (body?.type === 'form') bodyForm = toPairs(body.content as Record<string, string>)
  return {
    uid: ++uidSeq,
    name: step.name,
    method: step.method ?? 'GET',
    path: step.path,
    headers: toPairs(step.headers),
    bodyType: body?.type ?? 'none',
    bodyText,
    bodyForm,
    contentType: body?.content_type ?? '',
    extract: (step.extract ?? []).map((rule) => ({ ...rule })),
    expectedStatus: (step.expected_status ?? []).join(', '),
    assertContains: step.assert_contains ?? '',
    thinkMs: step.think_ms ?? 0,
  }
}

export function profileFromApi(raw: Record<string, unknown>): ProfileForm {
  const base = defaultProfile()
  const type = raw.type as LoadProfileType
  if (type === 'constant') {
    return { ...base, type, rate: num(raw.rate, base.rate), duration_sec: num(raw.duration_sec, base.duration_sec) }
  }
  if (type === 'ramp_up') {
    return {
      ...base,
      type,
      start_rate: num(raw.start_rate, base.start_rate),
      end_rate: num(raw.end_rate, base.end_rate),
      ramp_sec: num(raw.ramp_sec, base.ramp_sec),
      hold_sec: num(raw.hold_sec, 0),
    }
  }
  if (type === 'step') {
    return {
      ...base,
      type,
      step_start_rate: num(raw.start_rate, base.step_start_rate),
      step_rate: num(raw.step_rate, base.step_rate),
      step_duration_sec: num(raw.step_duration_sec, base.step_duration_sec),
      max_steps: num(raw.max_steps, base.max_steps),
      max_error_rate_pct: num(raw.max_error_rate, 0.05) * 100,
      max_p95_ms: raw.max_p95_ms === null || raw.max_p95_ms === undefined ? null : num(raw.max_p95_ms, 0),
    }
  }
  return base
}

export function scenarioFromApi(s: ScenarioOut): ScenarioForm {
  return {
    name: s.name,
    profile: profileFromApi(s.load_profile),
    steps: s.flow.map(stepFromApi),
    apdexTMs: s.apdex_t_ms,
    sloP95Ms: s.slo_p95_ms,
    sloErrorPct: s.slo_max_error_rate === null ? null : Math.round(s.slo_max_error_rate * 10000) / 100,
  }
}

export function profileToApi(p: ProfileForm): LoadProfile {
  switch (p.type) {
    case 'constant':
      return { type: 'constant', rate: p.rate, duration_sec: p.duration_sec }
    case 'ramp_up':
      return { type: 'ramp_up', start_rate: p.start_rate, end_rate: p.end_rate, ramp_sec: p.ramp_sec, hold_sec: p.hold_sec }
    case 'step':
      return {
        type: 'step',
        start_rate: p.step_start_rate,
        step_rate: p.step_rate,
        step_duration_sec: p.step_duration_sec,
        max_steps: p.max_steps,
        max_error_rate: p.max_error_rate_pct / 100,
        max_p95_ms: p.max_p95_ms || null,
      }
  }
}

/** Проверка JSON тела на лету; `null` — всё в порядке. */
export function jsonError(text: string): string | null {
  if (!text.trim()) return null
  try {
    JSON.parse(text)
    return null
  } catch (err) {
    return err instanceof Error ? err.message : String(err)
  }
}

export function stepToApi(s: StepForm): RequestStep {
  const step: RequestStep = {
    name: s.name.trim(),
    method: s.method,
    path: s.path.trim(),
    headers: fromPairs(s.headers),
    expected_status: s.expectedStatus
      .split(/[\s,]+/)
      .map((part) => part.trim().toLowerCase())
      .filter(Boolean),
    assert_contains: s.assertContains || null,
    extract: s.extract.filter((rule) => rule.var.trim() || rule.expr.trim()),
    think_ms: s.thinkMs || 0,
  }
  if (s.bodyType !== 'none') {
    let content: unknown
    if (s.bodyType === 'json') content = s.bodyText.trim() ? JSON.parse(s.bodyText) : {}
    else if (s.bodyType === 'form') content = fromPairs(s.bodyForm)
    else content = s.bodyText
    step.body = { type: s.bodyType, content, content_type: s.contentType.trim() || null }
  }
  return step
}

export function scenarioToApi(f: ScenarioForm): ScenarioIn {
  return {
    name: f.name.trim(),
    load_profile: profileToApi(f.profile),
    flow: f.steps.map(stepToApi),
    apdex_t_ms: f.apdexTMs,
    slo_p95_ms: f.sloP95Ms || null,
    slo_max_error_rate: f.sloErrorPct === null || (f.sloErrorPct as unknown) === '' ? null : f.sloErrorPct / 100,
  }
}

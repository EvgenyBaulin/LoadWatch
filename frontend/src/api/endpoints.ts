// Типизированные вызовы API. Каждая функция — один эндпоинт бэкенда.

import { download, http } from './client'
import type {
  AlertOut,
  AnnotationOut,
  AuthResponse,
  ChannelIn,
  ChannelOut,
  ChannelTestOut,
  DryRunOut,
  Locale,
  Page,
  PreferencesUpdate,
  RunCreate,
  RunDetailOut,
  RunOut,
  RunSource,
  RunStatus,
  ScenarioIn,
  ScenarioOut,
  ScheduleIn,
  ScheduleOut,
  SiteMetricsOut,
  SiteOut,
  SiteSummaryOut,
  TriggerTokenOut,
  UserOut,
  VerificationInfo,
  VerificationMethod,
} from './types'

export const authApi = {
  login: (email: string, password: string) =>
    http.post<AuthResponse>('/auth/login', { email, password }),
  register: (email: string, password: string, locale: Locale, inviteCode?: string) =>
    http.post<AuthResponse>('/auth/register', {
      email,
      password,
      locale,
      invite_code: inviteCode || null,
    }),
  me: () => http.get<UserOut>('/auth/me'),
  updatePreferences: (prefs: PreferencesUpdate) => http.put<UserOut>('/users/me/preferences', prefs),
}

export const sitesApi = {
  list: (limit = 100, offset = 0) => http.get<Page<SiteOut>>('/sites', { limit, offset }),
  get: (id: string) => http.get<SiteOut>(`/sites/${id}`),
  create: (url: string, name?: string) => http.post<SiteOut>('/sites', { url, name: name || null }),
  remove: (id: string) => http.delete(`/sites/${id}`),
  verification: (id: string) => http.get<VerificationInfo>(`/sites/${id}/verification`),
  verify: (id: string, method: VerificationMethod) =>
    http.post<SiteOut>(`/sites/${id}/verify`, { method }),
  summary: (id: string, days?: number) => http.get<SiteSummaryOut>(`/sites/${id}/summary`, { days }),
  metrics: (id: string, from: string, to: string, signal?: AbortSignal) =>
    http.get<SiteMetricsOut>(`/sites/${id}/metrics`, { from, to }, signal),
  annotations: (id: string, from: string, to: string) =>
    http.get<AnnotationOut[]>(`/sites/${id}/annotations`, { from, to }),
  alerts: (id: string, status: 'open' | 'resolved' = 'open') =>
    http.get<AlertOut[]>(`/sites/${id}/alerts`, { status }),
  schedule: (id: string) => http.get<ScheduleOut | null>(`/sites/${id}/schedule`),
  saveSchedule: (id: string, body: ScheduleIn) => http.put<ScheduleOut>(`/sites/${id}/schedule`, body),
  triggerToken: (id: string) => http.post<TriggerTokenOut>(`/sites/${id}/trigger-token`),
}

export const scenariosApi = {
  list: (siteId: string) =>
    http.get<Page<ScenarioOut>>(`/sites/${siteId}/scenarios`, { limit: 100, offset: 0 }),
  get: (id: string) => http.get<ScenarioOut>(`/scenarios/${id}`),
  create: (siteId: string, body: ScenarioIn) =>
    http.post<ScenarioOut>(`/sites/${siteId}/scenarios`, body),
  update: (id: string, body: ScenarioIn) => http.put<ScenarioOut>(`/scenarios/${id}`, body),
  remove: (id: string) => http.delete(`/scenarios/${id}`),
  updateSecrets: (id: string, secrets: Record<string, string | null>) =>
    http.put<ScenarioOut>(`/scenarios/${id}/secrets`, { secrets }),
  dryRun: (id: string) => http.post<DryRunOut>(`/scenarios/${id}/dry-run`),
}

export interface RunFilters {
  status?: RunStatus | ''
  scenario_id?: string
  source?: RunSource | ''
  limit: number
  offset: number
}

export const runsApi = {
  create: (siteId: string, body: RunCreate) => http.post<RunOut>(`/sites/${siteId}/runs`, body),
  list: (siteId: string, filters: RunFilters) =>
    http.get<Page<RunOut>>(`/sites/${siteId}/runs`, { ...filters }),
  get: (id: string) => http.get<RunDetailOut>(`/runs/${id}`),
  cancel: (id: string) => http.post<RunOut>(`/runs/${id}/cancel`),
  exportCsv: (id: string) => download(`/runs/${id}/export.csv`, `run-${id}.csv`),
  reportPdf: (id: string, lang: Locale) =>
    download(`/runs/${id}/report.pdf`, `run-${id}.pdf`, { lang }),
}

export const channelsApi = {
  list: () => http.get<ChannelOut[]>('/users/me/channels'),
  create: (body: ChannelIn) => http.post<ChannelOut>('/users/me/channels', body),
  update: (id: string, body: ChannelIn) => http.put<ChannelOut>(`/users/me/channels/${id}`, body),
  remove: (id: string) => http.delete(`/users/me/channels/${id}`),
  test: (id: string) => http.post<ChannelTestOut>(`/users/me/channels/${id}/test`),
}

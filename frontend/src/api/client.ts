// HTTP-клиент API: Bearer-токен, единый формат ошибок, скачивание файлов с авторизацией.

import type { ValidationIssue } from './types'

export const API_BASE = '/api/v1'

const TOKEN_KEY = 'lw.token'
const TOKEN_EXPIRES_KEY = 'lw.tokenExpiresAt'

/** Ошибка API: стабильный машинный `code`, по которому фронтенд выбирает перевод. */
export class ApiError extends Error {
  readonly status: number
  readonly code: string
  readonly details: unknown

  constructor(status: number, code: string, message: string, details?: unknown) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
    this.details = details
  }

  /** Ошибки валидации полей (422 validation_error). */
  get validationIssues(): ValidationIssue[] {
    if (this.code !== 'validation_error' || !Array.isArray(this.details)) return []
    return this.details.filter(
      (item): item is ValidationIssue =>
        typeof item === 'object' && item !== null && Array.isArray((item as ValidationIssue).loc),
    )
  }
}

// Хранение токена: localStorage (решение задокументировано в README фронтенда).
export const tokenStorage = {
  get(): string | null {
    try {
      const token = localStorage.getItem(TOKEN_KEY)
      const expiresAt = Number(localStorage.getItem(TOKEN_EXPIRES_KEY) ?? '0')
      if (!token) return null
      if (expiresAt && Date.now() >= expiresAt) {
        this.clear()
        return null
      }
      return token
    } catch {
      return null
    }
  },
  set(token: string, expiresInSec: number): void {
    try {
      localStorage.setItem(TOKEN_KEY, token)
      localStorage.setItem(TOKEN_EXPIRES_KEY, String(Date.now() + expiresInSec * 1000))
    } catch {
      // Приватный режим без localStorage: токен живёт до перезагрузки страницы.
    }
    memoryToken = token
  },
  clear(): void {
    memoryToken = null
    try {
      localStorage.removeItem(TOKEN_KEY)
      localStorage.removeItem(TOKEN_EXPIRES_KEY)
    } catch {
      // Нечего чистить.
    }
  },
}

let memoryToken: string | null = null
let unauthorizedHandler: (() => void) | null = null

/** Обработчик 401: стор авторизации выходит из аккаунта и уводит на /login. */
export function onUnauthorized(handler: () => void): void {
  unauthorizedHandler = handler
}

function currentToken(): string | null {
  return tokenStorage.get() ?? memoryToken
}

export type QueryValue = string | number | boolean | null | undefined
export type Query = Record<string, QueryValue>

export interface RequestOptions {
  query?: Query
  body?: unknown
  signal?: AbortSignal
}

export function buildUrl(path: string, query?: Query): string {
  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(query ?? {})) {
    if (value !== undefined && value !== null && value !== '') params.set(key, String(value))
  }
  const qs = params.toString()
  return `${API_BASE}${path}${qs ? `?${qs}` : ''}`
}

async function send(method: string, path: string, options: RequestOptions): Promise<Response> {
  const headers: Record<string, string> = { Accept: 'application/json' }
  const token = currentToken()
  if (token) headers.Authorization = `Bearer ${token}`
  let body: string | undefined
  if (options.body !== undefined) {
    headers['Content-Type'] = 'application/json'
    body = JSON.stringify(options.body)
  }
  let response: Response
  try {
    response = await fetch(buildUrl(path, options.query), {
      method,
      headers,
      body,
      signal: options.signal,
    })
  } catch (err) {
    if (err instanceof DOMException && err.name === 'AbortError') throw err
    throw new ApiError(0, 'network_error', String(err))
  }
  if (!response.ok) throw await toApiError(response, Boolean(token))
  return response
}

async function toApiError(response: Response, hadToken: boolean): Promise<ApiError> {
  let code = 'http_error'
  let message = response.statusText
  let details: unknown
  try {
    const data = (await response.json()) as { error?: { code?: string; message?: string; details?: unknown } }
    if (data.error?.code) {
      code = data.error.code
      message = data.error.message ?? message
      details = data.error.details
    }
  } catch {
    // Тело не JSON (например, ответ прокси) — остаётся общий код.
  }
  if (response.status === 401 && hadToken && code !== 'invalid_credentials') {
    // Токен истёк или отозван: выходим и показываем вход.
    unauthorizedHandler?.()
  }
  return new ApiError(response.status, code, message, details)
}

export async function request<T>(method: string, path: string, options: RequestOptions = {}): Promise<T> {
  const response = await send(method, path, options)
  if (response.status === 204) return undefined as T
  const text = await response.text()
  return (text ? JSON.parse(text) : undefined) as T
}

export const http = {
  get: <T>(path: string, query?: Query, signal?: AbortSignal) => request<T>('GET', path, { query, signal }),
  post: <T>(path: string, body?: unknown, query?: Query) => request<T>('POST', path, { body, query }),
  put: <T>(path: string, body?: unknown) => request<T>('PUT', path, { body }),
  delete: (path: string) => request<void>('DELETE', path),
}

function filenameFrom(response: Response, fallback: string): string {
  const header = response.headers.get('Content-Disposition') ?? ''
  const utf = /filename\*=UTF-8''([^;]+)/i.exec(header)
  if (utf?.[1]) return decodeURIComponent(utf[1])
  const plain = /filename="?([^";]+)"?/i.exec(header)
  return plain?.[1] ?? fallback
}

/** Скачивает файл с Bearer-токеном: fetch → blob → временная ссылка. */
export async function download(path: string, fallbackName: string, query?: Query): Promise<void> {
  const response = await send('GET', path, { query })
  const blob = await response.blob()
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filenameFrom(response, fallbackName)
  document.body.appendChild(link)
  link.click()
  link.remove()
  // Даём браузеру начать загрузку, потом освобождаем память.
  setTimeout(() => URL.revokeObjectURL(url), 10_000)
}

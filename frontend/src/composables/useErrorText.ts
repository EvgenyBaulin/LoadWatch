// Перевод ошибок API по стабильному `error.code` (для неизвестных кодов — общий текст).

import { useI18n } from 'vue-i18n'
import { ApiError } from '@/api/client'
import { ERROR_KEYS, REQUEST_ERROR_KEYS, VALIDATION_TYPE_KEYS, VERIFICATION_REASON_KEYS } from '@/i18n/labels'

export interface FieldError {
  text: string
  /** Пояснение сервера (на английском) — показываем приглушённо, когда тип ошибки общий. */
  detail?: string
}

function retryAfter(details: unknown): number | null {
  if (typeof details === 'object' && details !== null && 'retry_after' in details) {
    const value = Number((details as { retry_after: unknown }).retry_after)
    return Number.isFinite(value) ? value : null
  }
  return null
}

function reason(details: unknown): string | null {
  if (typeof details === 'object' && details !== null && 'reason' in details) {
    return String((details as { reason: unknown }).reason)
  }
  return null
}

export function useErrorText() {
  const { t } = useI18n()

  /** Текст ошибки для пользователя на текущем языке. */
  function errorText(err: unknown): string {
    if (!(err instanceof ApiError)) return t('errors.unknown')
    if (err.code === 'verification_failed') {
      const key = VERIFICATION_REASON_KEYS[reason(err.details) ?? '']
      if (key) return t(key)
    }
    const key = ERROR_KEYS[err.code]
    if (!key) return t('errors.unknown')
    const wait = retryAfter(err.details)
    if ((err.code === 'rate_limited' || err.code === 'cooldown_active') && wait !== null) {
      return t('errors.retryAfter', { message: t(key), seconds: wait })
    }
    return t(key)
  }

  /** Код ошибки запуска (`run.error`) или ошибки запроса → текст. */
  function codeText(code: string | null | undefined): string {
    if (!code) return ''
    const known = ERROR_KEYS[code] ?? REQUEST_ERROR_KEYS[code]
    if (known) return t(known)
    const http = /^http_(\d{3})$/.exec(code)
    if (http) return t('requestErrors.http_status', { status: http[1] })
    return code
  }

  /**
   * Ошибки валидации полей: ключ — путь без префикса `body`, например `flow.0.path`.
   * Сообщения pydantic на английском, поэтому переводим по `type`.
   */
  function fieldErrors(err: unknown): Record<string, FieldError> {
    const result: Record<string, FieldError> = {}
    if (!(err instanceof ApiError)) return result
    for (const issue of err.validationIssues) {
      const loc = issue.loc[0] === 'body' ? issue.loc.slice(1) : issue.loc
      // Для объединений pydantic добавляет имя варианта (`ConstantProfileIn`, тег `step`,
      // `function-after[...]`) — такие сегменты пропускаем.
      const path = loc
        .filter((part, index) => {
          if (typeof part === 'number') return true
          if (/^[A-Z]|[[(]/.test(part)) return false
          return !(loc[index - 1] === 'load_profile' && ['constant', 'ramp_up', 'step'].includes(part))
        })
        .join('.')
      const key = VALIDATION_TYPE_KEYS[issue.type]
      const entry: FieldError = key
        ? { text: t(key), detail: issue.type === 'value_error' ? issue.msg : undefined }
        : { text: t('validation.invalid'), detail: issue.msg }
      result[path] ??= entry
    }
    return result
  }

  return { errorText, codeText, fieldErrors }
}

// Настройка vue-i18n (Composition API). Все строки — в src/locales/{ru,en}.json.

import { createI18n } from 'vue-i18n'
import en from '@/locales/en.json'
import ru from '@/locales/ru.json'
import type { Locale } from '@/api/types'

export const SUPPORTED_LOCALES: readonly Locale[] = ['ru', 'en']
export const LOCALE_STORAGE_KEY = 'lw.locale'

type MessageSchema = typeof en

/**
 * Помечает строку как ключ перевода. Нужна там, где ключ хранится в данных
 * (таблицы соответствий, конфиги колонок), чтобы `npm run i18n:check` его проверил.
 */
export function tk<const T extends string>(key: T): T {
  return key
}

/** Русское склонение: 1 запуск, 2 запуска, 5 запусков (три формы + «ноль» при четырёх). */
function russianPlural(choice: number, choicesLength: number): number {
  const n = Math.abs(choice)
  if (choicesLength === 4) {
    if (n === 0) return 0
    return 1 + russianForm(n)
  }
  return russianForm(n)
}

function russianForm(n: number): number {
  const mod10 = n % 10
  const mod100 = n % 100
  if (mod10 === 1 && mod100 !== 11) return 0
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return 1
  return 2
}

export function isLocale(value: unknown): value is Locale {
  return value === 'ru' || value === 'en'
}

/** Язык по умолчанию: явный выбор из localStorage, иначе navigator.language (не ru → en). */
export function detectLocale(): Locale {
  try {
    const saved = localStorage.getItem(LOCALE_STORAGE_KEY)
    if (isLocale(saved)) return saved
  } catch {
    // localStorage недоступен — берём язык браузера.
  }
  return navigator.language.toLowerCase().startsWith('ru') ? 'ru' : 'en'
}

export const i18n = createI18n<[MessageSchema], Locale, false>({
  legacy: false,
  locale: detectLocale(),
  fallbackLocale: 'en',
  messages: { en, ru },
  pluralRules: { ru: russianPlural },
  missingWarn: import.meta.env.DEV,
  fallbackWarn: false,
})

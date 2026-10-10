#!/usr/bin/env node
// Проверка локалей: одинаковые наборы ключей в ru.json и en.json и наличие всех ключей,
// которые код использует статически: t('…'), $t('…'), te('…'), tk('…'), keypath="…".
// Запуск: npm run i18n:check (код возврата 1 при любой проблеме).
// С флагом --list печатает все найденные в коде ключи (удобно при добавлении строк).

import { readdirSync, readFileSync, statSync } from 'node:fs'
import { extname, join, relative } from 'node:path'
import { fileURLToPath } from 'node:url'

const root = fileURLToPath(new URL('..', import.meta.url))
const srcDir = join(root, 'src')
const localeFiles = { ru: join(srcDir, 'locales/ru.json'), en: join(srcDir, 'locales/en.json') }

/** Плоский набор ключей: { a: { b: 'x' } } → ['a.b']. */
function flatten(obj, prefix = '', out = new Set()) {
  for (const [key, value] of Object.entries(obj)) {
    const path = prefix ? `${prefix}.${key}` : key
    if (value && typeof value === 'object' && !Array.isArray(value)) flatten(value, path, out)
    else out.add(path)
  }
  return out
}

function walk(dir, files = []) {
  for (const name of readdirSync(dir)) {
    const path = join(dir, name)
    if (statSync(path).isDirectory()) walk(path, files)
    else if (['.ts', '.vue', '.js', '.mjs'].includes(extname(path))) files.push(path)
  }
  return files
}

const problems = []
const locales = {}
for (const [lang, file] of Object.entries(localeFiles)) {
  try {
    locales[lang] = flatten(JSON.parse(readFileSync(file, 'utf8')))
  } catch (err) {
    problems.push(`${relative(root, file)}: ${err.message}`)
  }
}

if (locales.ru && locales.en) {
  for (const key of locales.ru) if (!locales.en.has(key)) problems.push(`en.json: no key "${key}" (present in ru.json)`)
  for (const key of locales.en) if (!locales.ru.has(key)) problems.push(`ru.json: no key "${key}" (present in en.json)`)
}

// Ключ — строковый литерал без интерполяции первым аргументом.
const patterns = [
  /(?<![\w$.])(?:\$t|t|te|tk|tm|rt)\(\s*(['"`])([A-Za-z0-9_.-]+)\1/g,
  /\bkeypath=(["'])([A-Za-z0-9_.-]+)\1/g,
]

const used = new Map()
for (const file of walk(srcDir)) {
  const text = readFileSync(file, 'utf8')
  for (const pattern of patterns) {
    for (const match of text.matchAll(pattern)) {
      const key = match[2]
      // Только ключи с точкой: так отсекаются случайные вызовы t('x') вне i18n.
      if (!key.includes('.')) continue
      if (!used.has(key)) used.set(key, new Set())
      used.get(key).add(relative(root, file))
    }
  }
}

if (process.argv.includes('--list')) {
  for (const key of [...used.keys()].sort()) console.log(key)
}

for (const [lang, keys] of Object.entries(locales)) {
  for (const [key, files] of used) {
    if (!keys.has(key)) problems.push(`${lang}.json: missing key "${key}" used in ${[...files].join(', ')}`)
  }
}

if (problems.length) {
  console.error(`i18n check failed (${problems.length}):`)
  for (const problem of problems) console.error(`  - ${problem}`)
  process.exit(1)
}
console.log(`i18n check passed: ${locales.en?.size ?? 0} keys in each locale, ${used.size} keys used in code.`)

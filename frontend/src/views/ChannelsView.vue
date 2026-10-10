<script setup lang="ts">
// Каналы уведомлений (CRUD, тестовая отправка) и личные настройки (язык, пояс, дайджест).

import { computed, reactive, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { channelsApi } from '@/api/endpoints'
import type { ChannelEvent, ChannelIn, ChannelOut, ChannelType, Locale } from '@/api/types'
import CopyButton from '@/components/CopyButton.vue'
import EmptyState from '@/components/EmptyState.vue'
import ErrorMessage from '@/components/ErrorMessage.vue'
import Icon from '@/components/Icon.vue'
import Panel from '@/components/Panel.vue'
import Skeleton from '@/components/Skeleton.vue'
import { useErrorText } from '@/composables/useErrorText'
import { useResource } from '@/composables/useResource'
import { tk } from '@/i18n'
import { CHANNEL_EVENT_KEYS, CHANNEL_TYPE_KEYS } from '@/i18n/labels'
import { useAuthStore } from '@/stores/auth'
import { usePreferencesStore } from '@/stores/preferences'
import { useToastStore } from '@/stores/toasts'

const { t, locale } = useI18n()
const auth = useAuthStore()
const prefs = usePreferencesStore()
const toasts = useToastStore()
const { errorText, fieldErrors } = useErrorText()

const TYPES: ChannelType[] = ['telegram', 'email', 'slack', 'discord', 'webhook']
const EVENTS: ChannelEvent[] = ['regression', 'slo_breach', 'run_failed', 'site_unverified', 'recovery', 'digest']

/** Поле настроек канала для каждого типа; секретные поля сервер не возвращает. */
const CONFIG_FIELD: Record<ChannelType, { key: string; label: string; placeholder: string; secret: boolean; type: string }> = {
  telegram: { key: 'chat_id', label: tk('channels.config.chat_id'), placeholder: tk('channels.config.chatIdPlaceholder'), secret: false, type: 'text' },
  email: { key: 'email', label: tk('channels.config.email'), placeholder: tk('channels.config.emailPlaceholder'), secret: false, type: 'email' },
  slack: { key: 'webhook_url', label: tk('channels.config.webhook_url'), placeholder: tk('channels.config.slackPlaceholder'), secret: true, type: 'url' },
  discord: { key: 'webhook_url', label: tk('channels.config.webhook_url'), placeholder: tk('channels.config.discordPlaceholder'), secret: true, type: 'url' },
  webhook: { key: 'url', label: tk('channels.config.url'), placeholder: tk('channels.config.urlPlaceholder'), secret: true, type: 'url' },
}

const channels = useResource(() => channelsApi.list())

interface ChannelFormState {
  id: string | null
  type: ChannelType
  name: string
  value: string
  events: ChannelEvent[]
  enabled: boolean
}

function blankForm(): ChannelFormState {
  return { id: null, type: 'telegram', name: '', value: '', events: [...EVENTS], enabled: true }
}

const form = reactive<ChannelFormState>(blankForm())
const formOpen = ref(false)
const saving = ref(false)
const formError = ref<unknown>(null)
const signingSecret = ref<string | null>(null)
const testResults = ref<Record<string, { ok: boolean; error: string | null } | 'pending'>>({})
const formFields = computed(() => fieldErrors(formError.value))
const field = computed(() => CONFIG_FIELD[form.type])
// Секретное поле при редактировании можно оставить пустым — сервер сохранит прежнее значение.
const keepsSecret = computed(() => form.id !== null && field.value.secret)

function openCreate(): void {
  Object.assign(form, blankForm())
  formError.value = null
  formOpen.value = true
}

function openEdit(channel: ChannelOut): void {
  const cfg = CONFIG_FIELD[channel.type]
  Object.assign(form, {
    id: channel.id,
    type: channel.type,
    name: channel.name,
    value: channel.config[cfg.key] ?? '',
    events: [...channel.events],
    enabled: channel.enabled,
  })
  formError.value = null
  formOpen.value = true
}

async function save(): Promise<void> {
  saving.value = true
  formError.value = null
  const config: Record<string, string> = {}
  if (form.value.trim()) config[field.value.key] = form.value.trim()
  const body: ChannelIn = { type: form.type, name: form.name.trim(), config, events: form.events, enabled: form.enabled }
  try {
    if (form.id) {
      await channelsApi.update(form.id, body)
      toasts.success(t('channels.saved'))
    } else {
      const created = await channelsApi.create(body)
      signingSecret.value = created.signing_secret ?? null
      toasts.success(t('channels.created'))
    }
    formOpen.value = false
    await channels.reload(true)
  } catch (err) {
    formError.value = err
  } finally {
    saving.value = false
  }
}

function eventsText(channel: ChannelOut): string {
  return channel.events.map((event) => t(CHANNEL_EVENT_KEYS[event])).join(' · ') || t('channels.noEvents')
}

async function remove(channel: ChannelOut): Promise<void> {
  if (!window.confirm(t('channels.deleteConfirm', { name: channel.name }))) return
  try {
    await channelsApi.remove(channel.id)
    toasts.success(t('channels.deleted'))
    await channels.reload(true)
  } catch (err) {
    toasts.error(errorText(err))
  }
}

async function sendTest(channel: ChannelOut): Promise<void> {
  testResults.value = { ...testResults.value, [channel.id]: 'pending' }
  try {
    const result = await channelsApi.test(channel.id)
    testResults.value = { ...testResults.value, [channel.id]: result }
  } catch (err) {
    testResults.value = { ...testResults.value, [channel.id]: { ok: false, error: errorText(err) } }
  }
}

async function toggleEnabled(channel: ChannelOut): Promise<void> {
  try {
    await channelsApi.update(channel.id, {
      type: channel.type,
      name: channel.name,
      // Для вебхуков секретный URL не возвращается: пустой config — сервер оставит прежний.
      config: CONFIG_FIELD[channel.type].secret ? {} : channel.config,
      events: channel.events,
      enabled: !channel.enabled,
    })
    await channels.reload(true)
  } catch (err) {
    toasts.error(errorText(err))
  }
}

// ---- личные настройки ----

const timeZones = Intl.supportedValuesOf('timeZone')
const zones = timeZones.includes('UTC') ? timeZones : ['UTC', ...timeZones]
const settings = reactive({ locale: prefs.locale as Locale, timezone: 'UTC', digest_enabled: true, digest_weekday: 0, digest_hour: 9 })
const settingsSaving = ref(false)
const settingsError = ref<unknown>(null)

watch(
  () => auth.user,
  (user) => {
    if (!user) return
    Object.assign(settings, {
      locale: user.locale,
      timezone: user.timezone,
      digest_enabled: user.digest_enabled,
      digest_weekday: user.digest_weekday,
      digest_hour: user.digest_hour,
    })
  },
  { immediate: true },
)

// Названия дней недели — из Intl (0 — понедельник, как на сервере). 2024-01-01 — понедельник.
const weekdays = computed(() => {
  const f = new Intl.DateTimeFormat(locale.value, { weekday: 'long', timeZone: 'UTC' })
  return Array.from({ length: 7 }, (_, i) => f.format(new Date(Date.UTC(2024, 0, 1 + i))))
})
const hourFormat = computed(() => new Intl.DateTimeFormat(locale.value, { hour: '2-digit', minute: '2-digit', timeZone: 'UTC' }))
const hours = computed(() => Array.from({ length: 24 }, (_, h) => hourFormat.value.format(new Date(Date.UTC(2024, 0, 1, h)))))

async function saveSettings(): Promise<void> {
  settingsSaving.value = true
  settingsError.value = null
  try {
    await auth.updatePreferences({ ...settings })
    if (settings.locale !== prefs.locale) await prefs.setLocale(settings.locale, false)
    toasts.success(t('settings.saved'))
  } catch (err) {
    settingsError.value = err
  } finally {
    settingsSaving.value = false
  }
}
</script>

<template>
  <div class="page page-narrow">
    <div v-if="signingSecret" class="callout callout-warning" role="alert">
      <div class="stack-sm grow">
        <strong>{{ t('channels.signingSecretTitle') }}</strong>
        <span class="small">{{ t('channels.signingSecretText') }}</span>
        <div class="row"><code>{{ signingSecret }}</code><CopyButton :text="signingSecret" /></div>
      </div>
      <button type="button" class="btn btn-sm" @click="signingSecret = null">{{ t('channels.signingSecretDone') }}</button>
    </div>

    <Panel :title="t('channels.title')" flush>
      <template #actions>
        <button type="button" class="btn btn-sm btn-primary" @click="openCreate">
          <Icon name="plus" :size="12" />{{ t('channels.add') }}
        </button>
      </template>

      <form v-if="formOpen" class="channel-form stack" novalidate @submit.prevent="save">
        <h3>{{ form.id ? t('channels.editTitle') : t('channels.createTitle') }}</h3>
        <div class="form-grid">
          <label class="field">
            <span class="field-label">{{ t('channels.type') }}</span>
            <select v-model="form.type" class="select" :disabled="form.id !== null">
              <option v-for="type in TYPES" :key="type" :value="type">{{ t(CHANNEL_TYPE_KEYS[type]) }}</option>
            </select>
          </label>
          <label class="field">
            <span class="field-label">{{ t('channels.name') }}</span>
            <input v-model="form.name" class="input" maxlength="100" required :aria-invalid="Boolean(formFields.name)" />
            <span v-if="formFields.name" class="field-error">{{ formFields.name.text }}</span>
          </label>
          <label class="field">
            <span class="field-label">{{ t(field.label) }}</span>
            <input
              v-model="form.value"
              class="input mono"
              :type="field.type"
              :placeholder="keepsSecret ? t('channels.keepSecret') : t(field.placeholder)"
              :required="!keepsSecret"
            />
          </label>
        </div>
        <div v-if="form.type === 'telegram'" class="callout callout-info small">
          <div class="stack-sm">
            <strong>{{ t('channels.telegramHowTo.title') }}</strong>
            <ol class="howto">
              <li>{{ t('channels.telegramHowTo.step1') }}</li>
              <li>{{ t('channels.telegramHowTo.step2') }}</li>
              <li>{{ t('channels.telegramHowTo.step3') }}</li>
            </ol>
          </div>
        </div>
        <p v-if="form.type === 'webhook' && !form.id" class="field-hint">{{ t('channels.webhookHint') }}</p>
        <fieldset>
          <legend>{{ t('channels.eventsLegend') }}</legend>
          <div class="events">
            <label v-for="event in EVENTS" :key="event" class="checkbox">
              <input v-model="form.events" type="checkbox" :value="event" />
              {{ t(CHANNEL_EVENT_KEYS[event]) }}
            </label>
          </div>
        </fieldset>
        <label class="checkbox">
          <input v-model="form.enabled" type="checkbox" />
          {{ t('channels.enabled') }}
        </label>
        <ErrorMessage v-if="formError" :error="formError" />
        <div class="row">
          <button type="submit" class="btn btn-primary" :disabled="saving || !form.name.trim()">
            {{ saving ? t('common.saving') : t('common.save') }}
          </button>
          <button type="button" class="btn" @click="formOpen = false">{{ t('common.cancel') }}</button>
        </div>
      </form>

      <div v-if="channels.loading.value && !channels.data.value" class="pad"><Skeleton :lines="3" /></div>
      <div v-else-if="channels.error.value" class="pad">
        <ErrorMessage :error="channels.error.value" retry @retry="channels.reload()" />
      </div>
      <EmptyState v-else-if="!channels.data.value?.length" :title="t('channels.emptyTitle')" :text="t('channels.emptyText')" compact />
      <ul v-else class="channel-list">
        <li v-for="channel in channels.data.value" :key="channel.id" class="channel">
          <div class="channel-main">
            <div class="row">
              <strong>{{ channel.name }}</strong>
              <span class="badge badge-neutral">{{ t(CHANNEL_TYPE_KEYS[channel.type]) }}</span>
              <span v-if="channel.config.host" class="mono small muted">{{ channel.config.host }}</span>
              <span v-else-if="channel.config.chat_id || channel.config.email" class="mono small muted">{{ channel.config.chat_id ?? channel.config.email }}</span>
              <span class="badge" :class="channel.enabled ? 'badge-success' : 'badge-neutral'">
                {{ channel.enabled ? t('channels.on') : t('channels.off') }}
              </span>
            </div>
            <p class="small muted">
              {{ eventsText(channel) }}
            </p>
            <p v-if="testResults[channel.id] && testResults[channel.id] !== 'pending'" class="small" role="status">
              <template v-if="(testResults[channel.id] as { ok: boolean }).ok">
                <span class="text-success">{{ t('channels.testOk') }}</span>
              </template>
              <template v-else>
                <span class="text-error">{{ t('channels.testFailed') }}</span>
                <span class="mono muted"> {{ (testResults[channel.id] as { error: string | null }).error }}</span>
              </template>
            </p>
          </div>
          <div class="row">
            <button type="button" class="btn btn-sm" :disabled="testResults[channel.id] === 'pending'" @click="sendTest(channel)">
              {{ testResults[channel.id] === 'pending' ? t('channels.testing') : t('channels.test') }}
            </button>
            <button type="button" class="btn btn-sm" @click="toggleEnabled(channel)">
              {{ channel.enabled ? t('channels.disable') : t('channels.enable') }}
            </button>
            <button type="button" class="btn btn-sm" :aria-label="t('channels.editNamed', { name: channel.name })" @click="openEdit(channel)">
              <Icon name="edit" :size="12" />{{ t('common.edit') }}
            </button>
            <button type="button" class="btn btn-sm btn-danger" :aria-label="t('channels.deleteNamed', { name: channel.name })" @click="remove(channel)">
              <Icon name="trash" :size="12" />
            </button>
          </div>
        </li>
      </ul>
    </Panel>

    <Panel :title="t('settings.title')">
      <form class="stack" @submit.prevent="saveSettings">
        <div class="form-grid">
          <label class="field">
            <span class="field-label">{{ t('settings.locale') }}</span>
            <select v-model="settings.locale" class="select">
              <option value="ru">{{ t('language.ru') }}</option>
              <option value="en">{{ t('language.en') }}</option>
            </select>
            <span class="field-hint">{{ t('settings.localeHint') }}</span>
          </label>
          <label class="field">
            <span class="field-label">{{ t('settings.timezone') }}</span>
            <select v-model="settings.timezone" class="select">
              <option v-for="zone in zones" :key="zone" :value="zone">{{ zone }}</option>
            </select>
            <span class="field-hint">{{ t('settings.timezoneHint') }}</span>
          </label>
        </div>
        <fieldset class="stack-sm">
          <legend>{{ t('settings.digest') }}</legend>
          <label class="checkbox">
            <input v-model="settings.digest_enabled" type="checkbox" />
            {{ t('settings.digestEnabled') }}
          </label>
          <div class="form-grid">
            <label class="field">
              <span class="field-label">{{ t('settings.digestWeekday') }}</span>
              <select v-model.number="settings.digest_weekday" class="select" :disabled="!settings.digest_enabled">
                <option v-for="(name, i) in weekdays" :key="i" :value="i">{{ name }}</option>
              </select>
            </label>
            <label class="field">
              <span class="field-label">{{ t('settings.digestHour') }}</span>
              <select v-model.number="settings.digest_hour" class="select" :disabled="!settings.digest_enabled">
                <option v-for="(label, h) in hours" :key="h" :value="h">{{ label }}</option>
              </select>
            </label>
          </div>
          <span class="field-hint">{{ t('settings.digestHint') }}</span>
        </fieldset>
        <ErrorMessage v-if="settingsError" :error="settingsError" />
        <div>
          <button type="submit" class="btn btn-primary" :disabled="settingsSaving">
            {{ settingsSaving ? t('common.saving') : t('common.save') }}
          </button>
        </div>
      </form>
    </Panel>
  </div>
</template>

<style scoped>
.pad {
  padding: var(--space-3);
}

.grow {
  flex: 1;
}

.channel-form {
  padding: var(--space-3);
  border-bottom: 1px solid var(--border);
  background: var(--surface-2);
}

.events {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
  gap: var(--space-2);
}

.howto {
  margin: 0;
  padding-left: var(--space-4);
}

.channel-list {
  list-style: none;
  margin: 0;
  padding: 0;
}

.channel {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-2) var(--space-3);
  border-bottom: 1px solid var(--border);
  flex-wrap: wrap;
}

.channel:last-child {
  border-bottom: none;
}

.channel-main {
  min-width: 0;
  flex: 1;
}

.channel-main p {
  margin: 2px 0 0;
}
</style>

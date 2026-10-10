<script setup lang="ts">
// Подтверждение владения сайтом: TXT-запись DNS или файл по HTTP.

import { ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { sitesApi } from '@/api/endpoints'
import type { SiteOut, VerificationMethod } from '@/api/types'
import CopyButton from '@/components/CopyButton.vue'
import ErrorMessage from '@/components/ErrorMessage.vue'
import Panel from '@/components/Panel.vue'
import Skeleton from '@/components/Skeleton.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import { useFormat } from '@/composables/useFormat'
import { useResource } from '@/composables/useResource'
import { useToastStore } from '@/stores/toasts'

const props = defineProps<{ site: SiteOut }>()
const emit = defineEmits<{ verified: [site: SiteOut] }>()

const { t } = useI18n()
const fmt = useFormat()
const toasts = useToastStore()

const info = useResource(() => sitesApi.verification(props.site.id))
const busy = ref<VerificationMethod | null>(null)
const error = ref<unknown>(null)

async function verify(method: VerificationMethod): Promise<void> {
  busy.value = method
  error.value = null
  try {
    const site = await sitesApi.verify(props.site.id, method)
    toasts.success(t('verification.success'))
    emit('verified', site)
    await info.reload(true)
  } catch (err) {
    error.value = err
  } finally {
    busy.value = null
  }
}
</script>

<template>
  <Panel :title="t('verification.title')">
    <template #actions>
      <StatusBadge kind="site" :status="site.verification_status" />
    </template>
    <Skeleton v-if="info.loading.value && !info.data.value" :lines="4" />
    <ErrorMessage v-else-if="info.error.value" :error="info.error.value" retry @retry="info.reload()" />
    <div v-else-if="info.data.value" class="stack">
      <p v-if="site.verification_status === 'verified'" class="small">
        {{
          t('verification.verifiedVia', {
            method: site.verification_method === 'dns' ? t('verification.dns') : t('verification.http'),
            date: fmt.dateTime(info.data.value.last_verified_at),
          })
        }}
        {{ t('verification.reverify', { n: info.data.value.reverify_days }, info.data.value.reverify_days) }}
      </p>
      <p v-else class="small">{{ t('verification.intro') }}</p>
      <div class="methods">
        <section v-for="item in info.data.value.instructions" :key="item.method" class="method">
          <h3>{{ item.method === 'dns' ? t('verification.dnsTitle') : t('verification.httpTitle') }}</h3>
          <p class="small muted">
            {{ item.method === 'dns' ? t('verification.dnsText') : t('verification.httpText') }}
          </p>
          <dl class="dl">
            <dt>{{ item.method === 'dns' ? t('verification.recordName') : t('verification.filePath') }}</dt>
            <dd class="row"><code>{{ item.record_name }}</code><CopyButton :text="item.record_name" /></dd>
            <dt v-if="item.method === 'dns'">{{ t('verification.recordType') }}</dt>
            <dd v-if="item.method === 'dns'"><code>TXT</code></dd>
            <dt>{{ item.method === 'dns' ? t('verification.recordValue') : t('verification.fileContent') }}</dt>
            <dd class="row"><code>{{ item.record_value }}</code><CopyButton :text="item.record_value" /></dd>
          </dl>
          <button
            type="button"
            class="btn"
            :class="{ 'btn-primary': site.verification_status !== 'verified' }"
            :disabled="busy !== null"
            @click="verify(item.method)"
          >
            {{ busy === item.method ? t('verification.checking') : item.method === 'dns' ? t('verification.verifyDns') : t('verification.verifyHttp') }}
          </button>
        </section>
      </div>
      <ErrorMessage v-if="error" :error="error" />
    </div>
  </Panel>
</template>

<style scoped>
.methods {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
  gap: var(--space-3);
}

.method {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  padding: var(--space-3);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--surface-2);
  align-items: flex-start;
}

.method .dl {
  width: 100%;
}
</style>

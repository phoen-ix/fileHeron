<!-- "Generate a password" for the secret compose form: a small panel with a
     length, the character classes and a preview, and a button that hands the
     value to the parent. Generated in the browser (utils/passwordGenerator);
     nothing is sent anywhere until the sender sends the secret. -->
<template>
  <div class="password-generator">
    <button
      type="button"
      class="fh-btn-text"
      :aria-expanded="open"
      :disabled="disabled"
      data-testid="generator-toggle"
      @click="toggle"
    >
      {{ t('secrets.generator.toggle') }}
    </button>
    <div v-if="open" class="gen-panel" data-testid="generator-panel">
      <label class="gen-length">
        <span class="fh-field-label">{{ t('secrets.generator.length', { n: opts.length }) }}</span>
        <input
          v-model.number="opts.length"
          type="range"
          :min="MIN_LENGTH"
          :max="MAX_LENGTH"
          @input="regenerate"
        />
      </label>
      <div class="gen-classes" role="group" :aria-label="t('secrets.generator.classes')">
        <label v-for="c in classKeys" :key="c" class="gen-class">
          <input v-model="opts[c]" type="checkbox" @change="regenerate" />
          <span>{{ t(`secrets.generator.class.${c}`) }}</span>
        </label>
      </div>
      <pre class="gen-preview fh-mono" data-testid="generator-preview">{{ candidate }}</pre>
      <div class="gen-actions">
        <button type="button" class="fh-btn-text" @click="regenerate">
          {{ t('secrets.generator.again') }}
        </button>
        <button type="button" class="fh-btn" data-testid="generator-use" @click="use">
          {{ t('secrets.generator.use') }}
        </button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
  import { reactive, ref } from 'vue'
  import { useI18n } from 'vue-i18n'

  import {
    DEFAULT_PASSWORD_OPTIONS,
    MAX_LENGTH,
    MIN_LENGTH,
    generatePassword,
    type PasswordOptions,
  } from '@/utils/passwordGenerator'

  defineProps<{ disabled?: boolean }>()
  const emit = defineEmits<{ use: [value: string] }>()
  const { t } = useI18n()

  const classKeys = ['lower', 'upper', 'digits', 'symbols', 'avoidAmbiguous'] as const
  const open = ref(false)
  const opts = reactive<PasswordOptions>({ ...DEFAULT_PASSWORD_OPTIONS })
  const candidate = ref('')

  function regenerate() {
    candidate.value = generatePassword({ ...opts })
  }

  function toggle() {
    open.value = !open.value
    if (open.value) regenerate()
  }

  function use() {
    emit('use', candidate.value)
    open.value = false
  }
</script>

<style scoped>
  .password-generator {
    display: flex;
    flex-direction: column;
    align-items: flex-start;
    gap: var(--fh-space-2);
  }

  .gen-panel {
    align-self: stretch;
    display: flex;
    flex-direction: column;
    gap: var(--fh-space-2);
    padding: var(--fh-space-3);
    background: var(--fh-paper-raised);
    border: var(--fh-border);
    border-radius: var(--fh-radius-sm);
  }

  .gen-length {
    display: flex;
    flex-direction: column;
    gap: var(--fh-space-1);
  }

  .gen-classes {
    display: flex;
    flex-wrap: wrap;
    gap: var(--fh-space-3);
  }

  .gen-class {
    display: inline-flex;
    gap: var(--fh-space-1);
    align-items: center;
    font-size: var(--fh-text-body-sm);
  }

  .gen-preview {
    margin: 0;
    padding: var(--fh-space-2) var(--fh-space-3);
    background: var(--fh-paper);
    border: var(--fh-border);
    border-radius: var(--fh-radius-sm);
    word-break: break-all;
    white-space: pre-wrap;
  }

  .gen-actions {
    display: flex;
    gap: var(--fh-space-3);
    align-items: center;
  }
</style>

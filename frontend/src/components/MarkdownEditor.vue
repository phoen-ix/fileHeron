<script setup lang="ts">
// Thin Milkdown wrapper - ALL Milkdown imports are confined to this file so the
// rest of the app is insulated from API churn, and this component is loaded via
// defineAsyncComponent so Milkdown lands in its own lazy chunk.
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'

import { Editor, defaultValueCtx, editorViewCtx, rootCtx } from '@milkdown/kit/core'
import {
  commonmark,
  toggleEmphasisCommand,
  toggleStrongCommand,
  wrapInBulletListCommand,
  wrapInHeadingCommand,
  wrapInOrderedListCommand,
} from '@milkdown/kit/preset/commonmark'
import { listener, listenerCtx } from '@milkdown/kit/plugin/listener'
import { callCommand, replaceAll } from '@milkdown/kit/utils'
import '@milkdown/kit/prose/view/style/prosemirror.css'

interface PlaceholderOption {
  token: string
  label: string
}

const props = withDefaults(
  defineProps<{
    modelValue: string
    placeholders?: PlaceholderOption[]
    disabled?: boolean
    ariaLabel?: string
  }>(),
  { placeholders: () => [], disabled: false, ariaLabel: '' },
)

const emit = defineEmits<{
  'update:modelValue': [string]
  ready: []
}>()

const host = ref<HTMLElement | null>(null)
let editor: Editor | null = null
// The last markdown value we set/emitted - guards the external-update watcher
// from echoing the editor's own changes back into a loop.
let lastValue = props.modelValue
let emitTimer: ReturnType<typeof setTimeout> | null = null

function run<T>(command: { key: T }, payload?: unknown) {
  editor?.action(callCommand(command.key as never, payload as never))
}

function insertText(token: string) {
  editor?.action((ctx) => {
    const view = ctx.get(editorViewCtx)
    const { state, dispatch } = view
    dispatch(state.tr.insertText(token, state.selection.from, state.selection.to))
    view.focus()
  })
}

function focus() {
  editor?.action((ctx) => ctx.get(editorViewCtx).focus())
}

defineExpose({ insertText, focus })

function onInsertPlaceholder(e: Event) {
  const sel = e.target as HTMLSelectElement
  if (sel.value) insertText(sel.value)
  sel.value = ''
}

watch(
  () => props.modelValue,
  (val) => {
    if (!editor || val === lastValue) return
    lastValue = val
    editor.action(replaceAll(val))
  },
)

onMounted(async () => {
  if (!host.value) return
  editor = await Editor.make()
    .config((ctx) => {
      ctx.set(rootCtx, host.value as HTMLElement)
      ctx.set(defaultValueCtx, props.modelValue)
      ctx.get(listenerCtx).markdownUpdated((_ctx, markdown) => {
        if (markdown === lastValue) return
        lastValue = markdown
        if (emitTimer) clearTimeout(emitTimer)
        emitTimer = setTimeout(() => emit('update:modelValue', markdown), 150)
      })
    })
    .use(commonmark)
    .use(listener)
    .create()
  emit('ready')
})

onBeforeUnmount(() => {
  if (emitTimer) clearTimeout(emitTimer)
  editor?.destroy()
  editor = null
})
</script>

<template>
  <div class="md-editor" :class="{ disabled }">
    <div class="md-toolbar" role="toolbar" :aria-label="ariaLabel || undefined">
      <button type="button" class="md-tool" :disabled="disabled" :title="$t('admin_email_templates.toolbar.bold')" @click="run(toggleStrongCommand)">
        <strong>B</strong>
      </button>
      <button type="button" class="md-tool" :disabled="disabled" :title="$t('admin_email_templates.toolbar.italic')" @click="run(toggleEmphasisCommand)">
        <em>I</em>
      </button>
      <button type="button" class="md-tool" :disabled="disabled" :title="$t('admin_email_templates.toolbar.heading')" @click="run(wrapInHeadingCommand, 2)">
        H
      </button>
      <button type="button" class="md-tool" :disabled="disabled" :title="$t('admin_email_templates.toolbar.bullet_list')" @click="run(wrapInBulletListCommand)">
        •
      </button>
      <button type="button" class="md-tool" :disabled="disabled" :title="$t('admin_email_templates.toolbar.ordered_list')" @click="run(wrapInOrderedListCommand)">
        1.
      </button>
      <span class="md-toolbar-spacer" />
      <select
        v-if="placeholders.length"
        class="md-placeholder-select"
        :disabled="disabled"
        :aria-label="$t('admin_email_templates.toolbar.insert_placeholder')"
        @change="onInsertPlaceholder"
      >
        <option value="">{{ $t('admin_email_templates.toolbar.insert_placeholder') }}</option>
        <option v-for="p in placeholders" :key="p.token" :value="p.token">
          {{ p.label }} - {{ p.token }}
        </option>
      </select>
    </div>
    <div ref="host" class="md-host" />
  </div>
</template>

<style scoped>
.md-editor {
  border: 1px solid var(--fh-border);
  border-radius: var(--fh-radius-md);
  background: var(--fh-paper-raised);
}
.md-editor.disabled {
  opacity: 0.6;
  pointer-events: none;
}
.md-toolbar {
  display: flex;
  align-items: center;
  gap: var(--fh-space-1);
  padding: var(--fh-space-2);
  border-bottom: 1px solid var(--fh-hairline);
}
.md-toolbar-spacer {
  flex: 1;
}
.md-tool {
  min-width: 2rem;
  padding: 0.25rem 0.5rem;
  border: 1px solid var(--fh-hairline);
  border-radius: var(--fh-radius-sm);
  background: var(--fh-paper);
  color: var(--fh-ink);
  font-family: var(--fh-font-body);
  cursor: pointer;
}
.md-tool:hover:not(:disabled) {
  border-color: var(--fh-border-strong);
}
.md-placeholder-select {
  padding: 0.25rem 0.5rem;
  border: 1px solid var(--fh-hairline);
  border-radius: var(--fh-radius-sm);
  background: var(--fh-paper);
  color: var(--fh-ink);
  font-family: var(--fh-font-mono);
  font-size: var(--fh-text-mono-sm);
  max-width: 16rem;
}
.md-host {
  padding: var(--fh-space-3);
  min-height: 14rem;
}
.md-host :deep(.ProseMirror) {
  outline: none;
  font-family: var(--fh-font-body);
  font-size: var(--fh-text-body-md);
  line-height: 1.55;
  color: var(--fh-ink);
}
.md-host :deep(.ProseMirror p) {
  margin: 0 0 0.75em 0;
}
.md-host :deep(.ProseMirror h1),
.md-host :deep(.ProseMirror h2),
.md-host :deep(.ProseMirror h3) {
  font-family: var(--fh-font-display);
  font-weight: normal;
  line-height: 1.2;
  margin: 0 0 0.5em 0;
}
.md-host :deep(.ProseMirror a) {
  color: var(--fh-accent);
}
.md-host :deep(.ProseMirror:focus) {
  outline: none;
}
</style>

<template>
  <div class="rt-editor" :class="{ disabled }">
    <!-- Every control here is a bare glyph (B, ↶, ⯇, </>). `title` is a
         tooltip, not an accessible name: a screen reader announced "button"
         and the glyph's own character name, so the whole toolbar was unusable
         without sight (audit 2026-07-30, fe-i18n-a11y-16). aria-label mirrors
         the already-localized title. -->
    <div class="rt-toolbar" role="toolbar" :aria-label="ariaLabel || undefined">
      <button type="button" class="rt-tool" :disabled="disabled" :title="t('richtext.undo')" :aria-label="t('richtext.undo')" @click="run(undo)">↶</button>
      <button type="button" class="rt-tool" :disabled="disabled" :title="t('richtext.redo')" :aria-label="t('richtext.redo')" @click="run(redo)">↷</button>
      <span class="rt-sep" />

      <select class="rt-select" :disabled="disabled" :title="t('richtext.block')" :aria-label="t('richtext.block')" :value="''" @change="onBlockSelect">
        <option value="" disabled>{{ t('richtext.block') }}</option>
        <option value="p">{{ t('richtext.paragraph') }}</option>
        <option v-for="lvl in [1, 2, 3, 4, 5, 6]" :key="lvl" :value="`h${lvl}`">H{{ lvl }}</option>
      </select>
      <span class="rt-sep" />

      <button type="button" class="rt-tool" :disabled="disabled" :title="t('richtext.bold')" :aria-label="t('richtext.bold')" @click="toggle('strong')"><strong>B</strong></button>
      <button type="button" class="rt-tool" :disabled="disabled" :title="t('richtext.italic')" :aria-label="t('richtext.italic')" @click="toggle('em')"><em>I</em></button>
      <button type="button" class="rt-tool" :disabled="disabled" :title="t('richtext.underline')" :aria-label="t('richtext.underline')" @click="toggle('underline')"><u>U</u></button>
      <button type="button" class="rt-tool" :disabled="disabled" :title="t('richtext.strike')" :aria-label="t('richtext.strike')" @click="toggle('strikethrough')"><s>S</s></button>
      <button type="button" class="rt-tool" :disabled="disabled" :title="t('richtext.code')" :aria-label="t('richtext.code')" @click="toggle('code')">&lt;/&gt;</button>
      <span class="rt-sep" />

      <button type="button" class="rt-tool" :disabled="disabled" :title="t('richtext.align_left')" :aria-label="t('richtext.align_left')" @click="run(setAlign('left'))">⯇</button>
      <button type="button" class="rt-tool" :disabled="disabled" :title="t('richtext.align_center')" :aria-label="t('richtext.align_center')" @click="run(setAlign('center'))">≡</button>
      <button type="button" class="rt-tool" :disabled="disabled" :title="t('richtext.align_right')" :aria-label="t('richtext.align_right')" @click="run(setAlign('right'))">⯈</button>
      <button type="button" class="rt-tool" :disabled="disabled" :title="t('richtext.align_justify')" :aria-label="t('richtext.align_justify')" @click="run(setAlign('justify'))">☰</button>
      <span class="rt-sep" />

      <button type="button" class="rt-tool" :disabled="disabled" :title="t('richtext.bullet_list')" :aria-label="t('richtext.bullet_list')" @click="run(wrapInList(schema.nodes.bullet_list))">•</button>
      <button type="button" class="rt-tool" :disabled="disabled" :title="t('richtext.ordered_list')" :aria-label="t('richtext.ordered_list')" @click="run(wrapInList(schema.nodes.ordered_list))">1.</button>
      <button type="button" class="rt-tool" :disabled="disabled" :title="t('richtext.blockquote')" :aria-label="t('richtext.blockquote')" @click="run(wrapIn(schema.nodes.blockquote))">❝</button>
      <button type="button" class="rt-tool" :disabled="disabled" :title="t('richtext.code_block')" :aria-label="t('richtext.code_block')" @click="run(setBlockType(schema.nodes.code_block))">{ }</button>
      <button type="button" class="rt-tool" :disabled="disabled" :title="t('richtext.hr')" :aria-label="t('richtext.hr')" @click="run(insertHr)">―</button>
      <span class="rt-sep" />

      <button type="button" class="rt-tool" :disabled="disabled" :title="t('richtext.link')" :aria-label="t('richtext.link')" @click="onLink">🔗</button>
      <button type="button" class="rt-tool" :disabled="disabled" :title="t('richtext.image')" :aria-label="t('richtext.image')" @click="onImage">🖼</button>
      <span class="rt-sep" />

      <button type="button" class="rt-tool" :disabled="disabled" :title="t('richtext.table')" :aria-label="t('richtext.table')" @click="run(insertTable)">▦</button>
      <button type="button" class="rt-tool" :disabled="disabled" :title="t('richtext.row_after')" :aria-label="t('richtext.row_after')" @click="run(addRowAfter)">+R</button>
      <button type="button" class="rt-tool" :disabled="disabled" :title="t('richtext.col_after')" :aria-label="t('richtext.col_after')" @click="run(addColumnAfter)">+C</button>
      <button type="button" class="rt-tool" :disabled="disabled" :title="t('richtext.del_row')" :aria-label="t('richtext.del_row')" @click="run(deleteRow)">−R</button>
      <button type="button" class="rt-tool" :disabled="disabled" :title="t('richtext.del_col')" :aria-label="t('richtext.del_col')" @click="run(deleteColumn)">−C</button>
      <button type="button" class="rt-tool" :disabled="disabled" :title="t('richtext.del_table')" :aria-label="t('richtext.del_table')" @click="run(deleteTable)">⌫▦</button>

      <template v-if="placeholders.length > 0">
        <span class="rt-toolbar-spacer" />
        <select class="rt-select rt-placeholder" :disabled="disabled" :value="''" @change="onPlaceholder">
          <option value="" disabled>{{ t('richtext.insert_placeholder') }}</option>
          <option v-for="p in placeholders" :key="p.token" :value="p.token">{{ p.label || p.token }}</option>
        </select>
      </template>
    </div>

    <div ref="host" class="rt-host" />
  </div>
</template>

<script setup lang="ts">
import { baseKeymap, chainCommands, exitCode, setBlockType, toggleMark, wrapIn } from 'prosemirror-commands'
import { history, redo, undo } from 'prosemirror-history'
import { keymap } from 'prosemirror-keymap'
import { liftListItem, sinkListItem, splitListItem, wrapInList } from 'prosemirror-schema-list'
import { dropCursor } from 'prosemirror-dropcursor'
import { gapCursor } from 'prosemirror-gapcursor'
import { EditorState, type Command } from 'prosemirror-state'
import {
  addColumnAfter,
  addRowAfter,
  columnResizing,
  deleteColumn,
  deleteRow,
  deleteTable,
  goToNextCell,
  tableEditing,
} from 'prosemirror-tables'
import { EditorView } from 'prosemirror-view'
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'

import { schema, type Alignment } from './richtext/schema'
import { docToHtml, htmlToDoc } from './richtext/html'

import 'prosemirror-view/style/prosemirror.css'
import 'prosemirror-gapcursor/style/gapcursor.css'
import 'prosemirror-tables/style/tables.css'

const props = withDefaults(
  defineProps<{
    modelValue: string
    placeholders?: { token: string; label?: string }[]
    disabled?: boolean
    ariaLabel?: string
  }>(),
  { placeholders: () => [], disabled: false, ariaLabel: '' },
)
const emit = defineEmits<{
  'update:modelValue': [value: string]
  ready: []
}>()

const { t } = useI18n()
const host = ref<HTMLElement | null>(null)
let view: EditorView | null = null
let lastValue = props.modelValue
let emitTimer: ReturnType<typeof setTimeout> | null = null

// --- commands -------------------------------------------------------------

const insertHr: Command = (state, dispatch) => {
  if (dispatch) dispatch(state.tr.replaceSelectionWith(schema.nodes.horizontal_rule.create()).scrollIntoView())
  return true
}

function setAlign(align: Alignment): Command {
  return (state, dispatch) => {
    const { from, to } = state.selection
    let tr = state.tr
    let found = false
    state.doc.nodesBetween(from, to, (node, pos) => {
      if (node.type === schema.nodes.paragraph || node.type === schema.nodes.heading) {
        tr = tr.setNodeMarkup(pos, undefined, { ...node.attrs, align })
        found = true
      }
    })
    if (found && dispatch) dispatch(tr)
    return found
  }
}

const insertTable: Command = (state, dispatch) => {
  const { table, table_row, table_cell } = schema.nodes
  const rows = []
  for (let r = 0; r < 3; r++) {
    const cells = []
    for (let c = 0; c < 3; c++) cells.push(table_cell.createAndFill()!)
    rows.push(table_row.create(null, cells))
  }
  if (dispatch) dispatch(state.tr.replaceSelectionWith(table.create(null, rows)).scrollIntoView())
  return true
}

function toggle(mark: 'strong' | 'em' | 'underline' | 'strikethrough' | 'code') {
  run(toggleMark(schema.marks[mark]))
}

function run(command: Command) {
  if (!view) return
  command(view.state, view.dispatch, view)
  view.focus()
}

function onBlockSelect(e: Event) {
  const value = (e.target as HTMLSelectElement).value
  if (value === 'p') run(setBlockType(schema.nodes.paragraph))
  else if (value) run(setBlockType(schema.nodes.heading, { level: Number(value.slice(1)) }))
  ;(e.target as HTMLSelectElement).value = ''
}

function onLink() {
  const href = window.prompt(t('richtext.link_prompt'))
  if (href === null) return
  if (href === '') run(toggleMark(schema.marks.link)) // empty clears
  else run(toggleMark(schema.marks.link, { href }))
}

function onImage() {
  const src = window.prompt(t('richtext.image_prompt'))
  if (!src) return
  run((state, dispatch) => {
    if (dispatch) dispatch(state.tr.replaceSelectionWith(schema.nodes.image.create({ src })).scrollIntoView())
    return true
  })
}

function insertText(text: string) {
  if (!view) return
  const { from, to } = view.state.selection
  view.dispatch(view.state.tr.insertText(text, from, to))
  view.focus()
}

function onPlaceholder(e: Event) {
  const el = e.target as HTMLSelectElement
  if (el.value) insertText(el.value)
  el.value = ''
}

defineExpose({ insertText })

// --- lifecycle ------------------------------------------------------------

const plugins = [
  history(),
  keymap({
    'Mod-b': toggleMark(schema.marks.strong),
    'Mod-i': toggleMark(schema.marks.em),
    'Mod-u': toggleMark(schema.marks.underline),
    'Mod-z': undo,
    'Mod-y': redo,
    'Shift-Mod-z': redo,
    Enter: splitListItem(schema.nodes.list_item),
    Tab: chainCommands(sinkListItem(schema.nodes.list_item), goToNextCell(1)),
    'Shift-Tab': chainCommands(liftListItem(schema.nodes.list_item), goToNextCell(-1)),
    'Shift-Enter': chainCommands(exitCode, (state, dispatch) => {
      if (dispatch) dispatch(state.tr.replaceSelectionWith(schema.nodes.hard_break.create()).scrollIntoView())
      return true
    }),
  }),
  keymap(baseKeymap),
  dropCursor(),
  gapCursor(),
  columnResizing(),
  tableEditing(),
]

function onDocChanged() {
  if (!view) return
  const html = docToHtml(view.state.doc)
  if (html === lastValue) return
  lastValue = html
  if (emitTimer) clearTimeout(emitTimer)
  emitTimer = setTimeout(() => emit('update:modelValue', html), 150)
}

onMounted(() => {
  if (!host.value) return
  const state = EditorState.create({ doc: htmlToDoc(props.modelValue), plugins })
  view = new EditorView(host.value, {
    state,
    editable: () => !props.disabled,
    attributes: { class: 'fh-prose', role: 'textbox', 'aria-label': props.ariaLabel || '' },
    dispatchTransaction(tr) {
      if (!view) return
      view.updateState(view.state.apply(tr))
      if (tr.docChanged) onDocChanged()
    },
  })
  emit('ready')
})

watch(
  () => props.modelValue,
  (val) => {
    if (!view || val === lastValue) return
    lastValue = val
    view.updateState(EditorState.create({ doc: htmlToDoc(val), plugins }))
  },
)

watch(
  () => props.disabled,
  () => view?.setProps({ editable: () => !props.disabled }),
)

onBeforeUnmount(() => {
  if (emitTimer) clearTimeout(emitTimer)
  view?.destroy()
  view = null
})
</script>

<style scoped>
.rt-editor {
  border: var(--fh-border);
  border-radius: var(--fh-radius-md);
  background: var(--fh-paper-raised);
}
.rt-editor.disabled {
  opacity: 0.6;
  pointer-events: none;
}
.rt-toolbar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--fh-space-1);
  padding: var(--fh-space-2);
  border-bottom: var(--fh-border);
}
.rt-sep {
  width: 1px;
  align-self: stretch;
  background: var(--fh-hairline);
  margin: 0 var(--fh-space-1);
}
.rt-toolbar-spacer {
  flex: 1;
}
.rt-tool {
  min-width: 2rem;
  padding: 0.25rem 0.45rem;
  border: var(--fh-border);
  border-radius: var(--fh-radius-sm);
  background: var(--fh-paper);
  color: var(--fh-ink);
  font: inherit;
  font-size: var(--fh-text-body-sm);
  cursor: pointer;
  line-height: 1.1;
}
.rt-tool:hover:not(:disabled) {
  background: var(--fh-paper-sunk);
}
.rt-select {
  font: inherit;
  font-size: var(--fh-text-body-sm);
  padding: 0.25rem 0.4rem;
  border: var(--fh-border);
  border-radius: var(--fh-radius-sm);
  background: var(--fh-paper);
  color: var(--fh-ink);
}
.rt-placeholder {
  font-family: var(--fh-font-mono);
  max-width: 16rem;
}
.rt-host {
  padding: var(--fh-space-3);
  min-height: 14rem;
}
.rt-host :deep(.ProseMirror) {
  outline: none;
  font-family: var(--fh-font-body);
  color: var(--fh-ink);
  line-height: 1.55;
}
.rt-host :deep(.ProseMirror p) {
  margin: 0 0 0.75em;
}
.rt-host :deep(.text-left) {
  text-align: left;
}
.rt-host :deep(.text-center) {
  text-align: center;
}
.rt-host :deep(.text-right) {
  text-align: right;
}
.rt-host :deep(.text-justify) {
  text-align: justify;
}
.rt-host :deep(.ProseMirror h1),
.rt-host :deep(.ProseMirror h2),
.rt-host :deep(.ProseMirror h3),
.rt-host :deep(.ProseMirror h4) {
  font-family: var(--fh-font-display);
  font-weight: 400;
  line-height: 1.2;
  margin: 0.6em 0 0.4em;
}
.rt-host :deep(.ProseMirror a) {
  color: var(--fh-accent);
}
.rt-host :deep(.ProseMirror blockquote) {
  border-left: 2px solid var(--fh-hairline);
  margin: 0 0 0.75em;
  padding-left: var(--fh-space-3);
  color: var(--fh-ink-soft);
}
.rt-host :deep(.ProseMirror img) {
  max-width: 100%;
  height: auto;
}
.rt-host :deep(.ProseMirror table) {
  border-collapse: collapse;
  width: 100%;
  margin: 0 0 0.75em;
}
.rt-host :deep(.ProseMirror td),
.rt-host :deep(.ProseMirror th) {
  border: 1px solid var(--fh-hairline);
  padding: 0.3em 0.5em;
  vertical-align: top;
}
</style>

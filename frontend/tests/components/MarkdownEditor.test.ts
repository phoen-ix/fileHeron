import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'

// Milkdown/ProseMirror is unreliable under happy-dom, so mock the kit internals
// and assert our wrapper's wiring (toolbar render, placeholder insert, v-model).
const shared = vi.hoisted(() => {
  const view = {
    state: { selection: { from: 1, to: 1 }, tr: { insertText: () => ({}) } },
    dispatch: vi.fn(),
    focus: vi.fn(),
  }
  return { view, listenerCb: null as null | ((c: unknown, md: string) => void) }
})

vi.mock('@milkdown/kit/prose/view/style/prosemirror.css', () => ({}))

vi.mock('@milkdown/kit/core', () => {
  const ctx = {
    set: vi.fn(),
    get: (slice: unknown) =>
      slice === 'LISTENER'
        ? { markdownUpdated: (cb: (c: unknown, md: string) => void) => (shared.listenerCb = cb) }
        : shared.view,
  }
  const builder: Record<string, unknown> = {}
  builder.config = (fn: (c: unknown) => void) => {
    fn(ctx)
    return builder
  }
  builder.use = () => builder
  builder.create = async () => ({
    action: (fn: unknown) => (typeof fn === 'function' ? (fn as (c: unknown) => unknown)(ctx) : undefined),
    destroy: () => {},
  })
  return { Editor: { make: () => builder }, rootCtx: 'ROOT', defaultValueCtx: 'DEFAULT', editorViewCtx: 'VIEW' }
})

vi.mock('@milkdown/kit/preset/commonmark', () => ({
  commonmark: {},
  toggleStrongCommand: { key: 'strong' },
  toggleEmphasisCommand: { key: 'em' },
  wrapInBulletListCommand: { key: 'ul' },
  wrapInHeadingCommand: { key: 'h' },
  wrapInOrderedListCommand: { key: 'ol' },
}))

vi.mock('@milkdown/kit/plugin/listener', () => ({ listener: {}, listenerCtx: 'LISTENER' }))
vi.mock('@milkdown/kit/utils', () => ({
  callCommand: () => () => {},
  replaceAll: () => () => {},
}))

import MarkdownEditor from '@/components/MarkdownEditor.vue'

const i18nStub = { mocks: { $t: (k: string) => k } }

afterEach(() => {
  shared.view.dispatch.mockClear()
  shared.listenerCb = null
})

describe('MarkdownEditor', () => {
  it('mounts, emits ready, and renders the toolbar', async () => {
    const w = mount(MarkdownEditor, {
      props: { modelValue: 'hi', placeholders: [{ token: '[X]', label: 'X' }] },
      global: i18nStub,
    })
    await flushPromises()
    expect(w.emitted('ready')).toBeTruthy()
    expect(w.findAll('.md-tool')).toHaveLength(5)
    const opts = w.findAll('.md-placeholder-select option')
    expect(opts).toHaveLength(2) // placeholder prompt + [X]
    expect(opts[1].text()).toContain('[X]')
  })

  it('insertText dispatches into the editor view', async () => {
    const w = mount(MarkdownEditor, {
      props: { modelValue: '', placeholders: [{ token: '[X]', label: 'X' }] },
      global: i18nStub,
    })
    await flushPromises()
    const select = w.find('.md-placeholder-select')
    ;(select.element as HTMLSelectElement).value = '[X]'
    await select.trigger('change')
    expect(shared.view.dispatch).toHaveBeenCalled()
  })

  it('emits update:modelValue when Milkdown reports a change', async () => {
    vi.useFakeTimers()
    const w = mount(MarkdownEditor, { props: { modelValue: 'a' }, global: i18nStub })
    await flushPromises()
    expect(shared.listenerCb).toBeTruthy()
    shared.listenerCb!(null, 'new markdown')
    vi.advanceTimersByTime(200)
    vi.useRealTimers()
    expect(w.emitted('update:modelValue')?.[0]).toEqual(['new markdown'])
  })
})

/* The guard in tests/setup/noVueWarnings.ts. Without it, a Vue warning in a
 * test goes to stderr and the test stays green. This file never imports the
 * guard - it must already be installed by vitest.config.ts `setupFiles`, as it
 * is for every other test file. */
import { mount } from '@vue/test-utils'
import { defineComponent, h } from 'vue'
import { describe, expect, it } from 'vitest'

import type { NoVueWarningsGuard } from './noVueWarnings'

const guard = (globalThis as { __noVueWarnings?: NoVueWarningsGuard }).__noVueWarnings

const NeedsAString = defineComponent({
  props: { active: { type: String, required: true } },
  setup: (props) => () => h('span', props.active),
})

describe('the no-Vue-warnings guard', () => {
  it('is installed for every test file by the vitest config', () => {
    expect(guard).toBeDefined()
  })

  it('collects a warning a mounted component raises', () => {
    mount(NeedsAString, { props: { active: null as unknown as string } })
    const taken = guard?.take() ?? []
    expect(taken).toHaveLength(1)
    expect(taken[0]).toMatch(/Invalid prop: type check failed for prop "active"/)
  })

  it('collects an unresolved component', () => {
    mount(defineComponent({ template: '<RouterLink to="/">x</RouterLink>' }))
    expect(guard?.take().join('\n')).toMatch(/Failed to resolve component: RouterLink/)
  })

  it('leaves nothing behind once the warnings are taken', () => {
    mount(NeedsAString, { props: { active: 'ok' } })
    expect(guard?.take()).toEqual([])
  })
})

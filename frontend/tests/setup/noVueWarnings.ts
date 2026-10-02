/* No frontend test may leave a Vue warning behind.
 *
 * A `[Vue warn]` goes to stderr and the test stays green, so the warnings
 * printed in every run and nobody read them: twenty "Failed to resolve
 * component: RouterLink" from a test that rendered a link it never stubbed,
 * and an "Invalid prop" from a mock returning `null` where the real composable
 * returns a string. Every warning a mounted component raises is collected here
 * and fails the test that raised it (from the afterEach below - the warning
 * itself is only a call, nothing throws). Stub what the component needs, or
 * mock with the real shape.
 *
 * Only components mounted through @vue/test-utils are covered: `config.global`
 * is what every mount() merges into its app. */
import { config } from '@vue/test-utils'
import { afterEach } from 'vitest'

const warnings: string[] = []

/** Hand over, and clear, the warnings collected so far. */
function takeVueWarnings(): string[] {
  return warnings.splice(0)
}

// The guard's own test reads this handle instead of importing the module, so
// it also proves vitest.config.ts installs the guard for every test file.
export interface NoVueWarningsGuard {
  take: () => string[]
}
;(globalThis as { __noVueWarnings?: NoVueWarningsGuard }).__noVueWarnings = {
  take: takeVueWarnings,
}

config.global.config.warnHandler = (msg, _instance, trace) => {
  warnings.push(trace ? `${msg}\n${trace}` : msg)
}

afterEach(() => {
  const raised = takeVueWarnings()
  if (raised.length) {
    throw new Error(`a test raised ${raised.length} Vue warning(s):\n${raised.join('\n---\n')}`)
  }
})

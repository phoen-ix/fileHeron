import { computed, ref } from 'vue'

export type SortDir = 'asc' | 'desc'

export interface UseTableSortOptions {
  defaultBy: string
  defaultDir?: SortDir
}

export interface UseTableSortReturn {
  sortBy: ReturnType<typeof ref<string>>
  sortDir: ReturnType<typeof ref<SortDir>>
  toggle: (column: string) => void
  indicator: (column: string) => '↑' | '↓' | ''
  ariaSort: (column: string) => 'ascending' | 'descending' | 'none'
  reset: () => void
}

/**
 * Sortable-table primitive shared by /outbox, /inbox, and the admin
 * file history table. Click cycles `asc → desc → off`. "Off" resets
 * to the configured default column + direction.
 */
export function useTableSort(opts: UseTableSortOptions): UseTableSortReturn {
  const defaultDir: SortDir = opts.defaultDir ?? 'desc'
  const sortBy = ref<string>(opts.defaultBy)
  const sortDir = ref<SortDir>(defaultDir)

  function toggle(column: string) {
    if (sortBy.value !== column) {
      sortBy.value = column
      sortDir.value = 'asc'
      return
    }
    if (sortDir.value === 'asc') {
      sortDir.value = 'desc'
      return
    }
    // Was desc → off → reset to default.
    sortBy.value = opts.defaultBy
    sortDir.value = defaultDir
  }

  function indicator(column: string): '↑' | '↓' | '' {
    if (sortBy.value !== column) return ''
    return sortDir.value === 'asc' ? '↑' : '↓'
  }

  function ariaSort(column: string): 'ascending' | 'descending' | 'none' {
    if (sortBy.value !== column) return 'none'
    return sortDir.value === 'asc' ? 'ascending' : 'descending'
  }

  function reset() {
    sortBy.value = opts.defaultBy
    sortDir.value = defaultDir
  }

  return {
    sortBy,
    sortDir,
    toggle,
    indicator,
    ariaSort,
    reset,
  }
}

import { ref } from 'vue'

/* Why a filtered list is empty.
 *
 * A list that opens on a filter ("Open" requests, "Active" secrets) cannot
 * tell from its own empty page whether the box is empty or the filter hides
 * what is there, and the two need different words: someone whose one request
 * had just been answered - it is "Closed" then - read "You haven't asked for a
 * secret yet." So when a NARROWED page comes back empty, the list asks once
 * how many rows the box holds without the filter. A page with rows, or an
 * unfiltered one, costs no extra request. */
export function useHiddenByFilter() {
  /** The list is empty only because of the filter or the search. */
  const hiddenByFilter = ref(false)
  let asked = 0

  /** Pass a loaded page through; `countAll` is asked only when it is empty and
   * `narrowed`. A slower, older answer never overwrites a newer one. */
  async function check<T extends { total?: number }>(
    page: T,
    narrowed: boolean,
    countAll: () => Promise<number>,
  ): Promise<T> {
    const mine = ++asked
    const hidden = (page.total ?? 0) === 0 && narrowed ? (await countAll()) > 0 : false
    if (mine === asked) hiddenByFilter.value = hidden
    return page
  }

  return { hiddenByFilter, check }
}

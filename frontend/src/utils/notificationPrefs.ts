import type { PreferenceItem } from '@/types/api'

/** The key one preference row is saved under: its category, or
 *  `<category>:<topic>` for a category chosen per topic (an operational
 *  alert's reason). The server's `notification_prefs.TOPIC_SEP`. */
export function preferenceKey(item: Pick<PreferenceItem, 'category' | 'topic'>): string {
  return item.topic ? `${item.category}:${item.topic}` : item.category
}

/**
 * HTML <-> ProseMirror document conversion. The editor's external value is an
 * HTML string; these are the only bridge between that and the doc model.
 */
import { DOMParser, DOMSerializer, type Node as PMNode } from 'prosemirror-model'

import { schema } from './schema'

/** Parse an HTML fragment into a ProseMirror document (unknown markup dropped). */
export function htmlToDoc(html: string): PMNode {
  const el = document.createElement('div')
  el.innerHTML = html || ''
  return DOMParser.fromSchema(schema).parse(el)
}

/** Serialise a ProseMirror document back to an HTML string. */
export function docToHtml(doc: PMNode): string {
  const fragment = DOMSerializer.fromSchema(schema).serializeFragment(doc.content)
  const div = document.createElement('div')
  div.appendChild(fragment)
  return div.innerHTML
}

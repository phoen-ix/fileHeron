/**
 * HTML <-> ProseMirror document conversion. The editor's external value is an
 * HTML string; these are the only bridge between that and the doc model.
 */
import { DOMParser, DOMSerializer, type Node as PMNode } from 'prosemirror-model'

import { schema } from './schema'

/** Parse an HTML fragment into a ProseMirror document (unknown markup dropped).
 *
 * Parsed into an INERT document, not the live one. `document.createElement`
 * produces a node belonging to the active document, so assigning to its
 * `innerHTML` makes the browser instantiate the markup immediately - and
 * `<img src=x onerror=...>` fires there and then, before ProseMirror ever gets
 * to drop the unknown element. The "unknown markup dropped" contract describes
 * the resulting doc, not the parse, and content reaching here is not guaranteed
 * sanitised: email-template bodies are stored raw so token placeholders survive
 * the editor, and a config-backup import can write them wholesale (audit
 * 2026-07-30).
 *
 * A document from `createHTMLDocument()` has no browsing context, so scripts and
 * event handlers in it never execute and resource URLs are never fetched. This
 * is the same reason sanitiser libraries parse into an inert document.
 */
const inertDoc = document.implementation.createHTMLDocument('richtext')

export function htmlToDoc(html: string): PMNode {
  const el = inertDoc.createElement('div')
  el.innerHTML = html || ''
  stripTableWidths(el)
  return DOMParser.fromSchema(schema).parse(el)
}

/** Drop column widths from pasted tables.
 *
 * prosemirror-tables' cell spec always carries a `colwidth` attribute and
 * parses it back out of `<td width>` / `<colgroup>`, so a table pasted from a
 * word processor arrived WITH widths, rendered with them in the editor - and
 * then lost them on save, because the backend sanitiser's allowlist has no
 * `col`, `colgroup` or width attribute (services/richtext.py). The user saw
 * their layout revert with no explanation, every time (audit 2026-07-30,
 * fe-xss-3).
 *
 * Dropping them here rather than widening the sanitiser keeps ONE definition
 * of what HTML is allowed - the backend's - and makes the editor show what will
 * actually be stored. */
function stripTableWidths(root: HTMLElement): void {
  root.querySelectorAll('colgroup, col').forEach((el) => el.remove())
  root.querySelectorAll('td[width], th[width]').forEach((el) => {
    el.removeAttribute('width')
  })
  root.querySelectorAll('table[style], td[style], th[style], col[style]').forEach(
    (el) => el.removeAttribute('style'),
  )
}

/** Serialise a ProseMirror document back to an HTML string. */
export function docToHtml(doc: PMNode): string {
  const fragment = DOMSerializer.fromSchema(schema).serializeFragment(doc.content)
  const div = document.createElement('div')
  div.appendChild(fragment)
  return div.innerHTML
}

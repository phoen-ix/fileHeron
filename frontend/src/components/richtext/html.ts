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
  return DOMParser.fromSchema(schema).parse(el)
}

/** Serialise a ProseMirror document back to an HTML string. */
export function docToHtml(doc: PMNode): string {
  const fragment = DOMSerializer.fromSchema(schema).serializeFragment(doc.content)
  const div = document.createElement('div')
  div.appendChild(fragment)
  return div.innerHTML
}

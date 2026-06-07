/**
 * ProseMirror schema for the rich-text (HTML) editor. MIT - built directly on
 * prosemirror-model, no editor vendor. The schema maps 1:1 to the HTML the
 * backend sanitiser (services/richtext.py) allows; alignment is a node attr
 * serialised to a `text-{left,center,right,justify}` CLASS (never inline style)
 * so it survives sanitisation.
 */
import OrderedMap from 'orderedmap'
import { Schema, type MarkSpec, type NodeSpec } from 'prosemirror-model'
import { addListNodes } from 'prosemirror-schema-list'
import { tableNodes } from 'prosemirror-tables'

export const ALIGNMENTS = ['left', 'center', 'right', 'justify'] as const
export type Alignment = (typeof ALIGNMENTS)[number]

function alignFromDOM(dom: HTMLElement): Alignment | null {
  const classes = (dom.getAttribute('class') || '').split(/\s+/)
  for (const a of ALIGNMENTS) if (classes.includes(`text-${a}`)) return a
  return null
}

function alignToDOM(align: Alignment | null): Record<string, string> {
  return align ? { class: `text-${align}` } : {}
}

const HEADING_LEVELS = [1, 2, 3, 4, 5, 6]

const nodeSpec: Record<string, NodeSpec> = {
  doc: { content: 'block+' },

  paragraph: {
    group: 'block',
    content: 'inline*',
    attrs: { align: { default: null } },
    parseDOM: [{ tag: 'p', getAttrs: (d) => ({ align: alignFromDOM(d as HTMLElement) }) }],
    toDOM: (node) => ['p', alignToDOM(node.attrs.align as Alignment | null), 0],
  },

  heading: {
    group: 'block',
    content: 'inline*',
    defining: true,
    attrs: { level: { default: 1 }, align: { default: null } },
    parseDOM: HEADING_LEVELS.map((level) => ({
      tag: `h${level}`,
      getAttrs: (d: string | HTMLElement) => ({
        level,
        align: alignFromDOM(d as HTMLElement),
      }),
    })),
    toDOM: (node) => [
      `h${node.attrs.level}`,
      alignToDOM(node.attrs.align as Alignment | null),
      0,
    ],
  },

  blockquote: {
    group: 'block',
    content: 'block+',
    defining: true,
    parseDOM: [{ tag: 'blockquote' }],
    toDOM: () => ['blockquote', 0],
  },

  code_block: {
    group: 'block',
    content: 'text*',
    marks: '',
    code: true,
    defining: true,
    parseDOM: [{ tag: 'pre', preserveWhitespace: 'full' }],
    toDOM: () => ['pre', ['code', 0]],
  },

  horizontal_rule: {
    group: 'block',
    parseDOM: [{ tag: 'hr' }],
    toDOM: () => ['hr'],
  },

  image: {
    group: 'inline',
    inline: true,
    draggable: true,
    attrs: { src: {}, alt: { default: null }, title: { default: null } },
    parseDOM: [
      {
        tag: 'img[src]',
        getAttrs: (d) => {
          const e = d as HTMLElement
          return {
            src: e.getAttribute('src'),
            alt: e.getAttribute('alt'),
            title: e.getAttribute('title'),
          }
        },
      },
    ],
    toDOM: (node) => {
      const { src, alt, title } = node.attrs
      const attrs: Record<string, string> = { src }
      if (alt) attrs.alt = alt
      if (title) attrs.title = title
      return ['img', attrs]
    },
  },

  hard_break: {
    group: 'inline',
    inline: true,
    selectable: false,
    parseDOM: [{ tag: 'br' }],
    toDOM: () => ['br'],
  },

  text: { group: 'inline' },
}

const markSpec: Record<string, MarkSpec> = {
  strong: {
    parseDOM: [
      { tag: 'strong' },
      { tag: 'b', getAttrs: (n) => (n as HTMLElement).style.fontWeight !== 'normal' && null },
      { style: 'font-weight=bold' },
    ],
    toDOM: () => ['strong', 0],
  },
  em: {
    parseDOM: [{ tag: 'em' }, { tag: 'i' }, { style: 'font-style=italic' }],
    toDOM: () => ['em', 0],
  },
  underline: {
    parseDOM: [{ tag: 'u' }, { style: 'text-decoration=underline' }],
    toDOM: () => ['u', 0],
  },
  strikethrough: {
    parseDOM: [{ tag: 's' }, { tag: 'strike' }, { tag: 'del' }, { style: 'text-decoration=line-through' }],
    toDOM: () => ['s', 0],
  },
  code: {
    parseDOM: [{ tag: 'code' }],
    toDOM: () => ['code', 0],
  },
  link: {
    attrs: { href: {}, title: { default: null } },
    inclusive: false,
    parseDOM: [
      {
        tag: 'a[href]',
        getAttrs: (d) => {
          const e = d as HTMLElement
          return { href: e.getAttribute('href'), title: e.getAttribute('title') }
        },
      },
    ],
    toDOM: (mark) => {
      const { href, title } = mark.attrs
      const attrs: Record<string, string> = { href, rel: 'noopener noreferrer nofollow' }
      if (title) attrs.title = title
      return ['a', attrs, 0]
    },
  },
}

// Compose: base nodes + list nodes + table nodes.
let nodes = OrderedMap.from(nodeSpec)
nodes = addListNodes(nodes, 'paragraph block*', 'block')
nodes = nodes.append(
  tableNodes({ tableGroup: 'block', cellContent: 'block+', cellAttributes: {} }),
)

export const schema = new Schema({ nodes, marks: markSpec })

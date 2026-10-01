/* Random passwords for the secret compose form (v2.24.0).
 *
 * Built on `crypto.getRandomValues` with rejection sampling: `n % len` over a
 * 32-bit value favours the first `2^32 % len` characters, a small but real
 * bias in exactly the place a password must not have one. Every class the
 * caller asks for is guaranteed to appear, by drawing again rather than by
 * planting a character at a predictable position. */

export interface PasswordOptions {
  length: number
  lower: boolean
  upper: boolean
  digits: boolean
  symbols: boolean
  /** Leave out characters people confuse when reading aloud or retyping. */
  avoidAmbiguous: boolean
}

export const DEFAULT_PASSWORD_OPTIONS: PasswordOptions = {
  length: 20,
  lower: true,
  upper: true,
  digits: true,
  symbols: true,
  avoidAmbiguous: true,
}

export const MIN_LENGTH = 8
export const MAX_LENGTH = 64

const AMBIGUOUS = new Set('0O1lI|`\'"'.split(''))
const SETS = {
  lower: 'abcdefghijklmnopqrstuvwxyz',
  upper: 'ABCDEFGHIJKLMNOPQRSTUVWXYZ',
  digits: '0123456789',
  symbols: '!#$%&*+-=?@^_~.,:;()[]{}<>/',
} as const

function classes(opts: PasswordOptions): string[] {
  const picked = (Object.keys(SETS) as (keyof typeof SETS)[])
    .filter((k) => opts[k])
    .map((k) => [...SETS[k]].filter((c) => !opts.avoidAmbiguous || !AMBIGUOUS.has(c)).join(''))
  return picked.length ? picked : [SETS.lower]
}

/** Fills a buffer with random values - `crypto.getRandomValues` in the app,
 *  a scripted source in tests. */
export type RandomFill = (buf: Uint32Array<ArrayBuffer>) => unknown

/** A uniform index in [0, n). */
export function randomIndex(n: number, rand: RandomFill): number {
  const limit = Math.floor(0x1_0000_0000 / n) * n
  const buf = new Uint32Array(1)
  for (;;) {
    rand(buf)
    if (buf[0] < limit) return buf[0] % n
  }
}

export function generatePassword(
  opts: PasswordOptions = DEFAULT_PASSWORD_OPTIONS,
  rand: RandomFill = (buf) => crypto.getRandomValues(buf),
): string {
  const sets = classes(opts)
  const alphabet = sets.join('')
  const length = Math.min(MAX_LENGTH, Math.max(MIN_LENGTH, Math.round(opts.length)))
  for (;;) {
    let out = ''
    for (let i = 0; i < length; i++) out += alphabet[randomIndex(alphabet.length, rand)]
    if (sets.every((set) => [...out].some((c) => set.includes(c)))) return out
  }
}

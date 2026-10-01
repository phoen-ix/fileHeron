/* The secret form's password generator (v2.24.0): uniform, every class it
 * claims present, never the look-alikes when asked, length clamped. */
import { describe, expect, it } from 'vitest'

import {
  DEFAULT_PASSWORD_OPTIONS,
  MAX_LENGTH,
  MIN_LENGTH,
  generatePassword,
  randomIndex,
  type RandomFill,
} from '@/utils/passwordGenerator'

/** A scripted random source: hands out `values` in order, then repeats. */
function scripted(values: number[]): RandomFill {
  let i = 0
  return (buf) => {
    buf[0] = values[i++ % values.length]
  }
}

describe('randomIndex', () => {
  it('rejects the biased tail instead of folding it back with %', () => {
    // n = 7: floor(2^32 / 7) * 7 = 4294967292. A draw at or above it would
    // favour the first few characters if reduced with %, so it is redrawn.
    const limit = Math.floor(0x1_0000_0000 / 7) * 7
    expect(limit).toBe(4294967292)
    const idx = randomIndex(7, scripted([limit, 0xffffffff, 10]))
    expect(idx).toBe(10 % 7)
  })

  it('stays in range', () => {
    for (let n = 1; n < 50; n++) {
      expect(randomIndex(n, scripted([0xffffffff, 12345]))).toBeLessThan(n)
    }
  })
})

describe('generatePassword', () => {
  it('uses the real CSPRNG by default and honours the length', () => {
    const pw = generatePassword()
    expect(pw).toHaveLength(DEFAULT_PASSWORD_OPTIONS.length)
    expect(generatePassword()).not.toBe(pw)
  })

  it('contains every class it was asked for', () => {
    for (let i = 0; i < 50; i++) {
      const pw = generatePassword({ ...DEFAULT_PASSWORD_OPTIONS, length: 8 })
      expect(pw).toMatch(/[a-z]/)
      expect(pw).toMatch(/[A-Z]/)
      expect(pw).toMatch(/[0-9]/)
      expect(pw).toMatch(/[^A-Za-z0-9]/)
    }
  })

  it('leaves out look-alikes when asked', () => {
    for (let i = 0; i < 50; i++) {
      expect(generatePassword({ ...DEFAULT_PASSWORD_OPTIONS, length: 64 })).not.toMatch(/[0O1lI]/)
    }
  })

  it('only uses the classes it was given', () => {
    const pw = generatePassword({
      length: 30,
      lower: false,
      upper: false,
      digits: true,
      symbols: false,
      avoidAmbiguous: false,
    })
    expect(pw).toMatch(/^[0-9]{30}$/)
  })

  it('clamps the length', () => {
    expect(generatePassword({ ...DEFAULT_PASSWORD_OPTIONS, length: 2 })).toHaveLength(MIN_LENGTH)
    expect(generatePassword({ ...DEFAULT_PASSWORD_OPTIONS, length: 999 })).toHaveLength(MAX_LENGTH)
  })
})

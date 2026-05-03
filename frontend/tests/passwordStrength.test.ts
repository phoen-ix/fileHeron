import { describe, expect, it } from 'vitest'

import { scorePassword } from '@/composables/usePasswordStrength'

describe('scorePassword', () => {
  it('returns weak for empty', () => {
    expect(scorePassword('')).toEqual({ score: 0, label: 'weak' })
  })

  it('returns weak for very short passwords', () => {
    expect(scorePassword('abc').label).toBe('weak')
    expect(scorePassword('1234567').label).toBe('weak')
  })

  it('returns fair for 12 chars all-lowercase (one class)', () => {
    // Length carries weight per NIST 800-63B; one-class at 12+ chars is
    // mediocre but not "weak".
    expect(scorePassword('aaaaaaaaaaaa').label).toBe('fair')
  })

  it('returns fair for 8-11 chars with 3 classes', () => {
    expect(scorePassword('Abc12345').label).toBe('fair')
  })

  it('returns good for 12-15 chars with 3+ classes', () => {
    expect(scorePassword('Abc12345!xyz').label).toBe('good')
  })

  it('returns strong for 16+ chars with 2+ classes', () => {
    expect(scorePassword('LongCorrectHorse123!').label).toBe('strong')
  })

  it('does not crash on whitespace and unicode', () => {
    expect(() => scorePassword('   ')).not.toThrow()
    expect(() => scorePassword('пароль123!ABC')).not.toThrow()
  })
})

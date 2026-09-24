/* Android user agents contain "Linux; Android" and iOS ones "like Mac OS X";
 * the OS was tested Linux/macOS first, so every phone session was listed as
 * "Linux" or "macOS" in the session lists people use to decide what to revoke. */
import { describe, expect, it } from 'vitest'

import { uaShort } from '@/utils/ua'

describe('uaShort', () => {
  it('names phones as phones', () => {
    expect(
      uaShort('Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0 Mobile Safari/537.36'),
    ).toBe('Chrome · Android')
    expect(
      uaShort('Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1'),
    ).toBe('Safari · iOS')
  })

  it('still names desktops', () => {
    expect(
      uaShort('Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15'),
    ).toBe('Safari · macOS')
    expect(
      uaShort('Mozilla/5.0 (X11; Linux x86_64; rv:130.0) Gecko/20100101 Firefox/130.0'),
    ).toBe('Firefox · Linux')
  })
})

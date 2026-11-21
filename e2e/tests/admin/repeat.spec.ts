import { api, confirm, expect, newUser, test, toast, uid } from './_admin'

/* The same thing done twice in a row in the admin console, by mouse and typing
 * alone (COVERAGE.b.md, section R).
 *
 * The other journeys do each action once on a fresh page; "a second recipient
 * cannot be added" shipped that way (fixed 2026-11-20), and reading the code
 * for the same shape found it again here: the setting search stayed shut after
 * Escape, and adding a group member rebuilt the whole page each time. Between
 * the two uses nothing happens that a user would not do. A step added to make
 * one of these pass is the defect. */

const OK_SINK = 'http://backend:8000/api/telemetry/page-404'

test('the setting search opens again when the user types, or arrows, after Escape', async ({
  admin: page,
}) => {
  await page.goto('/admin')
  const search = page.getByRole('combobox', { name: 'Find a setting' })
  const options = page.getByRole('listbox', { name: 'Search results' }).getByRole('option')
  await search.click()
  await page.keyboard.type('timezone')
  await expect(options.first()).toBeVisible()

  await page.keyboard.press('Escape')
  await expect(options.first()).toBeHidden()
  await page.keyboard.press('Backspace')
  await expect(options.first()).toBeVisible()

  await page.keyboard.press('Escape')
  await expect(options.first()).toBeHidden()
  await page.keyboard.press('ArrowDown')
  await expect(options.first()).toBeVisible()
  await page.keyboard.press('Enter')
  await expect(page).toHaveURL(/\/admin\/settings\/general/)
})

test('group members: two added in a row, and the page is not rebuilt in between', async ({
  admin: page,
}) => {
  const s = uid()
  const g = await api<{ id: number }>('/api/groups', {
    method: 'POST',
    body: JSON.stringify({ name: `Twice ${s}`, description: '' }),
  })
  const people = [await newUser('employee', `first-${s}`), await newUser('employee', `second-${s}`)]
  await page.goto(`/admin/groups/${g.id}`)
  const search = page.getByRole('textbox', { name: 'Search to add a member…' })
  // A mark on the element itself: a field the page rebuilt is a new element
  // without it, and with it went the focus and the scroll position.
  await search.evaluate((el) => el.setAttribute('data-same-element', 'yes'))

  for (const [i, person] of people.entries()) {
    await search.fill(person.email)
    await page.locator('.add-result', { hasText: person.email }).getByRole('button', { name: 'Add' }).click()
    await expect(page.locator('.member-row', { hasText: person.email })).toBeVisible()
    await expect(page.getByRole('heading', { name: `Members (${i + 1})` })).toBeVisible()
    await expect(search).toHaveAttribute('data-same-element', 'yes')
    await expect(search).toHaveValue('')
    await expect(page.locator('.add-result')).toHaveCount(0)
  }
})

test('a policy allowlist takes two people in a row', async ({ admin: page }) => {
  const s = uid()
  const people = [await newUser('employee', `allow-a-${s}`), await newUser('employee', `allow-b-${s}`)]
  // Nothing is saved: the list is built in the form and the page is left.
  await page.goto('/admin/settings/api-tokens')
  await page.getByRole('radio', { name: /^Admins only/ }).check()
  const search = page.getByRole('searchbox', { name: 'Search by name or email…' })
  for (const person of people) {
    await search.fill(person.email)
    await page.locator('.user-suggest', { hasText: person.email }).click()
    await expect(page.locator('.picked-row', { hasText: person.email })).toBeVisible()
    await expect(search).toHaveValue('')
    await expect(page.locator('.user-suggest')).toHaveCount(0)
  }
  await expect(page.locator('.picked-row')).toHaveCount(2)
})

test('the token owner picker takes a second person over the first', async ({ admin: page }) => {
  const s = uid()
  const first = await newUser('employee', `owner-a-${s}`)
  const second = await newUser('employee', `owner-b-${s}`)
  await page.goto('/admin/api-tokens')
  await page.getByRole('button', { name: /generate token for user/i }).click()
  const search = page.getByRole('searchbox')

  await search.fill(first.email)
  await page.locator('.user-suggest', { hasText: first.email }).click()
  await expect(page.locator('.picked-user')).toContainText(first.email)
  await expect(page.locator('.user-suggest')).toHaveCount(0)

  // Typing over the box drops the first pick; the second one replaces it.
  await search.fill(second.email)
  await expect(page.locator('.picked-user')).toHaveCount(0)
  await page.locator('.user-suggest', { hasText: second.email }).click()
  await expect(page.locator('.picked-user')).toContainText(second.email)
  await expect(page.locator('.user-suggest')).toHaveCount(0)
})

test('two webhooks are created in a row from a form that empties itself', async ({ admin: page }) => {
  const names = [`Twice A ${uid()}`, `Twice B ${uid()}`]
  await page.goto('/admin/settings/webhooks')
  for (const name of names) {
    await expect(page.getByRole('textbox', { name: 'Name' })).toHaveValue('')
    await expect(page.getByRole('checkbox', { name: 'Share created' })).not.toBeChecked()
    await page.getByRole('textbox', { name: 'Name' }).fill(name)
    await page.getByRole('textbox', { name: 'Endpoint URL' }).fill(OK_SINK)
    await page.getByRole('checkbox', { name: 'Share created' }).check()
    await page.getByRole('button', { name: 'Create webhook' }).click()
    await expect(page.getByText('Signing secret - shown once')).toBeVisible()
    await page.getByRole('button', { name: "I've saved it" }).click()
    await expect(page.locator('li.hook', { hasText: name })).toBeVisible()
  }
  // Leave no hook behind that would fire on other journeys' shares.
  for (const name of names) {
    await page.locator('li.hook', { hasText: name }).getByRole('button', { name: 'Delete' }).click()
    await confirm(page)
    await expect(page.locator('li.hook', { hasText: name })).toHaveCount(0)
  }
})

test('two invites are sent in a row; the form opens empty each time', async ({ admin: page }) => {
  const emails = [`twice-a-${uid()}@e2e.local`, `twice-b-${uid()}@e2e.local`]
  await page.goto('/admin/users')
  for (const email of emails) {
    await page.getByRole('button', { name: /invite user/i }).click()
    await expect(page.getByRole('heading', { name: 'Invite a new user' })).toBeVisible()
    await expect(page.getByRole('textbox', { name: /^Email/ })).toHaveValue('')
    await page.getByRole('textbox', { name: /^Email/ }).fill(email)
    await page.getByRole('textbox', { name: /^Display name/ }).fill('Invited Twice')
    await page.getByRole('button', { name: 'Send invite' }).click()
    await expect(page.locator('.invites-table tr', { hasText: email })).toBeVisible()
  }
})

test('the group form opens empty after a refused name was cancelled, and creates two in a row', async ({
  admin: page,
}) => {
  const taken = `Taken ${uid()}`
  await api('/api/groups', { method: 'POST', body: JSON.stringify({ name: taken, description: '' }) })
  await page.goto('/admin/groups')
  const name = page.getByRole('textbox', { name: 'Name' })
  const form = page.locator('form.create-form')

  await page.getByRole('button', { name: /new group/i }).click()
  await name.fill(taken)
  await page.getByRole('button', { name: 'Create', exact: true }).click()
  await expect(form.getByRole('alert')).toBeVisible()
  await page.getByRole('button', { name: 'Cancel' }).click()

  for (const fresh of [`Fresh A ${uid()}`, `Fresh B ${uid()}`]) {
    await page.getByRole('button', { name: /new group/i }).click()
    await expect(name).toHaveValue('')
    await expect(form.getByRole('alert')).toHaveCount(0)
    await name.fill(fresh)
    await page.getByRole('button', { name: 'Create', exact: true }).click()
    await expect(toast(page, 'Group created.')).toBeVisible()
    await expect(page.locator('.group-table tbody tr', { hasText: fresh })).toBeVisible()
  }
})

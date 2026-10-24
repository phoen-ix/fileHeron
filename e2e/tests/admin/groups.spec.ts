import { api, confirm, expect, heading, newUser, test, toast, uid } from './_admin'

/* /admin/groups and /admin/groups/:id - create, edit, members, delete, and
 * the refusal to delete a group an active share still targets. */

test('create a group from the list, then open it', async ({ admin: page }) => {
  const name = `Team ${uid()}`
  await page.goto('/admin/groups')
  await page.getByRole('button', { name: /new group/i }).click()
  const create = page.getByRole('button', { name: 'Create', exact: true })
  await expect(create).toBeDisabled()
  await page.getByRole('textbox', { name: 'Name' }).fill(name)
  await page.getByRole('textbox', { name: 'Description' }).fill('People who test things')
  await page.getByRole('checkbox', { name: /mark as company inbox/i }).check()
  await create.click()
  await expect(toast(page, 'Group created.')).toBeVisible()
  const row = page.locator('.group-table tbody tr', { hasText: name })
  await expect(row).toContainText('People who test things')
  await expect(row).toContainText('0')
  await row.click()
  await expect(page).toHaveURL(/\/admin\/groups\/\d+$/)
  await expect(heading(page)).toHaveText(name)
})

test('cancel closes the create form', async ({ admin: page }) => {
  await page.goto('/admin/groups')
  await page.getByRole('button', { name: /new group/i }).click()
  await page.getByRole('button', { name: 'Cancel' }).click()
  await expect(page.getByRole('heading', { name: 'Create group' })).toHaveCount(0)
})

test('edit a group, add and remove members', async ({ admin: page }) => {
  const s = uid()
  const g = await api<{ id: number }>('/api/groups', {
    method: 'POST',
    body: JSON.stringify({ name: `Edit me ${s}`, description: '' }),
  })
  const member = await newUser('employee', `member-${s}`)
  await page.goto(`/admin/groups/${g.id}`)

  await page.getByRole('button', { name: 'Edit' }).click()
  await page.getByRole('textbox', { name: 'Name' }).fill(`Edited ${s}`)
  await page.getByRole('textbox', { name: 'Description' }).fill('Now with a description')
  await page.getByRole('button', { name: 'Save' }).click()
  await expect(heading(page)).toHaveText(`Edited ${s}`)
  await expect(page.locator('.description')).toHaveText('Now with a description')

  await page.getByRole('textbox', { name: 'Search to add a member…' }).fill(`member-${s}`)
  const result = page.locator('.add-result', { hasText: member.email })
  await result.getByRole('button', { name: 'Add' }).click()
  const row = page.locator('.member-row', { hasText: member.email })
  await expect(row).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Members (1)' })).toBeVisible()

  await row.getByRole('button', { name: 'Remove' }).click()
  await confirm(page)
  await expect(row).toHaveCount(0)
  await expect(page.getByRole('heading', { name: 'Members (0)' })).toBeVisible()
})

test('delete a group; a group an active share targets cannot be deleted', async ({
  admin: page,
}) => {
  const s = uid()
  const free = await api<{ id: number }>('/api/groups', {
    method: 'POST',
    body: JSON.stringify({ name: `Delete me ${s}`, description: '' }),
  })
  await page.goto(`/admin/groups/${free.id}`)
  await page.getByRole('button', { name: 'Delete group' }).click()
  await confirm(page)
  await expect(toast(page, 'Group deleted.')).toBeVisible()
  await expect(page).toHaveURL(/\/admin\/groups$/)
  await expect(page.locator('.group-table tbody tr', { hasText: `Delete me ${s}` })).toHaveCount(0)

  const busy = await api<{ id: number }>('/api/groups', {
    method: 'POST',
    body: JSON.stringify({ name: `Busy ${s}`, description: '' }),
  })
  const member = await newUser('client', `busy-${s}`)
  await api(`/api/groups/${busy.id}/members`, {
    method: 'POST',
    body: JSON.stringify({ user_ids: [member.id] }),
  })
  await api('/api/shares', {
    method: 'POST',
    body: JSON.stringify({
      kind: 'outbound',
      recipients: { user_ids: [], group_ids: [busy.id] },
      expires_at: null,
      subject: `Share for ${s}`,
    }),
  })
  await page.goto(`/admin/groups/${busy.id}`)
  await page.getByRole('button', { name: 'Delete group' }).click()
  await confirm(page)
  await expect(page.locator('.toast[data-tone="error"]')).toBeVisible()
  await expect(heading(page)).toHaveText(`Busy ${s}`)
})

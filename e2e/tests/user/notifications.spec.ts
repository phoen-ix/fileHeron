import {
  ANNOUNCED_WITHIN,
  connect,
  createShare,
  expect,
  mailedToken,
  newUser,
  signIn,
  test,
  tokenOf,
} from './lib'

/* Notifications: the bell updates live when a share arrives and leads to it,
 * and the manage-subscriptions page every notification mail links to saves a
 * channel, applies a one-click unsubscribe with Undo, and refuses a bad link. */

test('the bell lights up live for a new share, opens it, and clears', async ({ page }) => {
  test.setTimeout(120_000)
  const sender = await newUser('employee', 'bellfrom')
  const to = await newUser('client', 'bellto')
  await connect(sender, to)
  await signIn(page, to)
  await page.goto('/inbox')
  const bell = page.getByRole('button', { name: 'Notifications' })
  // Signing in from a new device leaves its alert in the bell.
  await expect(bell.locator('.bell-badge')).toHaveText('1')

  const share = await createShare(await tokenOf(sender), {
    users: [to.id],
    notify: true,
    files: { 'ring.txt': 'ring' },
  })
  // Pushed over SSE: no reload.
  await expect(bell.locator('.bell-badge')).toHaveText('2', { timeout: ANNOUNCED_WITHIN })
  await bell.click()
  const pop = page.getByRole('region', { name: 'Notifications' })
  await pop.getByText(`${sender.name} sent you 1 file.`).click()
  await expect(page).toHaveURL(new RegExp(`/share/${share.id}$`))

  await bell.click()
  await pop.getByRole('button', { name: 'Delete all' }).click()
  await expect(pop.getByText('No notifications yet.')).toBeVisible()
  await expect(bell.locator('.bell-badge')).toHaveCount(0)
})

test('the mailed manage link saves a channel, unsubscribes in one click, and undoes it', async ({
  page,
}) => {
  test.setTimeout(120_000)
  const sender = await newUser('employee', 'mailfrom')
  const to = await newUser('client', 'mailto')
  await connect(sender, to)
  await createShare(await tokenOf(sender), { users: [to.id], notify: true, files: { 'm.txt': 'm' } })
  const token = await mailedToken('manage-notifications', to.email, ANNOUNCED_WITHIN)

  await page.goto(`/manage-notifications/${token}`)
  await expect(page.getByRole('heading', { level: 1, name: 'Manage subscriptions' })).toBeVisible()
  await expect(page.getByText(`Notification settings for ${to.name}.`)).toBeVisible()
  const share = page.getByRole('combobox', { name: 'Notify me via, for: Share', exact: true })
  await share.selectOption('in_app')
  await expect(page.getByText('Saved.')).toBeVisible()
  await page.reload()
  await expect(share).toHaveValue('in_app')

  await page.goto(`/manage-notifications/${token}?off=share_created`)
  await expect(page.getByText('You\'ve been unsubscribed from "Share" emails.')).toBeVisible()
  await expect(share).toHaveValue('off')
  await page.getByRole('button', { name: 'Undo' }).click()
  await expect(share).toHaveValue('in_app')

  await page.goto('/manage-notifications/not-a-real-token')
  await expect(page.getByRole('heading', { level: 1, name: 'This link is no longer valid' })).toBeVisible()
})

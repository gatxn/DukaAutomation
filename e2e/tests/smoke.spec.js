// @ts-check
const { test, expect } = require('@playwright/test');

// Thin E2E smoke layer per PRODUCTION_AUDIT.md's testing gaps: covers signup, the demo sale
// flow, the product-detail dialog (FR-12, previously manual-only), and a Settings round trip.
// Requires the Django dev server running at DUKA_BASE_URL (default http://127.0.0.1:8000).

test('signup, demo sale flow, product detail dialog, and settings all work end to end', async ({ page }) => {
  const username = `e2e-${Date.now()}`;
  const password = 'A-Str0ng-E2E-Passw0rd!';

  await page.goto('/signup/');
  await page.locator('#id_username').fill(username);
  await page.locator('#id_password1').fill(password);
  await page.locator('#id_password2').fill(password);
  await page.getByRole('button', { name: /create sample shop/i }).click();

  await expect(page).toHaveURL(/\/(#.*)?$/);
  await expect(page.locator('h1')).toContainText(/karibu|selling/i, { timeout: 10000 });

  // Demo sale flow: open the sample inbox, create a sample order, simulate payment.
  await page.locator('a[href="#inbox"]').first().click();
  await expect(page.locator('.contacts .contact').first()).toBeVisible();
  await page.locator('.contacts .contact').first().click();
  const createOrderButton = page.locator('[data-action="order"]');
  await createOrderButton.click();
  await expect(page.locator('.order-summary strong')).not.toHaveText('Ready when they are.', { timeout: 10000 });
  const payButton = page.locator('[data-action="payment"]');
  await payButton.click();
  await expect(page.locator('.order-summary .pill')).toHaveText(/paid/i, { timeout: 10000 });

  // FR-12: product detail dialog.
  await page.locator('a[href="#products"]').first().click();
  await page.locator('[data-product]').first().click();
  const dialog = page.locator('#detail-dialog');
  await expect(dialog).toBeVisible();
  await expect(dialog.locator('dt', { hasText: 'Price' })).toBeVisible();
  await dialog.locator('[data-close]').first().click();
  await expect(dialog).toBeHidden();

  // Settings round trip.
  await page.locator('a[href="#settings"]').first().click();
  await expect(page.locator('#settings input[name="name"]')).toBeVisible();
});

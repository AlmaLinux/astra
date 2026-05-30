import { expect, test, type Page } from "@playwright/test";

async function loginViaForm(page: Page, username: string, password: string): Promise<void> {
  await page.goto("/login/");
  await page.getByLabel("Username", { exact: true }).fill(username);
  await page.getByLabel("Password", { exact: true }).fill(password);
  await page.getByRole("button", { name: /log in/i }).click();
}

test("login page loads", async ({ page }) => {
  await page.goto("/login/");

  await expect(page.getByLabel("Username", { exact: true })).toBeVisible();
  await expect(page.getByLabel("Password", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: /log in/i })).toBeVisible();
  await expect(page).toHaveURL(/\/login\/?$/);
});

test("regular user logs in and lands on the profile page", async ({ page }) => {
  await loginViaForm(page, "regular01", "password");

  await expect(page).toHaveURL(/\/user\/regular01\/?$/);
  await expect(page.locator("[data-user-profile-root]")).toBeVisible();
  await expect(page.getByText("Loading profile...")).toHaveCount(0);
  await expect(page.getByText("Unable to load profile right now.")).toHaveCount(0);
  await expect(page.locator('a[href="/admin/"]')).toHaveCount(0);
});

test("admin user logs in and lands on the profile page with admin navigation", async ({ page }) => {
  await loginViaForm(page, "admin", "admin-password");

  await expect(page).toHaveURL(/\/user\/admin\/?$/);
  await expect(page.locator("[data-user-profile-root]")).toBeVisible();
  await expect(page.getByText("Loading profile...")).toHaveCount(0);
  await expect(page.getByText("Unable to load profile right now.")).toHaveCount(0);
  await expect(page.locator('a[href="/admin/"]')).toBeVisible();
});

test("admin profile page includes text admin mode", async ({ page }) => {
  test.fail(true, "Missing product behavior: admin mode banner is not implemented on the profile page");

  await loginViaForm(page, "admin", "admin-password");

  await expect(page).toHaveURL(/\/user\/admin\/?$/);
  await expect(page.getByText("admin mode", { exact: false })).toBeVisible();
});
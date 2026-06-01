import { expect, test, type Page } from "@playwright/test";

import { readAuthResetState } from "./resetState";

const authResetState = readAuthResetState();

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

async function loginViaForm(page: Page, username: string, password: string): Promise<void> {
  await page.goto(authResetState.routes.login);
  await page.getByLabel("Username", { exact: true }).fill(username);
  await page.getByLabel("Password", { exact: true }).fill(password);
  await page.getByRole("button", { name: /log in/i }).click();
}

// As an anonymous visitor, I can open the login shell, see username/password fields, and submit the login form.
test("public-auth-login authenticates and redirects repeat visits away from the login shell", async ({ page }) => {
  const actor = authResetState.actors.regular06;

  await page.goto(authResetState.routes.login);
  await expect(page.getByLabel("Username", { exact: true })).toBeVisible();
  await expect(page.getByLabel("Password", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: /log in/i })).toBeVisible();

  await loginViaForm(page, actor.username, actor.password);
  await expect(page).toHaveURL(new RegExp(`${escapeRegExp(actor.profile_route)}$`));
  await expect(page.locator("[data-user-profile-root]")).toBeVisible();

  await page.goto(authResetState.routes.login);
  await expect(page).toHaveURL(new RegExp(`${escapeRegExp(actor.profile_route)}$`));
});
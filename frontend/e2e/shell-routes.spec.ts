import { expect, test, type Page } from "@playwright/test";

async function loginViaForm(page: Page, username: string, password: string): Promise<void> {
  await page.goto("/login/");
  await page.getByLabel("Username", { exact: true }).fill(username);
  await page.getByLabel("Password", { exact: true }).fill(password);
  await page.getByRole("button", { name: /log in/i }).click();
}

async function ensureMembershipManagementOpen(page: Page): Promise<void> {
  const requestsLink = page.getByRole("link", { name: "Requests", exact: true });
  if (!(await requestsLink.isVisible())) {
    await page.getByRole("link", { name: /Membership Management/ }).click();
  }
}

// As a directory-capable user, I can open the Users directory, search users, and paginate through results.
// As an authenticated user, I can use the global navbar search and get scope-appropriate behavior.
// As an authenticated user, I can use the sidebar and footer as navigational workflows rather than treating them as passive chrome.
// As a user following static/legal/support routes, I can reach legal content and support entry points that appear elsewhere in the shell.
test("shell-routes-users-search-and-static-links", async ({ page }) => {
  await loginViaForm(page, "admin", "admin-password");
  await page.getByRole("link", { name: "Users", exact: true }).click();

  await expect(page).toHaveURL(/\/users\/?$/);
  await expect(page.locator("[data-users-root]")).toBeVisible();
  await expect(page.getByText("Loading users...")).toHaveCount(0);
  await expect(page.getByText("Unable to load users right now.")).toHaveCount(0);

  await page.getByLabel("Search users", { exact: true }).fill("regular0");
  await page.getByRole("button", { name: "Search", exact: true }).click();
  await expect(page).toHaveURL(/\/users\/\?q=regular0/);
  await expect(page.getByText("regular01", { exact: false })).toBeVisible();

  const pageTwoLink = page.getByRole("link", { name: "2", exact: true }).first();
  await expect(pageTwoLink).toBeVisible();
  await pageTwoLink.click();
  await expect(page).toHaveURL(/page=2/);
  await page.goBack();
  await expect(page).toHaveURL(/\/users\/\?q=regular0/);

  await page.locator("#global-search-input").fill("regular01");
  await expect(page.locator("#global-search-menu")).toBeVisible();
  await expect(page.locator("#global-search-menu")).toContainText(/regular01/i);
  await page.locator("#global-search-menu").getByRole("link", { name: /regular01/i }).first().click();
  await expect(page).toHaveURL(/\/user\/regular01\/?$/);

  await expect(page.getByRole("link", { name: "My Profile", exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: "Groups", exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: "Organizations", exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: "Elections", exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: "Admin", exact: true })).toBeVisible();

  const supportLink = page.locator('footer a[href^="mailto:"]');
  await expect(supportLink).toBeVisible();
  await expect(supportLink).toHaveText("Contact Support");
  await expect(supportLink).toHaveAttribute("href", /^mailto:/);

  await page.getByRole("link", { name: "Privacy Policy", exact: true }).click();
  await expect(page).toHaveURL(/\/privacy-policy\/?$/);
  await expect(page.getByRole("heading", { name: /privacy policy/i })).toBeVisible();

  await page.goto("/coc/");
  await expect(page).toHaveURL(/\/(coc|agreements)\//);
});

// As a membership operator, I can use the notifications dropdown as a cross-page entry point.
// As an authenticated user, I can use the sidebar and footer as navigational workflows rather than treating them as passive chrome.
test("shell-routes-notifications-and-sidebar", async ({ page }) => {
  await loginViaForm(page, "regular01", "password");

  await expect(page.locator('form[action="/logout/"] button')).toBeVisible();

  await ensureMembershipManagementOpen(page);
  await expect(page.getByRole("link", { name: "Requests", exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: "Invitations", exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: "Audit Log", exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: "Sponsors", exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: "Statistics", exact: true })).toBeVisible();

  await page.getByLabel("Notifications", { exact: true }).click();
  const notificationsMenu = page.locator(".dropdown-menu.show").filter({ hasText: "Notifications" }).first();
  await expect(notificationsMenu).toBeVisible();

  const availableNotification = notificationsMenu.getByRole("link").filter({ hasText: /Pending requests|On hold requests|Accepted invitations/ }).first();
  await expect(availableNotification).toBeVisible();
  await availableNotification.click();
  await expect(page).toHaveURL(/\/(membership\/requests|membership\/account-invitations)\//);

  await page.goBack();
  await expect(page.locator('footer a[href^="mailto:"]')).toBeVisible();
  await expect(page.locator('form[action="/logout/"] button')).toBeVisible();
});
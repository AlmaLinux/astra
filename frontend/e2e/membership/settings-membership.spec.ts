import { expect, test, type Page } from "@playwright/test";

import { readSelfServiceResetState } from "./self-service-reset-state";

function formatDate(value: string | null): string {
  if (!value) {
    return "";
  }
  const match = value.match(/^(\d{4}-\d{2}-\d{2})/);
  return match ? match[1] : value;
}

function formatDateTime(value: string): string {
  const match = value.match(/^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})(?::\d{2})?(?:\.\d+)?([+-]\d{2}:\d{2}|Z)?$/);
  if (!match) {
    return value;
  }
  const zone = !match[3] || match[3] === "Z" ? "UTC" : `UTC${match[3]}`;
  return `${match[1]} ${match[2]} ${zone}`;
}

async function loginViaForm(page: Page, username: string, password: string): Promise<void> {
  await page.goto("/login/");
  await page.getByLabel("Username", { exact: true }).fill(username);
  await page.getByLabel("Password", { exact: true }).fill(password);
  await Promise.all([
    page.waitForURL((url) => !url.pathname.endsWith("/login/")),
    page.getByRole("button", { name: /log in/i }).click(),
  ]);
}

function activeLeaveForm(page: Page, terminateRoute: string) {
  return page.locator(`form[action="${terminateRoute}"]`).first();
}

function activeMembershipList(page: Page) {
  return page
    .locator('[data-settings-tab-pane="membership"]')
    .getByRole("heading", { name: "Active memberships", exact: true })
    .locator("xpath=following-sibling::ul[1]");
}

// As an active member, I can open the settings membership tab and review my current membership details without shell errors.
test("membership-settings-shell renders the seeded membership tab without shell errors", async ({ page }) => {
  const resetState = readSelfServiceResetState();
  const settingsMembership = resetState.settings.membership;
  const actor = resetState.actors[settingsMembership.actor_username];

  await loginViaForm(page, actor.username, actor.password);
  await page.goto(settingsMembership.route);

  await expect(page.locator("[data-settings-root]")).toBeVisible();
  await expect(page.locator('[data-settings-tab-pane="membership"]')).toHaveClass(/active/);
  await expect(page.getByText("Loading settings...")).toHaveCount(0);
  await expect(
    page.getByText(
      "This action cannot be completed right now because AlmaLinux Accounts is temporarily unavailable.",
    ),
  ).toHaveCount(0);

  const activeMembership = settingsMembership.active_membership;
  const activeList = activeMembershipList(page);
  await expect(page.getByText(activeMembership.membership_type_name, { exact: true }).first()).toBeVisible();
  await expect(
    page.getByText(
      `Joined ${formatDate(activeMembership.created_at)}. Current term ends ${formatDate(activeMembership.expires_at)}.`,
      { exact: true },
    ),
  ).toBeVisible();
  await expect(activeList.getByRole("button", { name: "Leave membership", exact: true })).toHaveCount(1);
  await expect(activeList.locator(`form[action="${activeMembership.terminate_route}"]`)).toHaveCount(1);
});

// As a user, I can leave an active membership from settings.
test("membership-settings-history-and-exit-controls can leave the seeded membership", async ({ page }) => {
  const resetState = readSelfServiceResetState();
  const settingsMembership = resetState.settings.membership;
  const actor = resetState.actors[settingsMembership.actor_username];

  await loginViaForm(page, actor.username, actor.password);
  await page.goto(settingsMembership.route);

  const membershipPane = page.locator('[data-settings-tab-pane="membership"]');
  const historyHeading = membershipPane.getByRole("heading", { name: "Recent history", exact: true });
  const historyItems = historyHeading.locator("xpath=following-sibling::ul[1]/li");

  await expect(historyHeading).toBeVisible();
  expect(await historyItems.count()).toBeGreaterThanOrEqual(settingsMembership.ordered_history_aliases.length);

  for (const [index, alias] of settingsMembership.ordered_history_aliases.entries()) {
    const row = settingsMembership.history_rows[alias];
    const historyItem = historyItems.nth(index);
    await expect(historyItem).toContainText(row.membership_type_name);
    await expect(historyItem).toContainText(formatDateTime(row.created_at));
    await expect(historyItem).toContainText(row.action_label);
  }

  let activeList = activeMembershipList(page);
  await activeList.getByRole("button", { name: "Leave membership", exact: true }).click();
  const leaveForm = activeLeaveForm(page, settingsMembership.active_membership.terminate_route);
  await expect(leaveForm).toBeVisible();
  await expect(leaveForm.getByLabel(/why are you leaving this membership\?/i)).toBeVisible();
  await expect(leaveForm.getByLabel("Optional details", { exact: true })).toBeVisible();
  await expect(leaveForm.getByLabel(/current password/i)).toBeVisible();

  await leaveForm.getByLabel(/why are you leaving this membership\?/i).selectOption("privacy");
  await leaveForm.getByLabel("Optional details", { exact: true }).fill("E2E coverage for membership exit controls.");
  await leaveForm.getByLabel(/current password/i).fill(actor.password);

  await Promise.all([
    page.waitForURL(/\/settings\/\?tab=membership&status=terminated$/),
    leaveForm.getByRole("button", { name: "Leave membership", exact: true }).click(),
  ]);

  await expect(page.getByText("Membership terminated.", { exact: true })).toBeVisible();
  activeList = activeMembershipList(page);
  await expect(activeList.locator(`form[action="${settingsMembership.active_membership.terminate_route}"]`)).toHaveCount(0);
  await expect(activeList.getByRole("button", { name: "Leave membership", exact: true })).toHaveCount(0);
  await expect(activeList.getByText("You do not have any active memberships.", { exact: true })).toBeVisible();

  await expect(historyHeading).toBeVisible();
  expect(await historyItems.count()).toBeGreaterThanOrEqual(1);

  const terminationItem = historyItems.first();
  await expect(terminationItem).toContainText(settingsMembership.active_membership.membership_type_name);
  await expect(terminationItem).toContainText("Terminated");
});

import { expect, test, type Page } from "@playwright/test";

import { readAuthResetState } from "./resetState";

const authResetState = readAuthResetState();

async function loginViaForm(page: Page, username: string, password: string): Promise<void> {
  await page.goto(authResetState.routes.login);
  await page.getByLabel("Username", { exact: true }).fill(username);
  await page.getByLabel("Password", { exact: true }).fill(password);
  await page.getByRole("button", { name: /log in/i }).click();
}

// As an authenticated user, I can navigate across settings tabs.
// As a user, I can edit profile fields including names, pronouns, country, locale, timezone, websites, RSS URLs, chat nicknames, and GitHub/GitLab usernames.
test("settings-profile tabs expose the seeded profile editors and avatar controls", async ({ page }) => {
  const actor = authResetState.actors.regular05;

  await loginViaForm(page, actor.username, actor.password);
  await page.goto(`${authResetState.routes.settings_profile}&highlight=country_code`);

  await expect(page.locator("[data-settings-root]")).toBeVisible();
  await expect(page.locator('[data-settings-tab-pane="profile"]')).toHaveClass(/active/);
  await expect(page.locator("#country-code-field-wrapper")).toHaveClass(/settings-field-highlight/);
  await expect(page.locator('input[name="fasPronoun"]')).toHaveAttribute("list", "pronoun-options");
  await expect(page.locator('input[name="fasLocale"]')).toHaveAttribute("list", "locale-options");
  await expect(page.locator('input[name="fasTimezone"]')).toHaveAttribute("list", "timezone-options");
  expect(await page.locator("#pronoun-options option").count()).toBeGreaterThan(0);
  expect(await page.locator("#locale-options option").count()).toBeGreaterThan(0);
  expect(await page.locator("#timezone-options option").count()).toBeGreaterThan(0);

  await page.locator('[data-settings-tab="emails"]').click();
  await expect(page).toHaveURL(/tab=emails/);
  await page.locator('[data-settings-tab="security"]').click();
  await expect(page).toHaveURL(/tab=security/);
  await page.locator('[data-settings-tab="privacy"]').click();
  await expect(page).toHaveURL(/tab=privacy/);

  await page.goto(`${authResetState.routes.settings_profile}&highlight=country_code`);
  await page.getByRole("button", { name: /change avatar/i }).click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await expect(page.getByRole("link", { name: /manage at provider/i })).toBeVisible();
  await page.getByRole("button", { name: /close/i }).click();
  await expect(page.getByRole("button", { name: /^save$/i })).toBeVisible();
});

// As a user, I can manage email and bugzilla email fields.
// As a user following an email-validation link, I can review the pending address change and choose whether to confirm or cancel it.
test("settings-emails exposes both address fields and supports primary confirm plus bugzilla cancel", async ({ page }) => {
  const actor = authResetState.actors.regular08;

  await loginViaForm(page, actor.username, actor.password);
  await page.goto(authResetState.routes.settings_emails);

  await expect(page.getByLabel("E-mail Address")).toHaveValue("regular08@example.test");
  await expect(page.getByLabel("Red Hat Bugzilla Email")).toBeVisible();

  await page.goto(authResetState.routes.settings_email_validate_primary);
  await expect(page.locator("[data-settings-email-validation-root]")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Confirm email address change" })).toBeVisible();
  await expect(page.getByText("updated-regular08@example.test")).toBeVisible();
  await expect(page.getByRole("link", { name: /^cancel$/i })).toHaveAttribute("href", authResetState.routes.settings_emails);
  await page.getByRole("button", { name: /^confirm$/i }).click();

  await expect(page).toHaveURL(/tab=emails/);
  await expect(page.locator('[data-settings-tab-pane="emails"]')).toHaveClass(/active/);
  await expect(page.getByText("Your email address has been validated.")).toBeVisible();
  await expect(page.getByLabel("E-mail Address")).toHaveValue("updated-regular08@example.test");

  await page.goto(authResetState.routes.settings_email_validate_bugzilla);
  await expect(page.locator("[data-settings-email-validation-root]")).toBeVisible();
  await expect(page.getByText("Red Hat Bugzilla Email")).toBeVisible();
  await page.getByRole("link", { name: /^cancel$/i }).click();
  await expect(page).toHaveURL(/tab=emails/);
});

// As a user, I can manage keys.
test("settings-keys exposes the fallback GPG and SSH editors", async ({ page }) => {
  const actor = authResetState.actors.regular09;

  await loginViaForm(page, actor.username, actor.password);
  await page.goto(authResetState.routes.settings_keys);

  await expect(page.getByLabel("GPG Key IDs")).toContainText("ABCDEF0123456709");
  await expect(page.getByLabel("SSH Public Keys")).toContainText("ssh-ed25519");
});

// As a user, I can change password and manage OTP tokens.
test("settings-security exposes password fields, no-token state, and the add-token modal flow", async ({ page }) => {
  const actor = authResetState.actors.regular10;

  await loginViaForm(page, actor.username, actor.password);
  await page.goto(authResetState.routes.settings_security);

  await expect(page.getByLabel("Current Password", { exact: true })).toBeVisible();
  await expect(page.getByLabel("New Password", { exact: true })).toBeVisible();
  await expect(page.getByLabel("Confirm New Password", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: /change password/i })).toBeVisible();
  await expect(page.getByText("You have no OTP tokens")).toBeVisible();

  await page.getByRole("button", { name: /add otp token/i }).click();
  const modal = page.locator("#add-token-modal");
  await expect(modal).toBeVisible();
  await expect(modal.getByLabel("Token name")).toBeVisible();
  await expect(modal.getByLabel("Enter your current password")).toBeVisible();
  await expect(modal.getByRole("button", { name: /generate otp token/i })).toBeVisible();
});

// As a user, I can save privacy settings and request account deletion.
test("settings-privacy exposes the visibility checkbox and deletion-request form", async ({ page }) => {
  const actor = authResetState.actors.regular11;

  await loginViaForm(page, actor.username, actor.password);
  await page.goto(authResetState.routes.settings_privacy);

  const privacyCheckbox = page.getByLabel("Hide profile details");
  await expect(privacyCheckbox).toBeVisible();
  await expect(page.getByRole("button", { name: /save privacy settings/i })).toBeVisible();

  const privacyPane = page.locator('[data-settings-tab-pane="privacy"]');
  const deletionCard = privacyPane.locator(".card.border-danger");
  await expect(deletionCard.getByLabel("Why are you requesting account deletion?")).toBeVisible();
  await expect(deletionCard.locator('input[name="current_password"]')).toBeVisible();
  await expect(deletionCard.getByRole("button", { name: /submit deletion request/i })).toBeVisible();
});

// As a user, I can review and sign agreements.
test("settings-agreements can open agreement detail and sign an unsigned agreement", async ({ page }) => {
  const actor = authResetState.actors.regular12;

  await loginViaForm(page, actor.username, actor.password);
  await page.goto(authResetState.routes.settings_agreements);

  const agreementRow = page.locator("tr", { hasText: authResetState.agreements.optional_unsigned.cn });
  await expect(agreementRow).toBeVisible();
  await agreementRow.getByRole("link", { name: /view agreement/i }).click();
  await expect(page).toHaveURL(new RegExp(`agreement=${authResetState.agreements.optional_unsigned.cn}$`));
  await expect(page.getByRole("button", { name: /^sign$/i })).toBeVisible();
  await page.getByRole("button", { name: /^sign$/i }).click();

  await expect(page).toHaveURL(/tab=agreements/);
  const signedRow = page.locator("tr", { hasText: authResetState.agreements.optional_unsigned.cn });
  await expect(signedRow).toBeVisible();
  await expect(signedRow.getByText("Signed", { exact: true })).toBeVisible();
});

// As a user, I can leave an active membership from settings.
test("settings-membership exposes the self-service leave-membership form", async ({ page }) => {
  const actor = authResetState.actors.regular03;

  await loginViaForm(page, actor.username, actor.password);
  await page.goto(authResetState.routes.settings_membership);

  await expect(page.locator('[data-settings-tab-pane="membership"]')).toHaveClass(/active/);
  const membershipPane = page.locator('[data-settings-tab-pane="membership"]');
  await membershipPane.locator('button[data-toggle="collapse"]').click();

  await expect(membershipPane.locator('select[name="reason_category"]')).toBeVisible();
  await expect(membershipPane.locator('textarea[name="reason_text"]')).toBeVisible();
  await expect(membershipPane.locator('input[name="current_password"]')).toBeVisible();
  await expect(membershipPane.locator('button[type="submit"]', { hasText: "Leave membership" })).toBeVisible();
});
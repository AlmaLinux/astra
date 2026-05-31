import { expect, test, type Page } from "@playwright/test";

import { readAuthResetState } from "./resetState";

const authResetState = readAuthResetState();
const RESET_PASSWORD = "Reset-password-123!";
const ACTIVATED_PASSWORD = "Activated-password-123!";

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

async function loginViaForm(page: Page, username: string, password: string): Promise<void> {
  await page.goto(authResetState.routes.login);
  await page.getByLabel("Username", { exact: true }).fill(username);
  await page.getByLabel("Password", { exact: true }).fill(password);
  await page.getByRole("button", { name: /log in/i }).click();
}

// As an anonymous visitor, I can request a password reset.
test("public-auth-password-reset-request shell renders", async ({ page }) => {
  await page.goto(authResetState.routes.password_reset_request);
  await expect(page.locator("[data-auth-recovery-password-reset-shell]")).toBeVisible();
  await expect(page.getByLabel("Username or email")).toBeVisible();
  await expect(page.getByRole("link", { name: /back to login/i })).toBeVisible();
  await expect(page.getByRole("button", { name: /send reset email/i })).toBeVisible();
});

// As a user with an expired password or OTP sync need, I can reach the specialized recovery shells.
test("public-auth-specialized-recovery shells render", async ({ page }) => {
  await page.goto(authResetState.routes.password_expired);
  await expect(page.locator("[data-auth-recovery-password-expired-shell]")).toBeVisible();
  await expect(page.getByRole("heading", { name: /you must change your password/i })).toBeVisible();
  await expect(page.getByRole("link", { name: /back to login/i })).toBeVisible();

  await page.goto(authResetState.routes.otp_sync);
  await expect(page.locator("[data-auth-recovery-otp-sync-shell]")).toBeVisible();
  await expect(page.getByRole("heading", { name: /synchronize otp token/i })).toBeVisible();
  await expect(page.getByRole("button", { name: /synchronize token/i })).toBeVisible();
});

// As a new user, I can open the registration shell and see whether registration is open.
test("public-auth-register shell renders the open registration flow", async ({ page }) => {
  await page.goto(authResetState.routes.register);
  await expect(page.locator("[data-register-shell]")).toBeVisible();
  await expect(page.getByText("Step 1 of 3: Account details")).toBeVisible();
  await expect(page.getByRole("link", { name: /^login$/i })).toBeVisible();
  await expect(page.getByRole("link", { name: /^register$/i })).toBeVisible();
  await expect(page.getByRole("button", { name: /register/i })).toBeVisible();
});

// As a visitor following a valid password-reset token, I can open `/password-reset/confirm/?token=...`, set a new password, and supply an OTP code when the account requires it.
test("public-auth-password-reset-confirm renders the password and OTP-aware form", async ({ page }) => {
  await page.goto(authResetState.routes.password_reset_confirm);
  await expect(page.locator("[data-auth-recovery-password-reset-confirm-shell]")).toBeVisible();
  await expect(page.getByRole("heading", { name: /set a new password/i })).toBeVisible();
  await expect(page.locator('input[name="password"]')).toBeVisible();
  await expect(page.locator('input[name="password_confirm"]')).toBeVisible();
  await expect(page.locator('input[name="otp"]')).toBeVisible();
  await expect(page.getByRole("link", { name: /back to login/i })).toBeVisible();
  await expect(page.getByRole("button", { name: /update password/i })).toBeVisible();
});

// As a registrant on `/register/confirm/?username=...`, I can review the email-validation step and resend the verification email.
test("public-auth-register-confirm can resend the staged verification email", async ({ page }) => {
  await page.goto(authResetState.routes.register_confirm);

  await expect(page.locator("[data-register-confirm-shell]")).toBeVisible();
  await expect(page.getByText("Step 2 of 3: Verify your email")).toBeVisible();
  await expect(page.getByText("signup-confirm-01", { exact: true })).toBeVisible();
  await expect(page.getByText("signup-confirm-01@example.test")).toBeVisible();

  await page.getByRole("button", { name: /resend email/i }).click();
  await expect(page).toHaveURL(/\/register\/confirm\/\?username=signup-confirm-01$/);
  await expect(
    page.getByText("The address validation email has be sent again. Make sure it did not land in your spam folder"),
  ).toBeVisible();
});

// As a registrant following a valid activation token, I can open `/register/activate/?token=...`, choose a password, and activate the account.
test("public-auth-register-activate can choose a password and activate the staged account", async ({ page }) => {
  await page.goto(authResetState.routes.register_activate);

  await expect(page.locator("[data-register-activate-shell]")).toBeVisible();
  await expect(page.getByText("Step 3 of 3: Choose a password")).toBeVisible();
  await page.locator('input[name="password"]').fill(ACTIVATED_PASSWORD);
  await page.locator('input[name="password_confirm"]').fill(ACTIVATED_PASSWORD);
  await page.getByRole("button", { name: /^activate$/i }).click();

  await expect(page).toHaveURL(/\/login\/?$/);
  await expect(page.locator("body")).toContainText(/your account has been created/i);
});
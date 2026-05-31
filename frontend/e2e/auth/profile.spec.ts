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

// As a profile owner, I can load my own profile shell and see summary, memberships, and groups.
// As a membership owner, I can request membership, request renewal, request tier change, and inspect pending requests from the membership card.
test("auth-profile-owner sees the seeded profile, memberships, and pending request state", async ({ page }) => {
  const actor = authResetState.actors.regular03;

  await loginViaForm(page, actor.username, actor.password);

  await expect(page).toHaveURL(new RegExp(`${escapeRegExp(actor.profile_route)}$`));
  await expect(page.locator("[data-user-profile-root]")).toBeVisible();
  await expect(page.getByText("Loading profile...")).toHaveCount(0);
  await expect(page.getByText("Unable to load profile right now.")).toHaveCount(0);
  await expect(page.locator(".profile-username")).toHaveText("Regular 03 User");
  await expect(page.locator("#user_mail")).toContainText("regular03@example.test");
  await expect(page.locator("[title='Pronouns']")).toBeVisible();
  await expect(page.locator("[data-user-profile-membership-root]")).toBeVisible();
  await expect(page.getByRole("link", { name: /request #/i })).toBeVisible();
});

// As another-profile viewer, I can target `/user/<username>/` directly; current source wiring indicates the shell route itself is not protected by the authenticated user-directory gate.
// As a non-self ordinary viewer of a private profile, I still see the profile shell with the redacted fields proven by the current detail-payload tests plus the group/agreement data that the present source path continues to expose.
test("auth-profile-private ordinary viewers get the private-profile shell with redacted data", async ({ page }) => {
  const actor = authResetState.actors.regular04;

  await loginViaForm(page, actor.username, actor.password);
  await page.goto(authResetState.actors.regular07.profile_route);

  await expect(page.locator("[data-user-profile-root]")).toBeVisible();
  await expect(page.locator(".profile-username")).toHaveText("regular07");
  await expect(page.locator("#user_username")).toHaveText("regular07");
  await expect(page.locator("#user_mail")).toHaveCount(0);
  await expect(page.locator("[title='Pronouns']")).toHaveCount(0);
  await expect(page.getByRole("link", { name: "https://regular07.example.test" })).toHaveCount(0);
  await expect(page.locator("[data-user-profile-membership-root]")).toHaveCount(0);
});

// As a membership committee viewer of a private profile, I can see committee-only membership-review fields without restoring the full private profile.
test("auth-profile-private committee viewers regain committee-only fields without unredacting the full profile", async ({ page }) => {
  const actor = authResetState.actors.regular01;

  await loginViaForm(page, actor.username, actor.password);
  await page.goto(authResetState.actors.regular07.profile_route);

  await expect(page.locator("[data-user-profile-root]")).toBeVisible();
  await expect(page.locator(".profile-username")).toHaveText("regular07");
  await expect(page.locator("#user_mail")).toContainText("regular07@example.test");
  const countryRow = page.locator("#user_attributes li").filter({ has: page.locator("[title='Country']") });
  await expect(countryRow).toBeVisible();
  await expect(countryRow).toContainText(/United States|US/);
  await expect(page.locator("[data-user-profile-membership-root]")).toBeVisible();
  await expect(page.getByRole("link", { name: "https://regular07.example.test" })).toHaveCount(0);
});

// As a user with unmet prerequisites, I see `Action required` or dismissible `Recommended` alerts with deep links.
test("auth-profile-account-setup alerts expose deep links and recommended dismissal persists", async ({ page }) => {
  const actor = authResetState.actors.regular01;

  await loginViaForm(page, actor.username, actor.password);
  await expect(page).toHaveURL(new RegExp(`${escapeRegExp(actor.profile_route)}$`));

  const requiredAlert = page.locator("#account-setup-required-alert");
  await expect(requiredAlert).toBeVisible();
  await expect(requiredAlert.getByRole("link", { name: "Set country code" })).toHaveAttribute(
    "href",
    /tab=profile&highlight=country_code/,
  );

  const recommendedAlert = page.locator("#account-setup-recommended-alert");
  await expect(recommendedAlert).toBeVisible();
  await expect(recommendedAlert.getByText("Request an individual membership")).toBeVisible();
  await recommendedAlert.getByRole("button", { name: "Close" }).click();
  await expect(page.locator("#account-setup-recommended-alert")).toHaveCount(0);

  await page.reload();
  await expect(page.locator("#account-setup-recommended-alert")).toHaveCount(0);
});

// As an operator with management rights over the viewed user, I can edit expiration dates and terminate memberships after a typed-name confirmation.
test("auth-profile-membership-operator sees the management modal and typed-name termination guard", async ({ page }) => {
  const actor = authResetState.actors.regular01;

  await loginViaForm(page, actor.username, actor.password);
  await page.goto(authResetState.actors.regular03.profile_route);

  await expect(page.locator("[data-user-profile-membership-root]")).toBeVisible();
  await page.getByRole("button", { name: /edit expiration/i }).click();

  const modal = page.locator("#expiry-modal-1");
  await expect(modal).toBeVisible();
  await expect(modal.getByLabel("Expiration date")).toBeVisible();
  await expect(modal.getByRole("button", { name: /save expiration/i })).toBeVisible();

  await modal.getByRole("button", { name: /terminate membership/i }).click();
  const confirmInput = modal.getByLabel("Type the name to confirm");
  const terminateButton = modal.getByRole("button", { name: /^terminate membership$/i });

  await expect(terminateButton).toBeDisabled();
  await confirmInput.fill("wrong-name");
  await expect(terminateButton).toBeDisabled();
  await confirmInput.fill("regular03");
  await expect(terminateButton).toBeEnabled();
});
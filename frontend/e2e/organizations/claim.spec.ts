import { expect, test, type Page } from "@playwright/test";

import { readOrganizationsResetState } from "./resetState";

function escapeForRegex(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

async function loginViaForm(page: Page, username: string, password: string): Promise<void> {
  await page.goto("/login/");
  await page.getByLabel("Username", { exact: true }).fill(username);
  await page.getByLabel("Password", { exact: true }).fill(password);
  await page.getByRole("button", { name: /log in/i }).click();
}

// As a user following a claim token, I can claim an unclaimed organization and become its representative.
test("organizations-claim-happy-path uses the reset-issued claim URL and redirects to organization detail", async ({ page }) => {
  const resetState = readOrganizationsResetState();
  const actor = resetState.actors.claim_happy_actor;
  const claimRoute = resetState.claim_routes["organizations-claim-happy-path"];
  const expectedDetailPath = resetState.scenarios["organizations-claim-happy-path"].route_target;

  await loginViaForm(page, actor.username, actor.password);
  await page.goto(claimRoute);

  await expect(page.locator("[data-organization-claim-root]")).toBeVisible();
  await expect(page.getByText(resetState.organizations.claimable_org.name, { exact: true })).toBeVisible();
  await expect(page.getByText(resetState.organizations.claimable_org.business_contact_email, { exact: true })).toBeVisible();

  await Promise.all([
    page.waitForURL(new RegExp(`${escapeForRegex(expectedDetailPath)}$`)),
    page.getByRole("button", { name: "Claim organization", exact: true }).click(),
  ]);

  await expect(page.locator("[data-organization-detail-root]")).toBeVisible();
  await expect(page.getByText("You are now the representative for this organization.", { exact: true })).toBeVisible();
});

// As a user following a claim token, I can claim an unclaimed organization and become its representative.
test("organizations-claim-already-claimed renders the rejection state from the reset-issued claim URL", async ({ page }) => {
  const resetState = readOrganizationsResetState();
  const actor = resetState.actors.claim_rejection_actor;
  const claimRoute = resetState.claim_routes["organizations-claim-already-claimed"];

  await loginViaForm(page, actor.username, actor.password);
  await page.goto(claimRoute);

  await expect(page.locator("[data-organization-claim-root]")).toBeVisible();
  await expect(page.getByText(/already been claimed/i)).toBeVisible();
  await expect(page.getByRole("link", { name: /contact the Membership Committee/i })).toBeVisible();
});

// As a user following a claim token, I can claim an unclaimed organization and become its representative.
test("organizations-claim-invalid-token renders the invalid claim state", async ({ page }) => {
  const resetState = readOrganizationsResetState();
  const actor = resetState.actors.claim_happy_actor;

  await loginViaForm(page, actor.username, actor.password);
  await page.goto("/organizations/claim/not-a-valid-token/");

  await expect(page.locator("[data-organization-claim-root]")).toBeVisible();
  await expect(page.getByText(/invalid or has expired/i)).toBeVisible();
});
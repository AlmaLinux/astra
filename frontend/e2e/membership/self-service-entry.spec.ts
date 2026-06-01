import { expect, test, type Locator, type Page } from "@playwright/test";

import { readSelfServiceResetState } from "./self-service-reset-state";

async function loginViaForm(page: Page, username: string, password: string): Promise<void> {
  await page.goto("/login/");
  await page.getByLabel("Username", { exact: true }).fill(username);
  await page.getByLabel("Password", { exact: true }).fill(password);
  await page.getByRole("button", { name: /log in/i }).click();
}

async function firstProfileRequestLink(page: Page, expectedName: RegExp): Promise<Locator> {
  const requestLinks = page.locator('a[href*="/membership/request/"]');
  await expect(requestLinks.filter({ hasText: expectedName }).first()).toBeVisible();
  return requestLinks.filter({ hasText: expectedName }).first();
}

// As a user, I can open the membership request form, choose a membership type, and see conditional question sets.
test("membership-create-individual submits a new individual request and opens the pending detail view", async ({ page }) => {
  const resetState = readSelfServiceResetState();
  const actor = resetState.actors.regular01;

  await loginViaForm(page, actor.username, actor.password);

  await page.goto(resetState.routes.create);
  await expect(page.locator("[data-membership-request-form-root]")).toBeVisible();
  await page.getByLabel("Membership type").selectOption("mirror");
  await expect(page.getByLabel(/domain name of the mirror/i)).toBeVisible();
  await expect(page.getByLabel(/link to your pull request/i)).toBeVisible();
  await page.getByLabel("Membership type").selectOption("individual");
  await page
    .getByLabel(/summary of your contributions/i)
    .fill("I contributed docs and CI improvements for the Wave 7 browser slice.");
  await page.getByRole("button", { name: /submit request/i }).click();

  await expect(page).toHaveURL(new RegExp(`${actor.profile_route.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}$`));
  await expect(page.getByText("Membership request submitted for review.")).toBeVisible();

  const detailLink = await firstProfileRequestLink(page, /under review/i);
  await detailLink.click();
  await expect(page).toHaveURL(/\/membership\/request\/\d+\/?$/);
  await expect(page.getByText("Pending", { exact: true })).toBeVisible();
});

// As a user, I can open the membership request form, choose a membership type, and see conditional question sets.
test("membership-duplicate-individual hides the unavailable individual category and links to the existing pending request", async ({ page }) => {
  const resetState = readSelfServiceResetState();
  const actor = resetState.actors[resetState.requests.duplicate_pending.actor_username];
  const detailRoute = resetState.requests.duplicate_pending.detail_route;

  await loginViaForm(page, actor.username, actor.password);

  await page.goto(resetState.routes.create);
  await expect(page.locator("[data-membership-request-form-root]")).toBeVisible();
  await expect(page.getByLabel("Membership type").locator("option[value='individual']")).toHaveCount(0);
  await page.goto(actor.profile_route);

  const pendingLink = await firstProfileRequestLink(page, /under review/i);
  await pendingLink.click();
  await expect(page).toHaveURL(new RegExp(`${detailRoute.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}$`));
  await expect(page.getByText("Pending", { exact: true })).toBeVisible();
});

// As a user with a prior request history, I may see prefilled mirror data or a message that the prefilled type is unavailable.
test("membership-renewal-prefill-mirror prefills the mirror renewal form from the approved request history", async ({ page }) => {
  const resetState = readSelfServiceResetState();
  const actor = resetState.actors[resetState.requests.renewal_source.actor_username];

  await loginViaForm(page, actor.username, actor.password);

  await page.goto(`${resetState.routes.create}?membership_type=mirror`);
  await expect(page.locator("[data-membership-request-form-root]")).toBeVisible();
  await page.getByLabel("Membership type").selectOption("mirror");
  await expect(page.getByLabel(/domain name of the mirror/i)).toHaveValue("https://mirror.regular33.example.test");
  await expect(page.getByLabel(/link to your pull request/i)).toHaveValue(
    "https://github.com/AlmaLinux/mirrors/pull/303",
  );
  await expect(page.getByLabel(/additional information/i)).toHaveValue("Primary EU mirror");
});

// As a user, I can open the membership request form, choose a membership type, and see conditional question sets.
test("membership-organization-target-form submits an organization-target request and opens the pending detail view", async ({ page }) => {
  const resetState = readSelfServiceResetState();
  const actor = resetState.actors.regular01;
  const organization = resetState.organizations.representative_form_org;

  await loginViaForm(page, actor.username, actor.password);

  await page.goto(organization.request_route);
  await expect(page.locator("[data-membership-request-form-root]")).toBeVisible();
  await page.getByLabel("Membership type").selectOption("gold");
  await expect(page.locator('[data-test="questions-sponsorship"]')).toBeVisible();
  await page.locator("#id_q_sponsorship_details").fill(
    "We sponsor release engineering and community events for the Wave 7 browser slice.",
  );
  await page.getByRole("button", { name: /submit request/i }).click();

  await expect(page).toHaveURL(new RegExp(`${organization.detail_route.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}$`));
  await expect(page.getByText("Membership request submitted for review.")).toBeVisible();
  await expect(page.getByRole("listitem").filter({ hasText: /Gold Sponsor Member/ })).toContainText("Under review");
});

// As a user with all available types already held, I see a no-types-available state instead of a submittable form.
test("membership-no-types-available shows the non-submittable empty state for a fully subscribed organization", async ({ page }) => {
  const resetState = readSelfServiceResetState();
  const organization = resetState.organizations.representative_no_types_org;
  const actor = resetState.actors[organization.representative_username];

  await loginViaForm(page, actor.username, actor.password);

  await page.goto(organization.request_route);
  await expect(page.locator("[data-membership-request-form-root]")).toBeVisible();
  await expect(page.getByText("Thank you for your support of AlmaLinux!", { exact: true })).toBeVisible();
  await expect(
    page.getByText(/there are no additional memberships available for you to apply for at this time\./i),
  ).toBeVisible();
  await expect(page.getByRole("button", { name: /submit request/i })).toHaveCount(0);
});
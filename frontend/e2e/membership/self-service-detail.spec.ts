import { expect, test, type Locator, type Page } from "@playwright/test";

import { readSelfServiceResetState } from "./self-service-reset-state";

async function loginViaForm(page: Page, username: string, password: string): Promise<void> {
  await page.goto("/login/");
  await page.getByLabel("Username", { exact: true }).fill(username);
  await page.getByLabel("Password", { exact: true }).fill(password);
  await page.getByRole("button", { name: /log in/i }).click();
}

async function logout(page: Page): Promise<void> {
  await page.getByRole("button", { name: /^Logout$/ }).click();
  await expect(page).toHaveURL(/\/login\/?$/);
}

async function firstProfileRequestLink(page: Page, expectedName: RegExp): Promise<Locator> {
  const requestLinks = page.locator('a[href*="/membership/request/"]');
  await expect(requestLinks.filter({ hasText: expectedName }).first()).toBeVisible();
  return requestLinks.filter({ hasText: expectedName }).first();
}

// As an authenticated user viewing my profile membership card, I can follow a link to request membership.
test("membership-profile-membership-card-links expose request-membership navigation", async ({ page }) => {
  const resetState = readSelfServiceResetState();
  const actor = resetState.actors.regular01;

  await loginViaForm(page, actor.username, actor.password);
  await page.goto(actor.profile_route);
  await expect(page.locator("[data-user-profile-root]")).toBeVisible();
  const membershipPanel = page.locator("[data-user-profile-membership-root]");
  await expect(membershipPanel).toBeVisible();
  await expect(membershipPanel.getByRole("link", { name: "Request membership", exact: true }).first()).toHaveAttribute(
    "href",
    resetState.routes.create,
  );
});

// As an authenticated user viewing my profile membership card, I can follow links to request membership, renew membership, and review pending requests.
test("membership-profile-pending-links surfaces the seeded pending and on-hold request links", async ({ page }) => {
  const resetState = readSelfServiceResetState();
  const onHoldActor = resetState.actors[resetState.requests.resubmit_on_hold.actor_username];
  const pendingActor = resetState.actors[resetState.requests.rescind_pending.actor_username];

  await loginViaForm(page, onHoldActor.username, onHoldActor.password);
  await page.goto(onHoldActor.profile_route);
  await expect(page.locator("[data-user-profile-root]")).toBeVisible();
  await expect(page.locator("[data-user-profile-membership-root]")).toBeVisible();
  const onHoldLink = await firstProfileRequestLink(page, /action required/i);
  await expect(onHoldLink).toHaveAttribute("href", resetState.requests.resubmit_on_hold.detail_route);
  await logout(page);

  await loginViaForm(page, pendingActor.username, pendingActor.password);
  await page.goto(pendingActor.profile_route);
  await expect(page.locator("[data-user-profile-root]")).toBeVisible();
  await expect(page.locator("[data-user-profile-membership-root]")).toBeVisible();
  const pendingLink = await firstProfileRequestLink(page, /under review/i);
  await expect(pendingLink).toHaveAttribute("href", resetState.requests.rescind_pending.detail_route);
});

// As a user with an organization-target pending request, I can inspect the requested organization and submitted response links from the detail route.
test("membership-organization-target-detail-links exposes the requested-for organization link and response links", async ({ page }) => {
  const resetState = readSelfServiceResetState();
  const actor = resetState.actors.regular01;
  const organizationRequest = resetState.requests.organization_target_pending;
  const organization = resetState.organizations.representative_form_org;

  await loginViaForm(page, actor.username, actor.password);

  await page.goto(organizationRequest.detail_route);
  await expect(page.locator("[data-membership-request-detail-root]")).toBeVisible();
  await expect(page.getByText("Pending", { exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: organization.name, exact: true })).toHaveAttribute(
    "href",
    organization.detail_route,
  );
  await expect(page.getByRole("link", { name: "https://mirror.regular01-org.example.test", exact: true })).toHaveAttribute(
    "href",
    "https://mirror.regular01-org.example.test",
  );
  await expect(
    page.getByRole("link", { name: "https://github.com/AlmaLinux/mirrors/pull/601", exact: true }),
  ).toHaveAttribute("href", "https://github.com/AlmaLinux/mirrors/pull/601");
});

// As a user with an on_hold request, I can update the self-service form and resubmit for review.
test("membership-resubmit-on-hold-mirror validates and resubmits an on-hold mirror request", async ({ page }) => {
  const resetState = readSelfServiceResetState();
  const actor = resetState.actors[resetState.requests.resubmit_on_hold.actor_username];

  await loginViaForm(page, actor.username, actor.password);

  await page.goto(resetState.requests.resubmit_on_hold.detail_route);
  await expect(page.locator("[data-membership-request-detail-root]")).toBeVisible();
  await expect(page.getByText("On Hold", { exact: true })).toBeVisible();

  const domainField = page.getByLabel("Domain", { exact: true });
  const pullRequestField = page.getByLabel("Pull request", { exact: true });
  await expect(domainField).toHaveValue("https://mirror.regular34.example.test");
  await expect(pullRequestField).toHaveValue("https://github.com/AlmaLinux/mirrors/pull/404");
  await domainField.fill("not a url");
  await pullRequestField.fill("https://github.com/AlmaLinux/mirrors/pull/444");
  await page.getByRole("button", { name: /submit request/i }).click();
  await expect(page.locator(".invalid-feedback").first()).toBeVisible();

  await domainField.fill("https://mirror.regular34.example.test");
  await pullRequestField.fill("https://github.com/AlmaLinux/mirrors/pull/445");
  await page.getByLabel(/additional information/i).fill("Updated mirror details for committee review.");
  await page.getByRole("button", { name: /submit request/i }).click();
  await expect(page.getByText("Pending", { exact: true })).toBeVisible();
});

// As a user with a pending request, I can open the self-service detail route, review submitted responses, and rescind the request.
test("membership-rescind-pending-individual rescinds a pending request from the self-service detail route", async ({ page }) => {
  const resetState = readSelfServiceResetState();
  const actor = resetState.actors[resetState.requests.rescind_pending.actor_username];

  await loginViaForm(page, actor.username, actor.password);

  await page.goto(resetState.requests.rescind_pending.detail_route);
  await expect(page.locator("[data-membership-request-detail-root]")).toBeVisible();
  await expect(page.getByRole("button", { name: /^Rescind request$/i }).first()).toBeVisible();

  await page.getByRole("button", { name: /^Rescind request$/i }).first().click();
  await page.locator("#rescind-confirm-modal").getByRole("button", { name: /^Rescind request$/i }).click();

  await expect(page).toHaveURL(new RegExp(`${actor.profile_route.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}$`));
  await expect(page.getByText("Your request has been rescinded.")).toBeVisible();
});
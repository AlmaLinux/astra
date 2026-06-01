import { expect, test, type Page } from "@playwright/test";

import { readOrganizationsResetState } from "./resetState";

async function loginViaForm(page: Page, username: string, password: string): Promise<void> {
  await page.goto("/login/");
  await page.getByLabel("Username", { exact: true }).fill(username);
  await page.getByLabel("Password", { exact: true }).fill(password);
  await page.getByRole("button", { name: /log in/i }).click();
}

// As a user, I can browse organizations, search sponsor and mirror cards independently, paginate both lists, and create my own organization if none exists.
test("organizations-list-shell renders the real organizations shell with deterministic sponsor and mirror cards", async ({ page }) => {
  const resetState = readOrganizationsResetState();
  const observer = resetState.actors.representative_observer;
  const myOrganizationCard = page.locator(".card").filter({
    has: page.getByRole("heading", { name: "My Organization", exact: true }),
  }).first();

  await loginViaForm(page, observer.username, observer.password);

  const organizationsResponse = page.waitForResponse((response) => {
    return response.url().includes("/api/v1/organizations") && response.request().method() === "GET";
  });

  await page.goto("/organizations/");
  await expect(page.locator("[data-organizations-root]")).toBeVisible();
  await organizationsResponse;

  await expect(page.getByRole("heading", { name: "My Organization", exact: true })).toBeVisible();
  await expect(myOrganizationCard.getByRole("link", { name: resetState.organizations.my_org.name, exact: true })).toBeVisible();
  await expect(page.getByText(resetState.organizations.sponsor_shell_observer.name, { exact: true })).toBeVisible();
  await expect(page.getByText(resetState.organizations.mirror_shell_observer.name, { exact: true })).toBeVisible();
  await expect(page.locator("[data-organizations-root] table")).toHaveCount(0);
});

// As a user, I can browse organizations, search sponsor and mirror cards independently, paginate both lists, and create my own organization if none exists.
test("organizations-sponsor-search-mirror-stability narrows the sponsor card without collapsing the mirror card", async ({ page }) => {
  const resetState = readOrganizationsResetState();
  const observer = resetState.actors.representative_observer;
  const searchTerm = resetState.organizations.sponsor_search_hit.name;

  await loginViaForm(page, observer.username, observer.password);
  await page.goto(`/organizations/?q_sponsor=${encodeURIComponent(searchTerm)}`);
  await expect(page.locator("[data-organizations-root]")).toBeVisible();
  await expect(page.locator('input[name="q_sponsor"]')).toHaveValue(searchTerm);
  await expect(page.getByText(resetState.organizations.sponsor_search_hit.name, { exact: true })).toBeVisible();
  await expect(page.getByText(resetState.organizations.sponsor_shell_observer.name, { exact: true })).toHaveCount(0);
  await expect(page.getByText(resetState.organizations.mirror_shell_observer.name, { exact: true })).toBeVisible();
});

// As a representative, I can see action-required styling for on-hold sponsorship requests.
test("organizations-detail-membership-state shows active membership metadata and representative on-hold copy", async ({ page }) => {
  const resetState = readOrganizationsResetState();
  const observer = resetState.actors.representative_observer;
  const detailTarget = resetState.scenarios["organizations-detail-membership-state"].route_target;
  const requestId = resetState.requests.detail_pending_request.request_id;

  await loginViaForm(page, observer.username, observer.password);

  const detailResponse = page.waitForResponse((response) => {
    return response.url().includes(`/api/v1/organizations/${resetState.organizations.detail_focus_org.organization_id}/detail`)
      && response.request().method() === "GET";
  });

  await page.goto(detailTarget);
  await expect(page.locator("[data-organization-detail-root]")).toBeVisible();
  await detailResponse;

  await expect(page.locator("[data-membership-card-root]")).toBeVisible();
  await expect(
    page.locator("[data-membership-card-root] .badge").filter({ hasText: /^Gold Sponsor Member$/ }),
  ).toBeVisible();
  await expect(page.getByText(/Member since/)).toBeVisible();
  await expect(page.getByText(/Expires/)).toBeVisible();
  await expect(page.getByRole("link", { name: `Request #${requestId}` })).toBeVisible();
  await expect(page.getByText("Action required", { exact: true })).toBeVisible();
});

// As a user, I can browse organizations, search sponsor and mirror cards independently, paginate both lists, and create my own organization if none exists.
test("organizations-mirror-search-stability narrows the mirror card without collapsing the sponsor card", async ({ page }) => {
  const resetState = readOrganizationsResetState();
  const observer = resetState.actors.representative_observer;
  const searchTerm = resetState.organizations.mirror_shell_observer.name;

  await loginViaForm(page, observer.username, observer.password);
  await page.goto(`/organizations/?q_mirror=${encodeURIComponent(searchTerm)}`);
  await expect(page.locator("[data-organizations-root]")).toBeVisible();

  await expect(page.locator('input[name="q_mirror"]')).toHaveValue(searchTerm);
  await expect(page.getByText(resetState.organizations.mirror_shell_observer.name, { exact: true })).toBeVisible();
  await expect(page.getByText(resetState.organizations.sponsor_shell_observer.name, { exact: true })).toBeVisible();
  await expect(page.getByText(resetState.organizations.sponsor_search_hit.name, { exact: true })).toBeVisible();
});

// As a user, I can browse organizations, search sponsor and mirror cards independently, paginate both lists, and create my own organization if none exists.
test("organizations-list-pagination-and-create-cta paginates sponsor and mirror cards independently for a user without an organization", async ({ page }) => {
  const resetState = readOrganizationsResetState();
  const actor = resetState.actors.no_org_actor;
  const sponsorCard = page.locator(".card").filter({ has: page.getByRole("heading", { name: /AlmaLinux Sponsor Members/ }) }).first();
  const mirrorCard = page.locator(".card").filter({ has: page.getByRole("heading", { name: /Mirror Sponsor Members/ }) }).first();

  await loginViaForm(page, actor.username, actor.password);
  await page.goto("/organizations/");
  await expect(page.locator("[data-organizations-root]")).toBeVisible();
  await expect(page.getByRole("link", { name: "Create organization", exact: true })).toBeVisible();

  const sponsorPageTwoResponse = page.waitForResponse((response) => {
    return response.url().includes("/api/v1/organizations?page_sponsor=2") && response.request().method() === "GET";
  });
  await sponsorCard.getByLabel("Next", { exact: true }).click();
  await sponsorPageTwoResponse;

  await expect(page.getByText(resetState.organizations.sponsor_page_two_org.name, { exact: true })).toBeVisible();
  await expect(page.getByText(resetState.organizations.mirror_page_two_org.name, { exact: true })).toHaveCount(0);

  const mirrorPageTwoResponse = page.waitForResponse((response) => {
    return response.url().includes("/api/v1/organizations?page_sponsor=2&page_mirror=2") && response.request().method() === "GET";
  });
  await mirrorCard.getByLabel("Next", { exact: true }).click();
  await mirrorPageTwoResponse;

  await expect(page.getByText(resetState.organizations.sponsor_page_two_org.name, { exact: true })).toBeVisible();
  await expect(page.getByText(resetState.organizations.mirror_page_two_org.name, { exact: true })).toBeVisible();
  await expect(page).toHaveURL(/\/organizations\/\?page_sponsor=2&page_mirror=2$/);
});

// As a representative or privileged user, I can create or edit an organization profile using the dedicated form.
test("organizations-create-form renders the dedicated organization form for a user without an organization", async ({ page }) => {
  const resetState = readOrganizationsResetState();
  const actor = resetState.actors.no_org_actor;

  await loginViaForm(page, actor.username, actor.password);
  await page.goto("/organizations/");
  await page.getByRole("link", { name: "Create organization", exact: true }).click();

  await expect(page.locator("[data-organization-form-root]")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Create organization", exact: true })).toBeVisible();
  await expect(page.getByText(/Create an organization profile only if you are an employee or authorized representative/i)).toBeVisible();
  await expect(page.getByRole("link", { name: "AlmaLinux OS Foundation Privacy Policy", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Create", exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: "Cancel", exact: true })).toBeVisible();
});

// As a representative or privileged operator, I can inspect representative/business/marketing/technical contacts and use the detail-page action bar.
test("organizations-detail-contacts-action-bar exposes the edit action and contact tabs", async ({ page }) => {
  const resetState = readOrganizationsResetState();
  const observer = resetState.actors.representative_observer;
  const detailTarget = resetState.scenarios["organizations-detail-membership-state"].route_target;
  const organizationDetailRoot = page.locator("[data-organization-detail-root]");

  await loginViaForm(page, observer.username, observer.password);

  const detailResponse = page.waitForResponse((response) => {
    return response.url().includes(`/api/v1/organizations/${resetState.organizations.detail_focus_org.organization_id}/detail`)
      && response.request().method() === "GET";
  });

  await page.goto(detailTarget);
  await expect(organizationDetailRoot).toBeVisible();
  await detailResponse;

  await expect(page.getByRole("link", { name: /Edit details$/ })).toBeVisible();
  await expect(organizationDetailRoot.locator("dl").getByRole("link", { name: observer.username, exact: true })).toBeVisible();

  await page.getByRole("button", { name: "Business", exact: true }).click();
  await expect(page.getByRole("link", { name: "observer@wave4-my-org.example.test", exact: true })).toBeVisible();

  await page.getByRole("button", { name: "PR and marketing", exact: true }).click();
  await expect(page.getByRole("link", { name: "marketing@wave4-my-org.example.test", exact: true })).toBeVisible();

  await page.getByRole("button", { name: "Technical", exact: true }).click();
  await expect(page.getByRole("link", { name: "tech@wave4-my-org.example.test", exact: true })).toBeVisible();
});

// As a representative or operator, I can review active and pending sponsorship memberships, request tier changes, edit expiration dates, and terminate sponsorships with typed confirmation.
test("organizations-detail-sponsorship-actions expose tier-change and typed termination controls", async ({ page }) => {
  const resetState = readOrganizationsResetState();
  const observer = resetState.actors.representative_observer;
  const detailTarget = resetState.scenarios["organizations-detail-membership-state"].route_target;
  const orgName = resetState.organizations.detail_focus_org.name;

  await loginViaForm(page, observer.username, observer.password);
  await page.goto(detailTarget);
  await expect(page.locator("[data-organization-detail-root]")).toBeVisible();

  await expect(page.getByRole("link", { name: "Change tier", exact: true })).toHaveAttribute(
    "href",
    /membership_type=ruby/,
  );

  await page.getByRole("button", { name: "Edit expiration", exact: true }).click();

  const managementModal = page.locator(".modal.show").filter({ hasText: `Manage membership: Gold Sponsor Member for ${orgName}` }).first();
  await expect(managementModal).toBeVisible();
  await expect(managementModal.getByRole("button", { name: "Save expiration", exact: true })).toBeVisible();
  await expect(managementModal.getByRole("button", { name: /^Terminate membership/ }).first()).toBeVisible();

  await managementModal.getByRole("button", { name: /^Terminate membership/ }).first().click();
  const terminateInput = managementModal.locator('input[name="confirm"]');
  const terminateButton = managementModal.getByRole("button", { name: "Terminate membership", exact: true });
  await expect(terminateButton).toBeDisabled();

  await terminateInput.fill(orgName);
  await expect(terminateButton).toBeEnabled();

  await managementModal.getByRole("button", { name: "Cancel termination", exact: true }).click();
  await expect(terminateInput).toHaveValue("");
});

// As a privileged user, I can create or edit an organization profile using the dedicated form.
test("organizations-edit-form renders the dedicated organization form for the representative", async ({ page }) => {
  const resetState = readOrganizationsResetState();
  const observer = resetState.actors.representative_observer;
  const detailTarget = resetState.scenarios["organizations-detail-membership-state"].route_target;

  await loginViaForm(page, observer.username, observer.password);
  await page.goto(detailTarget);
  await page.getByRole("link", { name: /Edit details$/ }).click();

  await expect(page.locator("[data-organization-form-root]")).toBeVisible();
  await expect(page.getByRole("heading", { name: `Edit ${resetState.organizations.detail_focus_org.name}`, exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Save", exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: "Cancel", exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: "AlmaLinux OS Foundation Privacy Policy", exact: true })).toBeVisible();
});

// As a privileged operator, I can delete an organization through the detail-page confirmation modal.
test("organizations-detail-delete-modal exposes destructive confirmation copy without submitting", async ({ page }) => {
  const resetState = readOrganizationsResetState();
  const observer = resetState.actors.representative_observer;
  const detailTarget = resetState.scenarios["organizations-detail-membership-state"].route_target;
  const orgName = resetState.organizations.detail_focus_org.name;

  await loginViaForm(page, observer.username, observer.password);
  await page.goto(detailTarget);
  await page.getByRole("button", { name: /Delete organization$/ }).click();

  const deleteModal = page.locator("#organization-delete-modal");
  await expect(deleteModal).toBeVisible();
  await expect(deleteModal.getByText(`Delete organization ${orgName}? This will terminate the membership and remove the representative from the member group. This cannot be undone. After deletion, you will be redirected to the organizations list.`)).toBeVisible();
  await deleteModal.getByRole("button", { name: "Cancel", exact: true }).click();
  await expect(deleteModal).not.toHaveClass(/show/);
  await expect(page.locator("[data-organization-detail-root]")).toBeVisible();
  await expect(page.getByRole("heading", { name: orgName, exact: true })).toBeVisible();
});
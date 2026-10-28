import { readFileSync } from "node:fs";

import { expect, test, type Page } from "@playwright/test";
import { getDocument } from "pdfjs-dist/legacy/build/pdf.mjs";

import { readOrganizationsResetState } from "./resetState";

// The E2E stack prints the bank details from this file (see docker-compose.yml).
const PAYMENT_DETAILS_FILE = new URL("../../../docker/invoice-payment-details.example.txt", import.meta.url);

/** Extract a PDF's text, with whitespace collapsed so assertions ignore line wrapping. */
async function pdfText(data: Buffer): Promise<string> {
  const pdf = await getDocument({ data: new Uint8Array(data) }).promise;
  const pages: string[] = [];
  for (let pageNumber = 1; pageNumber <= pdf.numPages; pageNumber += 1) {
    const content = await (await pdf.getPage(pageNumber)).getTextContent();
    pages.push(content.items.map((item) => ("str" in item ? item.str : "")).join(" "));
  }
  return pages.join(" ").replace(/\s+/g, " ");
}

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
  // Header actions live in the shared membership-card actions slot.
  await expect(
    page.locator("[data-membership-card-root]").getByRole("link", { name: "History", exact: true }),
  ).toHaveAttribute("href", `/membership/log/org/${resetState.organizations.detail_focus_org.organization_id}/`);
  // The membership title names the tier; there is no duplicate tier badge.
  await expect(page.locator("[data-membership-card-root]").getByText("Gold Sponsor", { exact: true })).toHaveCount(1);
  await expect(
    page.locator("[data-membership-card-root] .badge").filter({ hasText: /^Gold Sponsor$/ }),
  ).toHaveCount(0);
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
    /membership_type=platinum/,
  );

  await page.getByRole("button", { name: "Edit membership", exact: true }).click();

  const managementModal = page.locator(".modal.show").filter({ hasText: `Manage membership: Gold Sponsor for ${orgName}` }).first();
  await expect(managementModal).toBeVisible();
  await expect(managementModal.getByRole("button", { name: "Save changes", exact: true })).toBeVisible();
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

// As a representative, I can request a renewal of a sponsorship that is about to expire.
test("organizations-detail-renewal-cta offers the representative a same-tier renewal link", async ({ page }) => {
  const resetState = readOrganizationsResetState();
  const actor = resetState.actors.renewal_representative;
  const renewalOrg = resetState.organizations.renewal_focus_org;
  const detailTarget = resetState.scenarios["organizations-detail-renewal-cta"].route_target;

  await loginViaForm(page, actor.username, actor.password);

  const detailResponse = page.waitForResponse((response) => {
    return response.url().includes(`/api/v1/organizations/${renewalOrg.organization_id}/detail`)
      && response.request().method() === "GET";
  });

  await page.goto(detailTarget);
  await expect(page.locator("[data-organization-detail-root]")).toBeVisible();
  await detailResponse;

  // The expiring sponsorship must surface the warning styling and the renewal CTA.
  await expect(page.locator("[data-membership-card-root] .text-danger")).toContainText(/Expires/);

  const renewalLink = page.getByRole("link", { name: "Request renewal", exact: true });
  await expect(renewalLink).toBeVisible();
  // Renewal targets the tier the organization already holds, not a suggested different tier.
  await expect(renewalLink).toHaveAttribute("href", /membership_type=gold/);

  await renewalLink.click();
  await expect(page).toHaveURL(
    new RegExp(`/organization/${renewalOrg.organization_id}/membership/request/\\?membership_type=gold$`),
  );
  await expect(page.locator("[data-membership-request-form-root]")).toBeVisible();
});

// As a representative, I do not see a renewal CTA for a sponsorship that is not close to expiring.
test("organizations-detail-membership-state withholds the renewal CTA outside the expiring-soon window", async ({ page }) => {
  const resetState = readOrganizationsResetState();
  const observer = resetState.actors.representative_observer;
  const detailTarget = resetState.scenarios["organizations-detail-membership-state"].route_target;

  await loginViaForm(page, observer.username, observer.password);
  await page.goto(detailTarget);
  await expect(page.locator("[data-membership-card-root]")).toBeVisible();

  await expect(page.getByRole("link", { name: "Request renewal", exact: true })).toHaveCount(0);
  await expect(page.getByRole("link", { name: "Change tier", exact: true })).toBeVisible();
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
// As a representative with a renewal due, I can generate, download, and regenerate my sponsorship invoice.
test("organizations-detail-invoice lets the representative generate download and regenerate the renewal invoice", async ({ page }) => {
  const resetState = readOrganizationsResetState();
  const actor = resetState.actors.renewal_representative;
  const detailTarget = resetState.scenarios["organizations-detail-renewal-cta"].route_target;

  await loginViaForm(page, actor.username, actor.password);
  await page.goto(detailTarget);
  await expect(page.locator("[data-organization-detail-root]")).toBeVisible();

  const representativeActions = page.locator('[data-membership-actions="representative"]');
  await expect(page.locator('[data-membership-actions="committee"]')).toHaveCount(0);

  // A fresh reset has no invoice yet; a rerun without a reset may already have one.
  await representativeActions.getByRole("button", { name: /^(Generate|Regenerate) invoice$/ }).click();
  const modal = page.locator(".modal.show").filter({ hasText: "will be rebuilt" });
  await expect(modal).toBeVisible();
  await modal.getByRole("button", { name: /^(Generate|Regenerate) invoice$/ }).click();

  await expect(page.getByText("Invoice generated.", { exact: true })).toBeVisible();
  const download = page.getByRole("link", { name: /Download invoice \d{4}-\d{4}-\d{6}$/ });
  await expect(download).toBeVisible();
  const pdf = await page.request.get((await download.getAttribute("href")) ?? "");
  expect(pdf.status()).toBe(200);
  expect(pdf.headers()["content-type"]).toBe("application/pdf");
  expect((await pdf.body()).subarray(0, 5).toString()).toBe("%PDF-");

  await expect(representativeActions.getByRole("button", { name: "Regenerate invoice", exact: true })).toBeVisible();
});

// As a membership committee member, my committee-only controls sit on their own row below the representative actions.
test("organizations-detail-sponsorship-actions separate committee-only controls from representative actions", async ({ page }) => {
  const resetState = readOrganizationsResetState();
  const observer = resetState.actors.representative_observer;
  const detailTarget = resetState.scenarios["organizations-detail-membership-state"].route_target;

  await loginViaForm(page, observer.username, observer.password);
  await page.goto(detailTarget);
  await expect(page.locator("[data-membership-card-root]")).toBeVisible();

  const representativeActions = page.locator('[data-membership-actions="representative"]');
  const committeeActions = page.locator('[data-membership-actions="committee"]');
  await expect(representativeActions.getByRole("link", { name: "Change tier", exact: true })).toBeVisible();
  // Outside the renewal window only the committee may (re)issue the current term's invoice.
  await expect(representativeActions.getByRole("button", { name: /^(Generate|Regenerate) invoice$/ })).toBeVisible();
  await expect(committeeActions.getByRole("button", { name: "Edit membership", exact: true })).toBeVisible();
  await expect(representativeActions.getByRole("button", { name: "Edit membership", exact: true })).toHaveCount(0);
});

// As a representative, I can record billing notes, such as a PO number, that are printed on my invoices.
test("organizations-edit-billing-notes saves billing notes shown on the detail page", async ({ page }) => {
  const resetState = readOrganizationsResetState();
  const observer = resetState.actors.representative_observer;
  const detailTarget = resetState.scenarios["organizations-detail-membership-state"].route_target;

  await loginViaForm(page, observer.username, observer.password);
  await page.goto(detailTarget);
  await page.getByRole("link", { name: /Edit details$/ }).click();
  await expect(page.locator("[data-organization-form-root]")).toBeVisible();

  await page.getByLabel("Billing notes:", { exact: true }).fill("PO: 4500012345");
  await page.getByRole("button", { name: "Save", exact: true }).click();

  await expect(page.locator("[data-organization-detail-root]")).toBeVisible();
  const infoCard = page.locator(".organization-info-card");
  await expect(infoCard.getByText("Billing notes", { exact: true })).toBeVisible();
  await expect(infoCard.getByText("PO: 4500012345", { exact: true })).toBeVisible();
});

// As a sponsor, I receive an invoice that bills my organization, tier, and price, carries my billing notes, and tells me how to pay.
test("organizations-invoice-pdf-content prints billing details notes price and payment instructions", async ({ page }) => {
  const resetState = readOrganizationsResetState();
  const observer = resetState.actors.representative_observer;
  const organization = resetState.organizations.detail_focus_org;
  const detailTarget = resetState.scenarios["organizations-detail-membership-state"].route_target;
  const billingNotes = "PO: 4500067890 Attn: Accounts Payable";

  await loginViaForm(page, observer.username, observer.password);
  await page.goto(detailTarget);
  await page.getByRole("link", { name: /Edit details$/ }).click();
  await page.getByLabel("Billing notes:", { exact: true }).fill(billingNotes);
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await expect(page.locator("[data-organization-detail-root]")).toBeVisible();

  // Regenerate (or generate) so the stored PDF reflects the notes just saved.
  const representativeActions = page.locator('[data-membership-actions="representative"]');
  await representativeActions.getByRole("button", { name: /^(Generate|Regenerate) invoice$/ }).click();
  const modal = page.locator(".modal.show").filter({ hasText: "will be rebuilt" });
  await modal.getByRole("button", { name: /^(Generate|Regenerate) invoice$/ }).click();
  await expect(page.getByText("Invoice generated.", { exact: true })).toBeVisible();

  const download = page.getByRole("link", { name: /Download invoice \d{4}-\d{4}-\d{6}$/ });
  const invoiceNumber = ((await download.textContent()) ?? "").match(/\d{4}-\d{4}-\d{6}/)?.[0] ?? "";
  const response = await page.request.get((await download.getAttribute("href")) ?? "");
  expect(response.status()).toBe(200);
  const text = await pdfText(await response.body());

  expect(text).toContain("Sponsor member invoice");
  expect(text).toContain(organization.name);
  expect(text).toContain(billingNotes);
  expect(text).toContain("United States");
  expect(text).toContain(`Invoice # ${invoiceNumber}`);
  expect(text).toMatch(/Invoice date \w+ \d{1,2}, \d{4}/);
  expect(text).toContain("Gold Sponsor");
  expect(text).toMatch(/from \w+ \d{1,2}(st|nd|rd|th) \d{4} to \w+ \d{1,2}(st|nd|rd|th) \d{4}/);
  expect(text).toContain("Amount due $20,000");
  expect(text).not.toContain("List price");
  expect(text).toContain("Please complete payment upon receipt.");
  for (const line of readFileSync(PAYMENT_DETAILS_FILE, "utf-8").split("\n").map((value) => value.trim()).filter(Boolean)) {
    expect(text).toContain(line.replace(/\s+/g, " "));
  }
});

// As a membership committee member, I record an in-kind agreement as a dues discount, and the sponsor's invoice shows it.
test("organizations-inkind-dues-invoice lets the committee discount dues and prints the discount on the invoice", async ({ page }) => {
  const resetState = readOrganizationsResetState();
  const observer = resetState.actors.representative_observer;
  const detailTarget = resetState.scenarios["organizations-detail-renewal-cta"].route_target;

  await loginViaForm(page, observer.username, observer.password);
  await page.goto(detailTarget);
  await expect(page.locator("[data-organization-detail-root]")).toBeVisible();

  await page.locator('[data-membership-actions="committee"]').getByRole("button", { name: "Edit membership", exact: true }).click();
  const managementModal = page.locator(".modal.show").filter({ hasText: "Manage membership: Gold Sponsor" }).first();
  const save = managementModal.getByRole("button", { name: "Save changes", exact: true });
  const duesInput = managementModal.getByLabel("Annual dues (USD)", { exact: true });
  // Nothing to save until something changes.
  await expect(save).toBeDisabled();
  // A sponsor paying the standard price starts with an empty field, which only takes digits.
  await expect(duesInput).toHaveValue("");
  await duesInput.pressSequentially("50a00");
  await expect(duesInput).toHaveValue("5000");
  // A dues change cannot even be submitted without a reason.
  await expect(save).toBeDisabled();
  await expect(managementModal.locator('span[title="Enter a reason for the dues change first."]')).toContainText("Save changes");
  await managementModal.getByLabel("Reason for the dues change", { exact: true }).fill("Hosts our CI runners worth $15,000 a year");
  await expect(save).toBeEnabled();
  await save.click();

  await expect(page.getByText("Sponsorship updated.", { exact: true })).toBeVisible();
  await expect(page.locator('[data-membership-dues="gold"]')).toHaveText("Annual dues: $5,000 (discounted from $20,000)");

  await page.locator('[data-membership-actions="representative"]').getByRole("button", { name: /^(Generate|Regenerate) invoice$/ }).click();
  const invoiceModal = page.locator(".modal.show").filter({ hasText: "will be rebuilt" });
  await invoiceModal.getByRole("button", { name: /^(Generate|Regenerate) invoice$/ }).click();
  await expect(page.getByText("Invoice generated.", { exact: true })).toBeVisible();

  const download = page.getByRole("link", { name: /Download invoice \d{4}-\d{4}-\d{6}$/ });
  const response = await page.request.get((await download.getAttribute("href")) ?? "");
  expect(response.status()).toBe(200);
  const text = await pdfText(await response.body());
  expect(text).toContain("List price $20,000");
  expect(text).toContain("In-kind discount (75%) -$15,000");
  expect(text).toContain("Amount due $5,000");
});

// As a membership committee member, I confirm each in-kind renewal individually, and approving it keeps the sponsor's discount.
test("organizations-inkind-renewal-review keeps discounted renewals out of bulk approval and carries the discount", async ({ page }) => {
  const resetState = readOrganizationsResetState();
  const observer = resetState.actors.representative_observer;
  const requestId = resetState.requests.inkind_renewal_request.request_id;
  const organization = resetState.organizations.inkind_renewal_org;

  await loginViaForm(page, observer.username, observer.password);
  await page.goto("/membership/requests/?filter=sponsorships");
  const pending = page.locator(".card").filter({ hasText: /Pending:\s*\d+/ }).first();
  const row = pending.locator(`tbody tr:has(input[type="checkbox"][name="selected"][value="${requestId}"])`);
  await expect(row.getByText("In-kind", { exact: true })).toHaveAttribute("title", "Pays $5,000 of $20,000");

  await row.locator('input[type="checkbox"][name="selected"]').check();
  await pending.locator('select[name="bulk_action"]').selectOption("accept");
  await pending.getByRole("button", { name: "Apply", exact: true }).click();
  await expect(pending.getByText(`Skipped 1 discounted renewal (#${requestId})`)).toBeVisible();
  await expect(row).toBeVisible();

  await row.getByRole("button", { name: "Approve", exact: true }).click();
  const modal = page.locator('.modal[aria-modal="true"]');
  await expect(modal.getByText("Pays $5,000 of $20,000 under an in-kind agreement")).toBeVisible();
  await expect(modal.getByLabel("Annual dues (USD)", { exact: true })).toHaveValue("5000");
  await modal.getByRole("button", { name: "Approve", exact: true }).click();
  await expect(row).toHaveCount(0);

  await page.goto(organization.detail_url);
  await expect(page.locator('[data-membership-dues="gold"]')).toHaveText("Annual dues: $5,000 (discounted from $20,000)");
});

// As a representative of an in-kind sponsor, I can see my discount, but I can neither change it nor ask for one.
test("organizations-inkind-representative sees the discount but cannot change or request one", async ({ page }) => {
  const resetState = readOrganizationsResetState();
  const representative = resetState.actors.inkind_representative;
  const organization = resetState.organizations.inkind_renewal_org;
  const requestId = resetState.requests.inkind_renewal_request.request_id;
  const discount = "Annual dues: $5,000 (discounted from $20,000)";

  await loginViaForm(page, representative.username, representative.password);
  await page.goto(organization.detail_url);
  const root = page.locator("[data-organization-detail-root]");
  await expect(page.locator('[data-membership-dues="gold"]')).toHaveText(discount);
  await expect(page.locator('[data-membership-actions="committee"]')).toHaveCount(0);
  await expect(page.locator('input[name="dues_usd"]')).toHaveCount(0);

  // Posting straight to the committee endpoints changes nothing.
  const headers = { "X-CSRFToken": (await root.getAttribute("data-organization-detail-csrf-token")) ?? "" };
  const detail = await (await page.request.get(`/api/v1/organizations/${organization.organization_id}/detail`)).json();
  await page.request.post(`/membership/${detail.organization.memberships[0].id}/update/`, {
    headers,
    form: { expires_on: "2099-12-31", dues_usd: "0", dues_reason: "We would like a discount" },
  });
  const approve = await page.request.post(`/api/v1/membership/request/${requestId}/approve`, {
    headers,
    form: { dues_usd: "0", dues_reason: "We would like a discount" },
  });
  expect(approve.status()).toBe(403);
  await page.reload();
  await expect(page.locator('[data-membership-dues="gold"]')).toHaveText(discount);

  // The sponsorship request form has nowhere to ask for a discount.
  await page.goto(`/organization/${organization.organization_id}/membership/request/`);
  await expect(page.locator("form")).not.toHaveCount(0);
  await expect(page.locator('[name="dues_usd"]')).toHaveCount(0);
  await expect(page.getByText(/discount|dues waiver/i)).toHaveCount(0);
});

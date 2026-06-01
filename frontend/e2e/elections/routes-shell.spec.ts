import { expect, test, type Page } from "@playwright/test";

import { readElectionsResetState } from "./resetState";

const resetState = readElectionsResetState();

async function loginViaForm(page: Page, username: string, password: string): Promise<void> {
  await page.goto("/login/");
  await page.getByLabel("Username", { exact: true }).fill(username);
  await page.getByLabel("Password", { exact: true }).fill(password);
  await page.getByRole("button", { name: /log in/i }).click();
}

// As an ineligible viewer, I see the ineligible state and membership-request link rather than a vote CTA.
test("elections-vote-ineligible-state shows the membership-request action on the open election detail page", async ({ page }) => {
  const viewer = resetState.actors.viewer;

  await loginViaForm(page, viewer.username, viewer.password);
  await page.goto(resetState.routes.open_detail);

  const actionCard = page.locator("[data-election-action-card-vue-root]");
  await expect(actionCard).toBeVisible();
  await expect(actionCard.getByText("You're not eligible to vote in this election.", { exact: true })).toBeVisible();
  await expect(actionCard.getByRole("link", { name: "Request membership", exact: true })).toBeVisible();
  await expect(actionCard.getByRole("link", { name: "Vote", exact: true })).toHaveCount(0);
});

// As an election operator, I can use mounted detail-route action components for credential resend, extend end date, conclude, tally, candidate paging, and voter search/modals.
test("elections-detail-operator-actions loads credential resend controls and voter search/modals from the detail route", async ({ page }) => {
  const manager = resetState.actors.manager;

  await loginViaForm(page, manager.username, manager.password);
  await page.goto(resetState.scenarios["elections-detail-operator-actions"].route_target);

  await expect(page.locator("[data-election-detail-vue-root]")).toBeVisible();
  await expect(page.locator("[data-election-extend-action-root]")).toBeVisible();
  await expect(page.locator("[data-election-conclude-action-vue-root]").getByRole("button", { name: "Conclude Election", exact: true })).toBeVisible();
  await expect(page.locator("[data-election-tally-action-vue-root]")).toHaveCount(0);

  const voterGrid = page.locator("[data-election-eligible-voters-vue-root]");
  await expect(voterGrid).toBeVisible();
  await expect(voterGrid.getByText("Eligible voters", { exact: true })).toBeVisible();
  await expect(voterGrid.getByText("Ineligible voters", { exact: true })).toBeVisible();
  const collapseToggles = voterGrid.getByTitle("Expand or collapse this section");
  await expect(collapseToggles).toHaveCount(2);
  const voterCards = voterGrid.locator(".election-voter-card");

  const eligibleCard = voterCards.nth(0);
  await collapseToggles.nth(0).click();
  await expect(eligibleCard.getByRole("button", { name: "Resend all credentials", exact: true })).toBeVisible();
  await expect(eligibleCard.getByRole("button", { name: "Resend voting credential", exact: true })).toBeVisible();
  await expect(page.locator("#eligible-voter-usernames option")).toHaveCount(3);
  await expect(eligibleCard.locator(`a[href="/user/${manager.username}/"]`).first()).toBeVisible();

  const ineligibleCard = voterCards.nth(1);
  await collapseToggles.nth(1).click();
  await expect(ineligibleCard).toContainText("No ineligible voters found.");
});

// As a viewer or operator, I can open the turnout report page separately from election detail.
test("elections-turnout-report-shell renders the standalone turnout report table and chart", async ({ page }) => {
  const manager = resetState.actors.manager;

  await loginViaForm(page, manager.username, manager.password);

  const turnoutResponse = page.waitForResponse((response) => {
    return response.url().includes("/api/v1/elections/reports/turnout") && response.request().method() === "GET";
  });

  await page.goto(resetState.routes.turnout_report);
  await expect(page.locator("[data-elections-turnout-report-vue-root]")).toBeVisible();
  await turnoutResponse;

  await expect(page.getByText("Cross-election turnout comparison", { exact: true })).toBeVisible();
  await expect(page.getByText("Turnout trend by election", { exact: true })).toBeVisible();
  await expect(page.getByText("Turnout % (count)", { exact: true })).toBeVisible();
  await expect(page.locator("canvas")).toBeVisible();
  await expect(page.locator(`a[href="${resetState.routes.tallied_detail}"]`)).toBeVisible();
  await expect(page.locator(`a[href="${resetState.elections.past_list_election.route}"]`)).toBeVisible();
});

// As a viewer of a finished election, I can reach audit log and public ballots/audit artifact links.
test("elections-audit-log-finished-shell renders jump links and the tally Sankey chart on the audit route", async ({ page }) => {
  const manager = resetState.actors.manager;
  const talliedElectionId = resetState.elections.detail_tallied_election.id;

  await loginViaForm(page, manager.username, manager.password);

  const auditResponse = page.waitForResponse((response) => {
    return response.url().includes(`/api/v1/elections/${talliedElectionId}/audit-log`) && response.request().method() === "GET";
  });
  const summaryResponse = page.waitForResponse((response) => {
    return response.url().includes(`/api/v1/elections/${talliedElectionId}/audit-summary`) && response.request().method() === "GET";
  });

  await page.goto(resetState.routes.audit_tallied);
  await expect(page.locator("[data-election-audit-log-vue-root]")).toBeVisible();
  await auditResponse;
  await summaryResponse;

  await expect(page.getByRole("group", { name: "Jump to", exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: "Back to election page", exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: "Election algorithm", exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: "Download ballots (JSON)", exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: "Download audit log (JSON)", exact: true })).toBeVisible();
  await expect(page.locator("#tally-sankey-chart")).toBeVisible();
  await expect(page.getByRole("link", { name: "Back to top", exact: true })).toBeVisible();
});

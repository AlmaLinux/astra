import { expect, test, type Page } from "@playwright/test";

import { readElectionsResetState } from "./resetState";

const resetState = readElectionsResetState();

async function loginViaForm(page: Page, username: string, password: string): Promise<void> {
  await page.goto("/login/");
  await page.getByLabel("Username", { exact: true }).fill(username);
  await page.getByLabel("Password", { exact: true }).fill(password);
  await page.getByRole("button", { name: /log in/i }).click();
}

// As a user, I can browse open and past elections.
test("elections-list-viewer-shell shows the seeded open and past sections without manager-only draft rows", async ({ page }) => {
  const viewer = resetState.actors.viewer;

  await loginViaForm(page, viewer.username, viewer.password);

  const electionsResponse = page.waitForResponse((response) => {
    return response.url().includes("/api/v1/elections") && response.request().method() === "GET";
  });

  await page.goto(resetState.scenarios["elections-list-viewer-shell"].route_target);
  await expect(page.locator("[data-elections-vue-root]")).toBeVisible();
  await electionsResponse;

  await expect(page.getByText("Open elections", { exact: true })).toBeVisible();
  await expect(page.getByText("Past elections", { exact: true })).toBeVisible();
  await expect(page.locator(`a[href="${resetState.elections.open_list_election.route}"]`)).toBeVisible();
  await expect(page.locator(`a[href="${resetState.elections.past_list_election.route}"]`)).toBeVisible();
  await expect(page.locator(`a[href="${resetState.elections.draft_manager_election.route}"]`)).toHaveCount(0);
  await expect(page.locator("table")).toHaveCount(0);
  await expect(page.locator('[title="Show or hide past elections"]')).toBeVisible();
});

// As a user, I can browse open and past elections.
test("elections-list-manager-draft-routing routes draft rows to edit while keeping published rows on detail", async ({ page }) => {
  const manager = resetState.actors.manager;

  await loginViaForm(page, manager.username, manager.password);
  await page.goto(resetState.scenarios["elections-list-manager-draft-routing"].route_target);
  await expect(page.locator("[data-elections-vue-root]")).toBeVisible();

  await expect(page.locator(`a[href="${resetState.elections.draft_manager_election.route}"]`)).toBeVisible();
  await expect(page.locator(`a[href="${resetState.elections.manager_open_election.route}"]`)).toBeVisible();
});

// As a user, I can browse open and past elections.
test("elections-detail-open-summary loads the canonical summary and candidates endpoints for the seeded open election", async ({ page }) => {
  const viewer = resetState.actors.viewer;
  const electionId = resetState.elections.detail_open_election.id;

  await loginViaForm(page, viewer.username, viewer.password);

  const detailResponse = page.waitForResponse((response) => {
    return response.url().includes(`/api/v1/elections/${electionId}/detail`) && response.request().method() === "GET";
  });
  const candidatesResponse = page.waitForResponse((response) => {
    return response.url().includes(`/api/v1/elections/${electionId}/candidates`) && response.request().method() === "GET";
  });

  await page.goto(resetState.scenarios["elections-detail-open-summary"].route_target);
  await expect(page.locator("[data-election-detail-vue-root]")).toBeVisible();
  await detailResponse;
  await candidatesResponse;

  await expect(page.getByText("Wave 6 Open Election", { exact: true })).toBeVisible();
  await expect(page.getByText("Status", { exact: true })).toBeVisible();
  await expect(page.getByText("Open", { exact: true })).toBeVisible();
  await expect(page.getByText("Candidates", { exact: true })).toBeVisible();
  await expect(page.getByText("alice", { exact: true })).toBeVisible();
  await expect(page.getByText("bob", { exact: true })).toBeVisible();
  await expect(page.getByText("Results", { exact: true })).toHaveCount(0);
});

// As a viewer of a finished election, I can reach audit log and public ballots/audit artifact links.
test("elections-detail-tallied-results renders winners, turnout history, and follow-through links without traversing them", async ({ page }) => {
  const manager = resetState.actors.manager;
  const electionId = resetState.elections.detail_tallied_election.id;

  await loginViaForm(page, manager.username, manager.password);

  const detailResponse = page.waitForResponse((response) => {
    return response.url().includes(`/api/v1/elections/${electionId}/detail`) && response.request().method() === "GET";
  });
  const candidatesResponse = page.waitForResponse((response) => {
    return response.url().includes(`/api/v1/elections/${electionId}/candidates`) && response.request().method() === "GET";
  });

  await page.goto(resetState.scenarios["elections-detail-tallied-results"].route_target);
  await expect(page.locator("[data-election-detail-vue-root]")).toBeVisible();
  await detailResponse;
  await candidatesResponse;

  await expect(page.getByText("Wave 6 Tallied Election", { exact: true })).toBeVisible();
  await expect(page.getByText("Results", { exact: true })).toBeVisible();
  await expect(page.locator(`a[href="/user/alice/"]`).first()).toBeVisible();
  await expect(page.getByText("Empty seats: 1", { exact: true })).toBeVisible();
  await expect(page.getByText("Ballots submitted over time (including superseded ballots)", { exact: true })).toBeVisible();
  await expect(page.locator("canvas")).toBeVisible();
  const actionCard = page.locator("[data-election-action-card-vue-root]");
  await expect(actionCard.getByRole("link", { name: "View audit log", exact: true })).toBeVisible();
  await expect(actionCard.getByRole("link", { name: "Download ballots (JSON)", exact: true })).toBeVisible();
  await expect(actionCard.getByRole("link", { name: "Download audit log (JSON)", exact: true })).toBeVisible();
});
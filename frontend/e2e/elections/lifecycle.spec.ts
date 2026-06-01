import { expect, test, type Page } from "@playwright/test";

import { readElectionsResetState } from "./resetState";

const resetState = readElectionsResetState();

async function loginViaForm(page: Page, username: string, password: string): Promise<void> {
  await page.goto("/login/");
  await page.getByLabel("Username", { exact: true }).fill(username);
  await page.getByLabel("Password", { exact: true }).fill(password);
  await page.getByRole("button", { name: /log in/i }).click();
}

async function selectUserFromSelect2(page: Page, fieldName: string, username: string): Promise<void> {
  const select = page.locator(`select[name="${fieldName}"]`);
  await expect(select).toBeAttached();
  const ajaxUrl = await select.getAttribute("data-ajax-url");
  if (!ajaxUrl) {
    throw new Error(`Select2 field ${fieldName} is missing data-ajax-url`);
  }
  const ajaxPath = new URL(ajaxUrl, "http://127.0.0.1").pathname;

  await select.locator("xpath=following-sibling::span[contains(@class, 'select2')]").click();

  const searchResponse = page.waitForResponse((response) => {
    const responseUrl = new URL(response.url());
    return responseUrl.pathname === ajaxPath
      && responseUrl.searchParams.get("q") === username
      && response.request().method() === "GET";
  });
  const searchInput = page.locator(".select2-container--open .select2-search__field");
  await searchInput.fill("");
  await searchInput.pressSequentially(username);
  const response = await searchResponse;
  if (!response.ok()) {
    throw new Error(`Select2 search for ${fieldName} returned ${response.status()}: ${await response.text()}`);
  }

  const result = page.locator(".select2-results__option").filter({ hasText: username }).first();
  await expect(result).toBeVisible();
  await result.click();

  await expect(select).toHaveValue(username);
}

test.describe.configure({ mode: "serial" });

// As an election operator, I can save a draft election before it is open.
// As an election operator, I can manage candidates and exclusion groups from the edit route.
// As an election operator, I can compose and preserve the credential email content tied to the election.
test("elections-edit-manage-candidates-exclusion-groups-and-email opens the draft editor affordances and saves a bounded draft change", async ({ page }) => {
  const manager = resetState.actors.manager;

  await loginViaForm(page, manager.username, manager.password);
  await page.goto(resetState.routes.edit_draft);

  await expect(page.getByRole("heading", { name: "Election edit", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Add candidate", exact: true }).click();
  await selectUserFromSelect2(page, "candidates-0-freeipa_username", "regular18");
  await selectUserFromSelect2(page, "candidates-0-nominated_by", "regular19");
  await page.locator('textarea[name="candidates-0-description"]').fill("Lifecycle candidate.");

  await page.getByRole("button", { name: /Configure exclusion groups/i }).click();
  await page.getByRole("button", { name: "Add exclusion group", exact: true }).click();
  await page.locator('input[name="groups-0-name"]').fill("Lifecycle Group");
  await page.locator('input[name="groups-0-max_elected"]').fill("1");
  await page.locator('select[name="groups-0-candidate_usernames"]').selectOption("regular18");

  const subjectField = page.getByRole("textbox", { name: "Subject:", exact: true });
  await subjectField.fill("Wave 7 lifecycle credential email");
  await page.getByRole("button", { name: "Save Draft", exact: true }).click();

  await expect(subjectField).toHaveValue("Wave 7 lifecycle credential email");
  await expect(page.locator('select[name="candidates-0-freeipa_username"]')).toHaveValue("regular18");
  await expect(page.locator('input[name="groups-0-name"]')).toHaveValue("Lifecycle Group");
});

// As an election operator, I can start a draft election through an explicit confirmation modal.
test("elections-edit-draft-save-and-start opens the draft start confirmation modal without mutating the shared fixture", async ({ page }) => {
  const manager = resetState.actors.manager;

  await loginViaForm(page, manager.username, manager.password);
  await page.goto(resetState.routes.edit_draft);

  await page.getByRole("button", { name: "Start Election", exact: true }).click();
  await expect(page.locator("#start-election-modal")).toBeVisible();
  await expect(page.locator("#start-election-modal")).toContainText("This will open the election and email voting credentials to all eligible voters.");
  await expect(page.locator("#start-election-modal")).toContainText("Eligible voters:");
  await page.locator("#start-election-modal").getByRole("button", { name: "Cancel", exact: true }).click();
  await expect(page.locator("#start-election-modal")).not.toBeVisible();
});

// As an election operator, I can close an open election with or without immediate tally.
test("elections-close-and-tally-modal exercises the typed-confirm conclude and tally modals without submitting them", async ({ page }) => {
  const manager = resetState.actors.manager;

  await loginViaForm(page, manager.username, manager.password);
  await page.goto(resetState.routes.open_detail);

  await page.locator("[data-election-conclude-action-vue-root]").getByRole("button", { name: "Conclude Election", exact: true }).click();
  await expect(page.locator("#conclude-submit")).toBeDisabled();
  await page.getByText("Close election, but do not tally votes", { exact: true }).click();
  await expect(page.locator("#conclude-skip-tally")).toBeChecked();
  await page.locator("#conclude-confirm").fill(resetState.elections.detail_open_election.name);
  await expect(page.locator("#conclude-submit")).toBeEnabled();
  await page.locator("[data-election-conclude-action-vue-root]").getByRole("button", { name: "Cancel", exact: true }).click();

  await page.goto(resetState.elections.past_list_election.route);
  await page.locator("[data-election-tally-action-vue-root]").getByRole("button", { name: "Tally Election", exact: true }).click();
  await expect(page.locator("#tally-submit")).toBeDisabled();
  await page.locator("#tally-confirm").fill(resetState.elections.past_list_election.name);
  await expect(page.locator("#tally-submit")).toBeEnabled();
  await page.locator("[data-election-tally-action-vue-root]").getByRole("button", { name: "Cancel", exact: true }).click();
});

// As a voter, auditor, or operator, I can open the election algorithm page and follow the verification resources it publishes.
test("elections-algorithm-shell opens the standalone algorithm page and verification-resource links", async ({ page }) => {
  const manager = resetState.actors.manager;

  await loginViaForm(page, manager.username, manager.password);
  await page.goto(resetState.routes.algorithm);
  await expect(page.locator("[data-election-algorithm-root]")).toBeVisible();
  await expect(page.getByText("Meek STV (High-Precision Variant)", { exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: "verify-ballot-hash.py", exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: "verify-ballot-chain.py", exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: "verify-audit-log.py", exact: true })).toBeVisible();
});

// As a voter, I can open the vote page, enter my credential, rank candidates, inspect vote-weight tooltip details, submit a ballot, and copy the ballot receipt.
test("elections-vote-ranking-submit-and-copy-receipt submits a ranked ballot and copies the resulting receipt", async ({ page }) => {
  const manager = resetState.actors.manager;
  const credential = resetState.credentials.open_manager_credential.public_id;
  const electionId = resetState.elections.detail_open_election.id;

  await loginViaForm(page, manager.username, manager.password);

  const loadResponse = page.waitForResponse((response) => {
    return response.url().includes(`/api/v1/elections/${electionId}/vote`) && response.request().method() === "GET";
  });

  await page.goto(`${resetState.routes.open_vote}#credential=${credential}`);
  await expect(page.locator("[data-election-vote-vue-root]")).toBeVisible();
  await loadResponse;

  await expect(page.getByLabel("Voting credential", { exact: true })).toHaveValue(credential);
  await expect.poll(async () => page.evaluate(() => window.location.hash)).toBe("");

  const addButtons = page.getByRole("button", { name: "Add to ranking", exact: true });
  await addButtons.nth(0).click();
  await addButtons.nth(1).click();
  await page.locator("#election-ranking-list").getByRole("button", { name: "↑", exact: true }).last().click();

  const submitResponse = page.waitForResponse((response) => {
    return response.url().includes(`/api/v1/elections/${electionId}/vote/submit`) && response.request().method() === "POST";
  });
  await page.getByRole("button", { name: "Submit vote", exact: true }).click();
  await submitResponse;

  const receiptInput = page.locator("#election-receipt");
  await expect(page.locator("#election-vote-result")).toContainText("Your vote was recorded.");
  await expect(receiptInput).toBeVisible();
  await expect(receiptInput).toHaveValue(/[a-f0-9]{64}/);

  const receiptValue = await receiptInput.inputValue();
  await page.getByRole("button", { name: "Copy", exact: true }).click();
  await expect(page.locator("#election-vote-result")).toContainText("Ballot receipt code copied to clipboard.");
  await expect(page.locator("#election-receipt-verify")).toHaveAttribute("href", `${resetState.routes.ballot_verify}?receipt=${receiptValue}`);
});
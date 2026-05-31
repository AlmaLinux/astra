import { expect, test } from "@playwright/test";

import { readElectionsResetState } from "./resetState";

const resetState = readElectionsResetState();

// As a user verifying a ballot receipt, I can use the ballot-verification route without leaving the browser shell blind to throttling or tally state.
test("elections-ballot-verify-closed-public-state shows the recorded-and-locked branch for the seeded closed receipt", async ({ page }) => {
  const electionId = resetState.elections.past_list_election.id;
  const receipt = resetState.receipts.verify_closed_receipt.ballot_hash;

  await page.goto(resetState.scenarios["elections-ballot-verify-closed-public-state"].route_target);
  await expect(page.locator("[data-ballot-verify-vue-root]")).toBeVisible();

  const closedResponse = page.waitForResponse((response) => {
    return response.url().includes(`/api/v1/elections/ballot/verify?receipt=${receipt}`) && response.request().method() === "GET";
  });
  await page.getByLabel("Ballot receipt code", { exact: true }).fill(receipt);
  await page.getByRole("button", { name: "Verify", exact: true }).click();
  await closedResponse;

  await expect(page.getByText("Yes — a ballot with this receipt code is recorded for this election.", { exact: true })).toBeVisible();
  await expect(page.getByText("This ballot is recorded and locked and will be included in the upcoming tally.", { exact: true })).toBeVisible();
  await expect(page.locator(`a[href="/elections/${electionId}/public/ballots.json"]`)).toBeVisible();
  await expect(page.locator(`a[href="/elections/${electionId}/audit/"]`)).toBeVisible();
});

// As a user verifying a ballot receipt, I can use the ballot-verification route without leaving the browser shell blind to throttling or tally state.
test("elections-ballot-verify-tallied-public-states distinguishes invalid, missing, tallied, and superseded receipts", async ({ page }) => {
  const electionId = resetState.elections.detail_tallied_election.id;
  const talliedReceipt = resetState.receipts.verify_tallied_receipt.ballot_hash;
  const supersededReceipt = resetState.receipts.verify_superseded_receipt.ballot_hash;
  const missingReceipt = "a".repeat(64);

  await page.goto(resetState.scenarios["elections-ballot-verify-tallied-public-states"].route_target);
  await expect(page.locator("[data-ballot-verify-vue-root]")).toBeVisible();

  await page.getByLabel("Ballot receipt code", { exact: true }).fill("not-a-hash");
  await page.getByRole("button", { name: "Verify", exact: true }).click();
  await expect(page.getByText("Invalid ballot receipt code.", { exact: false })).toBeVisible();

  const missingResponse = page.waitForResponse((response) => {
    return response.url().includes(`/api/v1/elections/ballot/verify?receipt=${missingReceipt}`) && response.request().method() === "GET";
  });
  await page.getByLabel("Ballot receipt code", { exact: true }).fill(missingReceipt);
  await page.getByRole("button", { name: "Verify", exact: true }).click();
  await missingResponse;
  await expect(page.getByText("No ballot with this receipt code was found.", { exact: true })).toBeVisible();

  const talliedResponse = page.waitForResponse((response) => {
    return response.url().includes(`/api/v1/elections/ballot/verify?receipt=${talliedReceipt}`) && response.request().method() === "GET";
  });
  await page.getByLabel("Ballot receipt code", { exact: true }).fill(talliedReceipt);
  await page.getByRole("button", { name: "Verify", exact: true }).click();
  await talliedResponse;
  await expect(page.getByText("This ballot was included in the final tally.", { exact: true })).toBeVisible();
  const verificationCard = page.locator("[data-ballot-verify-vue-root]");
  await expect(verificationCard.getByRole("link", { name: "Public ballots ledger (JSON)", exact: true })).toBeVisible();
  await expect(verificationCard.getByRole("link", { name: "Audit log", exact: true })).toBeVisible();

  const supersededResponse = page.waitForResponse((response) => {
    return response.url().includes(`/api/v1/elections/ballot/verify?receipt=${supersededReceipt}`) && response.request().method() === "GET";
  });
  await page.getByLabel("Ballot receipt code", { exact: true }).fill(supersededReceipt);
  await page.getByRole("button", { name: "Verify", exact: true }).click();
  await supersededResponse;
  await expect(page.getByText("This ballot was replaced by a later submission from the same voter and was not included in the final tally.", { exact: true })).toBeVisible();
  await expect(page.locator("[data-ballot-verify-vue-root]")).not.toContainText(talliedReceipt);
});
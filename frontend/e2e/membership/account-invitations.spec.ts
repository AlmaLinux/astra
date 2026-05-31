import { expect, test, type Locator, type Page, type Response } from "@playwright/test";

type InvitationRow = {
  invitation_id: number;
  email: string;
  full_name: string;
  note: string;
  organization_id?: number;
  organization_name?: string;
  invited_by_username: string;
  invited_at: string | null;
  send_count: number;
  last_sent_at: string | null;
  status: "pending" | "accepted" | "dismissed";
  accepted_at?: string;
  accepted_username?: string;
  freeipa_matched_usernames?: string[];
};

type InvitationPayload = {
  data: InvitationRow[];
};

type InvitationTables = {
  pendingRows: InvitationRow[];
  acceptedRows: InvitationRow[];
};

const invitationEmails = {
  pendingShellObserver: "wave3+pending-shell-observer@membership-invitations.invalid",
  acceptedShellObserver: "regular02@example.test",
  pendingRefreshAcceptance: "regular06@example.test",
  pendingRowResend: "wave3+pending-row-resend@membership-invitations.invalid",
  pendingRowDismiss: "wave3+pending-row-dismiss@membership-invitations.invalid",
  pendingBulkResendPrimary: "wave3+pending-bulk-resend-primary@membership-invitations.invalid",
  pendingBulkResendSecondary: "wave3+pending-bulk-resend-secondary@membership-invitations.invalid",
  pendingBulkResendExtra: "wave3+pending-bulk-resend-extra@membership-invitations.invalid",
  pendingBulkDismissPrimary: "wave3+pending-bulk-dismiss-primary@membership-invitations.invalid",
  pendingBulkDismissSecondary: "wave3+pending-bulk-dismiss-secondary@membership-invitations.invalid",
  acceptedBulkDismissPrimary: "regular03@example.test",
  acceptedBulkDismissSecondary: "regular04@example.test",
  acceptedBulkDismissExtra: "regular05@example.test",
  acceptedSingleDismiss: "regular07@example.test",
  acceptedMultiMatchInspection: "regular06+multimatch@example.test",
} as const;

async function loginViaForm(page: Page, username: string, password: string): Promise<void> {
  await page.goto("/login/");
  await page.getByLabel("Username", { exact: true }).fill(username);
  await page.getByLabel("Password", { exact: true }).fill(password);
  await page.getByRole("button", { name: /log in/i }).click();
}

async function gotoAccountInvitations(page: Page): Promise<InvitationTables> {
  const pendingResponsePromise = page.waitForResponse((response) => {
    return response.url().includes("/api/v1/membership/invitations/pending/detail")
      && response.request().method() === "GET";
  });
  const acceptedResponsePromise = page.waitForResponse((response) => {
    return response.url().includes("/api/v1/membership/invitations/accepted/detail")
      && response.request().method() === "GET";
  });

  await page.goto("/membership/account-invitations/");
  await expect(page.locator("[data-account-invitations-root]")).toBeVisible();

  const [pendingResponse, acceptedResponse] = await Promise.all([pendingResponsePromise, acceptedResponsePromise]);
  const [pendingPayload, acceptedPayload] = await Promise.all([
    pendingResponse.json() as Promise<InvitationPayload>,
    acceptedResponse.json() as Promise<InvitationPayload>,
  ]);

  return {
    pendingRows: pendingPayload.data,
    acceptedRows: acceptedPayload.data,
  };
}

function pendingCard(page: Page): Locator {
  return page.locator(".card").filter({ hasText: /Pending:\s*\d+/ }).first();
}

function acceptedCard(page: Page): Locator {
  return page.locator(".card").filter({ hasText: /Accepted:\s*\d+/ }).first();
}

function rowByInvitationId(card: Locator, invitationId: number): Locator {
  return card.locator(`tbody tr:has(input[type="checkbox"][name="selected"][value="${invitationId}"])`);
}

function rowCheckboxByInvitationId(card: Locator, invitationId: number): Locator {
  return card.locator(`tbody input[type="checkbox"][name="selected"][value="${invitationId}"]`);
}

function findInvitationId(rows: InvitationRow[], email: string): number {
  const row = rows.find((candidate) => candidate.email === email);
  if (!row) {
    throw new Error(`Unable to find invitation id for ${email}.`);
  }
  return row.invitation_id;
}

async function latestInvitationRows(response: Response): Promise<InvitationRow[]> {
  const payload = (await response.json()) as InvitationPayload;
  return payload.data;
}

// As a membership operator, I can open the invitations screen and view accepted and pending sections with independent pagination and bulk-selection affordances.
test("invitations-list-shell renders both invitation sections with independent pagination controls", async ({ page }) => {
  await loginViaForm(page, "regular01", "password");
  const tables = await gotoAccountInvitations(page);

  const pending = pendingCard(page);
  const accepted = acceptedCard(page);
  const pendingObserverId = findInvitationId(tables.pendingRows, invitationEmails.pendingShellObserver);
  const acceptedObserverId = findInvitationId(tables.acceptedRows, invitationEmails.acceptedShellObserver);
  const pendingObserver = tables.pendingRows.find((row) => row.invitation_id === pendingObserverId);
  const acceptedObserver = tables.acceptedRows.find((row) => row.invitation_id === acceptedObserverId);
  const pendingNoOrgId = findInvitationId(tables.pendingRows, invitationEmails.pendingRefreshAcceptance);

  if (!pendingObserver?.organization_id || !acceptedObserver?.organization_id) {
    throw new Error("Expected organization-linked shell rows in invitation reset payload.");
  }

  await expect(page.getByRole("heading", { name: "Account Invitations", exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Waiting for account creation", exact: true })).toBeVisible();
  await expect(accepted.getByText(/Accepted:\s*\d+/)).toBeVisible();
  await expect(pending.getByText(/Pending:\s*\d+/)).toBeVisible();
  await expect(accepted.locator('input[aria-label="Select all accepted invitations"]')).toBeVisible();
  await expect(pending.locator('input[aria-label="Select all pending invitations"]')).toBeVisible();
  await expect(rowByInvitationId(accepted, acceptedObserverId)).toBeVisible();
  await expect(rowByInvitationId(pending, pendingObserverId)).toBeVisible();
  await expect(rowByInvitationId(accepted, acceptedObserverId).getByRole("link", { name: "regular02", exact: true })).toBeVisible();
  await expect(rowByInvitationId(pending, pendingObserverId).getByRole("link", { name: "Wave 7 Pending Invitation Org", exact: true })).toHaveAttribute(
    "href",
    `/organization/${pendingObserver.organization_id}/`,
  );
  await expect(rowByInvitationId(accepted, acceptedObserverId).getByRole("link", { name: "Wave 7 Accepted Invitation Org", exact: true })).toHaveAttribute(
    "href",
    `/organization/${acceptedObserver.organization_id}/`,
  );
  await expect(rowByInvitationId(pending, pendingNoOrgId).locator("td").nth(3).getByText("-", { exact: true })).toBeVisible();
  await expect(accepted.getByRole("link", { name: "2", exact: true })).toBeVisible();
  await expect(pending.getByRole("link", { name: "2", exact: true })).toBeVisible();
});

// As a membership operator, I can refresh invitation state from FreeIPA, resend or dismiss a single invitation, and use bulk resend or bulk dismiss actions where the current scope allows them.
test("invitations-pending-row-actions uses canonical resend and dismiss endpoints with refreshed row state", async ({ page }) => {
  await loginViaForm(page, "regular01", "password");
  const tables = await gotoAccountInvitations(page);

  const pending = pendingCard(page);
  const resendId = findInvitationId(tables.pendingRows, invitationEmails.pendingRowResend);
  const dismissId = findInvitationId(tables.pendingRows, invitationEmails.pendingRowDismiss);
  const resendRow = rowByInvitationId(pending, resendId);
  const dismissRow = rowByInvitationId(pending, dismissId);

  await expect(resendRow.locator("td").nth(8)).toHaveText("0");

  const resendRequest = page.waitForResponse((response) => {
    return response.url().includes(`/api/v1/membership/invitations/${resendId}/resend`)
      && response.request().method() === "POST";
  });
  const resendRefresh = page.waitForResponse((response) => {
    return response.url().includes("/api/v1/membership/invitations/pending/detail")
      && response.request().method() === "GET";
  });

  await resendRow.getByRole("button", { name: "Resend", exact: true }).click();

  await resendRequest;
  await latestInvitationRows(await resendRefresh);

  await expect(pending.getByText(`Invitation resent to ${invitationEmails.pendingRowResend}`)).toBeVisible();
  await expect(rowByInvitationId(pending, resendId).locator("td").nth(8)).toHaveText("1");
  await expect(rowByInvitationId(pending, resendId).locator("td").nth(7)).not.toHaveText("-");

  const dismissRequest = page.waitForResponse((response) => {
    return response.url().includes(`/api/v1/membership/invitations/${dismissId}/dismiss`)
      && response.request().method() === "POST";
  });
  const dismissRefresh = page.waitForResponse((response) => {
    return response.url().includes("/api/v1/membership/invitations/pending/detail")
      && response.request().method() === "GET";
  });

  await dismissRow.getByRole("button", { name: "Dismiss", exact: true }).click();

  await dismissRequest;
  await latestInvitationRows(await dismissRefresh);

  await expect(pending.getByText("Invitation dismissed")).toBeVisible();
  await expect(rowByInvitationId(pending, dismissId)).toHaveCount(0);
  await expect(rowByInvitationId(pending, resendId)).toBeVisible();
});

// As a membership operator, I can refresh invitation state from FreeIPA, resend or dismiss a single invitation, and use bulk resend or bulk dismiss actions where the current scope allows them.
test("invitations-pending-bulk-resend keeps selection table-scoped and refreshes only selected pending rows", async ({ page }) => {
  await loginViaForm(page, "regular01", "password");
  const tables = await gotoAccountInvitations(page);

  const pending = pendingCard(page);
  const accepted = acceptedCard(page);
  const primaryId = findInvitationId(tables.pendingRows, invitationEmails.pendingBulkResendPrimary);
  const secondaryId = findInvitationId(tables.pendingRows, invitationEmails.pendingBulkResendSecondary);
  const extraId = findInvitationId(tables.pendingRows, invitationEmails.pendingBulkResendExtra);
  const acceptedObserverId = findInvitationId(tables.acceptedRows, invitationEmails.acceptedShellObserver);

  const applyButton = pending.getByRole("button", { name: "Apply", exact: true });
  await expect(applyButton).toBeDisabled();
  await expect(accepted.getByText(/^Selected:/)).toHaveCount(0);

  await rowCheckboxByInvitationId(pending, primaryId).check();
  await rowCheckboxByInvitationId(pending, secondaryId).check();

  await expect(pending.getByText(/Selected:\s*2/)).toBeVisible();
  await expect(accepted.getByText(/^Selected:/)).toHaveCount(0);

  const bulkRequest = page.waitForResponse((response) => {
    return response.url().includes("/api/v1/membership/invitations/bulk")
      && response.request().method() === "POST";
  });
  const pendingRefresh = page.waitForResponse((response) => {
    return response.url().includes("/api/v1/membership/invitations/pending/detail")
      && response.request().method() === "GET";
  });

  await pending.locator('select[name="bulk_action"]').selectOption("resend");
  await applyButton.click();

  await bulkRequest;
  await latestInvitationRows(await pendingRefresh);

  await expect(pending.getByText("Resent 2 invitation(s)")).toBeVisible();
  await expect(rowByInvitationId(pending, primaryId).locator("td").nth(8)).toHaveText("1");
  await expect(rowByInvitationId(pending, secondaryId).locator("td").nth(8)).toHaveText("1");
  await expect(rowByInvitationId(pending, extraId).locator("td").nth(8)).toHaveText("0");
  await expect(rowByInvitationId(accepted, acceptedObserverId)).toBeVisible();
});

// As a membership operator, I can refresh invitation state from FreeIPA, resend or dismiss a single invitation, and use bulk resend or bulk dismiss actions where the current scope allows them.
test("invitations-accepted-bulk-dismiss keeps accepted selection isolated and preserves pending rows", async ({ page }) => {
  await loginViaForm(page, "regular01", "password");
  const tables = await gotoAccountInvitations(page);

  const pending = pendingCard(page);
  const accepted = acceptedCard(page);
  const primaryId = findInvitationId(tables.acceptedRows, invitationEmails.acceptedBulkDismissPrimary);
  const secondaryId = findInvitationId(tables.acceptedRows, invitationEmails.acceptedBulkDismissSecondary);
  const extraId = findInvitationId(tables.acceptedRows, invitationEmails.acceptedBulkDismissExtra);
  const pendingObserverId = findInvitationId(tables.pendingRows, invitationEmails.pendingShellObserver);

  const applyButton = accepted.getByRole("button", { name: "Apply", exact: true });
  await expect(applyButton).toBeDisabled();
  await expect(pending.getByText(/^Selected:/)).toHaveCount(0);

  await rowCheckboxByInvitationId(accepted, primaryId).check();
  await rowCheckboxByInvitationId(accepted, secondaryId).check();

  await expect(accepted.getByText(/Selected:\s*2/)).toBeVisible();
  await expect(pending.getByText(/^Selected:/)).toHaveCount(0);

  const bulkRequest = page.waitForResponse((response) => {
    return response.url().includes("/api/v1/membership/invitations/bulk")
      && response.request().method() === "POST";
  });
  const acceptedRefresh = page.waitForResponse((response) => {
    return response.url().includes("/api/v1/membership/invitations/accepted/detail")
      && response.request().method() === "GET";
  });

  await accepted.locator('select[name="bulk_action"]').selectOption("dismiss");
  await applyButton.click();

  await bulkRequest;
  await latestInvitationRows(await acceptedRefresh);

  await expect(accepted.getByText("Dismissed 2 invitation(s)")).toBeVisible();
  await expect(rowByInvitationId(accepted, primaryId)).toHaveCount(0);
  await expect(rowByInvitationId(accepted, secondaryId)).toHaveCount(0);
  await expect(rowByInvitationId(accepted, extraId)).toBeVisible();
  await expect(rowByInvitationId(pending, pendingObserverId)).toBeVisible();
});

// As a membership operator, I can refresh invitation state from FreeIPA, resend or dismiss a single invitation, and use bulk resend or bulk dismiss actions where the current scope allows them.
test("invitations-refresh-now moves a matched pending invitation into accepted after a real UI refresh", async ({ page }) => {
  await loginViaForm(page, "regular01", "password");
  const tables = await gotoAccountInvitations(page);

  const pending = pendingCard(page);
  const accepted = acceptedCard(page);
  const refreshInvitationId = findInvitationId(tables.pendingRows, invitationEmails.pendingRefreshAcceptance);

  await expect(rowByInvitationId(pending, refreshInvitationId)).toBeVisible();

  const refreshResponse = page.waitForResponse((response) => {
    return response.url().includes("/api/v1/membership/invitations/refresh")
      && response.request().method() === "POST";
  });
  const pendingRefresh = page.waitForResponse((response) => {
    return response.url().includes("/api/v1/membership/invitations/pending/detail")
      && response.request().method() === "GET";
  });
  const acceptedRefresh = page.waitForResponse((response) => {
    return response.url().includes("/api/v1/membership/invitations/accepted/detail")
      && response.request().method() === "GET";
  });

  await page.getByRole("button", { name: "Refresh now", exact: true }).click();
  await refreshResponse;
  await Promise.all([pendingRefresh, acceptedRefresh]);
  await expect(rowByInvitationId(pendingCard(page), refreshInvitationId)).toHaveCount(0);
  await expect(rowByInvitationId(acceptedCard(page), refreshInvitationId)).toBeVisible();
});

// As a membership operator, I can refresh invitation state from FreeIPA, resend or dismiss a single invitation, and use bulk resend or bulk dismiss actions where the current scope allows them.
test("invitations-pending-bulk-dismiss removes only the selected pending invitations", async ({ page }) => {
  await loginViaForm(page, "regular01", "password");
  const tables = await gotoAccountInvitations(page);

  const pending = pendingCard(page);
  const accepted = acceptedCard(page);
  const primaryId = findInvitationId(tables.pendingRows, invitationEmails.pendingBulkDismissPrimary);
  const secondaryId = findInvitationId(tables.pendingRows, invitationEmails.pendingBulkDismissSecondary);
  const observerId = findInvitationId(tables.pendingRows, invitationEmails.pendingShellObserver);
  const acceptedObserverId = findInvitationId(tables.acceptedRows, invitationEmails.acceptedShellObserver);

  await rowCheckboxByInvitationId(pending, primaryId).check();
  await rowCheckboxByInvitationId(pending, secondaryId).check();
  await expect(pending.getByText(/Selected:\s*2/)).toBeVisible();

  const bulkRequest = page.waitForResponse((response) => {
    return response.url().includes("/api/v1/membership/invitations/bulk")
      && response.request().method() === "POST";
  });
  const pendingRefresh = page.waitForResponse((response) => {
    return response.url().includes("/api/v1/membership/invitations/pending/detail")
      && response.request().method() === "GET";
  });

  await pending.locator('select[name="bulk_action"]').selectOption("dismiss");
  await pending.getByRole("button", { name: "Apply", exact: true }).click();

  await bulkRequest;
  await latestInvitationRows(await pendingRefresh);

  await expect(pending.getByText("Dismissed 2 invitation(s)")).toBeVisible();
  await expect(rowByInvitationId(pending, primaryId)).toHaveCount(0);
  await expect(rowByInvitationId(pending, secondaryId)).toHaveCount(0);
  await expect(rowByInvitationId(pending, observerId)).toBeVisible();
  await expect(rowByInvitationId(accepted, acceptedObserverId)).toBeVisible();
});

// As a membership operator, I can refresh invitation state from FreeIPA, resend or dismiss a single invitation, and use bulk resend or bulk dismiss actions where the current scope allows them.
test("invitations-accepted-single-dismiss removes one accepted row without affecting pending rows", async ({ page }) => {
  await loginViaForm(page, "regular01", "password");
  const tables = await gotoAccountInvitations(page);

  const pending = pendingCard(page);
  const accepted = acceptedCard(page);
  const dismissId = findInvitationId(tables.acceptedRows, invitationEmails.acceptedSingleDismiss);
  const pendingObserverId = findInvitationId(tables.pendingRows, invitationEmails.pendingShellObserver);

  const dismissRequest = page.waitForResponse((response) => {
    return response.url().includes(`/api/v1/membership/invitations/${dismissId}/dismiss`)
      && response.request().method() === "POST";
  });
  const acceptedRefresh = page.waitForResponse((response) => {
    return response.url().includes("/api/v1/membership/invitations/accepted/detail")
      && response.request().method() === "GET";
  });

  await rowByInvitationId(accepted, dismissId).getByRole("button", { name: "Dismiss", exact: true }).click();

  await dismissRequest;
  await latestInvitationRows(await acceptedRefresh);

  await expect(accepted.getByText("Invitation dismissed")).toBeVisible();
  await expect(rowByInvitationId(accepted, dismissId)).toHaveCount(0);
  await expect(rowByInvitationId(pending, pendingObserverId)).toBeVisible();
});

// As a membership operator, I can inspect accepted invitations including matched FreeIPA usernames, accepted usernames, and multi-match details before deciding whether follow-up is needed.
test("invitations-accepted-inspection shows accepted username and multi-match details", async ({ page }) => {
  await loginViaForm(page, "regular01", "password");
  const tables = await gotoAccountInvitations(page);

  const accepted = acceptedCard(page);
  const inspectionId = findInvitationId(tables.acceptedRows, invitationEmails.acceptedMultiMatchInspection);
  const inspectionRow = rowByInvitationId(accepted, inspectionId);

  await expect(inspectionRow).toContainText("Accepted (multiple matches)");
  await expect(inspectionRow.getByRole("link", { name: "regular06", exact: true }).first()).toBeVisible();
  await expect(inspectionRow.getByRole("link", { name: "regular16", exact: true })).toBeVisible();
  await expect(inspectionRow).toContainText("as regular06");
});

// As a membership operator, I can upload a CSV of invitations and preview the parsed results before sending anything.
// As a membership operator, I can send invitations from preview only after reviewing the modal confirmation and template choice.
test("invitations-upload-preview-and-send uses the real CSV workflow and confirmation modal", async ({ page }) => {
  await loginViaForm(page, "regular01", "password");

  await page.goto("/membership/account-invitations/upload/");
  await expect(page.getByRole("heading", { name: "Upload Account Invitations", exact: true })).toBeVisible();

  const uniqueEmail = `wave7+upload-${Date.now()}@membership-invitations.invalid`;
  await page.locator('input[type="file"]').setInputFiles({
    name: "invitations.csv",
    mimeType: "text/csv",
    buffer: Buffer.from(`email,full_name,note\n${uniqueEmail},Wave Seven Operator,Uploaded from Playwright\n`, "utf-8"),
  });
  await page.getByRole("button", { name: "Preview invitations", exact: true }).click();

  await expect(page.getByRole("heading", { name: "Account Invitation Preview", exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: "Upload another CSV", exact: true })).toBeVisible();
  await expect(page.getByText(uniqueEmail, { exact: true })).toBeVisible();
  await expect(page.getByText("New:", { exact: false })).toBeVisible();
  await expect(page.getByText("Resend:", { exact: false })).toBeVisible();
  await expect(page.getByText("Existing:", { exact: false })).toBeVisible();
  await expect(page.getByText("Invalid:", { exact: false })).toBeVisible();
  await expect(page.getByText("Duplicates:", { exact: false })).toBeVisible();
  await expect(page.getByText("New invite", { exact: true })).toBeVisible();

  await page.getByRole("button", { name: "Send invitations", exact: true }).click();
  const modal = page.locator("#confirm-send-modal");
  await expect(modal).toBeVisible();
  await expect(modal.getByText("This will queue 1 emails.", { exact: false })).toBeVisible();
  await expect(modal.getByLabel("Email template", { exact: true })).toBeVisible();

  await modal.getByRole("button", { name: "Confirm and send", exact: true }).click();
  await expect(page).toHaveURL(/\/membership\/account-invitations\/$/);
  await expect(page.getByText("Queued 1 invitation(s).", { exact: true })).toBeVisible();
  await expect(page.getByText(uniqueEmail, { exact: true })).toBeVisible();
});
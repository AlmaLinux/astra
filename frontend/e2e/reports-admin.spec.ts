import { expect, test, type Page } from "@playwright/test";

async function loginViaForm(page: Page, username: string, password: string): Promise<void> {
  await page.goto("/login/");
  await page.getByLabel("Username", { exact: true }).fill(username);
  await page.getByLabel("Password", { exact: true }).fill(password);
  await page.getByRole("button", { name: /log in/i }).click();
}

async function logoutViaForm(page: Page): Promise<void> {
  await page.locator('form[action="/logout/"] button').click();
}

async function ensureMembershipManagementOpen(page: Page): Promise<void> {
  const auditLink = page.getByRole("link", { name: "Audit Log", exact: true });
  if (!(await auditLink.isVisible())) {
    await page.getByRole("link", { name: /Membership Management/ }).click();
  }
}

// As a membership viewer/operator, I can use the audit log route as a searchable report surface.
// As a membership viewer/operator, I can use the Sponsors report as both a lookup and export surface.
// As a membership viewer/operator, I can use the Statistics page filters and cards to inspect membership health.
test("reports-admin-audit-sponsors-stats", async ({ page }) => {
  await loginViaForm(page, "regular01", "password");
  await ensureMembershipManagementOpen(page);
  await page.getByRole("link", { name: "Audit Log", exact: true }).click();

  await expect(page).toHaveURL(/\/membership\/log\/?$/);
  await expect(page.locator("[data-membership-audit-log-root]")).toBeVisible();
  await expect(page.getByText("Loading membership audit log...")).toHaveCount(0);
  await expect(page.getByText("No audit log entries.")).toHaveCount(0);

  await page.getByLabel("Search membership audit log", { exact: true }).fill("regular0");
  await page.getByRole("button", { name: "Search", exact: true }).click();
  await expect(page).toHaveURL(/q=regular0/);
  await expect(page.getByText(/Request #\d+/)).toBeVisible();
  const responseSummary = page.getByText("Request responses").first();
  await expect(responseSummary).toBeVisible();

  await ensureMembershipManagementOpen(page);
  await page.getByRole("link", { name: "Sponsors", exact: true }).click();

  await expect(page).toHaveURL(/\/membership\/sponsors\/?$/);
  await expect(page.locator("[data-membership-sponsors-root]")).toBeVisible();
  await expect(page.getByRole("button", { name: "Copy", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "CSV", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Excel", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "PDF", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Print", exact: true })).toBeVisible();
  await page.getByLabel("Search sponsors", { exact: true }).fill("org");
  await page.getByRole("button", { name: "Search", exact: true }).click();
  await expect(page).toHaveURL(/q=org/i);
  await page.locator("[data-colvis-toggle]").click();
  await expect(page.getByLabel("Expires", { exact: true })).toBeVisible();

  await ensureMembershipManagementOpen(page);
  await page.getByRole("link", { name: "Statistics", exact: true }).click();

  await expect(page).toHaveURL(/\/membership\/stats\/?$/);
  await expect(page.locator("[data-membership-stats-root]")).toBeVisible();
  await expect(page.getByRole("button", { name: "30 days", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "90 days", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "180 days", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "365 days", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "All time", exact: true })).toBeVisible();
  await expect(page.getByText("Total FreeIPA Users", { exact: true })).toBeVisible();
  await expect(page.getByText("Pending Requests", { exact: true })).toBeVisible();
  await expect(page.locator('[data-chart-loading="membership-types"]')).toHaveCount(0);
  await page.getByRole("button", { name: "30 days", exact: true }).click();
  await expect(page.getByRole("button", { name: "30 days", exact: true })).toHaveAttribute("aria-pressed", "true");
});

// As an admin on the account-deletion request change form, I can approve/delete or reject a request through change-form object tools.
test("reports-admin-account-deletion-object-tools", async ({ page }) => {
  await loginViaForm(page, "regular08", "password");
  await page.goto("/settings/?tab=privacy");

  await expect(page).toHaveURL(/\/settings\/\?tab=privacy/);
  await expect(page.getByRole("heading", { name: "Privacy", exact: true })).toBeVisible();
  const deletionCard = page.locator(".card.border-danger").filter({ hasText: "Delete my account" }).first();
  await deletionCard.locator("#id_reason_category").selectOption("privacy");
  await deletionCard.locator("#id_acknowledge_retained_data").check();
  await deletionCard.locator('input[name="current_password"]').fill("password");
  await deletionCard.getByRole("button", { name: "Submit deletion request", exact: true }).click();

  await expect(page.getByText("Your current deletion request status is")).toBeVisible();
  await expect(page.getByText("Pending review", { exact: true })).toBeVisible();

  await logoutViaForm(page);
  await loginViaForm(page, "admin", "admin-password");
  await page.goto("/admin/core/accountdeletionrequest/");

  const deletionRow = page.locator("#result_list tbody tr").filter({ hasText: "regular08" }).first();
  await expect(deletionRow).toBeVisible();
  await deletionRow.getByRole("link").first().click();

  await expect(page.getByRole("button", { name: "Approve and delete", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Reject request", exact: true })).toBeVisible();

  await page.getByRole("button", { name: "Approve and delete", exact: true }).click();
  const approveModal = page.locator("#account-deletion-approve-modal");
  await expect(approveModal).toBeVisible();
  await expect(approveModal.getByText("Approve this request and delete the FreeIPA account?")).toBeVisible();
  await approveModal.getByRole("button", { name: "Cancel", exact: true }).click();

  await page.getByRole("button", { name: "Reject request", exact: true }).click();
  const rejectModal = page.locator("#account-deletion-reject-modal");
  await expect(rejectModal).toBeVisible();
  await expect(rejectModal.getByText("Reject this account deletion request?")).toBeVisible();
  await rejectModal.getByRole("button", { name: "Cancel", exact: true }).click();
});

// As staff/superuser, I can open the Django admin from the global sidebar.
// As an admin on the IPA user change form, I can use templated object-tool buttons to queue a password reset email or disable all OTP tokens for that user.
// As an admin running the membership CSV importer, I can upload a CSV, preview the mapping results, inspect match stats, and verify the preview exposes the confirm-import affordance.
// As an admin running the organization CSV importer, I can upload a CSV, preview organizations to import, and inspect the representative selectors before any import submission.
// As an admin running the organization-membership CSV importer, I can preview skipped organization-membership rows before any import submission.
test("reports-admin-django-admin-and-imports", async ({ page }) => {
  await loginViaForm(page, "admin", "admin-password");
  await page.getByRole("link", { name: "Admin", exact: true }).click();

  await expect(page).toHaveURL(/\/admin\/?$/);
  await expect(page.getByRole("heading", { name: /site administration/i })).toBeVisible();

  await page.goto("/admin/auth/ipauser/regular01/change/");
  await expect(page.getByRole("button", { name: "Reset user's password", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Reset user's password", exact: true }).click();
  await expect(page.locator("#admin-password-reset-modal")).toBeVisible();
  if (await page.getByRole("button", { name: "Disable user's OTP tokens", exact: true }).isVisible()) {
    await page.getByRole("button", { name: "Disable user's OTP tokens", exact: true }).click();
    await expect(page.locator("#admin-disable-otp-modal")).toBeVisible();
    await page.locator("#admin-disable-otp-modal").getByRole("button", { name: "Cancel", exact: true }).click();
  }
  await page.locator("#admin-password-reset-modal").getByRole("button", { name: "Cancel", exact: true }).click();

  await page.goto("/admin/core/membershipcsvimportlink/import/");
  await page.selectOption("#id_membership_type", "individual");
  await page.locator("#id_import_file").setInputFiles({
    name: "membership-import.csv",
    mimeType: "text/csv",
    buffer: Buffer.from(
      "Name,Email,Active Member,Membership Start Date,Membership Type\nWave Seven,regular11@example.test,Active Member,2024-01-02,individual\n",
      "utf-8",
    ),
  });
  await expect(page.locator("#csv-header-preview")).toBeVisible();
  await expect(page.locator("#csv-header-preview")).toContainText("Email");
  await page.getByRole("button", { name: "Upload and Preview", exact: true }).click();
  await expect(page.getByText(/Confirm import/i)).toBeVisible();

  await page.goto("/admin/core/organizationcsvimportlink/import/");
  await page.locator("#id_import_file").setInputFiles({
    name: "organizations.csv",
    mimeType: "text/csv",
    buffer: Buffer.from(
      "name,country_code,business_contact_name,business_contact_email,pr_marketing_contact_name,pr_marketing_contact_email,technical_contact_name,technical_contact_email,website\nWave 7 Browser Org,US,Biz Person,biz@example.org,PR Person,pr@example.org,Tech Person,tech@example.org,https://example.org\n",
      "utf-8",
    ),
  });
  await expect(page.locator("#csv-header-preview")).toBeVisible();
  await page.getByRole("button", { name: "Upload and Preview", exact: true }).click();
  await expect(page.getByText("Organizations to Import", { exact: true })).toBeVisible();
  await expect(page.getByRole("combobox").filter({ has: page.locator('option[value=""]') }).first()).toBeVisible();

  await page.goto("/admin/core/organizationmembershipcsvimportlink/import/");
  await page.selectOption("#id_membership_type", { index: 1 });
  await page.locator("#id_import_file").setInputFiles({
    name: "organization-memberships.csv",
    mimeType: "text/csv",
    buffer: Buffer.from(
      "organization_id,organization_name,membership_start_date,committee_notes\n999999,Missing Org,2024-01-02,Missing row\n",
      "utf-8",
    ),
  });
  await expect(page.locator("#csv-header-preview")).toBeVisible();
  await page.getByRole("button", { name: "Upload and Preview", exact: true }).click();
  await expect(page.getByText("Organizations to Import", { exact: true })).toBeVisible();
  await expect(page.getByText("Skipped", { exact: true })).toBeVisible();
});
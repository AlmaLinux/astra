import { expect, test, type Locator, type Page } from "@playwright/test";

type MembershipRequestRow = {
  request_id: number;
  membership_type: {
    code: string;
  };
  target: {
    label: string;
    secondary_label: string;
    username?: string;
  };
};

type QueuePayload = {
  data: MembershipRequestRow[];
};

type CommitteeQueueData = {
  pendingRows: MembershipRequestRow[];
  onHoldRows: MembershipRequestRow[];
};

async function loginViaForm(page: Page, username: string, password: string): Promise<void> {
  await page.goto("/login/");
  await page.getByLabel("Username", { exact: true }).fill(username);
  await page.getByLabel("Password", { exact: true }).fill(password);
  await page.getByRole("button", { name: /log in/i }).click();
  await page.waitForURL((url) => !url.pathname.endsWith("/login/"));
  await expect(page.getByRole("link", { name: username, exact: true })).toBeVisible();
}

async function gotoCommitteeQueue(page: Page, suffix: string = ""): Promise<CommitteeQueueData> {
  const pendingResponsePromise = page.waitForResponse((response) => {
    return response.url().includes("/api/v1/membership/requests/pending")
      && response.request().method() === "GET";
  });
  const onHoldResponsePromise = page.waitForResponse((response) => {
    return response.url().includes("/api/v1/membership/requests/on-hold")
      && response.request().method() === "GET";
  });

  await page.goto(`/membership/requests/${suffix}`);
  await expect(page.locator("[data-membership-requests-root]")).toBeVisible();

  const [pendingResponse, onHoldResponse] = await Promise.all([pendingResponsePromise, onHoldResponsePromise]);
  const [pendingPayload, onHoldPayload] = await Promise.all([
    pendingResponse.json() as Promise<QueuePayload>,
    onHoldResponse.json() as Promise<QueuePayload>,
  ]);

  return {
    pendingRows: pendingPayload.data,
    onHoldRows: onHoldPayload.data,
  };
}

function pendingCard(page: Page): Locator {
  return page.locator(".card").filter({ hasText: /Pending:\s*\d+/ }).first();
}

function onHoldCard(page: Page): Locator {
  return page.locator(".card").filter({ hasText: /On hold:\s*\d+/ }).first();
}

function rowCheckboxByRequestId(card: Locator, requestId: number): Locator {
  return card.locator(`tbody input[type="checkbox"][name="selected"][value="${requestId}"]`);
}

function rowByRequestId(card: Locator, requestId: number): Locator {
  return card.locator(`tbody tr:has(input[type="checkbox"][name="selected"][value="${requestId}"])`);
}

function requestIdLink(card: Locator, requestId: number): Locator {
  return card.getByRole("link", { name: `Request #${requestId}` });
}

function rowTargetIdentifiers(row: MembershipRequestRow): string[] {
  return [row.target.username, row.target.secondary_label, row.target.label]
    .filter((value): value is string => Boolean(value))
    .map((value) => value.toLowerCase());
}

function findRequestId(rows: MembershipRequestRow[], username: string, membershipTypeCode: string): number {
  const normalizedUsername = username.toLowerCase();
  const row = rows.find((candidate) => {
    return candidate.membership_type.code === membershipTypeCode
      && rowTargetIdentifiers(candidate).includes(normalizedUsername);
  });
  if (!row) {
    throw new Error(`Unable to find request id for ${username}/${membershipTypeCode}.`);
  }
  return row.request_id;
}

async function expectCheckedCount(card: Locator, checkboxClass: string, count: number): Promise<void> {
  await expect(card.locator(`tbody input.${checkboxClass}:checked`)).toHaveCount(count);
}

// As a committee operator, I can view separate pending and on-hold queues.
test("committee-queue-shell renders the queue shell with table-scoped selection and action controls", async ({ page }) => {
  await loginViaForm(page, "regular01", "password");
  const queue = await gotoCommitteeQueue(page);

  const pending = pendingCard(page);
  const onHold = onHoldCard(page);
  const pendingShellObserverId = findRequestId(queue.pendingRows, "regular02", "mirror");
  const onHoldShellObserverId = findRequestId(queue.onHoldRows, "regular03", "mirror");

  await expect(page.getByRole("heading", { name: /membership requests/i })).toBeVisible();
  await expect(page.getByRole("heading", { name: /waiting for requester response/i })).toBeVisible();
  await expect(pending.getByText(/Pending:\s*\d+/)).toBeVisible();
  await expect(onHold.getByText(/On hold:\s*\d+/)).toBeVisible();
  await expect(pending.locator('input[aria-label="Select all requests"]')).toBeVisible();
  await expect(onHold.locator('input[aria-label="Select all requests"]')).toBeVisible();
  await expect(pending.locator('tbody input.request-checkbox--pending[type="checkbox"]').first()).toBeVisible();
  await expect(onHold.locator('tbody input.request-checkbox--on-hold[type="checkbox"]').first()).toBeVisible();
  await expect(rowByRequestId(pending, pendingShellObserverId)).toBeVisible();
  await expect(requestIdLink(pending, pendingShellObserverId)).toBeVisible();
  await expect(rowByRequestId(pending, pendingShellObserverId).locator('a[href="/user/regular02/"]').first()).toBeVisible();
  await expect(rowByRequestId(onHold, onHoldShellObserverId)).toBeVisible();
  await expect(requestIdLink(onHold, onHoldShellObserverId)).toBeVisible();
  await expect(rowByRequestId(onHold, onHoldShellObserverId).locator('a[href="/user/regular03/"]').first()).toBeVisible();
  await expect(pending.getByText("Renewal", { exact: true }).first()).toBeVisible();
  await expect(rowByRequestId(pending, pendingShellObserverId).getByRole("button", { name: "Approve", exact: true })).toBeVisible();
  await expect(rowByRequestId(pending, pendingShellObserverId).getByRole("button", { name: "Request for Information" })).toBeVisible();
  await expect(rowByRequestId(onHold, onHoldShellObserverId).getByRole("button", { name: "Approve", exact: true })).toBeVisible();
  await expect(rowByRequestId(onHold, onHoldShellObserverId).getByRole("button", { name: "Ignore", exact: true })).toBeVisible();
});

// As a committee operator, I can view separate pending and on-hold queues.
test("committee-pending-filter-renewals preserves on-hold pagination while filtering pending renewals", async ({ page }) => {
  await loginViaForm(page, "regular01", "password");
  const queue = await gotoCommitteeQueue(page);

  const pending = pendingCard(page);
  const onHold = onHoldCard(page);
  const renewalRequestId = findRequestId(queue.pendingRows, "regular04", "mirror");
  const nonRenewalRequestId = findRequestId(queue.pendingRows, "regular05", "mirror");
  const onHoldPageTwoResponse = page.waitForResponse((response) => {
    return response.url().includes("/api/v1/membership/requests/on-hold")
      && response.url().includes("start=10")
      && response.request().method() === "GET";
  });

  await onHold.getByRole("link", { name: "2", exact: true }).click();
  const onHoldPageTwoPayload = (await (await onHoldPageTwoResponse).json()) as QueuePayload;
  await expect(page).toHaveURL(/on_hold_page=2/);
  await expect(onHold.locator(".page-item.active").getByRole("link", { name: "2", exact: true })).toBeVisible();

  const filterResponse = page.waitForResponse((response) => {
    return response.url().includes("/api/v1/membership/requests/pending") && response.url().includes("queue_filter=renewals");
  });

  await pending.getByLabel("Filter requests").selectOption("renewals");
  await filterResponse;

  await expect(page).toHaveURL(/filter=renewals/);
  await expect(page).toHaveURL(/on_hold_page=2/);
  await expect(rowByRequestId(pending, renewalRequestId)).toBeVisible();
  await expect(rowByRequestId(pending, nonRenewalRequestId)).toHaveCount(0);
  await expect(rowByRequestId(pending, renewalRequestId).getByText("Renewal", { exact: true })).toBeVisible();
  await expect(onHold.locator(".page-item.active").getByRole("link", { name: "2", exact: true })).toBeVisible();
  await expect(onHold.locator("tbody tr")).toHaveCount(onHoldPageTwoPayload.data.length);
  for (const row of onHoldPageTwoPayload.data) {
    await expect(rowByRequestId(onHold, row.request_id)).toBeVisible();
  }

  const clearFilterResponse = page.waitForResponse((response) => {
    return response.url().includes("/api/v1/membership/requests/pending")
      && response.url().includes("queue_filter=all")
      && response.request().method() === "GET";
  });
  await pending.getByLabel("Filter requests").selectOption("all");
  await clearFilterResponse;

  await expect(page).not.toHaveURL(/filter=renewals/);
  await expect(rowByRequestId(pendingCard(page), renewalRequestId)).toBeVisible();
  await expect(rowByRequestId(pendingCard(page), nonRenewalRequestId)).toBeVisible();
});

// As a committee operator, I can inspect request detail, compliance warnings, notes, contact action, and reopen ignored requests.
test("committee-request-detail covers compliance warning notes contact and reopen ignored", async ({ page }) => {
  await loginViaForm(page, "regular01", "password");
  const queue = await gotoCommitteeQueue(page);

  const pending = pendingCard(page);
  const detailRequestId = findRequestId(queue.pendingRows, "regular06", "mirror");
  const complianceWarningText = /This user's declared country, .+ \([A-Z]{2}\), is on the list of embargoed countries\./;

  await requestIdLink(pending, detailRequestId).click();
  await expect(page.locator("[data-membership-request-detail-vue-root]")).toBeVisible();
  await expect(page.getByRole("heading", { name: `Membership Request #${detailRequestId}` })).toBeVisible();
  await expect(page.locator('a[href="/user/regular06/"]').first()).toBeVisible();
  await expect(page.getByRole("link", { name: "Contact", exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: "Contact", exact: true })).toHaveAttribute("href", /email-tools\/send-mail/);
  await expect(page.getByRole("alert").filter({ hasText: "Compliance warning:" })).toContainText(complianceWarningText);

  const notesToggle = page.locator(`[data-membership-notes-toggle="${detailRequestId}"]`);
  await notesToggle.click();
  await expect(page.locator(`[data-membership-notes-form="${detailRequestId}"]`)).toBeVisible();
  await expect(page.locator(`[data-membership-notes-messages="${detailRequestId}"]`)).toContainText(complianceWarningText);

  await page.locator(`[data-membership-notes-form="${detailRequestId}"] textarea[name="message"]`).fill(
    "Playwright committee detail note from the operator workflow coverage.",
  );
  await page.getByRole("button", { name: "Send note" }).click();
  await expect(page.getByText("Playwright committee detail note from the operator workflow coverage.")).toBeVisible();

  const noteTextarea = page.locator(`[data-membership-notes-form="${detailRequestId}"] textarea[name="message"]`);
  await noteTextarea.fill("Playwright committee detail note sent with Ctrl+Enter.");
  await noteTextarea.press("Control+Enter");
  await expect(page.getByText("Playwright committee detail note sent with Ctrl+Enter.")).toBeVisible();

  const ignoreResponse = page.waitForResponse((response) => {
    return response.url().includes(`/api/v1/membership/request/${detailRequestId}/ignore`)
      && response.request().method() === "POST";
  });
  await page.getByRole("button", { name: "Ignore", exact: true }).click();
  let modal = page.locator('.modal[aria-modal="true"]');
  await expect(modal).toBeVisible();
  await modal.getByRole("button", { name: "Ignore", exact: true }).click();
  await ignoreResponse;
  await expect(page.getByRole("button", { name: "Reopen", exact: true })).toBeVisible();

  const reopenResponse = page.waitForResponse((response) => {
    return response.url().includes(`/api/v1/membership/request/${detailRequestId}/reopen`)
      && response.request().method() === "POST";
  });
  await page.getByRole("button", { name: "Reopen", exact: true }).click();
  await reopenResponse;
  await expect(page.getByText(/^Pending$/)).toBeVisible();
});

// As a committee operator, I can open per-row actions for approve, reject, RFI, ignore, and approve-on-hold.
test("committee-pending-row-actions uses the canonical modal path and refreshes away the acted-on row", async ({ page }) => {
  await loginViaForm(page, "regular01", "password");
  const queue = await gotoCommitteeQueue(page);

  const pending = pendingCard(page);
  const targetRequestId = findRequestId(queue.pendingRows, "regular06", "mirror");
  const targetRow = rowByRequestId(pending, targetRequestId);
  const ignoreResponse = page.waitForResponse((response) => {
    return response.url().includes("/api/v1/membership/request/")
      && response.url().endsWith("/ignore")
      && response.request().method() === "POST";
  });

  await targetRow.getByRole("button", { name: "Ignore", exact: true }).click();

  const modal = page.locator('.modal[aria-modal="true"]');
  await expect(modal).toBeVisible();
  await expect(modal.getByRole("heading", { name: /ignore mirror request/i })).toBeVisible();
  await expect(modal.getByText(/Requested for/)).toContainText("regular06");
  await modal.getByRole("button", { name: "Ignore", exact: true }).click();

  await ignoreResponse;

  await expect(modal).toHaveCount(0);
  await expect(targetRow).toHaveCount(0);
  await expect(rowByRequestId(pending, findRequestId(queue.pendingRows, "regular04", "mirror"))).toBeVisible();
});

// As a committee operator, I can perform bulk actions.
test("committee-pending-bulk-accept isolates pending selection and refreshes away only the selected rows", async ({ page }) => {
  await loginViaForm(page, "regular01", "password");
  const queue = await gotoCommitteeQueue(page);

  const pending = pendingCard(page);
  const onHold = onHoldCard(page);
  const filterResponse = page.waitForResponse((response) => {
    return response.url().includes("/api/v1/membership/requests/pending") && response.url().includes("queue_filter=individuals");
  });

  await pending.getByLabel("Filter requests").selectOption("individuals");
  const filterPayload = (await (await filterResponse).json()) as QueuePayload;
  const pendingPrimaryId = findRequestId(filterPayload.data, "regular07", "individual");
  const pendingSecondaryId = findRequestId(filterPayload.data, "regular08", "individual");
  const pendingExtraId = findRequestId(filterPayload.data, "regular09", "individual");
  const onHoldPrimaryId = findRequestId(queue.onHoldRows, "regular10", "individual");
  const pendingShellObserverId = findRequestId(queue.pendingRows, "regular02", "mirror");
  const pendingRenewalId = findRequestId(queue.pendingRows, "regular04", "mirror");

  const applyButton = pending.getByRole("button", { name: "Apply", exact: true });
  await expect(applyButton).toBeDisabled();
  await expect(rowByRequestId(pending, pendingPrimaryId)).toBeVisible();
  await expect(rowByRequestId(pending, pendingSecondaryId)).toBeVisible();
  await expect(rowByRequestId(pending, pendingExtraId)).toBeVisible();

  await rowCheckboxByRequestId(pending, pendingPrimaryId).check();
  await rowCheckboxByRequestId(pending, pendingSecondaryId).check();
  await rowCheckboxByRequestId(pending, pendingExtraId).check();
  await expectCheckedCount(pending, "request-checkbox--pending", 3);
  await expectCheckedCount(onHold, "request-checkbox--on-hold", 0);
  await expect(pending.getByText(/Selected:\s*3/)).toBeVisible();

  const bulkResponse = page.waitForResponse((response) => {
    return response.url().includes("/api/v1/membership/requests/bulk") && response.request().method() === "POST";
  });

  await pending.locator('select[name="bulk_action"]').selectOption("accept");
  await applyButton.click();

  await bulkResponse;

  await expect(rowByRequestId(pending, pendingPrimaryId)).toHaveCount(0);
  await expect(rowByRequestId(pending, pendingSecondaryId)).toHaveCount(0);
  await expect(rowByRequestId(pending, pendingExtraId)).toHaveCount(0);
  await expect(pending.getByText(/^Selected:/)).toHaveCount(0);

  const resetFilterResponse = page.waitForResponse((response) => {
    return response.url().includes("/api/v1/membership/requests/pending") && response.url().includes("queue_filter=all");
  });
  await pending.getByLabel("Filter requests").selectOption("all");
  await resetFilterResponse;

  await expect(rowByRequestId(pending, pendingShellObserverId)).toBeVisible();
  await expect(rowByRequestId(pending, pendingRenewalId)).toBeVisible();
  await expect(rowByRequestId(onHold, onHoldPrimaryId)).toBeVisible();
});

// As a committee operator, I can perform bulk actions.
test("committee-on-hold-bulk-approve reuses one justification for selected rows and refreshes only the on-hold queue", async ({ page }) => {
  await loginViaForm(page, "regular01", "password");
  const queue = await gotoCommitteeQueue(page);

  const pending = pendingCard(page);
  const onHold = onHoldCard(page);
  const pendingObserverId = findRequestId(queue.pendingRows, "regular02", "mirror");
  const selectedIds = [
    findRequestId(queue.onHoldRows, "regular10", "individual"),
    findRequestId(queue.onHoldRows, "regular02", "individual"),
    findRequestId(queue.onHoldRows, "regular04", "individual"),
  ];

  await rowCheckboxByRequestId(pending, pendingObserverId).check();

  for (const requestId of selectedIds) {
    await rowCheckboxByRequestId(onHold, requestId).check();
  }

  await expectCheckedCount(pending, "request-checkbox--pending", 1);
  await expectCheckedCount(onHold, "request-checkbox--on-hold", 3);
  await expect(pending.getByText(/Selected:\s*1/)).toBeVisible();
  await expect(onHold.getByText(/Selected:\s*3/)).toBeVisible();

  await onHold.locator('select[name="bulk_action"]').selectOption("accept");
  await onHold.getByRole("button", { name: "Apply", exact: true }).click();

  const modal = page.locator('.modal[aria-modal="true"]');
  await expect(modal).toBeVisible();
  await expect(modal.getByRole("heading", { name: /approve 3 selected requests/i })).toBeVisible();
  await expect(modal.locator(".modal-body")).toContainText("3 selected requests");

  const justification = "Wave 2 committee override justification for selected on-hold requests.";
  await modal.locator("#membership-request-action-text").fill(justification);

  const approveResponses = selectedIds.map((requestId) => page.waitForResponse((response) => {
    return response.url().includes(`/api/v1/membership/request/${requestId}/approve-on-hold`)
      && response.request().method() === "POST";
  }));

  await modal.getByRole("button", { name: "Approve", exact: true }).click();

  await Promise.all(approveResponses);

  await expect(modal).toHaveCount(0);
  for (const requestId of selectedIds) {
    await expect(rowByRequestId(onHold, requestId)).toHaveCount(0);
  }
  await expect(rowByRequestId(onHold, findRequestId(queue.onHoldRows, "regular03", "mirror"))).toBeVisible();
});

// As a committee operator, I can open per-row actions for approve, reject, RFI, ignore, and approve-on-hold.
test("committee-row-actions execute approve reject rfi and on-hold approve through their canonical modal paths", async ({ page }) => {
  await loginViaForm(page, "regular01", "password");
  const queue = await gotoCommitteeQueue(page);

  const pending = pendingCard(page);
  const onHold = onHoldCard(page);
  const approveRequestId = findRequestId(queue.pendingRows, "regular11", "mirror");
  const rejectRequestId = findRequestId(queue.pendingRows, "regular12", "mirror");
  const rfiRequestId = findRequestId(queue.pendingRows, "regular13", "mirror");
  const onHoldApproveRequestId = findRequestId(queue.onHoldRows, "regular14", "mirror");

  const approveResponse = page.waitForResponse((response) => {
    return response.url().includes(`/api/v1/membership/request/${approveRequestId}/approve`)
      && response.request().method() === "POST";
  });
  await rowByRequestId(pending, approveRequestId).getByRole("button", { name: "Approve", exact: true }).click();
  let modal = page.locator('.modal[aria-modal="true"]');
  await expect(modal).toBeVisible();
  await modal.getByRole("button", { name: "Approve", exact: true }).click();
  await approveResponse;
  await expect(rowByRequestId(pending, approveRequestId)).toHaveCount(0);

  const rejectResponse = page.waitForResponse((response) => {
    return response.url().includes(`/api/v1/membership/request/${rejectRequestId}/reject`)
      && response.request().method() === "POST";
  });
  await rowByRequestId(pending, rejectRequestId).getByRole("button", { name: "Reject", exact: true }).click();
  modal = page.locator('.modal[aria-modal="true"]');
  await expect(modal).toBeVisible();
  await modal.getByRole("button", { name: "Reject", exact: true }).click();
  await rejectResponse;
  await expect(rowByRequestId(pending, rejectRequestId)).toHaveCount(0);

  const rfiResponse = page.waitForResponse((response) => {
    return response.url().includes(`/api/v1/membership/request/${rfiRequestId}/rfi`)
      && response.request().method() === "POST";
  });
  await rowByRequestId(pending, rfiRequestId).getByRole("button", { name: "Request for Information" }).click();
  modal = page.locator('.modal[aria-modal="true"]');
  await expect(modal).toBeVisible();
  await modal.locator("#membership-request-action-text").fill("Need one more operator detail before approval.");
  await modal.getByRole("button", { name: "Send RFI", exact: true }).click();
  await rfiResponse;
  await expect(rowByRequestId(pending, rfiRequestId)).toHaveCount(0);

  const onHoldApproveResponse = page.waitForResponse((response) => {
    return response.url().includes(`/api/v1/membership/request/${onHoldApproveRequestId}/approve-on-hold`)
      && response.request().method() === "POST";
  });
  await rowByRequestId(onHold, onHoldApproveRequestId).getByRole("button", { name: "Approve", exact: true }).click();
  modal = page.locator('.modal[aria-modal="true"]');
  await expect(modal).toBeVisible();
  await modal.locator("#membership-request-action-text").fill("Committee override approved after clarification.");
  await modal.getByRole("button", { name: "Approve", exact: true }).click();
  await onHoldApproveResponse;
  await expect(modal).toHaveCount(0);
});

import { expect, test, type Locator, type Page } from "@playwright/test";

const VIEWER = {
  username: "regular16",
  password: "password",
};

const SPONSOR = {
  username: "regular17",
  password: "password",
};

const GROUPS = {
  detailFocus: "wave5-detail-focus-group",
  detailChild: "wave5-detail-child-group",
  detailLeader: "wave5-detail-leader-group",
};

const USERS = {
  detailDirectMember: "regular18",
};

const REQUIRED_AGREEMENT = "wave5-group-access-agreement";

async function loginViaForm(page: Page, username: string, password: string): Promise<void> {
  await page.goto("/login/");
  await page.getByLabel("Username", { exact: true }).fill(username);
  await page.getByLabel("Password", { exact: true }).fill(password);
  await page.getByRole("button", { name: /log in/i }).click();
}

async function selectOptionFromSelect2(page: Page, fieldName: string, searchTerm: string, optionText: string): Promise<void> {
  const select = page.locator(`select[name="${fieldName}"]`);
  await expect(select).toHaveCount(1);
  const ajaxUrl = await select.getAttribute("data-ajax-url");
  if (!ajaxUrl) {
    throw new Error(`Select2 field ${fieldName} is missing data-ajax-url`);
  }
  const ajaxPath = new URL(ajaxUrl, "http://127.0.0.1").pathname;

  const select2Container = select.locator("xpath=following-sibling::span[contains(@class, 'select2')]");
  await expect(select2Container).toBeVisible();
  await select2Container.click();

  const searchResponse = page.waitForResponse((response) => {
    const responseUrl = new URL(response.url());
    return responseUrl.pathname === ajaxPath
      && responseUrl.searchParams.get("q") === searchTerm
      && response.request().method() === "GET";
  });

  const searchInput = page.locator(".select2-container--open .select2-search__field");
  await searchInput.fill("");
  await searchInput.pressSequentially(searchTerm);
  const response = await searchResponse;
  if (!response.ok()) {
    throw new Error(`Select2 search for ${fieldName} returned ${response.status()}: ${await response.text()}`);
  }

  const result = page.locator(".select2-results__option").filter({ hasText: optionText }).first();
  await expect(result).toBeVisible();
  await result.click();
  await expect(select).toHaveValue(searchTerm);
}

async function logoutViaForm(page: Page): Promise<void> {
  await page.locator('form[action="/logout/"] button').click();
  await expect(page).toHaveURL(/\/login\/?$/);
}

function groupDetailUrl(groupName: string): string {
  return `/group/${groupName}/`;
}

function groupActionUrl(groupName: string): string {
  return `/api/v1/groups/${groupName}/action`;
}

function cardByHeading(page: Page, headingName: RegExp): Locator {
  return page.locator(".card").filter({ has: page.getByRole("heading", { name: headingName }) }).first();
}

function userCard(page: Page, container: Locator, username: string): Locator {
  return container.locator(".card-widget").filter({
    has: page.locator(`a[href="/user/${username}/"]`),
  }).first();
}

function actionResponse(page: Page, action: string): Promise<unknown> {
  return page.waitForResponse((response) => {
    const postData = response.request().postData() || "";
    return response.url().includes(groupActionUrl(GROUPS.detailFocus))
      && response.request().method() === "POST"
      && postData.includes(`"action":"${action}"`);
  });
}

async function confirmPendingAction(page: Page, action: string): Promise<void> {
  const response = actionResponse(page, action);
  await page.locator(".modal.d-block").getByRole("button", { name: "Confirm", exact: true }).click();
  await response;
}

async function signRequiredAgreement(page: Page): Promise<void> {
  await loginViaForm(page, USERS.detailDirectMember, SPONSOR.password);
  await page.goto(`/settings/?tab=agreements&agreement=${encodeURIComponent(REQUIRED_AGREEMENT)}`);
  const signButton = page.getByRole("button", { name: /^sign$/i });
  if (await signButton.isVisible()) {
    await signButton.click();
    await expect(page.getByText("Signed", { exact: true }).first()).toBeVisible();
  }
  await logoutViaForm(page);
}

// As a sponsor/team lead, I can inspect management controls, searchable select2 pickers, and agreement prerequisites before taking action.
test("groups-detail-team-membership-controls expose select2 member and group pickers with agreement prerequisites", async ({ page }) => {
  await loginViaForm(page, SPONSOR.username, SPONSOR.password);

  await page.goto(`/group/${GROUPS.detailFocus}/`);
  await expect(page.locator("[data-group-detail-root]")).toBeVisible();

  const memberSelect = page.locator('select[name="username"]');
  const groupSelect = page.locator('select[name="group_name"]');

  await expect(memberSelect).toHaveCount(1);
  await expect(groupSelect).toHaveCount(1);
  await expect(memberSelect).toHaveAttribute("data-ajax-url", /\/groups\/member-users\/search\/$/);
  await expect(groupSelect).toHaveAttribute("data-ajax-url", /\/groups\/member-groups\/search\/$/);

  const memberContainer = memberSelect.locator("xpath=following-sibling::span[contains(@class, 'select2')]");
  const groupContainer = groupSelect.locator("xpath=following-sibling::span[contains(@class, 'select2')]");
  await expect(memberContainer).toBeVisible();
  await expect(groupContainer).toBeVisible();
  await expect(memberContainer).toContainText("Add member by username");
  await expect(groupContainer).toContainText("Add member group by name");
  await expect(page.getByRole("button", { name: "Add", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Add group", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Stop being a Team Lead", exact: true })).toBeVisible();

  await selectOptionFromSelect2(page, "username", VIEWER.username, VIEWER.username);
  await expect(page.getByText("wave5-group-access-agreement (Signed)", { exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: "Settings → Agreements", exact: true })).toBeVisible();
  await expect(page.getByText("Users must have signed required agreements before being added.", { exact: true })).toBeVisible();
});

// As a sponsor/team lead, I can inspect destructive management confirmations without submitting them.
test("groups-detail-confirm-modal-variants cover team-lead and member actions", async ({ page }) => {
  await loginViaForm(page, SPONSOR.username, SPONSOR.password);

  await page.goto(`/group/${GROUPS.detailFocus}/`);
  await expect(page.locator("[data-group-detail-root]")).toBeVisible();

  const childGroupCard = page.locator(".position-relative").filter({
    has: page.locator(`a[href="/group/${GROUPS.detailChild}/"]`).first(),
  }).first();
  const leaderGroupCard = page.locator(".position-relative").filter({
    has: page.locator(`a[href="/group/${GROUPS.detailLeader}/"]`).first(),
  }).first();

  await page.getByRole("button", { name: "Stop being a Team Lead", exact: true }).click();
  await expect(page.getByText("Stop being a Team Lead?", { exact: true })).toBeVisible();
  await expect(page.getByText("Are you sure you want to stop being a Team Lead for this group?", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Cancel", exact: true }).click();

  await page.getByRole("button", { name: "Remove member", exact: true }).first().click();
  await expect(page.getByText("Remove member?", { exact: true })).toBeVisible();
  await expect(page.getByText(`Remove ${USERS.detailDirectMember} from this group?`, { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Cancel", exact: true }).click();

  await page.getByRole("button", { name: "Promote to Team Lead", exact: true }).first().click();
  await expect(page.getByText("Promote member to Team Lead?", { exact: true })).toBeVisible();
  await expect(page.getByText(`Promote ${USERS.detailDirectMember} to Team Lead for this group?`, { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Cancel", exact: true }).click();

  await childGroupCard.getByRole("button", { name: "Remove member group", exact: true }).click();
  await expect(page.getByText("Remove member group?", { exact: true })).toBeVisible();
  await expect(page.getByText(`Remove ${GROUPS.detailChild} from this group?`, { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Cancel", exact: true }).click();

  await childGroupCard.getByRole("button", { name: "Promote group to Team Lead", exact: true }).click();
  await expect(page.getByText("Promote member group to Team Lead?", { exact: true })).toBeVisible();
  await expect(page.getByText(`Promote ${GROUPS.detailChild} to Team Lead for this group?`, { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Cancel", exact: true }).click();

  await leaderGroupCard.getByRole("button", { name: "Remove Team Lead", exact: true }).click();
  await expect(page.getByText("Demote Team Lead?", { exact: true })).toBeVisible();
  await expect(page.getByText(`Remove Team Lead access for ${GROUPS.detailLeader}?`, { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Cancel", exact: true }).click();
});

// As a sponsor/team lead, I can manage membership of my group.
test("groups-detail-team-lead-manages-member-actions", async ({ page }) => {
  await signRequiredAgreement(page);
  await loginViaForm(page, SPONSOR.username, SPONSOR.password);

  await page.goto(groupDetailUrl(GROUPS.detailFocus));
  await expect(page.locator("[data-group-detail-root]")).toBeVisible();

  let teamLeadsCard = cardByHeading(page, /^Team Leads?$/);
  let membersCard = cardByHeading(page, /^Members$/);
  let directMemberCard = userCard(page, membersCard, USERS.detailDirectMember);

  await expect(directMemberCard).toBeVisible();
  await expect(directMemberCard.getByRole("button", { name: "Promote to Team Lead", exact: true })).toBeVisible();
  await expect(userCard(page, teamLeadsCard, USERS.detailDirectMember)).toHaveCount(0);

  await directMemberCard.getByRole("button", { name: "Promote to Team Lead", exact: true }).click();
  await expect(page.getByText(`Promote ${USERS.detailDirectMember} to Team Lead for this group?`, { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Cancel", exact: true }).click();
  await expect(directMemberCard.getByRole("button", { name: "Promote to Team Lead", exact: true })).toBeVisible();
  await expect(userCard(page, teamLeadsCard, USERS.detailDirectMember)).toHaveCount(0);

  await directMemberCard.getByRole("button", { name: "Promote to Team Lead", exact: true }).click();
  await confirmPendingAction(page, "promote_member");

  await expect(userCard(page, membersCard, USERS.detailDirectMember)).toBeVisible();
  const leadersPageTwoResponse = page.waitForResponse((response) => {
    return response.url().includes(`/api/v1/groups/${GROUPS.detailFocus}/leaders?page=2`)
      && response.request().method() === "GET";
  });
  await teamLeadsCard.getByLabel("Next", { exact: true }).click();
  await leadersPageTwoResponse;
  teamLeadsCard = cardByHeading(page, /^Team Leads?$/);
  directMemberCard = userCard(page, teamLeadsCard, USERS.detailDirectMember);
  await expect(directMemberCard).toBeVisible();
  await expect(userCard(page, membersCard, USERS.detailDirectMember)).toBeVisible();

  await directMemberCard.getByRole("button", { name: "Remove Team Lead", exact: true }).click();
  await expect(page.getByText(`Remove Team Lead access for ${USERS.detailDirectMember}?`, { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Cancel", exact: true }).click();
  await expect(userCard(page, teamLeadsCard, USERS.detailDirectMember)).toBeVisible();
  await expect(userCard(page, membersCard, USERS.detailDirectMember)).toBeVisible();

  await directMemberCard.getByRole("button", { name: "Remove Team Lead", exact: true }).click();
  await confirmPendingAction(page, "demote_sponsor");
  teamLeadsCard = cardByHeading(page, /^Team Leads?$/);
  membersCard = cardByHeading(page, /^Members$/);
  await expect(userCard(page, teamLeadsCard, USERS.detailDirectMember)).toHaveCount(0);
  directMemberCard = userCard(page, membersCard, USERS.detailDirectMember);
  await expect(directMemberCard).toBeVisible();
  await expect(directMemberCard.getByRole("button", { name: "Promote to Team Lead", exact: true })).toBeVisible();

  await directMemberCard.getByRole("button", { name: "Remove member", exact: true }).click();
  await expect(page.getByText(`Remove ${USERS.detailDirectMember} from this group?`, { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Cancel", exact: true }).click();
  await expect(userCard(page, membersCard, USERS.detailDirectMember)).toBeVisible();

  await directMemberCard.getByRole("button", { name: "Remove member", exact: true }).click();
  await confirmPendingAction(page, "remove_member");
  teamLeadsCard = cardByHeading(page, /^Team Leads?$/);
  membersCard = cardByHeading(page, /^Members$/);
  await expect(userCard(page, membersCard, USERS.detailDirectMember)).toHaveCount(0);
  await expect(userCard(page, teamLeadsCard, USERS.detailDirectMember)).toHaveCount(0);

  const addMemberResponse = actionResponse(page, "add_member");
  await selectOptionFromSelect2(page, "username", USERS.detailDirectMember, USERS.detailDirectMember);
  await page.getByRole("button", { name: "Add", exact: true }).click();
  await addMemberResponse;
  membersCard = cardByHeading(page, /^Members$/);
  directMemberCard = userCard(page, membersCard, USERS.detailDirectMember);
  await expect(directMemberCard).toBeVisible();
  await expect(directMemberCard.getByRole("button", { name: "Promote to Team Lead", exact: true })).toBeVisible();

  const leadersPageOneResponse = page.waitForResponse((response) => {
    return response.url().includes(`/api/v1/groups/${GROUPS.detailFocus}/leaders`)
      && !response.url().includes("page=2")
      && response.request().method() === "GET";
  });
  await teamLeadsCard.getByLabel("Previous", { exact: true }).click();
  await leadersPageOneResponse;
  teamLeadsCard = cardByHeading(page, /^Team Leads?$/);
  await expect(userCard(page, teamLeadsCard, SPONSOR.username)).toBeVisible();

  await page.getByRole("button", { name: "Stop being a Team Lead", exact: true }).click();
  await expect(page.getByText("Are you sure you want to stop being a Team Lead for this group?", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Cancel", exact: true }).click();
  await expect(page.getByRole("button", { name: "Stop being a Team Lead", exact: true })).toBeVisible();
  await expect(userCard(page, teamLeadsCard, SPONSOR.username)).toBeVisible();

  await page.getByRole("button", { name: "Stop being a Team Lead", exact: true }).click();
  await confirmPendingAction(page, "stop_sponsoring");
  teamLeadsCard = cardByHeading(page, /^Team Leads?$/);
  await expect(page.getByRole("button", { name: "Stop being a Team Lead", exact: true })).toHaveCount(0);
  await expect(userCard(page, teamLeadsCard, SPONSOR.username)).toHaveCount(0);
  await expect(page.locator('select[name="username"]')).toHaveCount(0);
});
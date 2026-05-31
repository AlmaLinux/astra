import { expect, test, type Page } from "@playwright/test";

const VIEWER = {
  username: "regular16",
  password: "password",
};

const SPONSOR = {
  username: "regular17",
  password: "password",
};

const GROUPS = {
  alphaShell: "wave5-alpha-shell-group",
  betaShell: "wave5-beta-shell-group",
  searchHit: "wave5-groups-search-hit-group",
  pageTwo: "wave5-zz-page-two-group",
  detailFocus: "wave5-detail-focus-group",
  detailMemberPagination: "wave5-detail-member-pagination-group",
  detailChild: "wave5-detail-child-group",
  detailGrandchild: "wave5-detail-grandchild-group",
  detailLeader: "wave5-detail-leader-group",
};

const USERS = {
  detailDirectMember: "regular18",
  detailMemberSearch: "regular60",
  detailMemberPageTwo: "regular59",
  detailLeaderPageTwo: "regular49",
};

async function loginViaForm(page: Page, username: string, password: string): Promise<void> {
  await page.goto("/login/");
  await page.getByLabel("Username", { exact: true }).fill(username);
  await page.getByLabel("Password", { exact: true }).fill(password);
  await page.getByRole("button", { name: /log in/i }).click();
}

// As a user, I can browse groups, search, clear search, and paginate.
test("groups-list-shell renders the real groups shell with deterministic page-one rows", async ({ page }) => {
  await loginViaForm(page, VIEWER.username, VIEWER.password);

  const groupsResponse = page.waitForResponse((response) => {
    return response.url().includes("/api/v1/groups") && response.request().method() === "GET";
  });

  await page.goto("/groups/");
  await expect(page.locator("[data-groups-root]")).toBeVisible();
  await groupsResponse;

  await expect(page.getByRole("textbox", { name: "Search groups" })).toHaveValue("");
  await expect(page.getByRole("link", { name: GROUPS.alphaShell, exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: GROUPS.betaShell, exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: GROUPS.pageTwo, exact: true })).toHaveCount(0);
});

// As a user, I can browse groups, search, clear search, and paginate.
test("groups-list-search-pagination narrows to a seeded hit and traverses one stable page boundary", async ({ page }) => {
  await loginViaForm(page, VIEWER.username, VIEWER.password);
  const encodedSearchHit = encodeURIComponent(GROUPS.searchHit);
  const filteredResponse = page.waitForResponse((response) => {
    return response.url().includes(`/api/v1/groups?q=${encodedSearchHit}`) && response.request().method() === "GET";
  });
  await page.goto(`/groups/?q=${encodedSearchHit}`);
  await expect(page.locator("[data-groups-root]")).toBeVisible();
  await filteredResponse;

  await expect(page.getByRole("textbox", { name: "Search groups" })).toHaveValue(GROUPS.searchHit);
  await expect(page.getByRole("link", { name: GROUPS.searchHit, exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: GROUPS.alphaShell, exact: true })).toHaveCount(0);

  const clearedResponse = page.waitForResponse((response) => {
    return response.url().includes("/api/v1/groups")
      && !response.url().includes("q=")
      && response.request().method() === "GET";
  });
  await page.getByRole("button", { name: "Clear search", exact: true }).click();
  await clearedResponse;

  await expect(page.getByRole("link", { name: GROUPS.alphaShell, exact: true })).toBeVisible();

  const nextPageResponse = page.waitForResponse((response) => {
    return response.url().includes("/api/v1/groups?page=2") && response.request().method() === "GET";
  });
  await page.getByLabel("Next", { exact: true }).click();
  await nextPageResponse;

  await expect(page.getByRole("link", { name: GROUPS.pageTwo, exact: true })).toBeVisible();
  await expect(page).toHaveURL(/\/groups\/\?page=2$/);

  const previousPageResponse = page.waitForResponse((response) => {
    return response.url().includes("/api/v1/groups")
      && !response.url().includes("page=2")
      && response.request().method() === "GET";
  });
  await page.getByLabel("Previous", { exact: true }).click();
  await previousPageResponse;

  await expect(page.getByRole("link", { name: GROUPS.alphaShell, exact: true })).toBeVisible();
});

// As a user, I can open a group detail page and inspect info, team leads, members, and nested groups.
test("groups-detail-nested-members shows recursive membership count and keeps nested groups ahead of users", async ({ page }) => {
  await loginViaForm(page, SPONSOR.username, SPONSOR.password);

  await page.goto(`/group/${GROUPS.detailFocus}/`);
  await expect(page.locator("[data-group-detail-root]")).toBeVisible();

  const detailHeading = page.getByRole("heading", { name: new RegExp(`Group: ${GROUPS.detailFocus}`) });
  const childGroupLink = page.locator(`a[href="/group/${GROUPS.detailChild}/"]`).first();
  const grandchildGroupLink = page.locator(`a[href="/group/${GROUPS.detailGrandchild}/"]`).first();
  const directMemberLink = page.locator(`a[href="/user/${USERS.detailDirectMember}/"]`).first();
  await expect(detailHeading).toBeVisible();
  await expect(detailHeading).toContainText("3 members");
  await expect(childGroupLink).toBeVisible();
  await expect(grandchildGroupLink).toBeVisible();
  await expect(directMemberLink).toBeVisible();

  const orderCheck = await page.evaluate(({ detailChild, detailGrandchild, detailDirectMember }) => {
    const childLink = document.querySelector(`a[href="/group/${detailChild}/"]`);
    const grandchildLink = document.querySelector(`a[href="/group/${detailGrandchild}/"]`);
    const directMemberLink = document.querySelector(`a[href="/user/${detailDirectMember}/"]`);
    if (!(childLink instanceof HTMLAnchorElement) || !(grandchildLink instanceof HTMLAnchorElement) || !(directMemberLink instanceof HTMLAnchorElement)) {
      return false;
    }

    return (childLink.compareDocumentPosition(directMemberLink) & Node.DOCUMENT_POSITION_FOLLOWING) !== 0
      && (grandchildLink.compareDocumentPosition(directMemberLink) & Node.DOCUMENT_POSITION_FOLLOWING) !== 0;
  }, {
    detailChild: GROUPS.detailChild,
    detailGrandchild: GROUPS.detailGrandchild,
    detailDirectMember: USERS.detailDirectMember,
  });

  expect(orderCheck).toBe(true);
});

// As a user, I can open a group detail page and inspect info, team leads, members, and nested groups.
test("groups-detail-chat-links renders deterministic IRC, Matrix, and Mattermost links", async ({ page }) => {
  await loginViaForm(page, SPONSOR.username, SPONSOR.password);
  await page.goto(`/group/${GROUPS.detailFocus}/`);
  await expect(page.locator("[data-group-detail-root]")).toBeVisible();

  const ircLink = page.locator('a[href*="irc.libera.chat"]');
  const matrixLink = page.locator('a[href*="matrix.to"]');
  const mattermostLink = page.locator('a[href="https://chat.almalinux.org/almalinux/channels/wave5-groups"]');

  await expect(ircLink).toBeVisible();
  await expect(matrixLink).toBeVisible();
  await expect(mattermostLink).toBeVisible();
});

// As a sponsor/team lead, I can search members and paginate leaders and members independently.
test("groups-detail-leaders-pagination shows one mixed leaders page and advances once", async ({ page }) => {
  await loginViaForm(page, SPONSOR.username, SPONSOR.password);

  await page.goto(`/group/${GROUPS.detailFocus}/`);
  await expect(page.locator("[data-group-detail-root]")).toBeVisible();

  const teamLeadsCard = page.locator(".card").filter({ has: page.getByRole("heading", { name: "Team Leads" }) }).first();

  await expect(page.getByRole("heading", { name: "Team Leads" })).toBeVisible();
  await expect(teamLeadsCard.locator(`a[href="/group/${GROUPS.detailLeader}/"]`).first()).toBeVisible();
  await expect(teamLeadsCard.locator(`a[href="/user/${SPONSOR.username}/"]`).first()).toBeVisible();

  const leadersPageTwoResponse = page.waitForResponse((response) => {
    return response.url().includes(`/api/v1/groups/${GROUPS.detailFocus}/leaders?page=2`) && response.request().method() === "GET";
  });
  await page.getByLabel("Next", { exact: true }).click();
  await leadersPageTwoResponse;

  await expect(page.getByRole("link", { name: USERS.detailLeaderPageTwo, exact: true })).toBeVisible();
  await expect(page).toHaveURL(new RegExp(`/group/${GROUPS.detailFocus}/\\?leaders_page=2$`));
});

// As a sponsor/team lead, I can search members and paginate leaders and members independently.
test("groups-detail-member-search-and-pagination keeps member search and member paging independent from leaders", async ({ page }) => {
  await loginViaForm(page, SPONSOR.username, SPONSOR.password);

  await page.goto(`/group/${GROUPS.detailMemberPagination}/`);
  await expect(page.locator("[data-group-detail-root]")).toBeVisible();

  const membersCard = page.locator(".card").filter({ has: page.getByRole("heading", { name: /^Members$/ }) }).first();
  const teamLeadsCard = page.locator(".card").filter({ has: page.getByRole("heading", { name: /^Team Leads$/ }) }).first();

  const searchResponse = page.waitForResponse((response) => {
    return response.url().includes(`/api/v1/groups/${GROUPS.detailMemberPagination}/members?q=${USERS.detailMemberSearch}`)
      && response.request().method() === "GET";
  });
  await page.getByRole("textbox", { name: "Search members" }).fill(USERS.detailMemberSearch);
  await membersCard.getByRole("button", { name: "Search", exact: true }).click();
  await searchResponse;

  await expect(membersCard.getByRole("link", { name: USERS.detailMemberSearch, exact: true })).toBeVisible();
  await expect(membersCard.getByRole("link", { name: USERS.detailMemberPageTwo, exact: true })).toHaveCount(0);
  await expect(teamLeadsCard.getByRole("link", { name: SPONSOR.username, exact: true })).toBeVisible();

  const clearResponse = page.waitForResponse((response) => {
    return response.url().includes(`/api/v1/groups/${GROUPS.detailMemberPagination}/members`)
      && !response.url().includes("q=")
      && response.request().method() === "GET";
  });
  await membersCard.getByRole("button", { name: "Clear search", exact: true }).click();
  await clearResponse;

  const memberPageTwoResponse = page.waitForResponse((response) => {
    return response.url().includes(`/api/v1/groups/${GROUPS.detailMemberPagination}/members?page=2`)
      && response.request().method() === "GET";
  });
  await membersCard.getByLabel("Next", { exact: true }).click();
  await memberPageTwoResponse;

  await expect(membersCard.getByRole("link", { name: USERS.detailMemberPageTwo, exact: true })).toBeVisible();
  await expect(teamLeadsCard.getByRole("link", { name: SPONSOR.username, exact: true })).toBeVisible();
  await expect(page).toHaveURL(new RegExp(`/group/${GROUPS.detailMemberPagination}/\\?page=2$`));
});
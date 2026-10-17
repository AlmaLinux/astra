import { expect, test, type Page } from "@playwright/test";

import { readAuthResetState } from "./resetState";

const authResetState = readAuthResetState();

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

async function loginViaForm(page: Page, username: string, password: string): Promise<void> {
  await page.goto(authResetState.routes.login);
  await page.getByLabel("Username", { exact: true }).fill(username);
  await page.getByLabel("Password", { exact: true }).fill(password);
  await page.getByRole("button", { name: /log in/i }).click();
}

// As a profile owner, I can load my own profile shell and see summary, memberships, and groups.
// As a membership owner, I can request membership, request renewal, request tier change, and inspect pending requests from the membership card.
test("auth-profile-owner sees the seeded profile, memberships, and pending request state", async ({ page }) => {
  const actor = authResetState.actors.regular03;

  await loginViaForm(page, actor.username, actor.password);

  await expect(page).toHaveURL(new RegExp(`${escapeRegExp(actor.profile_route)}$`));
  await expect(page.locator("[data-user-profile-root]")).toBeVisible();
  await expect(page.getByText("Loading profile...")).toHaveCount(0);
  await expect(page.getByText("Unable to load profile right now.")).toHaveCount(0);
  await expect(page.locator(".profile-username")).toHaveText("Regular 03 User");
  await expect(page.locator("#user_mail")).toContainText("regular03@example.test");
  await expect(page.locator("[title='Pronouns']")).toBeVisible();
  await expect(page.locator("[data-user-profile-membership-root]")).toBeVisible();
  await expect(page.getByRole("link", { name: /request #/i }).first()).toBeVisible();
});

// As another-profile viewer, I can target `/user/<username>/` directly; current source wiring indicates the shell route itself is not protected by the authenticated user-directory gate.
// As a non-self ordinary viewer of a private profile, I still see the profile shell with the redacted fields proven by the current detail-payload tests plus the group/agreement data that the present source path continues to expose.
test("auth-profile-private ordinary viewers get the private-profile shell with redacted data", async ({ page }) => {
  const actor = authResetState.actors.regular04;

  await loginViaForm(page, actor.username, actor.password);
  await page.goto(authResetState.actors.regular07.profile_route);

  await expect(page.locator("[data-user-profile-root]")).toBeVisible();
  await expect(page.locator(".profile-username")).toHaveText("regular07");
  await expect(page.locator("#user_username")).toHaveText("regular07");
  await expect(page.locator("#user_mail")).toHaveCount(0);
  await expect(page.locator("[title='Pronouns']")).toHaveCount(0);
  await expect(page.getByRole("link", { name: "https://regular07.example.test" })).toHaveCount(0);
  await expect(page.locator("[data-user-profile-membership-root]")).toHaveCount(0);
});

// As a membership committee viewer of a private profile, I can see committee-only membership-review fields without restoring the full private profile.
test("auth-profile-private committee viewers regain committee-only fields without unredacting the full profile", async ({ page }) => {
  const actor = authResetState.actors.regular01;

  await loginViaForm(page, actor.username, actor.password);
  await page.goto(authResetState.actors.regular07.profile_route);

  await expect(page.locator("[data-user-profile-root]")).toBeVisible();
  await expect(page.locator(".profile-username")).toHaveText("regular07");
  await expect(page.locator("#user_mail")).toContainText("regular07@example.test");
  const countryRow = page.locator("#user_attributes li").filter({ has: page.locator("[title='Country']") });
  await expect(countryRow).toBeVisible();
  await expect(countryRow).toContainText(/United States|US/);
  await expect(page.locator("[data-user-profile-membership-root]")).toBeVisible();
  await expect(page.getByRole("link", { name: "https://regular07.example.test" })).toHaveCount(0);
});

// As a user with unmet prerequisites, I see `Action required` or dismissible `Recommended` alerts with deep links.
test("auth-profile-account-setup alerts expose deep links and recommended dismissal persists", async ({ page }) => {
  const actor = authResetState.actors.account_setup;

  await loginViaForm(page, actor.username, actor.password);
  await expect(page).toHaveURL(new RegExp(`${escapeRegExp(actor.profile_route)}$`));

  const requiredAlert = page.locator("#account-setup-required-alert");
  await expect(requiredAlert).toBeVisible();
  await expect(requiredAlert.getByRole("link", { name: "Set country code" })).toHaveAttribute(
    "href",
    /tab=profile&highlight=country_code/,
  );

  const recommendedAlert = page.locator("#account-setup-recommended-alert");
  await expect(recommendedAlert).toBeVisible();
  await expect(recommendedAlert.getByText("Request an individual membership")).toBeVisible();
  await recommendedAlert.getByRole("button", { name: "Close" }).click();
  await expect(page.locator("#account-setup-recommended-alert")).toHaveCount(0);

  await page.reload();
  await expect(page.locator("#account-setup-recommended-alert")).toHaveCount(0);
});

// As an operator with management rights over the viewed user, I can edit expiration dates and terminate memberships after a typed-name confirmation.
test("auth-profile-membership-operator sees the management modal and typed-name termination guard", async ({ page }) => {
  const actor = authResetState.actors.regular01;

  await loginViaForm(page, actor.username, actor.password);
  await page.goto(authResetState.actors.regular03.profile_route);

  await expect(page.locator("[data-user-profile-membership-root]")).toBeVisible();
  await page.getByRole("button", { name: /edit membership/i }).click();

  const modal = page.locator("#expiry-modal-1");
  await expect(modal).toBeVisible();
  await expect(modal.getByLabel("Expiration date")).toBeVisible();
  await expect(modal.getByRole("button", { name: /save changes/i })).toBeVisible();

  await modal.getByRole("button", { name: /terminate membership/i }).click();
  const confirmInput = modal.getByLabel("Type the name to confirm");
  const terminateButton = modal.getByRole("button", { name: /^terminate membership$/i });

  await expect(terminateButton).toBeDisabled();
  await confirmInput.fill("wrong-name");
  await expect(terminateButton).toBeDisabled();
  await confirmInput.fill("regular03");
  await expect(terminateButton).toBeEnabled();
});
// Runs in the browser. Clicking the toggle in the same task lets the caller measure the very
// first frame of the expand transition.
function measureSidebar(clickToggle: boolean) {
  if (clickToggle) {
    document.querySelector<HTMLElement>("[data-widget='pushmenu']")?.click();
  }
  const visibleLinks = Array.from(document.querySelectorAll(".nav-sidebar .nav-link")).filter(
    (link) => link.getClientRects().length > 0,
  );
  const links = visibleLinks.map((link) => {
    const icon = link.querySelector(".nav-icon")?.getBoundingClientRect();
    return {
      text: link.querySelector("p")?.firstChild?.textContent?.trim(),
      iconLeft: icon?.left,
      iconWidth: icon?.width,
      labelLeft: link.querySelector("p")?.getBoundingClientRect().left,
      height: link.getBoundingClientRect().height,
    };
  });
  return { sidebarWidth: document.querySelector<HTMLElement>(".main-sidebar")?.offsetWidth, links };
}

async function openSidebarLayout(page: Page) {
  await page.setViewportSize({ width: 1280, height: 900 });
  const actor = authResetState.actors.regular01;
  await loginViaForm(page, actor.username, actor.password);

  // Open a submenu so its links (some with badges) are measured too. AdminLTE wires treeviews on load.
  await page.waitForLoadState("load");
  await page.locator(".nav-sidebar .nav-link").filter({ hasText: "Membership Management" }).click();
  // jQuery's slideDown leaves only display: block behind once it has finished.
  await expect(page.locator(".nav-item.menu-open > .nav-treeview")).toHaveAttribute("style", "display: block;");

  const pinned = await page.evaluate(measureSidebar, false);
  const singleLineHeight = pinned.links.find((link) => link.text === "My Profile")?.height ?? 0;
  const longLabel = pinned.links.find((link) => link.text === "Membership Management");
  expect(longLabel?.height).toBeGreaterThan(singleLineHeight);
  expect(pinned.links.find((link) => link.text === "Sponsors")).toBeDefined();
  expectLabelsAligned(pinned);

  await page.locator("[data-widget='pushmenu']").click();
  await expect(page.locator(".main-sidebar")).toHaveJSProperty("offsetWidth", 74);
  return pinned;
}

type SidebarLayout = ReturnType<typeof measureSidebar>;

function expectLabelsAligned(layout: SidebarLayout): void {
  expect(new Set(layout.links.map((link) => link.labelLeft)).size).toBe(1);
}

// Mid-animation the layout must already be the open one. Labels are excluded from the position check because
// AdminLTE slides them all in together; they must stay aligned with each other while doing so.
function expectOpenLayoutWhileExpanding(expanding: SidebarLayout, pinned: SidebarLayout): void {
  const withoutLabelLeft = ({ labelLeft: _, ...link }: SidebarLayout["links"][number]) => link;
  expect(expanding.sidebarWidth).toBeLessThan(250);
  expect(expanding.links.map(withoutLabelLeft)).toEqual(pinned.links.map(withoutLabelLeft));
  expectLabelsAligned(expanding);
}

// As a signed-in user, expanding the collapsed sidebar reveals the final layout without reflowing labels
// mid-animation, including long labels that wrap in the open sidebar.
test("auth-profile-sidebar labels keep their open layout while the collapsed sidebar expands", async ({ page }) => {
  const pinned = await openSidebarLayout(page);

  expectOpenLayoutWhileExpanding(await page.evaluate(measureSidebar, true), pinned);

  await expect.poll(() => page.evaluate(measureSidebar, false)).toEqual(pinned);
});

// As a signed-in user, hovering the collapsed sidebar shows the same layout as pinning it open, throughout the animation.
test("auth-profile-sidebar hover-expanded layout matches the pinned-open sidebar", async ({ page }) => {
  const pinned = await openSidebarLayout(page);

  await page.mouse.move(30, 300);
  expectOpenLayoutWhileExpanding(await page.evaluate(measureSidebar, false), pinned);

  await expect.poll(() => page.evaluate(measureSidebar, false)).toEqual(pinned);
});

// As a profile owner, my avatar stays circular when its column is narrower than the avatar's nominal size.
test("auth-profile-avatar stays square when the profile column is narrower than the avatar", async ({ page }) => {
  const actor = authResetState.actors.regular03;

  await page.setViewportSize({ width: 800, height: 900 });
  await loginViaForm(page, actor.username, actor.password);

  const avatar = page.locator("[data-user-profile-summary-vue-root] .d-md-block img.img-circle");
  await expect(avatar).toBeVisible();
  const box = await avatar.boundingBox();
  expect(box?.width).toBeLessThan(220);
  expect(box?.height).toBeCloseTo(box?.width ?? 0, 1);
});

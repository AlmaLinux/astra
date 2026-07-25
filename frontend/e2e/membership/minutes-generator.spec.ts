import { expect, test, type Page } from "@playwright/test";

const MINUTES_ROUTE = "/membership/minutes/";
const MINUTES_DATA_URL = "/membership/minutes/data/";

function isoDaysAgoUtc(days: number): string {
  const date = new Date();
  date.setUTCDate(date.getUTCDate() - days);
  return date.toISOString().slice(0, 10);
}

function formatLong(isoDate: string): string {
  return new Intl.DateTimeFormat("en-US", {
    year: "numeric",
    month: "long",
    day: "numeric",
    timeZone: "UTC",
  }).format(new Date(`${isoDate}T00:00:00Z`));
}

async function loginViaForm(page: Page, username: string, password: string): Promise<void> {
  await page.goto("/login/");
  await page.getByLabel("Username", { exact: true }).fill(username);
  await page.getByLabel("Password", { exact: true }).fill(password);
  await page.getByRole("button", { name: /log in/i }).click();
  await page.waitForURL((url) => !url.pathname.endsWith("/login/"));
  await expect(page.getByRole("link", { name: username, exact: true })).toBeVisible();
}

async function generate(page: Page, start: string, end: string): Promise<void> {
  await page.fill("#minutes-start", start);
  await page.fill("#minutes-end", end);
  const responsePromise = page.waitForResponse(
    (response) =>
      response.url().includes(MINUTES_DATA_URL) && response.request().method() === "GET",
  );
  await page.getByRole("button", { name: /generate minutes/i }).click();
  await responsePromise;
}

test.describe("membership minutes generator", () => {
  test.beforeEach(async ({ page }) => {
    await loginViaForm(page, "regular01", "password");
  });

  test("is reachable from the Membership Management sidebar", async ({ page }) => {
    await page.goto("/membership/requests/");
    const link = page.getByRole("link", { name: "Minutes Generator" });
    await expect(link).toBeVisible();
    await link.click();
    await page.waitForURL((url) => url.pathname === MINUTES_ROUTE);
    await expect(page.locator("[data-membership-minutes-root]")).toBeVisible();
  });

  test("generates formatted minutes for a date range and syncs the URL", async ({ page }) => {
    const end = isoDaysAgoUtc(0);
    const start = isoDaysAgoUtc(120);

    await page.goto(MINUTES_ROUTE);
    await expect(page.locator("[data-membership-minutes-root]")).toBeVisible();

    await generate(page, start, end);

    const content = page.locator(".minutes-content");
    await expect(content).toBeVisible();

    // Summary heading uses the human-formatted selected dates.
    await expect(content).toContainText(
      `Summary of requests between ${formatLong(start)} and ${formatLong(end)}:`,
    );
    // Outcome breakdown and all three category sections are present.
    await expect(content).toContainText("Outcome of requests:");
    await expect(content).toContainText("Further information requested:");
    await expect(content).toContainText("Requests declined:");
    await expect(content).toContainText("Requests accepted:");
    await expect(content).toContainText("Review existing individual applicants");
    await expect(content).toContainText("mirror applications");
    await expect(content).toContainText("Review pending sponsor applications.");

    // The committee reset seeds decided requests, so at least one links out.
    await expect(content.locator('a[href*="/membership/request/"]').first()).toBeVisible();

    // Generating rewrites the URL so the page is shareable.
    await expect(page).toHaveURL(new RegExp(`start=${start}&end=${end}`));
  });

  test("auto-generates from URL parameters on load", async ({ page }) => {
    const end = isoDaysAgoUtc(0);
    const start = isoDaysAgoUtc(120);

    const responsePromise = page.waitForResponse(
      (response) =>
        response.url().includes(MINUTES_DATA_URL) && response.request().method() === "GET",
    );
    await page.goto(`${MINUTES_ROUTE}?start=${start}&end=${end}`);
    await responsePromise;

    const content = page.locator(".minutes-content");
    await expect(content).toBeVisible();
    await expect(content).toContainText(
      `Summary of requests between ${formatLong(start)} and ${formatLong(end)}:`,
    );
  });

  test("prevents choosing an end date beyond today", async ({ page }) => {
    await page.goto(MINUTES_ROUTE);
    const today = isoDaysAgoUtc(0);
    await expect(page.locator("#minutes-end")).toHaveAttribute("max", today);
  });

  test("API rejects future end dates and oversized ranges", async ({ page }) => {
    const today = isoDaysAgoUtc(0);
    const tomorrow = isoDaysAgoUtc(-1);

    const futureResp = await page.request.get(`${MINUTES_DATA_URL}?start=${today}&end=${tomorrow}`);
    expect(futureResp.status()).toBe(400);
    expect(((await futureResp.json()) as { error: string }).error.toLowerCase()).toContain("future");

    // > 550 days span (the MEMBERSHIP_MINUTES_MAX_RANGE_DAYS cap).
    const farStart = isoDaysAgoUtc(600);
    const oversizedResp = await page.request.get(`${MINUTES_DATA_URL}?start=${farStart}&end=${today}`);
    expect(oversizedResp.status()).toBe(400);
    expect(((await oversizedResp.json()) as { error: string }).error).toContain("550");

    const okResp = await page.request.get(`${MINUTES_DATA_URL}?start=${isoDaysAgoUtc(30)}&end=${today}`);
    expect(okResp.status()).toBe(200);
    const payload = (await okResp.json()) as { summary: { received_or_updated: number } };
    expect(payload.summary).toHaveProperty("received_or_updated");
  });

  test("copies the minutes to the clipboard as rich HTML and plain text", async ({ page }) => {
    await page.context().grantPermissions(["clipboard-read", "clipboard-write"]);

    await page.goto(MINUTES_ROUTE);
    await generate(page, isoDaysAgoUtc(120), isoDaysAgoUtc(0));
    await expect(page.locator(".minutes-content")).toBeVisible();

    await page.getByRole("button", { name: /copy to clipboard/i }).click();
    // The button confirms the copy succeeded.
    await expect(page.getByRole("button", { name: "Copied!" })).toBeVisible();

    // Plain-text flavour carries the readable minutes.
    const plain = await page.evaluate(() => navigator.clipboard.readText());
    expect(plain).toContain("Summary of requests between");

    // HTML flavour carries the formatted list with request links, so a paste
    // into Google Docs keeps its structure.
    const html = await page.evaluate(async () => {
      const items = await navigator.clipboard.read();
      for (const item of items) {
        if (item.types.includes("text/html")) {
          return (await item.getType("text/html")).text();
        }
      }
      return "";
    });
    expect(html).toContain("<ul>");
    expect(html).toContain('<a href="');
  });
});

import { afterEach, describe, expect, it, vi } from "vitest";

async function loadPlaywrightConfig() {
  vi.resetModules();
  return (await import("../../../playwright.config.ts")).default;
}

describe("playwright config", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it("defaults to the isolated e2e stack URL", async () => {
    vi.stubEnv("PLAYWRIGHT_BASE_URL", "");

    const config = await loadPlaywrightConfig();

    expect(config.use?.baseURL).toBe("http://127.0.0.1:18000");
  });

  it("still allows an explicit PLAYWRIGHT_BASE_URL override", async () => {
    vi.stubEnv("PLAYWRIGHT_BASE_URL", "http://127.0.0.1:8000");

    const config = await loadPlaywrightConfig();

    expect(config.use?.baseURL).toBe("http://127.0.0.1:8000");
  });
});
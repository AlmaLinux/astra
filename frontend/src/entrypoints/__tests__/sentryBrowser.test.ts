import { afterEach, describe, expect, it, vi } from "vitest";

const attachSentryFeedbackTrigger = vi.fn();

vi.mock("../../shared/sentryFeedback", () => ({
  attachSentryFeedbackTrigger,
}));

describe("sentryBrowser entrypoint", () => {
  afterEach(() => {
    document.head.innerHTML = "";
    document.body.innerHTML = "";
    attachSentryFeedbackTrigger.mockReset();
    vi.restoreAllMocks();
    vi.resetModules();
  });

  it("binds the shared footer support control globally on non-blocked pages", async () => {
    document.body.innerHTML = `
      <footer>
        <a href="mailto:support@example.com" data-sentry-feedback-link="">Contact Support</a>
      </footer>
    `;

    await import("../sentryBrowser");
    document.dispatchEvent(new Event("DOMContentLoaded"));

    expect(attachSentryFeedbackTrigger).toHaveBeenCalledTimes(1);
    expect(attachSentryFeedbackTrigger).toHaveBeenCalledWith(
      document.body,
      {
        allowScreenshot: true,
        surface: "global-footer",
      },
    );
  });

  it("binds immediately when the module loads after DOMContentLoaded at interactive readyState", async () => {
    vi.spyOn(document, "readyState", "get").mockReturnValue("interactive");
    document.body.innerHTML = `
      <footer>
        <a href="mailto:support@example.com" data-sentry-feedback-link="">Contact Support</a>
      </footer>
    `;

    await import("../sentryBrowser");

    expect(attachSentryFeedbackTrigger).toHaveBeenCalledTimes(1);
    expect(attachSentryFeedbackTrigger).toHaveBeenCalledWith(
      document.body,
      {
        allowScreenshot: true,
        surface: "global-footer",
      },
    );
  });

  it("still binds the shared footer support control on replay-disabled pages", async () => {
    document.head.innerHTML = '<meta name="sentry-replay-disabled" content="true">';
    document.body.innerHTML = `
      <footer>
        <a href="mailto:support@example.com" data-sentry-feedback-link="">Contact Support</a>
      </footer>
    `;

    await import("../sentryBrowser");
    document.dispatchEvent(new Event("DOMContentLoaded"));

    expect(attachSentryFeedbackTrigger).toHaveBeenCalledTimes(1);
    expect(attachSentryFeedbackTrigger).toHaveBeenCalledWith(
      document.body,
      {
        allowScreenshot: true,
        surface: "global-footer",
      },
    );
  });

  it("retries binding when Sentry becomes ready after the first attempt", async () => {
    document.body.innerHTML = `
      <footer>
        <a href="mailto:support@example.com" data-sentry-feedback-link="">Contact Support</a>
      </footer>
    `;
    attachSentryFeedbackTrigger
      .mockReturnValueOnce(false)
      .mockReturnValueOnce(true);

    await import("../sentryBrowser");

    expect(attachSentryFeedbackTrigger).toHaveBeenCalledTimes(1);

    document.dispatchEvent(new CustomEvent("astra:sentry-ready"));

    expect(attachSentryFeedbackTrigger).toHaveBeenCalledTimes(2);
    expect(attachSentryFeedbackTrigger).toHaveBeenNthCalledWith(
      2,
      document.body,
      {
        allowScreenshot: true,
        surface: "global-footer",
      },
    );
  });
});
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

  it("does not bind the shared footer support control on blocked pages", async () => {
    document.head.innerHTML = '<meta name="sentry-capture-disabled" content="true">';
    document.body.innerHTML = `
      <footer>
        <a href="mailto:support@example.com" data-sentry-feedback-link="">Contact Support</a>
      </footer>
    `;

    await import("../sentryBrowser");
    document.dispatchEvent(new Event("DOMContentLoaded"));

    expect(attachSentryFeedbackTrigger).not.toHaveBeenCalled();
  });
});
import { afterEach, describe, expect, it, vi } from "vitest";

import { attachSentryFeedbackTrigger } from "../sentryFeedback";

function buildRoot(): HTMLDivElement {
  const root = document.createElement("div");
  document.body.appendChild(root);
  return root;
}

function buildFooterLink(): HTMLAnchorElement {
  const footer = document.createElement("footer");
  footer.innerHTML = `
    <span class="d-none" data-sentry-feedback-footer="" data-sentry-feedback-hidden="true">
      <span class="mx-2">·</span>
      <a
        class="text-muted"
        href="#"
        data-sentry-feedback-link=""
        data-sentry-feedback-hidden="true"
        aria-hidden="true"
        tabindex="-1"
      >Report a bug</a>
    </span>
    <span class="mx-2">·</span>
  `;
  document.body.appendChild(footer);
  return footer.querySelector("[data-sentry-feedback-link]") as HTMLAnchorElement;
}

describe("attachSentryFeedbackTrigger", () => {
  afterEach(() => {
    document.body.innerHTML = "";
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("attaches a report issue trigger on eligible roots and enables screenshots only when allowed", () => {
    const attachTo = vi.fn();
    vi.stubGlobal("window", window);
    vi.stubGlobal("Sentry", {
      getFeedback: () => ({ attachTo }),
    });
    (window as typeof window & { Sentry?: unknown }).Sentry = {
      getFeedback: () => ({ attachTo }),
    };

    const root = buildRoot();
    const footerLink = buildFooterLink();
    const footerWrapper = document.querySelector("[data-sentry-feedback-footer]") as HTMLSpanElement;

    const attached = attachSentryFeedbackTrigger(root, {
      allowScreenshot: true,
      surface: "groups-detail",
    });

    expect(attached).toBe(true);
    expect(root.querySelector("[data-sentry-feedback-trigger]" )).toBeNull();
    expect(footerLink.textContent).toBe("Report a bug");
    expect(footerWrapper.classList.contains("d-none")).toBe(false);
    expect(footerWrapper.getAttribute("data-sentry-feedback-hidden")).toBe("false");
    expect(footerLink.getAttribute("data-sentry-feedback-hidden")).toBe("false");
    expect(footerLink.hasAttribute("aria-hidden")).toBe(false);
    expect(footerLink.hasAttribute("tabindex")).toBe(false);
    expect(attachTo).toHaveBeenCalledTimes(1);
    expect(attachTo).toHaveBeenCalledWith(
      footerLink,
      expect.objectContaining({
        enableScreenshot: true,
        showBranding: false,
        tags: expect.objectContaining({
          feedback_surface: "groups-detail",
        }),
      }),
    );
  });

  it("does not attach feedback on blocked roots", () => {
    const attachTo = vi.fn();
    (window as typeof window & { Sentry?: unknown }).Sentry = {
      getFeedback: () => ({ attachTo }),
    };

    const root = buildRoot();
    const footerLink = buildFooterLink();
    const footerWrapper = document.querySelector("[data-sentry-feedback-footer]") as HTMLSpanElement;
    root.setAttribute("data-sentry-capture-disabled", "");

    const attached = attachSentryFeedbackTrigger(root, {
      allowScreenshot: false,
      surface: "settings",
    });

    expect(attached).toBe(false);
    expect(root.querySelector("[data-sentry-feedback-trigger]")).toBeNull();
    expect(footerWrapper.classList.contains("d-none")).toBe(true);
    expect(footerWrapper.getAttribute("data-sentry-feedback-hidden")).toBe("true");
    expect(footerLink.getAttribute("data-sentry-feedback-hidden")).toBe("true");
    expect(attachTo).not.toHaveBeenCalled();
  });

  it("does not attach feedback when the mounted root sits inside a blocked ancestor boundary", () => {
    const attachTo = vi.fn();
    (window as typeof window & { Sentry?: unknown }).Sentry = {
      getFeedback: () => ({ attachTo }),
    };

    const blockedBoundary = document.createElement("section");
    blockedBoundary.setAttribute("data-sentry-capture-disabled", "");
    document.body.appendChild(blockedBoundary);

    const root = buildRoot();
    const footerLink = buildFooterLink();
    const footerWrapper = document.querySelector("[data-sentry-feedback-footer]") as HTMLSpanElement;
    blockedBoundary.appendChild(root);

    const attached = attachSentryFeedbackTrigger(root, {
      allowScreenshot: false,
      surface: "auth-recovery",
    });

    expect(attached).toBe(false);
    expect(root.querySelector("[data-sentry-feedback-trigger]")).toBeNull();
    expect(footerWrapper.classList.contains("d-none")).toBe(true);
    expect(footerLink.getAttribute("data-sentry-feedback-hidden")).toBe("true");
    expect(attachTo).not.toHaveBeenCalled();
  });

  it("does not create a page trigger when the shared footer link is absent", () => {
    const attachTo = vi.fn();
    (window as typeof window & { Sentry?: unknown }).Sentry = {
      getFeedback: () => ({ attachTo }),
    };

    const root = buildRoot();

    const attached = attachSentryFeedbackTrigger(root, {
      allowScreenshot: true,
      surface: "group-detail",
    });

    expect(attached).toBe(false);
    expect(root.querySelector("[data-sentry-feedback-trigger]")).toBeNull();
    expect(attachTo).not.toHaveBeenCalled();
  });
});
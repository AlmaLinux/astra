import { afterEach, describe, expect, it, vi } from "vitest";

import { attachSentryFeedbackTrigger } from "../sentryFeedback";

function buildRoot(): HTMLDivElement {
  const root = document.createElement("div");
  document.body.appendChild(root);
  return root;
}

function buildFooterLink(): HTMLElement {
  const footer = document.createElement("footer");
  footer.innerHTML = `
    <a
      class="text-muted"
      href="mailto:support@example.com"
      data-sentry-feedback-link=""
    >Contact Support</a>
    <span class="mx-2">·</span>
  `;
  document.body.appendChild(footer);
  return footer.querySelector("[data-sentry-feedback-link]") as HTMLElement;
}

function buildHiddenAnonymousFooterButton(): HTMLElement {
  const footer = document.createElement("footer");
  footer.innerHTML = `
    <span class="d-none" data-sentry-feedback-footer="" data-sentry-feedback-hidden="true">
      <button
        type="button"
        class="btn btn-link text-muted p-0 border-0 align-baseline"
        data-sentry-feedback-link=""
      >Contact Support</button>
    </span>
  `;
  document.body.appendChild(footer);
  return footer.querySelector("[data-sentry-feedback-link]") as HTMLElement;
}

function buildFeedbackShadowForm(): ShadowRoot {
  const host = document.createElement("div");
  document.body.appendChild(host);
  const shadowRoot = host.attachShadow({ mode: "open" });
  shadowRoot.innerHTML = `
    <div class="dialog__content">
      <form class="form">
        <fieldset class="form__right" data-sentry-feedback="true"></fieldset>
      </form>
    </div>
  `;
  return shadowRoot;
}

describe("attachSentryFeedbackTrigger", () => {
  afterEach(() => {
    document.body.innerHTML = "";
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("repurposes the shared contact-support link on eligible roots and injects a modal fallback email link", () => {
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

    const attached = attachSentryFeedbackTrigger(root, {
      allowScreenshot: true,
      surface: "groups-detail",
    });

    expect(attached).toBe(true);
    expect(root.querySelector("[data-sentry-feedback-trigger]" )).toBeNull();
    expect(footerLink.textContent).toBe("Contact Support");
    expect(footerLink.getAttribute("href")).toBe("mailto:support@example.com");

    const clickEvent = new MouseEvent("click", { bubbles: true, cancelable: true });
    footerLink.dispatchEvent(clickEvent);
    expect(clickEvent.defaultPrevented).toBe(true);

    expect(attachTo).toHaveBeenCalledTimes(1);
    expect(attachTo).toHaveBeenCalledWith(
      footerLink,
      expect.objectContaining({
        enableScreenshot: true,
        onFormOpen: expect.any(Function),
        showBranding: false,
        tags: expect.objectContaining({
          feedback_surface: "groups-detail",
        }),
      }),
    );

    const attachOptions = attachTo.mock.calls[0]?.[1];
    expect(attachOptions).toBeDefined();

    const shadowRoot = buildFeedbackShadowForm();
    attachOptions.onFormOpen();

    const fallback = shadowRoot.querySelector("[data-astra-support-fallback]") as HTMLParagraphElement | null;
    expect(fallback).not.toBeNull();
    expect(fallback?.textContent).toContain("Prefer email?");

    const fallbackLink = shadowRoot.querySelector("[data-astra-support-fallback-link]") as HTMLAnchorElement | null;
    expect(fallbackLink?.textContent).toBe("Email support");
    expect(fallbackLink?.getAttribute("href")).toBe("mailto:support@example.com");

    attachOptions.onFormOpen();
    expect(shadowRoot.querySelectorAll("[data-astra-support-fallback]")).toHaveLength(1);
  });

  it("reveals the hidden anonymous contact-support scaffold only after feedback binds", () => {
    const attachTo = vi.fn();
    (window as typeof window & { Sentry?: unknown }).Sentry = {
      getFeedback: () => ({ attachTo }),
    };

    const root = buildRoot();
    const footerLink = buildHiddenAnonymousFooterButton();
    const footerWrapper = document.querySelector("[data-sentry-feedback-footer]") as HTMLElement;

    expect(footerWrapper.classList.contains("d-none")).toBe(true);
    expect(footerWrapper.getAttribute("data-sentry-feedback-hidden")).toBe("true");

    const attached = attachSentryFeedbackTrigger(root, {
      allowScreenshot: false,
      surface: "anonymous-groups",
    });

    expect(attached).toBe(true);
    expect(footerLink.textContent).toBe("Contact Support");
    expect(footerLink.getAttribute("href")).toBeNull();
    expect(footerWrapper.classList.contains("d-none")).toBe(false);
    expect(footerWrapper.getAttribute("data-sentry-feedback-hidden")).toBe("false");
    expect(attachTo).toHaveBeenCalledWith(
      footerLink,
      expect.objectContaining({
        enableScreenshot: false,
        tags: expect.objectContaining({
          feedback_surface: "anonymous-groups",
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
    root.setAttribute("data-sentry-capture-disabled", "");

    const attached = attachSentryFeedbackTrigger(root, {
      allowScreenshot: false,
      surface: "settings",
    });

    expect(attached).toBe(false);
    expect(root.querySelector("[data-sentry-feedback-trigger]")).toBeNull();
    expect(footerLink.getAttribute("href")).toBe("mailto:support@example.com");

    const clickEvent = new MouseEvent("click", { bubbles: true, cancelable: true });
    footerLink.dispatchEvent(clickEvent);
    expect(clickEvent.defaultPrevented).toBe(false);
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
    blockedBoundary.appendChild(root);

    const attached = attachSentryFeedbackTrigger(root, {
      allowScreenshot: false,
      surface: "auth-recovery",
    });

    expect(attached).toBe(false);
    expect(root.querySelector("[data-sentry-feedback-trigger]")).toBeNull();
    expect(footerLink.getAttribute("href")).toBe("mailto:support@example.com");
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
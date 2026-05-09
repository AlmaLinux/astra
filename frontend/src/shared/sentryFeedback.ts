type SentryFeedbackAttachmentOptions = {
  autoInject?: boolean;
  enableScreenshot?: boolean;
  onFormOpen?: () => void;
  onFormSubmitted?: () => void;
  showBranding?: boolean;
  showEmail?: boolean;
  showName?: boolean;
  tags?: Record<string, string>;
  useSentryUser?: boolean;
};

type SentryFeedbackHandle = {
  attachTo: (element: Element, options?: SentryFeedbackAttachmentOptions) => void;
};

type SentryGlobal = {
  getFeedback?: () => SentryFeedbackHandle | null;
};

export type SentryFeedbackTriggerOptions = {
  allowScreenshot: boolean;
  surface: string;
};

const SUPPORT_FALLBACK_SELECTOR = "[data-astra-support-fallback]";
const SUPPORT_FALLBACK_MAX_ATTEMPTS = 10;

function findFeedbackShadowRoot(): ShadowRoot | null {
  for (const element of document.querySelectorAll<HTMLElement>("body *")) {
    const shadowRoot = element.shadowRoot;

    if (
      shadowRoot !== null
      && (
        shadowRoot.querySelector("form.form") !== null
        || shadowRoot.querySelector('[data-sentry-feedback="true"]') !== null
      )
    ) {
      return shadowRoot;
    }
  }

  return null;
}

function injectSupportFallbackIntoDialog(footerLink: HTMLElement): boolean {
  const mailtoHref = footerLink.getAttribute("href");
  if (mailtoHref === null || !mailtoHref.startsWith("mailto:")) {
    return true;
  }

  const shadowRoot = findFeedbackShadowRoot();
  if (shadowRoot === null) {
    return false;
  }

  const target = shadowRoot.querySelector<HTMLElement>('[data-sentry-feedback="true"]')
    ?? shadowRoot.querySelector<HTMLElement>("form.form")
    ?? shadowRoot.querySelector<HTMLElement>(".dialog__content");
  if (target === null || target.querySelector(SUPPORT_FALLBACK_SELECTOR) !== null) {
    return target !== null;
  }

  // The SDK exposes form lifecycle hooks but no stable footer slot, so Astra injects
  // one minimal fallback mailto note into the rendered dialog when it opens.
  const fallback = document.createElement("p");
  fallback.setAttribute("data-astra-support-fallback", "");
  fallback.style.cssText = "margin: 0.75rem 0 0; font-size: 0.875rem; line-height: 1.4; opacity: 0.72;";
  fallback.append(document.createTextNode("Prefer email? "));

  const fallbackLink = document.createElement("a");
  fallbackLink.setAttribute("data-astra-support-fallback-link", "");
  fallbackLink.href = mailtoHref;
  fallbackLink.textContent = "Email support";
  fallbackLink.style.cssText = "color: inherit; text-decoration: underline;";
  fallback.append(fallbackLink, document.createTextNode("."));

  target.append(fallback);
  return true;
}

function injectSupportFallbackWhenReady(
  footerLink: HTMLElement,
  attemptsRemaining: number = SUPPORT_FALLBACK_MAX_ATTEMPTS,
): void {
  if (injectSupportFallbackIntoDialog(footerLink) || attemptsRemaining <= 1) {
    return;
  }

  window.setTimeout(() => {
    injectSupportFallbackWhenReady(footerLink, attemptsRemaining - 1);
  }, 0);
}

function getSentryGlobal(): SentryGlobal | undefined {
  return (window as Window & { Sentry?: SentryGlobal }).Sentry;
}

export function attachSentryFeedbackTrigger(
  root: HTMLElement | null,
  options: SentryFeedbackTriggerOptions,
): boolean {
  if (root === null) {
    return false;
  }

  const footerLink = document.querySelector<HTMLElement>("[data-sentry-feedback-link]");
  if (footerLink === null) {
    return false;
  }
  const footerWrapper = footerLink.closest<HTMLElement>("[data-sentry-feedback-footer]");

  const sentry = getSentryGlobal();
  const feedback = sentry?.getFeedback?.();
  if (feedback === null || feedback === undefined) {
    return false;
  }

  footerWrapper?.classList.remove("d-none");
  footerWrapper?.setAttribute("data-sentry-feedback-hidden", "false");

  if (footerLink.getAttribute("data-sentry-feedback-click-bound") !== "true") {
    footerLink.addEventListener("click", (event: MouseEvent) => {
      event.preventDefault();
    });
    footerLink.setAttribute("data-sentry-feedback-click-bound", "true");
  }

  if (footerLink.getAttribute("data-sentry-feedback-bound") === "true") {
    return true;
  }

  feedback.attachTo(footerLink, {
    autoInject: false,
    enableScreenshot: options.allowScreenshot,
    onFormOpen: () => {
      injectSupportFallbackWhenReady(footerLink);
    },
    showBranding: false,
    showEmail: true,
    showName: true,
    tags: {
      feedback_path: window.location.pathname,
      feedback_screenshot_enabled: options.allowScreenshot ? "true" : "false",
      feedback_surface: options.surface,
    },
    useSentryUser: false,
  });
  footerLink.setAttribute("data-sentry-feedback-bound", "true");
  return true;
}
type SentryFeedbackAttachmentOptions = {
  autoInject?: boolean;
  enableScreenshot?: boolean;
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

function getSentryGlobal(): SentryGlobal | undefined {
  return (window as Window & { Sentry?: SentryGlobal }).Sentry;
}

export function attachSentryFeedbackTrigger(
  root: HTMLElement | null,
  options: SentryFeedbackTriggerOptions,
): boolean {
  if (root === null || root.closest("[data-sentry-capture-disabled]") !== null) {
    return false;
  }

  const footerLink = document.querySelector<HTMLAnchorElement>("[data-sentry-feedback-link]");
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
  footerLink.setAttribute("data-sentry-feedback-hidden", "false");
  footerLink.removeAttribute("aria-hidden");
  footerLink.removeAttribute("tabindex");

  if (footerLink.getAttribute("data-sentry-feedback-bound") === "true") {
    return true;
  }

  feedback.attachTo(footerLink, {
    autoInject: false,
    enableScreenshot: options.allowScreenshot,
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
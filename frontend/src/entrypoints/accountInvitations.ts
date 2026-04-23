/**
 * Entrypoint for account invitations Vue 3 module.
 * Hydrates the page when required bootstrap data is present.
 */

import { createApp } from "vue";
import AccountInvitationsPage from "../account-invitations/AccountInvitationsPage.vue";
import { readAccountInvitationsBootstrap } from "../account-invitations/types";

let pageComponentInstance: InstanceType<typeof AccountInvitationsPage> | null = null;

export function initAccountInvitations(): void {
  // Only proceed if bootstrap data is present
  const bootstrap = readAccountInvitationsBootstrap();
  if (!bootstrap) {
    console.debug("Account invitations bootstrap data not found; Vue app not initialized");
    return;
  }

  // Find root element
  const root = document.querySelector("[data-account-invitations-root]");
  if (!root) {
    console.debug("Account invitations root element not found");
    return;
  }

  // Create and mount Vue app
  try {
    const app = createApp(AccountInvitationsPage, { bootstrap });
    const instance = app.mount(root);
    pageComponentInstance = instance as any;

    // Wire the refresh form from the header
    const refreshForm = document.querySelector("[data-account-invitations-refresh-target]");
    if (refreshForm) {
      refreshForm.addEventListener("submit", (event) => {
        event.preventDefault();
        if (pageComponentInstance && typeof (pageComponentInstance as any).handleRefresh === "function") {
          (pageComponentInstance as any).handleRefresh();
        }
      });
    }
  } catch (err) {
    console.error("Failed to initialize account invitations Vue app:", err);
  }
}

// Auto-initialize if called directly (not as a module)
if (typeof window !== "undefined" && document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", initAccountInvitations);
} else if (typeof window !== "undefined") {
  initAccountInvitations();
}

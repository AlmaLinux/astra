<script setup lang="ts">
import { onBeforeUnmount, onMounted } from "vue";

let clockIntervalId: number | null = null;
let dismissListener: ((event: Event) => void) | null = null;
let dismissNode: HTMLElement | null = null;

function initializeRecommendedAlertDismissal(): void {
  dismissNode = document.getElementById("account-setup-recommended-alert");
  if (!(dismissNode instanceof HTMLElement)) {
    dismissNode = null;
    return;
  }

  const dismissKey = dismissNode.dataset.dismissKey || "";
  if (!dismissKey) {
    dismissNode = null;
    return;
  }

  try {
    if (window.localStorage && window.localStorage.getItem(dismissKey) === "1") {
      dismissNode.remove();
      dismissNode = null;
      return;
    }
  } catch {
    // Keep the alert visible when storage is unavailable.
  }

  dismissListener = () => {
    try {
      if (window.localStorage) {
        window.localStorage.setItem(dismissKey, "1");
      }
    } catch {
      // Ignore storage failures.
    }
  };
  dismissNode.addEventListener("closed.bs.alert", dismissListener);
}

function initializeTimezoneClock(): void {
  const timezoneNode = document.getElementById("user-timezone");
  const timeNode = document.getElementById("user-time");
  if (!(timezoneNode instanceof HTMLElement) || !(timeNode instanceof HTMLElement)) {
    return;
  }

  const timezone = timezoneNode.dataset.timezone || "";
  if (!timezone) {
    return;
  }

  const updateTime = (): void => {
    try {
      timeNode.textContent = new Date().toLocaleString(undefined, {
        timeZone: timezone,
        weekday: "long",
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
      });
    } catch {
      // Keep the server-rendered value if the timezone is unsupported.
    }
  };

  updateTime();
  clockIntervalId = window.setInterval(updateTime, 1000);
}

onMounted(() => {
  initializeRecommendedAlertDismissal();
  initializeTimezoneClock();
});

onBeforeUnmount(() => {
  if (clockIntervalId !== null) {
    window.clearInterval(clockIntervalId);
  }

  if (dismissNode && dismissListener) {
    dismissNode.removeEventListener("closed.bs.alert", dismissListener);
  }
});
</script>

<template>
  <div data-user-profile-controller-root hidden aria-hidden="true" />
</template>
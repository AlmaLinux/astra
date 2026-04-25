<script setup lang="ts">
import { onBeforeUnmount, onMounted } from "vue";

import type { IneligibleVoterModalBootstrap } from "./types";

const props = defineProps<{
  bootstrap: IneligibleVoterModalBootstrap;
}>();

type IneligibleVoterDetails = {
  reason?: string;
  term_start_date?: string;
  election_start_date?: string;
  days_at_start?: number | null;
  days_short?: number | null;
};

function readDetails(): Record<string, IneligibleVoterDetails> {
  const detailsEl = document.getElementById(props.bootstrap.detailsJsonId);
  if (!detailsEl?.textContent) {
    return {};
  }
  try {
    const raw = JSON.parse(detailsEl.textContent) as unknown;
    if (raw && typeof raw === "object") {
      return raw as Record<string, IneligibleVoterDetails>;
    }
  } catch {
    return {};
  }
  return {};
}

function reasonText(reason: string): string {
  if (reason === "no_membership") return "No qualifying membership or sponsorship found.";
  if (reason === "expired") return "Membership or sponsorship was not active at the reference date.";
  if (reason === "too_new") return "Membership or sponsorship is active, but too new at the reference date.";
  return reason;
}

function showModal(modalEl: HTMLElement): void {
  modalEl.classList.add("show", "d-block");
  modalEl.setAttribute("aria-hidden", "false");
  let backdrop = document.querySelector<HTMLElement>('[data-ineligible-voter-backdrop="true"]');
  if (!backdrop) {
    backdrop = document.createElement("div");
    backdrop.className = "modal-backdrop fade show";
    backdrop.dataset.ineligibleVoterBackdrop = "true";
    document.body.appendChild(backdrop);
  }
}

function hideModal(modalEl: HTMLElement): void {
  modalEl.classList.remove("show", "d-block");
  modalEl.setAttribute("aria-hidden", "true");
  document.querySelector<HTMLElement>('[data-ineligible-voter-backdrop="true"]')?.remove();
}

function bindModalBehavior(): () => void {
  const details = readDetails();
  const card = document.getElementById(props.bootstrap.cardId);
  const modalEl = document.getElementById("ineligible-voter-modal");
  if (!card || !modalEl) {
    return () => {};
  }

  const usernameEl = modalEl.querySelector<HTMLElement>(".js-ineligible-username");
  const reasonEl = modalEl.querySelector<HTMLElement>(".js-ineligible-reason");
  const termEl = modalEl.querySelector<HTMLElement>(".js-ineligible-term-start");
  const electionEl = modalEl.querySelector<HTMLElement>(".js-ineligible-election-start");
  const daysAtStartEl = modalEl.querySelector<HTMLElement>(".js-ineligible-days-at-start");
  const daysShortEl = modalEl.querySelector<HTMLElement>(".js-ineligible-days-short");

  const openForUsername = (username: string): void => {
    const detail = details[username];
    if (!detail) {
      return;
    }
    if (usernameEl) usernameEl.textContent = username;
    if (reasonEl) reasonEl.textContent = reasonText(String(detail.reason || ""));
    if (termEl) termEl.textContent = String(detail.term_start_date || "");
    if (electionEl) electionEl.textContent = String(detail.election_start_date || "");
    if (daysAtStartEl) daysAtStartEl.textContent = String(detail.days_at_start == null ? "" : detail.days_at_start);
    if (daysShortEl) daysShortEl.textContent = String(detail.days_short == null ? "" : detail.days_short);
    showModal(modalEl);
  };

  const clickHandler = (event: Event): void => {
    const target = event.target as HTMLElement | null;
    const link = target?.closest("a[href]") as HTMLAnchorElement | null;
    if (!link) {
      return;
    }
    const href = String(link.getAttribute("href") || "");
    const match = href.match(/\/user\/([^/]+)\/?$/);
    if (!match?.[1]) {
      return;
    }
    const username = decodeURIComponent(match[1]);
    if (!Object.prototype.hasOwnProperty.call(details, username)) {
      return;
    }
    event.preventDefault();
    openForUsername(username);
  };

  const closeHandler = (event: Event): void => {
    const target = event.target as HTMLElement | null;
    if (!target?.matches('[data-dismiss="modal"], [data-dismiss="modal"] *')) {
      return;
    }
    hideModal(modalEl);
  };

  card.addEventListener("click", clickHandler);
  modalEl.addEventListener("click", closeHandler);

  return () => {
    card.removeEventListener("click", clickHandler);
    modalEl.removeEventListener("click", closeHandler);
    hideModal(modalEl);
  };
}

let teardown: (() => void) | null = null;

onMounted(() => {
  teardown = bindModalBehavior();
});

onBeforeUnmount(() => {
  teardown?.();
});
</script>

<template>
  <div data-ineligible-voter-modal-vue-root></div>
</template>
<script setup lang="ts">
import { onMounted } from "vue";

type JQueryCollection = {
  data: (key: string, value?: unknown) => unknown;
  attr: (name: string) => string | undefined;
  select2?: (options?: unknown) => void;
};

type JQueryFunction = ((target: Element | string) => JQueryCollection) & {
  fn?: {
    select2?: unknown;
  };
};

function getJQuery(): JQueryFunction | null {
  const maybeJQuery = (window as typeof window & { jQuery?: JQueryFunction; $?: JQueryFunction }).jQuery
    || (window as typeof window & { jQuery?: JQueryFunction; $?: JQueryFunction }).$;
  return maybeJQuery || null;
}

function initRepresentativeSelect2(): void {
  const jq = getJQuery();
  if (!jq || !jq.fn || !jq.fn.select2) {
    return;
  }

  document.querySelectorAll<HTMLSelectElement>("select.alx-select2").forEach((element) => {
    if (element.dataset.alxSelect2Initialized === "true") {
      return;
    }

    element.dataset.alxSelect2Initialized = "true";
    const $select = jq(element);
    const ajaxUrl = String($select.attr("data-ajax-url") || "").trim();
    $select.select2?.({
      width: "100%",
      placeholder: element.dataset.placeholder || "Search users…",
      allowClear: true,
      ajax: ajaxUrl
        ? {
            url: ajaxUrl,
            dataType: "json",
            delay: 200,
            data: (params: { term?: string }) => ({ q: params.term || "" }),
            processResults: (data: { results?: unknown[] }) => ({
              results: Array.isArray(data.results) ? data.results : [],
            }),
            cache: true,
          }
        : undefined,
      minimumInputLength: 1,
    });
  });
}

function activateTab(link: HTMLAnchorElement): void {
  const tabsEl = document.getElementById("contacts-tabs");
  const tabContent = document.getElementById("contacts-tab-content");
  if (!tabsEl || !tabContent) {
    return;
  }

  const targetId = link.getAttribute("href") || "";
  if (!targetId.startsWith("#")) {
    return;
  }

  tabsEl.querySelectorAll<HTMLAnchorElement>(".nav-link").forEach((candidate) => {
    const isActive = candidate === link;
    candidate.classList.toggle("active", isActive);
    candidate.setAttribute("aria-selected", isActive ? "true" : "false");
  });

  tabContent.querySelectorAll<HTMLElement>(".tab-pane").forEach((pane) => {
    const isActive = `#${pane.id}` === targetId;
    pane.classList.toggle("active", isActive);
    pane.classList.toggle("show", isActive);
  });
}

function focusInvalidField(field: HTMLElement | null): void {
  if (!field) {
    return;
  }

  if (field.classList.contains("select2-hidden-accessible")) {
    const adjacentContainer = field.nextElementSibling as HTMLElement | null;
    const selection = adjacentContainer?.classList.contains("select2-selection")
      ? adjacentContainer
      : adjacentContainer?.querySelector<HTMLElement>(".select2-selection")
        || field.parentElement?.querySelector<HTMLElement>(".select2-selection")
        || null;

    const scrollTarget = selection || adjacentContainer || field;
    scrollTarget.scrollIntoView?.({ block: "center" });

    const jq = getJQuery();
    if (jq && jq.fn && jq.fn.select2) {
      jq(field).select2?.("open");
      return;
    }

    selection?.focus?.();
    return;
  }

  field.scrollIntoView?.({ block: "center" });
  field.focus?.();
}

function refreshTabErrors(): { firstErrorLink: HTMLAnchorElement | null; firstErrorField: HTMLElement | null } {
  const tabsEl = document.getElementById("contacts-tabs");
  const tabContent = document.getElementById("contacts-tab-content");
  if (!tabsEl || !tabContent) {
    return { firstErrorLink: null, firstErrorField: null };
  }

  let firstErrorLink: HTMLAnchorElement | null = null;
  let firstErrorField: HTMLElement | null = null;

  tabContent.querySelectorAll<HTMLElement>(".tab-pane").forEach((pane) => {
    const invalidField = pane.querySelector<HTMLElement>(":invalid");
    const link = tabsEl.querySelector<HTMLAnchorElement>(`[href="#${pane.id}"]`);
    const hasError = invalidField !== null;
    if (hasError && !firstErrorField) {
      firstErrorField = invalidField;
    }
    if (link) {
      link.classList.toggle("alx-tab-error", hasError);
      if (hasError && !firstErrorLink) {
        firstErrorLink = link;
      }
    }
  });

  return { firstErrorLink, firstErrorField };
}

function initTabValidation(): void {
  const tabsEl = document.getElementById("contacts-tabs");
  const formEl = tabsEl?.closest("form");
  if (!tabsEl || !formEl) {
    return;
  }

  formEl.addEventListener("submit", () => {
    const { firstErrorLink, firstErrorField } = refreshTabErrors();
    if (!firstErrorLink) {
      return;
    }

    if (firstErrorLink.classList.contains("active")) {
      focusInvalidField(firstErrorField);
      return;
    }

    activateTab(firstErrorLink);
    setTimeout(() => {
      focusInvalidField(firstErrorField);
    }, 0);
  });
}

onMounted(() => {
  initRepresentativeSelect2();
  initTabValidation();
});
</script>

<template>
  <div data-organization-form-vue-root hidden></div>
</template>

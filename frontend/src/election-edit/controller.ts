type JQueryLike = {
  fn?: Record<string, unknown>;
  (target: string | Element | Document): {
    data?: (key: string, value?: unknown) => unknown;
    find?: (selector: string) => { each?: (callback: (index: number, element: Element) => void) => void };
    each?: (callback: (index: number, element: Element) => void) => void;
    on?: (...args: unknown[]) => void;
    val?: (value?: unknown) => unknown;
    trigger?: (eventName: string) => void;
    prop?: (name: string) => unknown;
    attr?: (name: string) => string | undefined;
    append?: (option: HTMLOptionElement) => void;
    select2?: (options: Record<string, unknown>) => void;
    datetimepicker?: (options: Record<string, unknown>) => void;
    modal?: (action: string) => void;
    closest?: (selector: string) => ArrayLike<Element>;
    length?: number;
  };
};

type TemplatedEmailCompose = {
  getField: (name: string) => string;
  getValues: () => Record<string, string>;
  setRestoreEnabled: (enabled: boolean) => void;
  markBaseline: (values: Record<string, string>) => void;
  getCsrfToken: () => string;
  getTemplateSelectEl?: () => HTMLSelectElement | null;
  getTemplateId?: () => string;
};

type TemplatedEmailComposePreview = {
  schedulePreviewRefresh: (compose: TemplatedEmailCompose | null, delayMs: number) => void;
  getComposeFromEvent: (event: Event) => TemplatedEmailCompose | null;
};

type WindowWithElectionEdit = Window & typeof globalThis & {
  jQuery?: JQueryLike;
  TemplatedEmailCompose?: TemplatedEmailCompose;
  TemplatedEmailComposePreview?: TemplatedEmailComposePreview;
};

function currentWindow(): WindowWithElectionEdit {
  return window as WindowWithElectionEdit;
}

function byId(id: string): HTMLElement | null {
  return document.getElementById(id);
}

function getCompose(): TemplatedEmailCompose | null {
  return currentWindow().TemplatedEmailCompose ?? null;
}

function getField(name: string): string {
  const compose = getCompose();
  return compose ? compose.getField(name) : "";
}

function scheduleEmailPreviewRefresh(compose: TemplatedEmailCompose | null, delayMs: number): void {
  const preview = currentWindow().TemplatedEmailComposePreview;
  if (!preview) {
    return;
  }
  preview.schedulePreviewRefresh(compose ?? getCompose(), delayMs);
}

function markComposeBaselineFromFields(): void {
  const compose = getCompose();
  if (!compose) {
    return;
  }
  compose.setRestoreEnabled(true);
  compose.markBaseline(compose.getValues());
}

function electionIdFromPath(): number | null {
  const parts = String(window.location.pathname || "").split("/").filter(Boolean);
  if (parts.length >= 3 && parts[0] === "elections" && parts[2] === "edit") {
    const value = Number.parseInt(parts[1] ?? "", 10);
    return Number.isFinite(value) ? value : null;
  }
  if (parts.length >= 2 && parts[0] === "elections" && parts[1] === "new") {
    return 0;
  }
  return null;
}

function eligibleUsersSearchUrlFromPath(): string | null {
  const electionId = electionIdFromPath();
  if (electionId === null) {
    return null;
  }
  return `/elections/${encodeURIComponent(String(electionId))}/eligible-users/search/`;
}

function eligibleVotersCountUrlFromPath(): string | null {
  const base = eligibleUsersSearchUrlFromPath();
  if (!base) {
    return null;
  }
  return `${base}?count_only=1`;
}

function jq(): JQueryLike | undefined {
  return currentWindow().jQuery;
}

function jqSupportsSelect2(jquery: JQueryLike | undefined): jquery is JQueryLike {
  return Boolean(jquery && jquery.fn && typeof jquery.fn.select2 === "function");
}

function collectSelectedCandidates(): Array<{ id: string; text: string }> {
  const selected = Array.from(document.querySelectorAll<HTMLSelectElement>('select[name^="candidates-"][name$="-freeipa_username"]'));
  const seen = new Set<string>();
  const results: Array<{ id: string; text: string }> = [];

  selected.forEach((select) => {
    const match = String(select.name || "").match(/^candidates-(\d+)-freeipa_username$/);
    if (!match) {
      return;
    }

    const deleteField = document.querySelector<HTMLInputElement>(`input[name="candidates-${match[1]}-DELETE"]`);
    if (deleteField?.checked) {
      return;
    }

    const username = String(select.value || "").trim();
    if (!username || seen.has(username)) {
      return;
    }
    seen.add(username);

    const selectedOption = select.selectedOptions.item(0);
    const label = String(selectedOption?.textContent || username).trim() || username;
    results.push({ id: username, text: label });
  });

  return results.sort((left, right) => left.id.localeCompare(right.id, undefined, { sensitivity: "base" }));
}

function syncGroupCandidateOptions(): void {
  const groupSelects = Array.from(document.querySelectorAll<HTMLSelectElement>('select[name^="groups-"][name$="-candidate_usernames"]'));
  if (!groupSelects.length) {
    return;
  }

  const candidates = collectSelectedCandidates();
  const allowed = new Map(candidates.map((candidate) => [candidate.id, candidate.text]));

  groupSelects.forEach((select) => {
    const selected = Array.from(select.selectedOptions).map((option) => String(option.value || "").trim());

    Array.from(select.options).forEach((option) => {
      const value = String(option.value || "").trim();
      if (value && !allowed.has(value)) {
        option.remove();
      }
    });

    candidates.forEach((candidate) => {
      const hasOption = Array.from(select.options).some((option) => String(option.value || "") === candidate.id);
      if (!hasOption) {
        select.append(new Option(candidate.text, candidate.id, false, false));
      }
    });

    const nextSelected = selected.filter((value) => allowed.has(value));
    Array.from(select.options).forEach((option) => {
      option.selected = nextSelected.includes(String(option.value || "").trim());
    });
    select.dispatchEvent(new Event("change", { bubbles: true }));
  });
}

function normalizeUsername(value: string | null | undefined): string {
  return String(value || "").trim().toLowerCase();
}

function clearSelectValue(selectEl: HTMLSelectElement | null): void {
  if (!selectEl) {
    return;
  }
  const jquery = jq();
  if (jquery) {
    const wrapped = jquery(selectEl);
    wrapped.val?.(null);
    wrapped.trigger?.("change");
    return;
  }
  selectEl.value = "";
  selectEl.dispatchEvent(new Event("change", { bubbles: true }));
}

function enforceNoSelfNomination(row: HTMLTableRowElement | null): void {
  if (!row) {
    return;
  }
  const candidateEl = row.querySelector<HTMLSelectElement>('select[name$="-freeipa_username"]');
  const nominatorEl = row.querySelector<HTMLSelectElement>('select[name$="-nominated_by"]');
  if (!candidateEl || !nominatorEl) {
    return;
  }
  const candidate = normalizeUsername(candidateEl.value);
  const nominator = normalizeUsername(nominatorEl.value);
  if (candidate && nominator && candidate === nominator) {
    clearSelectValue(nominatorEl);
  }
}

async function saveTemplate(templateId: string): Promise<void> {
  if (!templateId) {
    window.alert("Select a template to save, or use Save as.");
    return;
  }

  const data = new FormData();
  data.append("email_template_id", templateId);
  data.append("subject", getField("subject"));
  data.append("html_content", getField("html_content"));
  data.append("text_content", getField("text_content"));

  try {
    const response = await fetch("/email-tools/templates/save/", {
      method: "POST",
      headers: {
        "X-CSRFToken": getCompose()?.getCsrfToken() ?? "",
        Accept: "application/json",
      },
      body: data,
    });
    const payload = await response.json() as { ok?: boolean; error?: string };
    if (!response.ok || payload.ok !== true) {
      throw new Error(payload.error ?? "save failed");
    }
    markComposeBaselineFromFields();
    window.alert("Template saved.");
  } catch {
    window.alert("Failed to save template.");
  }
}

async function saveAsTemplate(name: string): Promise<{ id?: string | number; name?: string } | null> {
  if (!name) {
    return null;
  }

  const data = new FormData();
  data.append("name", name);
  data.append("subject", getField("subject"));
  data.append("html_content", getField("html_content"));
  data.append("text_content", getField("text_content"));

  try {
    const response = await fetch("/email-tools/templates/save-as/", {
      method: "POST",
      headers: {
        "X-CSRFToken": getCompose()?.getCsrfToken() ?? "",
        Accept: "application/json",
      },
      body: data,
    });
    const payload = await response.json() as { ok?: boolean; id?: string | number; name?: string };
    if (!response.ok || payload.ok !== true) {
      throw new Error("save as failed");
    }
    return payload;
  } catch {
    return null;
  }
}

function initSelect2(root: ParentNode = document): void {
  const jquery = jq();
  if (!jqSupportsSelect2(jquery)) {
    return;
  }

  root.querySelectorAll<HTMLSelectElement>("select.alx-select2").forEach((select) => {
    const wrapped = jquery(select);
    if (wrapped.data?.("alx-select2-initialized")) {
      return;
    }
    wrapped.data?.("alx-select2-initialized", true);

    let ajaxUrl = String(select.dataset.ajaxUrl || "").trim();
    let startSourceId = String(select.dataset.startDatetimeSource || "").trim();
    const name = String(select.name || "");
    const id = String(select.id || "");
    const looksLikeEligibleUserSelect = Boolean(
      startSourceId || /-freeipa_username$/.test(name) || /-nominated_by$/.test(name) || /-freeipa_username$/.test(id) || /-nominated_by$/.test(id),
    );
    const looksLikeCandidateGroupSelect = Boolean(/-candidate_usernames$/.test(name) || /-candidate_usernames$/.test(id));

    if (looksLikeCandidateGroupSelect) {
      return;
    }
    if (!startSourceId && looksLikeEligibleUserSelect && document.getElementById("id_start_datetime")) {
      startSourceId = "id_start_datetime";
    }
    if (!ajaxUrl && looksLikeEligibleUserSelect) {
      ajaxUrl = eligibleUsersSearchUrlFromPath() ?? "";
    }

    const placeholder = String(select.dataset.placeholder || "").trim();
    if (ajaxUrl) {
      wrapped.select2?.({
        width: "100%",
        allowClear: true,
        placeholder,
        minimumInputLength: 0,
        closeOnSelect: !select.multiple,
        ajax: {
          url: ajaxUrl,
          dataType: "json",
          delay: 200,
          data: (params: { term?: string } | undefined) => {
            const payload: Record<string, string> = {
              q: params?.term != null ? String(params.term) : "",
            };
            if (startSourceId) {
              const startEl = document.getElementById(startSourceId) as HTMLInputElement | null;
              if (startEl?.value) {
                payload.start_datetime = String(startEl.value);
              }
            }
            const groupEl = document.getElementById("id_eligible_group_cn") as HTMLSelectElement | null;
            payload.eligible_group_cn = String(groupEl?.value || "");
            return payload;
          },
        },
      });
      return;
    }

    wrapped.select2?.({ width: "100%", closeOnSelect: !select.multiple, allowClear: true, placeholder });
  });
}

function addFormsetRow(prefix: string, emptyTemplateId: string, tbodyId: string): void {
  const totalEl = byId(`id_${prefix}-TOTAL_FORMS`) as HTMLInputElement | null;
  const template = byId(emptyTemplateId) as HTMLTemplateElement | null;
  const tbody = byId(tbodyId);
  if (!totalEl || !template || !tbody) {
    return;
  }

  const total = Number.parseInt(String(totalEl.value || "0"), 10);
  const html = String(template.innerHTML || "").replaceAll("__prefix__", String(Number.isFinite(total) ? total : 0));
  const tempBody = document.createElement("tbody");
  tempBody.innerHTML = html;
  while (tempBody.firstElementChild) {
    tbody.appendChild(tempBody.firstElementChild);
  }

  totalEl.value = String((Number.isFinite(total) ? total : 0) + 1);
  initSelect2(tbody);
  syncGroupCandidateOptions();
}

async function refreshEligibleVotersCount(): Promise<void> {
  const baseUrl = eligibleVotersCountUrlFromPath();
  if (!baseUrl) {
    return;
  }
  const startValue = String((document.getElementById("id_start_datetime") as HTMLInputElement | null)?.value || "");
  const groupValue = String((document.getElementById("id_eligible_group_cn") as HTMLSelectElement | null)?.value || "");
  let fullUrl = baseUrl;
  if (startValue) {
    fullUrl += `&start_datetime=${encodeURIComponent(startValue)}`;
  }
  fullUrl += `&eligible_group_cn=${encodeURIComponent(groupValue)}`;

  try {
    const response = await fetch(fullUrl, { headers: { Accept: "application/json" } });
    if (!response.ok) {
      return;
    }
    const payload = await response.json() as { count?: number };
    if (typeof payload.count !== "number") {
      return;
    }
    document.querySelectorAll<HTMLElement>(".js-eligible-voters-count").forEach((node) => {
      node.textContent = String(payload.count);
    });
  } catch {
    // Ignore transient failures.
  }
}

async function isUsernameEligibleForCurrentGroup(username: string): Promise<boolean> {
  const normalized = String(username || "").trim();
  if (!normalized) {
    return false;
  }
  const baseUrl = eligibleUsersSearchUrlFromPath();
  if (!baseUrl) {
    return true;
  }

  const startValue = String((document.getElementById("id_start_datetime") as HTMLInputElement | null)?.value || "");
  const groupValue = String((document.getElementById("id_eligible_group_cn") as HTMLSelectElement | null)?.value || "");
  let fullUrl = `${baseUrl}?q=${encodeURIComponent(normalized)}`;
  if (startValue) {
    fullUrl += `&start_datetime=${encodeURIComponent(startValue)}`;
  }
  fullUrl += `&eligible_group_cn=${encodeURIComponent(groupValue)}`;

  try {
    const response = await fetch(fullUrl, { headers: { Accept: "application/json" } });
    if (!response.ok) {
      return true;
    }
    const payload = await response.json() as { results?: Array<{ id?: string }> };
    return Array.isArray(payload.results)
      ? payload.results.some((result) => String(result?.id || "").trim() === normalized)
      : true;
  } catch {
    return true;
  }
}

async function clearIneligibleSelectedCandidates(): Promise<void> {
  const selects = Array.from(document.querySelectorAll<HTMLSelectElement>('select[name^="candidates-"][name$="-freeipa_username"]'));
  for (const select of selects) {
    const username = String(select.value || "").trim();
    if (!username) {
      continue;
    }
    const isEligible = await isUsernameEligibleForCurrentGroup(username);
    if (!isEligible) {
      clearSelectValue(select);
    }
  }
}

function markRowDeleted(row: HTMLTableRowElement | null): void {
  if (!row) {
    return;
  }
  const deleteField = row.querySelector<HTMLInputElement>('input[name$="-DELETE"]');
  if (deleteField) {
    deleteField.checked = true;
    deleteField.value = "on";
    deleteField.dispatchEvent(new Event("change", { bubbles: true }));
  }
  row.style.display = "none";
}

function initDateTimePickers(): void {
  const jquery = jq();
  if (!jquery || !jquery.fn || typeof jquery.fn.datetimepicker !== "function") {
    return;
  }
  document.querySelectorAll<HTMLElement>(".js-datetime-picker").forEach((element) => {
    const wrapped = jquery(element);
    if (wrapped.data?.("datetimepicker")) {
      return;
    }
    wrapped.datetimepicker?.({
      icons: {
        time: "far fa-clock",
        date: "far fa-calendar",
        up: "fas fa-arrow-up",
        down: "fas fa-arrow-down",
        previous: "fas fa-chevron-left",
        next: "fas fa-chevron-right",
        today: "far fa-calendar-check",
        clear: "far fa-trash-alt",
        close: "far fa-times-circle",
      },
    });
  });
}

function findRow(target: EventTarget | null): HTMLTableRowElement | null {
  if (!(target instanceof Element)) {
    return null;
  }
  const row = target.closest("tr");
  return row instanceof HTMLTableRowElement ? row : null;
}

export function initElectionEditController(): void {
  const form = document.getElementById("election-edit-form") as HTMLFormElement | null;
  if (form?.dataset.electionEditControllerInitialized === "1") {
    return;
  }
  if (form) {
    form.dataset.electionEditControllerInitialized = "1";
  }

  function resetEmailSaveMode(): void {
    const saveModeEl = byId("election-edit-email-save-mode") as HTMLInputElement | null;
    if (saveModeEl) {
      saveModeEl.value = "";
    }
  }

  const compose = getCompose();
  const templateSelect = compose?.getTemplateSelectEl?.() ?? null;
  if (templateSelect) {
    templateSelect.addEventListener("change", () => {
      resetEmailSaveMode();
      if (!String(templateSelect.value || "").trim()) {
        return;
      }
      scheduleEmailPreviewRefresh(getCompose(), 50);
    });
  }

  document.addEventListener("templated-email-compose:save-confirmed", (event) => {
    const preview = currentWindow().TemplatedEmailComposePreview;
    const composeForEvent = preview?.getComposeFromEvent(event) ?? getCompose();
    const templateId = composeForEvent?.getTemplateId?.() ?? String(templateSelect?.value || "").trim();
    void saveTemplate(templateId);
  });

  document.addEventListener("templated-email-compose:save-as-confirmed", async (event) => {
    const detail = event instanceof CustomEvent ? event.detail : undefined;
    const name = String(detail?.name || "").trim();
    if (!name) {
      return;
    }

    const payload = await saveAsTemplate(name);
    if (!payload) {
      window.alert("Failed to create template.");
      return;
    }

    if (templateSelect && payload.id != null) {
      const option = new Option(String(payload.name || name), String(payload.id), true, true);
      templateSelect.append(option);
      templateSelect.value = String(payload.id);
    }

    const saveModeEl = byId("election-edit-email-save-mode") as HTMLInputElement | null;
    if (saveModeEl) {
      saveModeEl.value = "save";
    }
    markComposeBaselineFromFields();
    scheduleEmailPreviewRefresh(getCompose(), 50);
    window.alert("Template created.");
  });

  markComposeBaselineFromFields();
  initDateTimePickers();
  syncGroupCandidateOptions();
  initSelect2(document);

  const groupEl = document.getElementById("id_eligible_group_cn");
  const jquery = jq();
  if (groupEl) {
    if (jquery) {
      jquery(groupEl).on?.("change select2:select select2:clear", () => {
        void refreshEligibleVotersCount();
        void clearIneligibleSelectedCandidates();
      });
    } else {
      groupEl.addEventListener("change", () => {
        void refreshEligibleVotersCount();
        void clearIneligibleSelectedCandidates();
      });
    }
  }

  const startEl = document.getElementById("id_start_datetime");
  if (startEl) {
    if (jquery) {
      jquery(startEl).on?.("change", () => {
        void refreshEligibleVotersCount();
      });
    } else {
      startEl.addEventListener("change", () => {
        void refreshEligibleVotersCount();
      });
    }
  }

  void refreshEligibleVotersCount();

  document.addEventListener("change", (event) => {
    const target = event.target;
    if (!(target instanceof HTMLInputElement || target instanceof HTMLSelectElement)) {
      return;
    }
    const name = String(target.name || "");
    if (/^candidates-\d+-freeipa_username$/.test(name) || /^candidates-\d+-DELETE$/.test(name)) {
      syncGroupCandidateOptions();
      enforceNoSelfNomination(findRow(target));
      return;
    }
    if (/^candidates-\d+-nominated_by$/.test(name)) {
      enforceNoSelfNomination(findRow(target));
    }
  });

  document.getElementById("candidates-add-row")?.addEventListener("click", (event) => {
    event.preventDefault();
    addFormsetRow("candidates", "candidates-empty-form", "candidates-formset-body");
  });

  document.getElementById("groups-add-row")?.addEventListener("click", (event) => {
    event.preventDefault();
    addFormsetRow("groups", "groups-empty-form", "groups-formset-body");
  });

  document.addEventListener("click", (event) => {
    const target = event.target;
    if (!(target instanceof Element)) {
      return;
    }
    const button = target.closest(".election-edit-remove-row");
    if (!button) {
      return;
    }
    event.preventDefault();
    markRowDeleted(findRow(button));
  });

  form?.addEventListener("submit", (event) => {
    const action = String((byId("election-edit-action") as HTMLInputElement | null)?.value || "");
    if (action !== "save_draft") {
      return;
    }

    const hasElection = String((byId("election-edit-has-election") as HTMLInputElement | null)?.value || "") === "1";
    const status = String((byId("election-edit-election-status") as HTMLInputElement | null)?.value || "");
    if (!hasElection || status !== "draft") {
      return;
    }

    const originalId = String((byId("election-edit-original-email-template-id") as HTMLInputElement | null)?.value || "").trim();
    const currentId = String(templateSelect?.value || "").trim();
    const saveModeEl = byId("election-edit-email-save-mode") as HTMLInputElement | null;
    const saveMode = String(saveModeEl?.value || "").trim();

    if (originalId !== currentId && !saveMode) {
      event.preventDefault();
      const modalTarget = jquery?.("#edit-template-changed-modal");
      if (modalTarget?.modal) {
        modalTarget.modal("show");
        return;
      }
      if (saveModeEl) {
        saveModeEl.value = "keep_existing";
      }
      form.submit();
    }
  });

  document.getElementById("edit-keep-existing-email-btn")?.addEventListener("click", (event) => {
    event.preventDefault();
    const saveModeEl = byId("election-edit-email-save-mode") as HTMLInputElement | null;
    if (saveModeEl) {
      saveModeEl.value = "keep_existing";
    }
    jquery?.("#edit-template-changed-modal")?.modal?.("hide");
    form?.submit();
  });

  document.getElementById("edit-save-email-btn")?.addEventListener("click", (event) => {
    event.preventDefault();
    const saveModeEl = byId("election-edit-email-save-mode") as HTMLInputElement | null;
    if (saveModeEl) {
      saveModeEl.value = "save";
    }
    jquery?.("#edit-template-changed-modal")?.modal?.("hide");
    form?.submit();
  });
}

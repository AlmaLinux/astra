<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from "vue";

import { fetchSettingsPayload, type SettingsBootstrap, type SettingsField, type SettingsForm, type SettingsPayload } from "./types";

declare global {
  interface Window {
    setupRowEditor?: (options: Record<string, unknown>) => void;
    ChatNicknamesEditor?: {
      initAll?: (scope?: ParentNode) => void;
    };
    jQuery?: ((selector: string) => { modal: (command: string) => void }) & { fn?: unknown };
  }
}

const props = defineProps<{
  bootstrap: SettingsBootstrap;
}>();

const payload = ref<SettingsPayload | null>(props.bootstrap.initialPayload);
const loadError = ref("");
const activeTab = ref(props.bootstrap.initialPayload?.activeTab || "profile");
const enhancementsApplied = ref(false);
const renamingTokenId = ref<string | null>(null);
const otpRenameDrafts = ref<Record<string, string>>({});

type SettingsOtpToken = SettingsPayload["security"]["otpTokens"][number];

const tabLabels: Record<string, string> = {
  profile: "Profile",
  emails: "Emails",
  keys: "SSH & GPG Keys",
  security: "Security",
  privacy: "Privacy",
  agreements: "Agreements",
  membership: "Membership",
};

const fieldLabels: Record<string, string> = {
  givenname: "First Name",
  sn: "Last Name",
  country_code: "Country",
  fasPronoun: "Pronouns",
  fasLocale: "Locale",
  fasTimezone: "Timezone",
  fasWebsiteUrl: "Website or Blog URL",
  fasRssUrl: "RSS URL",
  fasIRCNick: "Chat Nicknames",
  fasGitHubUsername: "GitHub Username",
  fasGitLabUsername: "GitLab Username",
  mail: "E-mail Address",
  fasRHBZEmail: "Red Hat Bugzilla Email",
  fasGPGKeyId: "GPG Key IDs",
  ipasshpubkey: "SSH Public Keys",
  current_password: "Current Password",
  otp: "OTP code",
  new_password: "New Password",
  confirm_new_password: "Confirm New Password",
  description: "Token name",
  code: "Verification Code",
  fasIsPrivate: "Hide profile details",
  reason_category: "Reason",
  reason_text: "Optional details",
  acknowledge_retained_data: "I understand that some records may be retained for legal, security, or audit reasons.",
  token: "Token",
};

const fieldHelpText: Record<string, string> = {
  country_code: "Required for compliance checks.",
  fasPronoun: "Comma-separated.",
  fasLocale: "Example: en-US",
  fasTimezone: "IANA timezone like Europe/Madrid",
  fasGPGKeyId: "One per line.",
  ipasshpubkey: "One per block/line.",
  password: "Please reauthenticate so we know it is you.",
  otp: "If your account has OTP enabled, enter your current OTP.",
  code: "Generate a code in your authenticator app and enter it here.",
};

const membershipActionLabels: Record<string, string> = {
  requested: "Requested",
  on_hold: "On Hold",
  resubmitted: "Resubmitted",
  approved: "Approved",
  rejected: "Rejected",
  ignored: "Ignored",
  reopened: "Reopened",
  rescinded: "Rescinded",
  representative_changed: "Representative changed",
  expiry_changed: "Expiry changed",
  terminated: "Terminated",
};

const accountDeletionStatusLabels: Record<string, string> = {
  pending_review: "Pending review",
  pending_privilege_check: "Pending privilege check",
  approved: "Approved",
  rejected: "Rejected",
  cancelled: "Cancelled",
  completed: "Completed",
};

const tabs = computed(() => payload.value?.tabs || []);
const hasAgreementsTab = computed(() => tabs.value.includes("agreements") && payload.value?.agreements !== undefined);

function findField(form: SettingsForm, name: string): SettingsField | null {
  if (!form) {
    return null;
  }
  return form.fields.find((field) => field.name === name) || null;
}

function accountDeletionField(name: string): SettingsField | null {
  const form = payload.value?.privacy.accountDeletionForm;
  if (form === null || form === undefined) {
    return null;
  }
  return findField(form, name);
}

function fieldLabel(field: SettingsField | null): string {
  if (field === null) {
    return "";
  }
  return fieldLabels[field.name] || field.name;
}

function helpText(field: SettingsField | null): string {
  if (field === null) {
    return "";
  }
  return fieldHelpText[field.name] || "";
}

function stringValue(field: SettingsField | null): string {
  return field?.value || "";
}

function fieldClass(field: SettingsField | null, fallback = "form-control"): string {
  const baseClass = field?.attrs.class || fallback;
  return hasFieldErrors(field) ? `${baseClass} is-invalid` : baseClass;
}

function checkboxFieldClass(field: SettingsField | null): string {
  const baseClass = (field?.attrs.class || "form-check-input").replace(/\bcustom-control-input\b/g, "form-check-input").trim() || "form-check-input";
  return hasFieldErrors(field) ? `${baseClass} is-invalid` : baseClass;
}

function fieldErrors(field: SettingsField | null): string[] {
  return field?.errors || [];
}

function hasFieldErrors(field: SettingsField | null): boolean {
  return fieldErrors(field).length > 0;
}

function fieldAttrs(field: SettingsField | null): Record<string, string> {
  if (field === null) {
    return {};
  }
  const attrs = { ...field.attrs };
  delete attrs.class;
  delete attrs.rows;
  return attrs;
}

function fieldRows(field: SettingsField | null, fallback = 4): number {
  return Number.parseInt(field?.attrs.rows || `${fallback}`, 10);
}

function isSelected(field: SettingsField | null, optionValue: string): boolean {
  return stringValue(field) === optionValue;
}

function isChecked(field: SettingsField | null): boolean {
  return Boolean(field?.checked);
}

function showOtpRename(token: SettingsOtpToken): void {
  renamingTokenId.value = token.uniqueId;
  otpRenameDrafts.value[token.uniqueId] = otpRenameDrafts.value[token.uniqueId] ?? token.description;
}

function hideOtpRename(): void {
  renamingTokenId.value = null;
}

function otpRenameValue(token: SettingsOtpToken): string {
  return otpRenameDrafts.value[token.uniqueId] ?? token.description;
}

function updateOtpRenameValue(tokenId: string, event: Event): void {
  otpRenameDrafts.value[tokenId] = (event.target as HTMLInputElement).value;
}

function tabHref(tab: string): string {
  switch (tab) {
    case "profile":
      return props.bootstrap.routeConfig.profileUrl;
    case "emails":
      return props.bootstrap.routeConfig.emailsUrl;
    case "keys":
      return props.bootstrap.routeConfig.keysUrl;
    case "security":
      return props.bootstrap.routeConfig.securityUrl;
    case "privacy":
      return props.bootstrap.routeConfig.privacyUrl;
    case "membership":
      return props.bootstrap.routeConfig.membershipUrl;
    case "agreements":
      return props.bootstrap.routeConfig.agreementsUrl;
    default:
      return props.bootstrap.routeConfig.profileUrl;
  }
}

function activateTab(tab: string, event?: Event): void {
  if (event) {
    event.preventDefault();
  }
  activeTab.value = tab;
  if (window.history?.replaceState) {
    const url = new URL(window.location.href);
    url.searchParams.set("tab", tab);
    window.history.replaceState({}, "", url.toString());
  }
}

function formatDate(value: string | null): string {
  if (!value) {
    return "";
  }
  const match = value.match(/^(\d{4}-\d{2}-\d{2})/);
  return match ? match[1] : value;
}

function formatDateTime(value: string): string {
  const match = value.match(/^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})(?::\d{2})?(?:\.\d+)?([+-]\d{2}:\d{2}|Z)?$/);
  if (!match) {
    return value;
  }
  const zone = !match[3] || match[3] === "Z" ? "UTC" : `UTC${match[3]}`;
  return `${match[1]} ${match[2]} ${zone}`;
}

function membershipActionLabel(action: string): string {
  return membershipActionLabels[action] || action;
}

function accountDeletionStatusLabel(status: string): string {
  return accountDeletionStatusLabels[status] || status;
}

function terminationUrl(code: string): string {
  return props.bootstrap.routeConfig.membershipTerminateUrlTemplate.replace("__membership_type_code__", encodeURIComponent(code));
}

function groupDetailUrl(groupName: string): string {
  return props.bootstrap.routeConfig.groupDetailUrlTemplate.replace("__group_name__", encodeURIComponent(groupName));
}

function agreementDetailUrl(agreementCn: string): string {
  return props.bootstrap.routeConfig.agreementDetailUrlTemplate.replace("__agreement_cn__", encodeURIComponent(agreementCn));
}

function formatAgreementHtml(markdown: string): string {
  const escaped = markdown
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
  return escaped
    .split(/\n{2,}/)
    .map((paragraph) => `<p>${paragraph.replace(/\n/g, "<br>")}</p>`)
    .join("");
}

function initEnhancements(): void {
  if (enhancementsApplied.value) {
    return;
  }
  enhancementsApplied.value = true;

  nextTick(() => {
    window.setupRowEditor?.({
      textareaId: "id_fasWebsiteUrl",
      widgetId: "website-urls-widget",
      fallbackId: "website-urls-fallback",
      tableBodySelector: "#website-urls-table tbody",
      addBtnId: "website-urls-add",
      rowClass: "website-urls-row",
      kind: "url",
      inputClass: "website-urls-value",
      placeholder: "https://example.com",
      splitComma: true,
    });
    window.setupRowEditor?.({
      textareaId: "id_fasRssUrl",
      widgetId: "rss-urls-widget",
      fallbackId: "rss-urls-fallback",
      tableBodySelector: "#rss-urls-table tbody",
      addBtnId: "rss-urls-add",
      rowClass: "rss-urls-row",
      kind: "url",
      inputClass: "rss-urls-value",
      placeholder: "https://example.com/rss.xml",
      splitComma: true,
    });
    window.setupRowEditor?.({
      textareaId: "id_fasGPGKeyId",
      widgetId: "gpg-keys-widget",
      fallbackId: "gpg-keys-fallback",
      tableBodySelector: "#gpg-keys-table tbody",
      addBtnId: "gpg-keys-add",
      rowClass: "gpg-keys-row",
      kind: "text",
      placeholder: "Key ID",
    });
    window.setupRowEditor?.({
      textareaId: "id_ipasshpubkey",
      widgetId: "ssh-keys-widget",
      fallbackId: "ssh-keys-fallback",
      tableBodySelector: "#ssh-keys-table tbody",
      addBtnId: "ssh-keys-add",
      rowClass: "ssh-keys-row",
      kind: "textarea",
      placeholder: "Paste a key",
    });
    window.ChatNicknamesEditor?.initAll?.(document);

    if (payload.value?.profile.highlight === "country_code") {
      document.getElementById("id_country_code")?.focus();
    }

    if (payload.value?.security.otpConfirm.otpUri && window.jQuery) {
      window.jQuery("#otp-modal").modal("show");
    }
    if (payload.value?.security.otpAdd.form.nonFieldErrors.length && window.jQuery) {
      window.jQuery("#add-token-modal").modal("show");
    }
  });
}

async function loadPayload(): Promise<void> {
  if (payload.value !== null || !props.bootstrap.apiUrl) {
    initEnhancements();
    return;
  }

  try {
    payload.value = await fetchSettingsPayload(props.bootstrap.apiUrl);
    activeTab.value = payload.value.activeTab;
  } catch {
    loadError.value = "Unable to load settings right now.";
  }
  initEnhancements();
}

watch(
  () => payload.value,
  (value) => {
    if (value) {
      activeTab.value = value.activeTab;
      renamingTokenId.value = null;
    }
  },
  { immediate: true },
);

onMounted(async () => {
  await loadPayload();
});
</script>

<template>
  <div data-settings-page>
    <div v-if="loadError" class="alert alert-danger" role="alert">{{ loadError }}</div>
    <div v-else-if="!payload" class="text-muted">Loading settings...</div>
    <div v-else class="settings-page">
      <h1 class="m-0 mb-3">Settings for <a :href="bootstrap.routeConfig.userProfileUrl">profile</a></h1>

      <div class="card card-primary card-tabs settings-card mt-3">
        <div class="card-header p-0 pt-1">
          <ul class="nav nav-tabs" role="tablist">
            <li v-for="tab in tabs" :key="tab" class="nav-item">
              <a
                class="nav-link"
                :class="{ active: activeTab === tab }"
                :href="tabHref(tab)"
                :data-settings-tab="tab"
                role="tab"
                @click="activateTab(tab, $event)"
              >{{ tabLabels[tab] || tab }}</a>
            </li>
          </ul>
        </div>

        <div class="card-body">
          <div class="tab-content">
            <div class="tab-pane" :class="{ active: activeTab === 'profile' }" data-settings-tab-pane="profile" role="tabpanel">
              <form method="post" novalidate class="m-0" :action="bootstrap.submitUrl">
                <input type="hidden" name="csrfmiddlewaretoken" :value="bootstrap.csrfToken">
                <input type="hidden" name="tab" value="profile">

                <div v-if="payload.profile.form.nonFieldErrors.length" class="alert alert-danger">
                  <div v-for="errorItem in payload.profile.form.nonFieldErrors" :key="errorItem">{{ errorItem }}</div>
                </div>

                <div class="settings-form">
                  <div class="settings-profile-hero mb-4">
                    <div class="d-flex align-items-center">
                      <div class="settings-profile-avatar">
                        <img :src="payload.profile.avatarUrl" class="img-fluid img-circle" style="width:96px;height:96px;object-fit:cover;" alt="Avatar">
                      </div>
                      <div class="ml-3">
                        <button type="button" class="btn btn-outline-secondary" data-toggle="modal" data-target="#avatar-modal" title="Update your avatar">Change avatar</button>
                        <div class="mt-1"><small class="text-muted">Upload a local avatar, or keep using your current avatar provider.</small></div>
                      </div>
                    </div>
                  </div>

                  <div class="form-row">
                    <div class="form-group col-md-6">
                      <label :for="findField(payload.profile.form, 'givenname')?.id">{{ fieldLabel(findField(payload.profile.form, 'givenname')) }}</label>
                      <input :id="findField(payload.profile.form, 'givenname')?.id || 'id_givenname'" name="givenname" type="text" :class="fieldClass(findField(payload.profile.form, 'givenname'))" :value="stringValue(findField(payload.profile.form, 'givenname'))" v-bind="fieldAttrs(findField(payload.profile.form, 'givenname'))">
                      <div v-if="fieldErrors(findField(payload.profile.form, 'givenname')).length" class="invalid-feedback d-block"><div v-for="errorItem in fieldErrors(findField(payload.profile.form, 'givenname'))" :key="errorItem">{{ errorItem }}</div></div>
                    </div>
                    <div class="form-group col-md-6">
                      <label :for="findField(payload.profile.form, 'sn')?.id">{{ fieldLabel(findField(payload.profile.form, 'sn')) }}</label>
                      <input :id="findField(payload.profile.form, 'sn')?.id || 'id_sn'" name="sn" type="text" :class="fieldClass(findField(payload.profile.form, 'sn'))" :value="stringValue(findField(payload.profile.form, 'sn'))" v-bind="fieldAttrs(findField(payload.profile.form, 'sn'))">
                      <div v-if="fieldErrors(findField(payload.profile.form, 'sn')).length" class="invalid-feedback d-block"><div v-for="errorItem in fieldErrors(findField(payload.profile.form, 'sn'))" :key="errorItem">{{ errorItem }}</div></div>
                    </div>
                  </div>

                  <div class="form-group">
                    <label :for="findField(payload.profile.form, 'fasPronoun')?.id">{{ fieldLabel(findField(payload.profile.form, 'fasPronoun')) }}</label>
                    <input :id="findField(payload.profile.form, 'fasPronoun')?.id || 'id_fasPronoun'" name="fasPronoun" type="text" :class="fieldClass(findField(payload.profile.form, 'fasPronoun'))" :value="stringValue(findField(payload.profile.form, 'fasPronoun'))" v-bind="fieldAttrs(findField(payload.profile.form, 'fasPronoun'))">
                    <div v-if="fieldErrors(findField(payload.profile.form, 'fasPronoun')).length" class="invalid-feedback d-block"><div v-for="errorItem in fieldErrors(findField(payload.profile.form, 'fasPronoun'))" :key="errorItem">{{ errorItem }}</div></div>
                    <small class="form-text text-muted">Comma-separated.</small>
                  </div>

                  <datalist id="pronoun-options">
                    <option value="she / her / hers"></option>
                    <option value="he / him / his"></option>
                    <option value="they / them / theirs"></option>
                  </datalist>

                  <div class="form-group" :class="payload.profile.highlight === 'country_code' ? 'settings-field-highlight' : ''" id="country-code-field-wrapper">
                    <label :for="findField(payload.profile.form, 'country_code')?.id">{{ fieldLabel(findField(payload.profile.form, 'country_code')) }}</label>
                    <select :id="findField(payload.profile.form, 'country_code')?.id || 'id_country_code'" name="country_code" :class="fieldClass(findField(payload.profile.form, 'country_code'))" v-bind="fieldAttrs(findField(payload.profile.form, 'country_code'))">
                      <option v-for="option in findField(payload.profile.form, 'country_code')?.options || []" :key="option.value || '__empty__'" :value="option.value" :selected="isSelected(findField(payload.profile.form, 'country_code'), option.value)">{{ option.label }}</option>
                    </select>
                    <div v-if="fieldErrors(findField(payload.profile.form, 'country_code')).length" class="invalid-feedback d-block"><div v-for="errorItem in fieldErrors(findField(payload.profile.form, 'country_code'))" :key="errorItem">{{ errorItem }}</div></div>
                    <small class="form-text text-muted">Required for compliance checks.</small>
                  </div>

                  <div class="form-group">
                    <label :for="findField(payload.profile.form, 'fasLocale')?.id">{{ fieldLabel(findField(payload.profile.form, 'fasLocale')) }}</label>
                    <input :id="findField(payload.profile.form, 'fasLocale')?.id || 'id_fasLocale'" name="fasLocale" type="text" :class="fieldClass(findField(payload.profile.form, 'fasLocale'))" :value="stringValue(findField(payload.profile.form, 'fasLocale'))" v-bind="fieldAttrs(findField(payload.profile.form, 'fasLocale'))">
                    <datalist id="locale-options"><option v-for="locale in payload.profile.localeOptions" :key="locale" :value="locale"></option></datalist>
                    <div v-if="fieldErrors(findField(payload.profile.form, 'fasLocale')).length" class="invalid-feedback d-block"><div v-for="errorItem in fieldErrors(findField(payload.profile.form, 'fasLocale'))" :key="errorItem">{{ errorItem }}</div></div>
                    <small class="form-text text-muted">Example: en-US</small>
                  </div>

                  <div class="form-group">
                    <label :for="findField(payload.profile.form, 'fasTimezone')?.id">{{ fieldLabel(findField(payload.profile.form, 'fasTimezone')) }}</label>
                    <input :id="findField(payload.profile.form, 'fasTimezone')?.id || 'id_fasTimezone'" name="fasTimezone" type="text" :class="fieldClass(findField(payload.profile.form, 'fasTimezone'))" :value="stringValue(findField(payload.profile.form, 'fasTimezone'))" v-bind="fieldAttrs(findField(payload.profile.form, 'fasTimezone'))">
                    <datalist id="timezone-options"><option v-for="timezoneValue in payload.profile.timezoneOptions" :key="timezoneValue" :value="timezoneValue"></option></datalist>
                    <div v-if="fieldErrors(findField(payload.profile.form, 'fasTimezone')).length" class="invalid-feedback d-block"><div v-for="errorItem in fieldErrors(findField(payload.profile.form, 'fasTimezone'))" :key="errorItem">{{ errorItem }}</div></div>
                    <small class="form-text text-muted">IANA timezone like Europe/Madrid</small>
                  </div>

                  <div id="website-urls-fallback" class="form-group">
                    <label for="id_fasWebsiteUrl">Website or Blog URL</label>
                    <textarea id="id_fasWebsiteUrl" name="fasWebsiteUrl" :class="fieldClass(findField(payload.profile.form, 'fasWebsiteUrl'))" :rows="fieldRows(findField(payload.profile.form, 'fasWebsiteUrl'), 2)" v-bind="fieldAttrs(findField(payload.profile.form, 'fasWebsiteUrl'))">{{ stringValue(findField(payload.profile.form, 'fasWebsiteUrl')) }}</textarea>
                    <div v-if="fieldErrors(findField(payload.profile.form, 'fasWebsiteUrl')).length" class="invalid-feedback d-block"><div v-for="errorItem in fieldErrors(findField(payload.profile.form, 'fasWebsiteUrl'))" :key="errorItem">{{ errorItem }}</div></div>
                  </div>
                  <div id="website-urls-widget" class="d-none">
                    <table class="table table-sm mb-2" id="website-urls-table"><tbody /></table>
                    <button id="website-urls-add" type="button" class="btn btn-sm btn-outline-secondary" title="Add another website URL">Add website URL</button>
                  </div>

                  <div id="rss-urls-fallback" class="form-group mt-3">
                    <label for="id_fasRssUrl">RSS URL</label>
                    <textarea id="id_fasRssUrl" name="fasRssUrl" :class="fieldClass(findField(payload.profile.form, 'fasRssUrl'))" :rows="fieldRows(findField(payload.profile.form, 'fasRssUrl'), 2)" v-bind="fieldAttrs(findField(payload.profile.form, 'fasRssUrl'))">{{ stringValue(findField(payload.profile.form, 'fasRssUrl')) }}</textarea>
                    <div v-if="fieldErrors(findField(payload.profile.form, 'fasRssUrl')).length" class="invalid-feedback d-block"><div v-for="errorItem in fieldErrors(findField(payload.profile.form, 'fasRssUrl'))" :key="errorItem">{{ errorItem }}</div></div>
                  </div>
                  <div id="rss-urls-widget" class="d-none">
                    <table class="table table-sm mb-2" id="rss-urls-table"><tbody /></table>
                    <button id="rss-urls-add" type="button" class="btn btn-sm btn-outline-secondary" title="Add another RSS URL">Add RSS URL</button>
                  </div>

                  <small class="form-text text-muted">
                    The format is either <strong>username</strong> or <strong>username:server.name</strong> (IRC/Matrix). For Mattermost custom servers use <strong>@username:server.name:team</strong>:
                  </small>
                  <ul class="mb-2">
                    <li>For Mattermost: <strong>{{ payload.profile.chatDefaults.mattermostServer }}</strong> (team <strong>{{ payload.profile.chatDefaults.mattermostTeam }}</strong>)</li>
                    <li>For IRC: <strong>{{ payload.profile.chatDefaults.ircServer }}</strong></li>
                    <li>For Matrix: <strong>{{ payload.profile.chatDefaults.matrixServer }}</strong></li>
                  </ul>

                  <div id="chat-nicks-fallback" class="form-group">
                    <label for="id_fasIRCNick">Chat Nicknames</label>
                    <textarea id="id_fasIRCNick" name="fasIRCNick" :class="fieldClass(findField(payload.profile.form, 'fasIRCNick'))" :rows="fieldRows(findField(payload.profile.form, 'fasIRCNick'), 3)" v-bind="fieldAttrs(findField(payload.profile.form, 'fasIRCNick'))">{{ stringValue(findField(payload.profile.form, 'fasIRCNick')) }}</textarea>
                    <div v-if="fieldErrors(findField(payload.profile.form, 'fasIRCNick')).length" class="invalid-feedback d-block"><div v-for="errorItem in fieldErrors(findField(payload.profile.form, 'fasIRCNick'))" :key="errorItem">{{ errorItem }}</div></div>
                  </div>
                  <div
                    id="chat-nicks-widget"
                    class="d-none js-chat-nicks-editor"
                    data-textarea-id="id_fasIRCNick"
                    data-fallback-id="chat-nicks-fallback"
                    :data-mattermost-default-server="payload.profile.chatDefaults.mattermostServer"
                    :data-mattermost-default-team="payload.profile.chatDefaults.mattermostTeam"
                    :data-irc-default-server="payload.profile.chatDefaults.ircServer"
                    :data-matrix-default-server="payload.profile.chatDefaults.matrixServer"
                  >
                    <table class="table table-sm" id="chat-nicks-table"><tbody /></table>
                    <button id="chat-nicks-add" type="button" class="btn btn-sm btn-outline-secondary js-chat-nicks-add" title="Add another chat nickname">Add nickname</button>
                  </div>

                  <div class="form-group mt-3">
                    <label :for="findField(payload.profile.form, 'fasGitHubUsername')?.id">GitHub Username</label>
                    <div class="input-group">
                      <div class="input-group-prepend"><span class="input-group-text">@</span></div>
                      <input :id="findField(payload.profile.form, 'fasGitHubUsername')?.id || 'id_fasGitHubUsername'" name="fasGitHubUsername" type="text" :class="fieldClass(findField(payload.profile.form, 'fasGitHubUsername'))" :value="stringValue(findField(payload.profile.form, 'fasGitHubUsername'))" v-bind="fieldAttrs(findField(payload.profile.form, 'fasGitHubUsername'))">
                    </div>
                    <div v-if="fieldErrors(findField(payload.profile.form, 'fasGitHubUsername')).length" class="invalid-feedback d-block"><div v-for="errorItem in fieldErrors(findField(payload.profile.form, 'fasGitHubUsername'))" :key="errorItem">{{ errorItem }}</div></div>
                  </div>

                  <div class="form-group">
                    <label :for="findField(payload.profile.form, 'fasGitLabUsername')?.id">GitLab Username</label>
                    <div class="input-group">
                      <div class="input-group-prepend"><span class="input-group-text">@</span></div>
                      <input :id="findField(payload.profile.form, 'fasGitLabUsername')?.id || 'id_fasGitLabUsername'" name="fasGitLabUsername" type="text" :class="fieldClass(findField(payload.profile.form, 'fasGitLabUsername'))" :value="stringValue(findField(payload.profile.form, 'fasGitLabUsername'))" v-bind="fieldAttrs(findField(payload.profile.form, 'fasGitLabUsername'))">
                    </div>
                    <div v-if="fieldErrors(findField(payload.profile.form, 'fasGitLabUsername')).length" class="invalid-feedback d-block"><div v-for="errorItem in fieldErrors(findField(payload.profile.form, 'fasGitLabUsername'))" :key="errorItem">{{ errorItem }}</div></div>
                  </div>
                </div>

                <div class="d-flex justify-content-end"><button type="submit" class="btn btn-primary" title="Save profile changes">Save</button></div>
              </form>

              <div class="modal fade" id="avatar-modal" tabindex="-1" role="dialog" aria-hidden="true" aria-labelledby="avatar-modal-title">
                <div class="modal-dialog" role="document" style="max-width: 520px;">
                  <div class="modal-content">
                    <div class="modal-header">
                      <h5 id="avatar-modal-title" class="modal-title">Avatar</h5>
                      <button type="button" class="close" data-dismiss="modal" aria-label="Close" title="Close avatar settings"><span aria-hidden="true">&times;</span></button>
                    </div>
                    <div class="modal-body">
                      <div class="d-flex align-items-center">
                        <div style="width: 96px; height: 96px;"><img :src="payload.profile.avatarUrl" class="img-fluid img-circle" style="width:96px;height:96px;object-fit:cover;" alt="Avatar"></div>
                        <div class="ml-3">
                          <div><strong>Source:</strong> {{ payload.profile.avatarProvider || 'unknown' }}</div>
                          <div v-if="payload.profile.avatarManageUrl && !payload.profile.avatarIsLocal" class="mt-1"><a :href="payload.profile.avatarManageUrl" target="_blank" rel="noopener">Manage at provider</a></div>
                        </div>
                      </div>
                      <hr>
                      <form method="post" :action="bootstrap.routeConfig.avatarUploadUrl" enctype="multipart/form-data" class="mb-2">
                        <input type="hidden" name="csrfmiddlewaretoken" :value="bootstrap.csrfToken">
                        <div class="form-group">
                          <label for="id_avatar_upload">Upload a new avatar</label>
                          <input id="id_avatar_upload" name="avatar" type="file" accept="image/*" class="form-control-file" required>
                          <small class="form-text text-muted">Uploading a new avatar replaces the previous one.</small>
                        </div>
                        <button type="submit" class="btn btn-primary" title="Upload a new avatar">Upload</button>
                      </form>
                      <form v-if="payload.profile.avatarIsLocal && bootstrap.routeConfig.avatarDeleteUrl" method="post" :action="bootstrap.routeConfig.avatarDeleteUrl">
                        <input type="hidden" name="csrfmiddlewaretoken" :value="bootstrap.csrfToken">
                        <button type="submit" class="btn btn-outline-danger" title="Remove uploaded avatar">Delete local avatar</button>
                      </form>
                    </div>
                  </div>
                </div>
              </div>
            </div>

            <div class="tab-pane" :class="{ active: activeTab === 'emails' }" data-settings-tab-pane="emails" role="tabpanel">
              <form method="post" novalidate :action="bootstrap.submitUrl">
                <input type="hidden" name="csrfmiddlewaretoken" :value="bootstrap.csrfToken">
                <input type="hidden" name="tab" value="emails">
                <div v-if="payload.emails.emailIsBlacklisted" class="callout callout-danger">
                  <h5>Email delivery problem</h5>
                  <p class="mb-2">Your current email address has been flagged for delivery problems - this can happen after a bounce (invalid address) or a spam complaint. Important notifications may not reach you.</p>
                  <p class="mb-0">Please update your email address to a working one below.</p>
                </div>
                <div v-if="payload.emails.form.nonFieldErrors.length" class="alert alert-danger"><div v-for="errorItem in payload.emails.form.nonFieldErrors" :key="errorItem">{{ errorItem }}</div></div>
                <div class="form-group">
                  <label :for="findField(payload.emails.form, 'mail')?.id">E-mail Address</label>
                  <input :id="findField(payload.emails.form, 'mail')?.id || 'id_mail'" name="mail" type="email" :class="fieldClass(findField(payload.emails.form, 'mail'))" :value="stringValue(findField(payload.emails.form, 'mail'))" v-bind="fieldAttrs(findField(payload.emails.form, 'mail'))">
                  <div v-if="fieldErrors(findField(payload.emails.form, 'mail')).length" class="invalid-feedback d-block"><div v-for="errorItem in fieldErrors(findField(payload.emails.form, 'mail'))" :key="errorItem">{{ errorItem }}</div></div>
                </div>
                <div class="form-group">
                  <label :for="findField(payload.emails.form, 'fasRHBZEmail')?.id">Red Hat Bugzilla Email</label>
                  <input :id="findField(payload.emails.form, 'fasRHBZEmail')?.id || 'id_fasRHBZEmail'" name="fasRHBZEmail" type="email" :class="fieldClass(findField(payload.emails.form, 'fasRHBZEmail'))" :value="stringValue(findField(payload.emails.form, 'fasRHBZEmail'))" v-bind="fieldAttrs(findField(payload.emails.form, 'fasRHBZEmail'))">
                  <div v-if="fieldErrors(findField(payload.emails.form, 'fasRHBZEmail')).length" class="invalid-feedback d-block"><div v-for="errorItem in fieldErrors(findField(payload.emails.form, 'fasRHBZEmail'))" :key="errorItem">{{ errorItem }}</div></div>
                </div>
                <button type="submit" class="btn btn-primary" title="Save email settings">Save</button>
              </form>
            </div>

            <div class="tab-pane" :class="{ active: activeTab === 'keys' }" data-settings-tab-pane="keys" role="tabpanel">
              <form method="post" novalidate :action="bootstrap.submitUrl">
                <input type="hidden" name="csrfmiddlewaretoken" :value="bootstrap.csrfToken">
                <input type="hidden" name="tab" value="keys">
                <div v-if="payload.keys.form.nonFieldErrors.length" class="alert alert-danger"><div v-for="errorItem in payload.keys.form.nonFieldErrors" :key="errorItem">{{ errorItem }}</div></div>

                <div id="gpg-keys-fallback" class="form-group">
                  <label for="id_fasGPGKeyId">GPG Key IDs</label>
                  <textarea id="id_fasGPGKeyId" name="fasGPGKeyId" :class="fieldClass(findField(payload.keys.form, 'fasGPGKeyId'))" :rows="fieldRows(findField(payload.keys.form, 'fasGPGKeyId'), 3)" v-bind="fieldAttrs(findField(payload.keys.form, 'fasGPGKeyId'))">{{ stringValue(findField(payload.keys.form, 'fasGPGKeyId')) }}</textarea>
                  <div v-if="fieldErrors(findField(payload.keys.form, 'fasGPGKeyId')).length" class="invalid-feedback d-block"><div v-for="errorItem in fieldErrors(findField(payload.keys.form, 'fasGPGKeyId'))" :key="errorItem">{{ errorItem }}</div></div>
                  <small class="form-text text-muted">One per line.</small>
                </div>
                <div id="gpg-keys-widget" class="d-none"><table class="table table-sm mb-2" id="gpg-keys-table"><tbody /></table><button id="gpg-keys-add" type="button" class="btn btn-sm btn-outline-secondary" title="Add another GPG key ID">Add GPG key ID</button></div>

                <div id="ssh-keys-fallback" class="form-group mt-3">
                  <label for="id_ipasshpubkey">SSH Public Keys</label>
                  <textarea id="id_ipasshpubkey" name="ipasshpubkey" :class="fieldClass(findField(payload.keys.form, 'ipasshpubkey'))" :rows="fieldRows(findField(payload.keys.form, 'ipasshpubkey'), 6)" v-bind="fieldAttrs(findField(payload.keys.form, 'ipasshpubkey'))">{{ stringValue(findField(payload.keys.form, 'ipasshpubkey')) }}</textarea>
                  <div v-if="fieldErrors(findField(payload.keys.form, 'ipasshpubkey')).length" class="invalid-feedback d-block"><div v-for="errorItem in fieldErrors(findField(payload.keys.form, 'ipasshpubkey'))" :key="errorItem">{{ errorItem }}</div></div>
                  <small class="form-text text-muted">One per block/line.</small>
                </div>
                <div id="ssh-keys-widget" class="d-none"><table class="table table-sm mb-2" id="ssh-keys-table"><tbody /></table><button id="ssh-keys-add" type="button" class="btn btn-sm btn-outline-secondary" title="Add another SSH key">Add SSH key</button></div>

                <button type="submit" class="btn btn-primary" title="Save key settings">Save</button>
              </form>
            </div>

            <div class="tab-pane" :class="{ active: activeTab === 'security' }" data-settings-tab-pane="security" role="tabpanel">
              <h5 class="mb-3">Change password</h5>
              <form method="post" novalidate :action="bootstrap.submitUrl">
                <input type="hidden" name="csrfmiddlewaretoken" :value="bootstrap.csrfToken">
                <input type="hidden" name="tab" value="security">
                <div v-if="payload.security.password.form.nonFieldErrors.length" class="alert alert-danger"><div v-for="errorItem in payload.security.password.form.nonFieldErrors" :key="errorItem">{{ errorItem }}</div></div>
                <div class="form-group"><label :for="findField(payload.security.password.form, 'current_password')?.id">Current Password</label><input :id="findField(payload.security.password.form, 'current_password')?.id || 'id_current_password'" name="current_password" type="password" :class="fieldClass(findField(payload.security.password.form, 'current_password'))" :value="stringValue(findField(payload.security.password.form, 'current_password'))"><div v-if="fieldErrors(findField(payload.security.password.form, 'current_password')).length" class="invalid-feedback d-block"><div v-for="errorItem in fieldErrors(findField(payload.security.password.form, 'current_password'))" :key="errorItem">{{ errorItem }}</div></div></div>
                <div v-if="payload.security.usingOtp" id="otpinput" class="form-group"><label :for="findField(payload.security.password.form, 'otp')?.id">OTP code</label><input :id="findField(payload.security.password.form, 'otp')?.id || 'id_otp'" name="otp" type="text" :class="fieldClass(findField(payload.security.password.form, 'otp'))" :value="stringValue(findField(payload.security.password.form, 'otp'))"><div v-if="fieldErrors(findField(payload.security.password.form, 'otp')).length" class="invalid-feedback d-block"><div v-for="errorItem in fieldErrors(findField(payload.security.password.form, 'otp'))" :key="errorItem">{{ errorItem }}</div></div><small class="form-text text-muted">Your account has OTP enabled; enter your current OTP.</small></div>
                <div class="form-group"><label :for="findField(payload.security.password.form, 'new_password')?.id">New Password</label><input :id="findField(payload.security.password.form, 'new_password')?.id || 'id_new_password'" name="new_password" type="password" :class="fieldClass(findField(payload.security.password.form, 'new_password'))" :value="stringValue(findField(payload.security.password.form, 'new_password'))"><div v-if="fieldErrors(findField(payload.security.password.form, 'new_password')).length" class="invalid-feedback d-block"><div v-for="errorItem in fieldErrors(findField(payload.security.password.form, 'new_password'))" :key="errorItem">{{ errorItem }}</div></div></div>
                <div class="form-group"><label :for="findField(payload.security.password.form, 'confirm_new_password')?.id">Confirm New Password</label><input :id="findField(payload.security.password.form, 'confirm_new_password')?.id || 'id_confirm_new_password'" name="confirm_new_password" type="password" :class="fieldClass(findField(payload.security.password.form, 'confirm_new_password'))" :value="stringValue(findField(payload.security.password.form, 'confirm_new_password'))"><div v-if="fieldErrors(findField(payload.security.password.form, 'confirm_new_password')).length" class="invalid-feedback d-block"><div v-for="errorItem in fieldErrors(findField(payload.security.password.form, 'confirm_new_password'))" :key="errorItem">{{ errorItem }}</div></div></div>
                <button type="submit" class="btn btn-primary" title="Update your password">Change password</button>
              </form>

              <hr class="my-4">
              <h5 class="mb-3">OTP Tokens</h5>

              <div class="modal fade" id="otp-modal" tabindex="-1" role="dialog" aria-hidden="true">
                <div class="modal-dialog" role="document"><div class="modal-content"><div class="modal-header"><h5 class="modal-title">Scan your new token</h5><button type="button" class="close" data-dismiss="modal" aria-label="Close" title="Close dialog"><span aria-hidden="true">&times;</span></button></div><div class="modal-body"><p>Your new token is ready. Click the button below to reveal the QR code and scan it.</p><div class="text-center"><button id="otp-toggle" class="btn btn-primary" type="button" title="Reveal the QR code" @click="document.getElementById('otp-qrcode')?.classList.toggle('d-none')">Reveal</button></div><div id="otp-qrcode" class="text-center mt-3 d-none"><img v-if="payload.security.otpConfirm.otpQrPngB64" alt="OTP QR code" class="img-fluid" :src="`data:image/png;base64,${payload.security.otpConfirm.otpQrPngB64}`"></div><p class="mb-1 mt-4">or copy and paste the following token URL if you can't scan the QR code:</p><input id="otp-uri" class="form-control" readonly :value="payload.security.otpConfirm.otpUri || ''"><form method="post" class="py-3" novalidate :action="bootstrap.submitUrl"><input type="hidden" name="csrfmiddlewaretoken" :value="bootstrap.csrfToken"><input type="hidden" name="tab" value="security"><input type="hidden" name="confirm-secret" :value="stringValue(findField(payload.security.otpConfirm.form, 'secret'))"><input type="hidden" name="confirm-description" :value="stringValue(findField(payload.security.otpConfirm.form, 'description'))"><p class="mb-2">After enrolling the token in your application, verify it by generating your first code and entering it below:</p><div class="form-group"><label :for="findField(payload.security.otpConfirm.form, 'code')?.id">Verification Code</label><input :id="findField(payload.security.otpConfirm.form, 'code')?.id || 'id_confirm-code'" name="confirm-code" type="text" :class="fieldClass(findField(payload.security.otpConfirm.form, 'code'))" :value="stringValue(findField(payload.security.otpConfirm.form, 'code'))"><div v-if="fieldErrors(findField(payload.security.otpConfirm.form, 'code')).length" class="invalid-feedback d-block"><div v-for="errorItem in fieldErrors(findField(payload.security.otpConfirm.form, 'code'))" :key="errorItem">{{ errorItem }}</div></div></div><div v-if="payload.security.otpConfirm.form.nonFieldErrors.length" class="alert alert-danger"><div v-for="errorItem in payload.security.otpConfirm.form.nonFieldErrors" :key="errorItem">{{ errorItem }}</div></div><div class="d-flex justify-content-between"><button type="submit" class="btn btn-primary" name="confirm-submit" value="1" title="Verify and enable this token">Verify and Enable OTP Token</button><button type="button" class="btn btn-secondary" data-dismiss="modal" aria-label="Cancel" title="Close dialog without enabling">Cancel</button></div></form></div></div></div>
              </div>

              <div class="modal fade" id="add-token-modal" tabindex="-1" role="dialog" aria-hidden="true">
                <div class="modal-dialog" role="document"><div class="modal-content"><div class="modal-header"><h5 class="modal-title">Add OTP Token</h5><button type="button" class="close" data-dismiss="modal" aria-label="Close" title="Close dialog"><span aria-hidden="true">&times;</span></button></div><div class="modal-body"><div v-if="payload.security.otpTokens.length === 0" class="alert alert-info"><div><small>Creating your first OTP token enables two-factor authentication using OTP.</small></div><div><small><strong>Once enabled, two-factor authentication cannot be disabled.</strong></small></div></div><form method="post" novalidate :action="bootstrap.submitUrl"><input type="hidden" name="csrfmiddlewaretoken" :value="bootstrap.csrfToken"><input type="hidden" name="tab" value="security"><div class="form-group"><label :for="findField(payload.security.otpAdd.form, 'description')?.id">Token name</label><input :id="findField(payload.security.otpAdd.form, 'description')?.id || 'id_add-description'" name="add-description" type="text" :class="fieldClass(findField(payload.security.otpAdd.form, 'description'))" :value="stringValue(findField(payload.security.otpAdd.form, 'description'))"><div v-if="fieldErrors(findField(payload.security.otpAdd.form, 'description')).length" class="invalid-feedback d-block"><div v-for="errorItem in fieldErrors(findField(payload.security.otpAdd.form, 'description'))" :key="errorItem">{{ errorItem }}</div></div></div><div class="form-group"><label :for="findField(payload.security.otpAdd.form, 'password')?.id">Enter your current password</label><input :id="findField(payload.security.otpAdd.form, 'password')?.id || 'id_add-password'" name="add-password" type="password" :class="fieldClass(findField(payload.security.otpAdd.form, 'password'))" :value="stringValue(findField(payload.security.otpAdd.form, 'password'))"><div v-if="fieldErrors(findField(payload.security.otpAdd.form, 'password')).length" class="invalid-feedback d-block"><div v-for="errorItem in fieldErrors(findField(payload.security.otpAdd.form, 'password'))" :key="errorItem">{{ errorItem }}</div></div></div><div v-if="payload.security.otpTokens.length" class="form-group"><label :for="findField(payload.security.otpAdd.form, 'otp')?.id">OTP code</label><input :id="findField(payload.security.otpAdd.form, 'otp')?.id || 'id_add-otp'" name="add-otp" type="text" :class="fieldClass(findField(payload.security.otpAdd.form, 'otp'))" :value="stringValue(findField(payload.security.otpAdd.form, 'otp'))"><div v-if="fieldErrors(findField(payload.security.otpAdd.form, 'otp')).length" class="invalid-feedback d-block"><div v-for="errorItem in fieldErrors(findField(payload.security.otpAdd.form, 'otp'))" :key="errorItem">{{ errorItem }}</div></div><small class="form-text text-muted">Enter your current OTP to authorize adding a new token.</small></div><div v-if="payload.security.otpAdd.form.nonFieldErrors.length" class="alert alert-danger"><div v-for="errorItem in payload.security.otpAdd.form.nonFieldErrors" :key="errorItem">{{ errorItem }}</div></div><button type="submit" class="btn btn-primary" name="add-submit" value="1" title="Generate a new OTP token">Generate OTP Token</button></form></div></div></div>
              </div>

              <div class="d-flex"><button class="btn btn-primary btn-sm ml-auto" data-toggle="modal" data-target="#add-token-modal" type="button" title="Add a new OTP token">Add OTP Token</button></div>
              <div class="list-group list-group-flush mt-3">
                <div v-for="token in payload.security.otpTokens" :key="token.uniqueId" class="list-group-item" :class="token.disabled ? 'text-muted bg-light' : ''">
                  <div class="row align-items-center"><div class="col"><div v-if="renamingTokenId !== token.uniqueId" class="font-weight-bold otp-description"><span data-role="token-description">{{ token.description || '(no name)' }}</span><button class="btn btn-sm btn-outline-secondary ml-1" type="button" title="Rename this token" @click="showOtpRename(token)"><i class="fa fa-edit"></i></button></div><div v-else class="otp-rename-form"><form :action="bootstrap.routeConfig.otpRenameUrl" method="post" class="form-inline"><input type="hidden" name="csrfmiddlewaretoken" :value="bootstrap.csrfToken"><input type="hidden" name="token" :value="token.uniqueId"><input type="text" class="form-control form-control-sm mr-2" name="description" :value="otpRenameValue(token)" @input="updateOtpRenameValue(token.uniqueId, $event)"><button type="submit" class="btn btn-sm btn-primary" title="Rename this token">Rename</button><button type="button" class="btn btn-sm btn-outline-secondary ml-2" title="Cancel renaming this token" @click="hideOtpRename">Cancel</button></form></div><div class="text-monospace">{{ token.uniqueId }}</div></div><div class="col-auto"><form v-if="!token.disabled" :action="bootstrap.routeConfig.otpDisableUrl" method="post" class="d-inline"><input type="hidden" name="csrfmiddlewaretoken" :value="bootstrap.csrfToken"><input type="hidden" name="token" :value="token.uniqueId"><button type="submit" class="btn btn-sm btn-outline-secondary" title="Disable this token">Disable</button></form><form v-else :action="bootstrap.routeConfig.otpEnableUrl" method="post" class="d-inline"><input type="hidden" name="csrfmiddlewaretoken" :value="bootstrap.csrfToken"><input type="hidden" name="token" :value="token.uniqueId"><button type="submit" class="btn btn-sm btn-outline-secondary" title="Enable this token">Enable</button></form><form :action="bootstrap.routeConfig.otpDeleteUrl" method="post" class="d-inline ml-1"><input type="hidden" name="csrfmiddlewaretoken" :value="bootstrap.csrfToken"><input type="hidden" name="token" :value="token.uniqueId"><button type="submit" class="btn btn-sm btn-outline-danger" title="Delete this token"><i class="fa fa-trash"></i></button></form></div></div>
                </div>
                <div v-if="payload.security.otpTokens.length === 0" class="list-group-item text-center bg-light text-muted font-weight-bold"><div>You have no OTP tokens</div><div><small>Add an OTP token to enable two-factor authentication on your account.</small></div></div>
              </div>
            </div>

            <div class="tab-pane" :class="{ active: activeTab === 'privacy' }" data-settings-tab-pane="privacy" role="tabpanel">
              <div class="settings-privacy-tab">
                <h2 class="h4">Privacy</h2>
                <p class="text-muted">Manage your profile visibility and request account deletion review.</p>
                <form method="post" :action="bootstrap.submitUrl" class="mb-4">
                  <input type="hidden" name="csrfmiddlewaretoken" :value="bootstrap.csrfToken">
                  <input type="hidden" name="tab" value="privacy">
                  <div class="form-group form-check mb-3"><input :id="findField(payload.privacy.form, 'fasIsPrivate')?.id || 'id_fasIsPrivate'" name="fasIsPrivate" type="checkbox" value="on" :class="checkboxFieldClass(findField(payload.privacy.form, 'fasIsPrivate'))" :checked="isChecked(findField(payload.privacy.form, 'fasIsPrivate'))" :required="findField(payload.privacy.form, 'fasIsPrivate')?.required" :disabled="findField(payload.privacy.form, 'fasIsPrivate')?.disabled" v-bind="fieldAttrs(findField(payload.privacy.form, 'fasIsPrivate'))"><label class="form-check-label" :for="findField(payload.privacy.form, 'fasIsPrivate')?.id || 'id_fasIsPrivate'">Hide profile details</label><div v-if="fieldErrors(findField(payload.privacy.form, 'fasIsPrivate')).length" class="invalid-feedback d-block"><div v-for="errorItem in fieldErrors(findField(payload.privacy.form, 'fasIsPrivate'))" :key="errorItem">{{ errorItem }}</div></div><small class="form-text text-muted">Hide personal details, including your name and email, and hide your memberships from other signed-in users. Your profile stays visible, as do your groups.</small></div>
                  <button type="submit" class="btn btn-primary">Save privacy settings</button>
                </form>

                <div class="card border-danger">
                  <div class="card-body">
                    <h3 class="h5">Delete my account</h3>
                    <p class="text-muted">Submitting a deletion request starts a staff-reviewed workflow. Some records may still be retained for legal, security, election, or audit reasons.</p>
                    <div v-if="payload.privacy.activeDeletionRequest" class="alert alert-info" role="alert">Your current deletion request status is <strong>{{ accountDeletionStatusLabel(payload.privacy.activeDeletionRequest.status) }}</strong>.</div>
                    <p v-if="payload.privacy.activeDeletionRequest" class="text-muted mb-0">This request is already in progress. Contact support if you need help with the review.</p>
                    <form v-else method="post" :action="bootstrap.routeConfig.accountDeletionSubmitUrl" class="needs-validation" novalidate>
                      <input type="hidden" name="csrfmiddlewaretoken" :value="bootstrap.csrfToken">
                      <div class="form-group">
                        <label :for="accountDeletionField('reason_category')?.id">Why are you requesting account deletion?</label>
                        <select :id="accountDeletionField('reason_category')?.id || 'id_reason_category'" name="reason_category" :class="fieldClass(accountDeletionField('reason_category'))">
                          <option v-for="option in accountDeletionField('reason_category')?.options || []" :key="option.value" :value="option.value" :selected="isSelected(accountDeletionField('reason_category'), option.value)">{{ option.label }}</option>
                        </select>
                        <div v-if="fieldErrors(accountDeletionField('reason_category')).length" class="invalid-feedback d-block"><div v-for="errorItem in fieldErrors(accountDeletionField('reason_category'))" :key="errorItem">{{ errorItem }}</div></div>
                      </div>
                      <div class="form-group">
                        <label :for="accountDeletionField('reason_text')?.id">Optional details</label>
                        <textarea :id="accountDeletionField('reason_text')?.id || 'id_reason_text'" name="reason_text" :class="fieldClass(accountDeletionField('reason_text'))" rows="4">{{ stringValue(accountDeletionField('reason_text')) }}</textarea>
                        <div v-if="fieldErrors(accountDeletionField('reason_text')).length" class="invalid-feedback d-block"><div v-for="errorItem in fieldErrors(accountDeletionField('reason_text'))" :key="errorItem">{{ errorItem }}</div></div>
                        <small class="form-text text-muted">Visible only to authorized operators and cleared after the retention window.</small>
                      </div>
                      <div class="form-group form-check mb-3">
                        <input :id="accountDeletionField('acknowledge_retained_data')?.id || 'id_acknowledge_retained_data'" name="acknowledge_retained_data" type="checkbox" value="on" :class="checkboxFieldClass(accountDeletionField('acknowledge_retained_data'))" :checked="isChecked(accountDeletionField('acknowledge_retained_data'))" :required="accountDeletionField('acknowledge_retained_data')?.required" :disabled="accountDeletionField('acknowledge_retained_data')?.disabled" v-bind="fieldAttrs(accountDeletionField('acknowledge_retained_data'))">
                        <label class="form-check-label" :for="accountDeletionField('acknowledge_retained_data')?.id || 'id_acknowledge_retained_data'">{{ fieldLabel(accountDeletionField('acknowledge_retained_data')) }}<span :data-required-indicator-for="accountDeletionField('acknowledge_retained_data')?.id || 'id_acknowledge_retained_data'" class="form-required-indicator text-danger font-weight-bold ml-1" title="Required" aria-hidden="true">*</span><span :data-required-indicator-text-for="accountDeletionField('acknowledge_retained_data')?.id || 'id_acknowledge_retained_data'" class="sr-only form-required-indicator-text">Required</span></label>
                        <div v-if="fieldErrors(accountDeletionField('acknowledge_retained_data')).length" class="invalid-feedback d-block"><div v-for="errorItem in fieldErrors(accountDeletionField('acknowledge_retained_data'))" :key="errorItem">{{ errorItem }}</div></div>
                      </div>
                      <div class="form-group">
                        <label :for="accountDeletionField('current_password')?.id">Current password</label>
                        <input :id="accountDeletionField('current_password')?.id || 'id_current_password_delete'" name="current_password" type="password" :class="fieldClass(accountDeletionField('current_password'))">
                        <div v-if="fieldErrors(accountDeletionField('current_password')).length" class="invalid-feedback d-block"><div v-for="errorItem in fieldErrors(accountDeletionField('current_password'))" :key="errorItem">{{ errorItem }}</div></div>
                      </div>
                      <button type="submit" class="btn btn-danger">Submit deletion request</button>
                    </form>
                    <div v-if="payload.privacy.privacyWarnings.length" class="alert alert-warning mt-3" role="alert"><strong>Manual review required.</strong><ul class="mb-0 mt-2"><li v-for="warning in payload.privacy.privacyWarnings" :key="warning">{{ warning }}</li></ul></div>
                  </div>
                </div>
              </div>
            </div>

            <div v-if="hasAgreementsTab" class="tab-pane" :class="{ active: activeTab === 'agreements' }" data-settings-tab-pane="agreements" role="tabpanel">
              <template v-if="payload.agreements?.agreement">
                <h4>{{ payload.agreements.agreement.cn }}</h4>
                <div class="mb-2"><span class="badge" :class="payload.agreements.agreement.signed ? 'badge-success' : 'badge-danger'">{{ payload.agreements.agreement.signed ? 'Signed' : 'Not signed' }}</span></div>
                <div v-if="payload.agreements.agreement.groups.length" class="mb-3"><strong>Required for:</strong> <span v-for="(groupName, index) in payload.agreements.agreement.groups" :key="groupName"><a :href="groupDetailUrl(groupName)">{{ groupName }}</a><span v-if="index < payload.agreements.agreement.groups.length - 1">, </span></span></div>
                <div class="mb-3" v-html="formatAgreementHtml(payload.agreements.agreement.descriptionMarkdown)" />
                <form method="post" novalidate :action="bootstrap.submitUrl"><input type="hidden" name="csrfmiddlewaretoken" :value="bootstrap.csrfToken"><input type="hidden" name="tab" value="agreements"><input type="hidden" name="action" value="sign"><input type="hidden" name="cn" :value="payload.agreements.agreement.cn"><button v-if="!payload.agreements.agreement.signed" type="submit" class="btn btn-primary" title="Sign this agreement">Sign</button><button v-else type="button" class="btn btn-outline-secondary" disabled title="Already signed">Signed</button></form>
                <div class="mt-3"><a :href="bootstrap.routeConfig.agreementsUrl">Back to agreements</a></div>
              </template>
              <template v-else>
                <h4>Agreements</h4>
                <div v-if="payload.agreements?.agreements.length" class="table-responsive"><table class="table table-sm table-hover mb-0"><tbody><tr v-for="agreementItem in payload.agreements?.agreements || []" :key="agreementItem.cn"><td><div><div class="d-flex align-items-center"><strong>{{ agreementItem.cn }}</strong><span class="badge ml-2" :class="agreementItem.signed ? 'badge-success' : 'badge-danger'">{{ agreementItem.signed ? 'Signed' : 'Not signed' }}</span></div><div v-if="!agreementItem.signed && agreementItem.groups.length" class="text-muted text-sm mt-1">Required for: <span v-for="(groupName, index) in agreementItem.groups" :key="groupName"><a :href="groupDetailUrl(groupName)">{{ groupName }}</a><span v-if="index < agreementItem.groups.length - 1">, </span></span></div></div></td><td class="text-right" style="white-space: nowrap;"><a class="btn btn-sm btn-outline-secondary" :href="agreementDetailUrl(agreementItem.cn)" title="View agreement details">View agreement</a><form v-if="!agreementItem.signed" method="post" class="d-inline ml-1" novalidate :action="bootstrap.submitUrl"><input type="hidden" name="csrfmiddlewaretoken" :value="bootstrap.csrfToken"><input type="hidden" name="tab" value="agreements"><input type="hidden" name="action" value="sign"><input type="hidden" name="cn" :value="agreementItem.cn"><button type="submit" class="btn btn-sm btn-primary" title="Sign this agreement">Sign</button></form></td></tr></tbody></table></div>
                <p v-else class="text-muted">No enabled agreements.</p>
              </template>
            </div>

            <div class="tab-pane" :class="{ active: activeTab === 'membership' }" data-settings-tab-pane="membership" role="tabpanel">
              <div class="settings-membership-tab"><h2 class="h4">Membership</h2><p class="text-muted">Review your active memberships, recent history, and self-service exit options.</p><h3 class="h5 mt-4">Active memberships</h3><ul class="list-group mb-4"><li v-for="membership in payload.membership.activeMemberships" :key="membership.membershipTypeCode" class="list-group-item px-3 py-3"><div class="d-flex flex-column flex-md-row justify-content-between align-items-md-start"><div class="mb-3 mb-md-0 pr-md-3"><div class="d-flex align-items-center flex-wrap"><strong class="mr-2">{{ membership.membershipTypeName }}</strong><span class="badge badge-success">Active</span></div><div class="small text-muted mt-1">Joined {{ formatDate(membership.createdAt) }}<span v-if="membership.expiresAt">. Current term ends {{ formatDate(membership.expiresAt) }}.</span></div></div><button type="button" class="btn btn-outline-danger btn-sm align-self-start" data-toggle="collapse" :data-target="`#membership-exit-${membership.membershipTypeCode}`" aria-expanded="false">Leave membership</button></div><div class="collapse mt-3" :id="`membership-exit-${membership.membershipTypeCode}`"><div class="border border-danger rounded p-3 bg-light"><p class="small text-muted mb-3">Leave <strong>{{ membership.membershipTypeName }}</strong> when you no longer want this membership. This keeps the action compact until you need it.</p><form method="post" :action="terminationUrl(membership.membershipTypeCode)" class="needs-validation" novalidate><input type="hidden" name="csrfmiddlewaretoken" :value="bootstrap.csrfToken"><div class="form-group"><label :for="findField(membership.terminationForm, 'reason_category')?.id">Why are you leaving this membership?</label><select :id="findField(membership.terminationForm, 'reason_category')?.id || `id_${membership.membershipTypeCode}_reason_category`" name="reason_category" :class="fieldClass(findField(membership.terminationForm, 'reason_category'))"><option v-for="option in findField(membership.terminationForm, 'reason_category')?.options || []" :key="option.value" :value="option.value">{{ option.label }}</option></select><div v-if="fieldErrors(findField(membership.terminationForm, 'reason_category')).length" class="invalid-feedback d-block"><div v-for="errorItem in fieldErrors(findField(membership.terminationForm, 'reason_category'))" :key="errorItem">{{ errorItem }}</div></div></div><div class="form-group"><label :for="findField(membership.terminationForm, 'reason_text')?.id">Optional details</label><textarea :id="findField(membership.terminationForm, 'reason_text')?.id || `id_${membership.membershipTypeCode}_reason_text`" name="reason_text" rows="4" :class="fieldClass(findField(membership.terminationForm, 'reason_text'))">{{ stringValue(findField(membership.terminationForm, 'reason_text')) }}</textarea><div v-if="fieldErrors(findField(membership.terminationForm, 'reason_text')).length" class="invalid-feedback d-block"><div v-for="errorItem in fieldErrors(findField(membership.terminationForm, 'reason_text'))" :key="errorItem">{{ errorItem }}</div></div><small class="form-text text-muted">Visible only to authorized operators and cleared after the retention window.</small></div><div class="form-group"><label :for="findField(membership.terminationForm, 'current_password')?.id">Current password</label><input :id="findField(membership.terminationForm, 'current_password')?.id || `id_${membership.membershipTypeCode}_current_password`" name="current_password" type="password" :class="fieldClass(findField(membership.terminationForm, 'current_password'))"><div v-if="fieldErrors(findField(membership.terminationForm, 'current_password')).length" class="invalid-feedback d-block"><div v-for="errorItem in fieldErrors(findField(membership.terminationForm, 'current_password'))" :key="errorItem">{{ errorItem }}</div></div></div><div class="d-flex justify-content-end"><button type="submit" class="btn btn-danger btn-sm">Leave membership</button></div></form></div></div></li><li v-if="payload.membership.activeMemberships.length === 0" class="list-group-item text-muted">You do not have any active memberships.</li></ul><h3 class="h5">Recent history</h3><ul class="list-group"><li v-for="entry in payload.membership.history" :key="`${entry.membershipTypeName}-${entry.createdAt}-${entry.action}`" class="list-group-item d-flex justify-content-between align-items-start flex-column flex-md-row"><div><strong>{{ entry.membershipTypeName }}</strong><div class="small text-muted mt-1">{{ formatDateTime(entry.createdAt) }}</div></div><span class="badge badge-light mt-2 mt-md-0">{{ membershipActionLabel(entry.action) }}</span></li><li v-if="payload.membership.history.length === 0" class="list-group-item text-muted">No membership history is available yet.</li></ul></div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
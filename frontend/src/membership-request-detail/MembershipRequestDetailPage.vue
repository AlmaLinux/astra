<script setup lang="ts">
import { computed, onMounted, ref } from "vue";

import MembershipNotesCard from "../membership-requests/components/MembershipNotesCard.vue";
import MembershipRequestDetailActions from "../membership-requests/components/MembershipRequestDetailActions.vue";
import type {
  MembershipRequestCompatibilityResponse,
  MembershipRequestDetailBootstrap,
  MembershipRequestDetailFormField,
  MembershipRequestDetailPayload,
  MembershipRequestResponseSegment,
} from "./types";

const props = defineProps<{
  bootstrap: MembershipRequestDetailBootstrap;
}>();

const payload = ref<MembershipRequestDetailPayload | null>(null);
const error = ref("");
const actionError = ref("");
const isLoading = ref(false);
const isSubmitting = ref(false);
const isReopening = ref(false);

const selfServiceForm = computed(() => payload.value?.self_service?.form ?? null);
const statusDisplayMap: Record<string, string> = {
  pending: "Pending",
  approved: "Approved",
  rejected: "Rejected",
  on_hold: "On Hold",
  ignored: "Ignored",
  rescinded: "Rescinded",
};

const statusDisplay = computed(() => {
  const status = payload.value?.request.status ?? "";
  if (!status) {
    return "";
  }
  return statusDisplayMap[status] ?? status.replace(/_/g, " ").replace(/\b\w/g, (char) => char.toUpperCase());
});

const requestedForLabel = computed(() => {
  if (payload.value?.viewer.mode === "self_service" && payload.value.request.requested_for.kind === "organization") {
    return "Organization";
  }
  return "Requested for";
});

function actorLabel(): string {
  const actor = payload.value?.request.requested_by;
  if (!actor) {
    return "";
  }
  if (actor.full_name) {
    return `${actor.full_name}${actor.username ? ` (${actor.username})` : ""}`;
  }
  return actor.username;
}

function targetLabel(): string {
  const target = payload.value?.request.requested_for;
  if (!target) {
    return "";
  }
  if (target.kind === "organization") {
    return target.label;
  }
  return target.username ? `${target.label} (${target.username})` : target.label;
}

function actorUrl(): string {
  const actor = payload.value?.request.requested_by;
  if (!actor?.username || actor.deleted) {
    return "";
  }
  return props.bootstrap.userProfileUrlTemplate.replace("__username__", encodeURIComponent(actor.username));
}

function targetUrl(): string {
  const target = payload.value?.request.requested_for;
  if (!target || target.deleted) {
    return "";
  }
  if (target.kind === "organization") {
    if (target.organization_id === null) {
      return "";
    }
    return props.bootstrap.organizationDetailUrlTemplate.replace("__organization_id__", encodeURIComponent(String(target.organization_id)));
  }
  if (!target.username) {
    return "";
  }
  return props.bootstrap.userProfileUrlTemplate.replace("__username__", encodeURIComponent(target.username));
}

function fieldByName(name: string): MembershipRequestDetailFormField | null {
  return selfServiceForm.value?.fields.find((field) => field.name === name) ?? null;
}

function clearFormErrors(): void {
  if (!selfServiceForm.value) {
    return;
  }
  selfServiceForm.value.non_field_errors = [];
  for (const field of selfServiceForm.value.fields) {
    field.errors = [];
  }
}

function applyCompatibilityErrors(result: MembershipRequestCompatibilityResponse): void {
  if (!selfServiceForm.value) {
    return;
  }
  clearFormErrors();
  selfServiceForm.value.non_field_errors = [...(result.non_field_errors ?? [])];
  const fieldErrors = result.field_errors ?? {};
  for (const field of selfServiceForm.value.fields) {
    field.errors = [...(fieldErrors[field.name] ?? [])];
  }
}

async function load(): Promise<void> {
  isLoading.value = true;
  error.value = "";
  actionError.value = "";
  try {
    const response = await fetch(props.bootstrap.apiUrl, {
      headers: { Accept: "application/json" },
      credentials: "same-origin",
    });
    if (!response.ok) {
      error.value = "Unable to load membership request details right now.";
      return;
    }
    payload.value = (await response.json()) as MembershipRequestDetailPayload;
  } catch {
    error.value = "Unable to load membership request details right now.";
  } finally {
    isLoading.value = false;
  }
}

async function onCommitteeActionSuccess(): Promise<void> {
  await load();
}

async function reopenIgnoredRequest(): Promise<void> {
  const reopenUrl = props.bootstrap.reopenUrl;
  if (!reopenUrl || isReopening.value) {
    return;
  }

  isReopening.value = true;
  actionError.value = "";
  try {
    const response = await fetch(reopenUrl, {
      method: "POST",
      headers: {
        Accept: "application/json",
        "X-CSRFToken": props.bootstrap.csrfToken,
      },
      credentials: "same-origin",
    });
    let result: { ok?: boolean } | null = null;
    try {
      result = (await response.json()) as { ok?: boolean };
    } catch {
      result = null;
    }
    if (!response.ok || result?.ok === false) {
      actionError.value = "Unable to reopen membership request right now.";
      return;
    }
    await load();
  } catch {
    actionError.value = "Unable to reopen membership request right now.";
  } finally {
    isReopening.value = false;
  }
}

async function submitSelfServiceForm(): Promise<void> {
  if (!selfServiceForm.value) {
    return;
  }

  isSubmitting.value = true;
  clearFormErrors();
  const body = new URLSearchParams();
  for (const field of selfServiceForm.value.fields) {
    body.set(field.name, field.value ?? "");
  }

  try {
    const response = await fetch(props.bootstrap.formActionUrl, {
      method: "POST",
      headers: {
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "X-Astra-Compatibility-Mode": "json",
        "X-CSRFToken": props.bootstrap.csrfToken,
      },
      body: body.toString(),
      credentials: "same-origin",
    });
    const result = (await response.json()) as MembershipRequestCompatibilityResponse;
    if (!response.ok || !result.ok) {
      applyCompatibilityErrors(result);
      return;
    }
    if ((result.reread_targets ?? []).includes("detail")) {
      await load();
    }
  } catch {
    if (selfServiceForm.value) {
      selfServiceForm.value.non_field_errors = ["Failed to submit request update."];
    }
  } finally {
    isSubmitting.value = false;
  }
}

function segmentKey(segment: MembershipRequestResponseSegment, index: number): string {
  return `${segment.kind}-${segment.text}-${index}`;
}

onMounted(async () => {
  await load();
});
</script>

<template>
  <div data-membership-request-detail-vue-root>
    <div v-if="error" class="text-muted mb-3">{{ error }}</div>
    <div v-else-if="isLoading && !payload" class="text-muted mb-3">Loading membership request details...</div>
    <template v-else-if="payload">
      <div v-if="actionError" class="alert alert-danger" role="alert">{{ actionError }}</div>
      <div class="d-flex align-items-center justify-content-between flex-wrap mb-3" style="gap: .5rem;">
        <h1 class="m-0">{{ bootstrap.pageTitle }}</h1>
        <div class="d-flex align-items-center" style="gap: .5rem;">
          <a
            v-if="payload.viewer.mode === 'committee' && bootstrap.contactUrl"
            :href="bootstrap.contactUrl"
            class="btn btn-sm btn-outline-primary"
          >Contact</a>
          <a
            :href="bootstrap.backLinkUrl"
            class="btn btn-sm btn-outline-secondary"
          >{{ bootstrap.backLinkLabel }}</a>
        </div>
      </div>

      <div class="card">
        <div class="card-body">
          <dl class="row mb-0">
            <dt class="col-sm-3">Status</dt>
            <dd class="col-sm-9">{{ statusDisplay }}</dd>

            <dt class="col-sm-3">Requested at</dt>
            <dd class="col-sm-9">{{ payload.request.requested_at }}</dd>

            <template v-if="payload.request.requested_by.show">
              <dt class="col-sm-3">Requested by</dt>
              <dd class="col-sm-9">
                <a v-if="actorUrl()" :href="actorUrl()">{{ actorLabel() }}</a>
                <template v-else>{{ actorLabel() }}</template>
                <span v-if="payload.request.requested_by.deleted" class="text-muted"> (deleted)</span>
              </dd>
            </template>

            <template v-if="payload.request.requested_for.show">
              <dt class="col-sm-3">{{ requestedForLabel }}</dt>
              <dd class="col-sm-9">
                <a v-if="targetUrl()" :href="targetUrl()">{{ targetLabel() }}</a>
                <template v-else>{{ targetLabel() }}</template>
                <span v-if="payload.request.requested_for.deleted" class="text-muted"> (deleted)</span>
              </dd>
            </template>

            <dt class="col-sm-3">Type</dt>
            <dd class="col-sm-9">{{ payload.request.membership_type.name }}</dd>
          </dl>

          <template v-if="payload.request.responses.length">
            <hr>
            <h6 class="text-muted">Request responses</h6>
            <dl class="row mb-0">
              <template v-for="responseRow in payload.request.responses" :key="responseRow.question">
                <dt class="col-sm-3">{{ responseRow.question }}</dt>
                <dd class="col-sm-9">
                  <template v-for="(segment, index) in responseRow.segments" :key="segmentKey(segment, index)">
                    <a v-if="segment.kind === 'link' && segment.url" :href="segment.url">{{ segment.text }}</a>
                    <span v-else>{{ segment.text }}</span>
                  </template>
                </dd>
              </template>
            </dl>
          </template>

          <template v-if="payload.viewer.mode === 'committee'">
            <template v-if="payload.committee?.compliance_warning?.message">
              <hr>
              <div class="alert alert-warning" role="alert">
                <strong>Compliance warning:</strong>
                {{ payload.committee.compliance_warning.message }}
              </div>
            </template>

            <template v-if="bootstrap.notesCanView">
              <hr>
              <MembershipNotesCard
                :request-id="payload.request.id"
                :summary-url="bootstrap.noteSummaryUrl"
                :detail-url="bootstrap.noteDetailUrl"
                :add-url="bootstrap.noteAddUrl"
                :csrf-token="bootstrap.csrfToken"
                :next-url="bootstrap.noteNextUrl"
                :can-view="bootstrap.notesCanView"
                :can-write="bootstrap.notesCanWrite"
                :can-vote="bootstrap.notesCanVote"
              />
            </template>

            <template v-if="payload.committee?.actions">
              <hr>
              <MembershipRequestDetailActions
                :request-id="payload.request.id"
                :request-status="payload.request.status"
                :membership-type-name="payload.request.membership_type.name"
                :request-target="targetLabel()"
                :approve-url="bootstrap.approveUrl"
                :approve-on-hold-url="bootstrap.approveOnHoldUrl"
                :reject-url="bootstrap.rejectUrl"
                :rfi-url="bootstrap.rfiUrl"
                :ignore-url="bootstrap.ignoreUrl"
                :can-request-info="payload.committee.actions.canRequestInfo"
                :show-on-hold-approve="payload.committee.actions.showOnHoldApprove"
                :csrf-token="bootstrap.csrfToken"
                @action-success="onCommitteeActionSuccess"
              />
            </template>

            <template v-if="payload.committee?.reopen.show">
              <hr>
              <button
                type="button"
                class="btn btn-sm btn-outline-secondary"
                data-test="reopen-request"
                :disabled="isReopening"
                @click="reopenIgnoredRequest"
              >Reopen</button>
            </template>
          </template>

          <template v-else-if="payload.self_service">
            <template v-if="payload.self_service.can_resubmit && payload.self_service.committee_email">
              <hr>
              <div class="callout callout-danger">
                We sent you an email
                <template v-if="payload.self_service.user_email">
                  to <strong>{{ payload.self_service.user_email }}</strong>
                </template>
                asking for clarifications.
                If you have questions, email us at
                <a :href="`mailto:${payload.self_service.committee_email}`">{{ payload.self_service.committee_email }}</a>.
              </div>
            </template>

            <template v-if="payload.self_service.form">
              <hr>
              <p>Please update your request below and submit it to resume review.</p>
              <form @submit.prevent="submitSelfServiceForm">
                <div v-if="payload.self_service.form.non_field_errors.length" class="alert alert-danger" role="alert">
                  <div v-for="formError in payload.self_service.form.non_field_errors" :key="formError">{{ formError }}</div>
                </div>

                <div v-for="field in payload.self_service.form.fields" :key="field.name" class="form-group">
                  <label :for="field.name">{{ field.label }}</label>
                  <textarea
                    v-if="field.widget === 'textarea'"
                    :id="field.name"
                    :name="field.name"
                    class="form-control"
                    :disabled="field.disabled"
                    :required="field.required"
                    v-model="field.value"
                  />
                  <input
                    v-else
                    :id="field.name"
                    :name="field.name"
                    type="text"
                    class="form-control"
                    :disabled="field.disabled"
                    :required="field.required"
                    v-model="field.value"
                  >
                  <small v-if="field.help_text" class="form-text text-muted">{{ field.help_text }}</small>
                  <div v-for="fieldError in field.errors" :key="fieldError" class="invalid-feedback d-block">{{ fieldError }}</div>
                </div>

                <button type="submit" class="btn btn-success" :disabled="isSubmitting">Submit request</button>
              </form>
            </template>

            <template v-if="payload.self_service.can_rescind && bootstrap.rescindUrl">
              <hr>
              <button
                type="button"
                class="btn btn-outline-danger"
                data-test="rescind-request"
                data-toggle="modal"
                data-target="#rescind-confirm-modal"
                title="Cancel your membership request"
              >Rescind request</button>

              <div
                id="rescind-confirm-modal"
                class="modal fade"
                tabindex="-1"
                role="dialog"
                aria-labelledby="rescind-confirm-modal-title"
                aria-hidden="true"
              >
                <div class="modal-dialog" role="document">
                  <div class="modal-content">
                    <div class="modal-header">
                      <h5 id="rescind-confirm-modal-title" class="modal-title">Rescind membership request?</h5>
                      <button type="button" class="close" data-dismiss="modal" aria-label="Close" title="Close dialog">
                        <span aria-hidden="true">&times;</span>
                      </button>
                    </div>
                    <div class="modal-body">
                      Are you sure you want to rescind this <strong>membership request</strong>? This action cannot be undone.
                    </div>
                    <div class="modal-footer">
                      <button type="button" class="btn btn-secondary" data-dismiss="modal">Cancel</button>
                      <form method="post" :action="bootstrap.rescindUrl" class="m-0">
                        <input type="hidden" name="csrfmiddlewaretoken" :value="bootstrap.csrfToken">
                        <button type="submit" class="btn btn-danger">Rescind request</button>
                      </form>
                    </div>
                  </div>
                </div>
              </div>
            </template>
          </template>
        </div>
      </div>
    </template>
  </div>
</template>
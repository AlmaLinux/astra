<script setup lang="ts">
import { computed, ref } from "vue";

import { duesAmountError, formatDateInputValue } from "../membershipPresentation";
import DuesAmountInput from "./DuesAmountInput.vue";
import GuardedButton from "./GuardedButton.vue";

// The committee's dialog for an active membership or sponsorship: the expiration and,
// for paid sponsorship tiers, the dues are saved together; termination is separate.
const props = defineProps<{
  modalId: string;
  membershipType: { name: string; annualDuesUsd: number };
  // Who holds the membership; typing it confirms a termination.
  targetName: string;
  // What targetName is, e.g. "username".
  targetNoun: string;
  expiresAt: string | null;
  expiresLabel: string;
  expiryMinDate: string;
  // A sponsorship's committee-set dues (null: the standard price). Omitted for
  // individual memberships, which have no dues to waive.
  duesUsd?: number | null;
  updateUrl: string;
  terminateUrl: string;
  csrfToken: string;
  nextUrl: string;
}>();

const showDues = computed(() => props.duesUsd !== undefined && props.membershipType.annualDuesUsd > 0);

// Dues as typed: "" charges the standard price.
function duesAmount(duesUsd: number | null | undefined): string {
  return duesUsd == null || duesUsd === props.membershipType.annualDuesUsd ? "" : String(duesUsd);
}

function payableDues(amount: string): number {
  return amount === "" ? props.membershipType.annualDuesUsd : Number(amount);
}

const savedExpiresOn = formatDateInputValue(props.expiresAt);
const savedDues = duesAmount(props.duesUsd);
// Controlled inputs: typing a reason re-renders the form, which would reset uncontrolled ones.
const expiresOn = ref(savedExpiresOn);
const expirationReason = ref("");
const dues = ref(savedDues);
const duesReason = ref("");
const terminationConfirm = ref("");

const duesChanged = computed(() => showDues.value && payableDues(dues.value) !== payableDues(savedDues));

// Why the changes can't be saved yet, or "" when they can.
const saveBlocker = computed(() => {
  if (!expiresOn.value) {
    return "Enter an expiration date first.";
  }
  const amountError = showDues.value ? duesAmountError(dues.value, props.membershipType.annualDuesUsd) : "";
  if (amountError) {
    return amountError;
  }
  if (expiresOn.value === savedExpiresOn && !duesChanged.value) {
    return showDues.value ? "Change the expiration date or the dues first." : "Change the expiration date first.";
  }
  if (duesChanged.value && !duesReason.value.trim()) {
    return "Enter a reason for the dues change first.";
  }
  return "";
});

const terminationMatches = computed(
  () => terminationConfirm.value.toLowerCase().trim() === props.targetName.toLowerCase().trim(),
);
</script>

<template>
  <div
    class="modal fade"
    :id="modalId"
    tabindex="-1"
    role="dialog"
    :aria-labelledby="`${modalId}-label`"
    aria-hidden="true"
  >
    <div class="modal-dialog" role="document">
      <div class="modal-content">
        <div class="modal-header">
          <h5 class="modal-title mb-0 mr-3" :id="`${modalId}-label`">Manage membership: {{ membershipType.name }} for {{ targetName }}</h5>
          <button type="button" class="close" data-dismiss="modal" aria-label="Close" title="Close dialog">
            <span aria-hidden="true">&times;</span>
          </button>
        </div>

        <div class="modal-body">
          <form method="post" :action="updateUrl" novalidate>
            <input type="hidden" name="csrfmiddlewaretoken" :value="csrfToken">
            <input v-if="nextUrl" type="hidden" name="next" :value="nextUrl">

            <div v-if="expiresLabel" class="mb-3">Current expiration: {{ expiresLabel }}</div>

            <div class="form-group">
              <label :for="`${modalId}-expires-on`">Expiration date</label>
              <input
                :id="`${modalId}-expires-on`"
                v-model="expiresOn"
                name="expires_on"
                type="date"
                class="form-control"
                :min="expiryMinDate"
                required
              >
              <small class="form-text text-muted">Expiration is an end-of-day date in UTC.</small>
            </div>

            <div class="form-group">
              <label :for="`${modalId}-expiration-reason`">Reason for the expiration change</label>
              <textarea
                :id="`${modalId}-expiration-reason`"
                v-model="expirationReason"
                name="expiration_reason"
                class="form-control"
                rows="2"
              />
              <small class="form-text text-muted">Optional. Recorded as a committee note.</small>
            </div>

            <div v-if="showDues" class="pt-3 border-top">
              <DuesAmountInput
                :id="`${modalId}-dues`"
                v-model="dues"
                name="dues_usd"
                :default-usd="membershipType.annualDuesUsd"
              />

              <div class="form-group">
                <label :for="`${modalId}-dues-reason`">Reason for the dues change</label>
                <textarea
                  :id="`${modalId}-dues-reason`"
                  v-model="duesReason"
                  name="dues_reason"
                  class="form-control"
                  rows="2"
                  :required="duesChanged"
                />
                <small class="form-text text-muted">
                  Required when changing the dues, e.g. the in-kind agreement it reflects. Recorded as a committee note.
                </small>
              </div>
            </div>

            <div class="d-flex justify-content-between">
              <button type="button" class="btn btn-secondary" data-dismiss="modal" title="Close dialog without saving">Cancel</button>
              <GuardedButton
                type="submit"
                class="btn btn-primary"
                :blocked-reason="saveBlocker"
                title="Save the changes to this membership"
              >Save changes</GuardedButton>
            </div>
          </form>

          <div class="mt-4 pt-3 border-top">
            <div class="border border-danger rounded p-3 bg-light">
              <div class="font-weight-bold text-danger mb-1">Danger zone</div>
              <div class="small text-muted mb-3">Ends this membership early.</div>

              <div class="d-flex justify-content-end">
                <button
                  type="button"
                  class="btn btn-outline-danger"
                  data-toggle="collapse"
                  :data-target="`#${modalId}-terminate-collapse`"
                  aria-expanded="false"
                  :aria-controls="`${modalId}-terminate-collapse`"
                  title="Show termination confirmation"
                >Terminate membership&hellip;</button>
              </div>

              <div class="collapse mt-3" :id="`${modalId}-terminate-collapse`">
                <div class="alert alert-danger mb-3" role="alert">This will end the membership early and cannot be undone.</div>

                <form method="post" :action="terminateUrl" class="m-0" novalidate>
                  <input type="hidden" name="csrfmiddlewaretoken" :value="csrfToken">
                  <input v-if="nextUrl" type="hidden" name="next" :value="nextUrl">

                  <div class="form-group">
                    <label :for="`${modalId}-terminate-confirm-input`">Type the name to confirm</label>
                    <input
                      :id="`${modalId}-terminate-confirm-input`"
                      v-model="terminationConfirm"
                      name="confirm"
                      type="text"
                      class="form-control"
                      :class="{
                        'is-valid': Boolean(terminationConfirm) && terminationMatches,
                        'is-invalid': Boolean(terminationConfirm) && !terminationMatches,
                      }"
                      :placeholder="targetName"
                      autocomplete="off"
                      required
                    >
                    <div class="invalid-feedback">Does not match. Type the name to enable termination (case-insensitive).</div>
                  </div>

                  <div class="d-flex justify-content-end">
                    <button
                      type="button"
                      class="btn btn-outline-secondary"
                      data-terminate-action="cancel"
                      data-toggle="collapse"
                      :data-target="`#${modalId}-terminate-collapse`"
                      title="Cancel termination and collapse this section"
                      @click="terminationConfirm = ''"
                    >Cancel termination</button>
                    <GuardedButton
                      type="submit"
                      class="btn btn-danger ml-2"
                      :id="`${modalId}-terminate-submit`"
                      :blocked-reason="terminationMatches ? '' : `Type the ${targetNoun} to confirm first.`"
                      title="Terminate this membership now"
                    >Terminate membership</GuardedButton>
                  </div>
                </form>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

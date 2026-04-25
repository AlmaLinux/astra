<script setup lang="ts">
import { computed, onMounted, ref } from "vue";

import type { ElectionActionCardBootstrap, ElectionInfoPayload, ElectionInfoResponse } from "./types";

const props = defineProps<{
  bootstrap: ElectionActionCardBootstrap;
}>();

const election = ref<ElectionInfoPayload | null>(null);
const error = ref("");

const showOpenVoterState = computed(() => election.value?.status === "open" && election.value.can_vote);
const showOpenIneligibleState = computed(() => election.value?.status === "open" && !election.value.can_vote);
const showFinishedState = computed(() => Boolean(election.value?.election_is_finished));

async function load(): Promise<void> {
  error.value = "";
  try {
    const response = await fetch(props.bootstrap.infoApiUrl, {
      headers: { Accept: "application/json" },
      credentials: "same-origin",
    });
    if (!response.ok) {
      error.value = "Unable to load election actions right now.";
      return;
    }
    const payload = (await response.json()) as ElectionInfoResponse;
    election.value = payload.election;
  } catch {
    error.value = "Unable to load election actions right now.";
  }
}

const credentialIssuedDisplay = computed(() => {
  if (!election.value?.credential_issued_at) {
    return "";
  }
  return election.value.credential_issued_at.replace("T", " ").replace(/([+-]\d\d:\d\d|Z)$/, "").slice(0, 16);
});

onMounted(async () => {
  await load();
});
</script>

<template>
  <div data-election-action-card-vue-root>
    <div v-if="error" class="alert alert-danger">{{ error }}</div>

    <template v-else-if="election">
      <template v-if="showOpenVoterState">
        <a class="btn btn-primary btn-block" :href="bootstrap.voteUrl" title="Cast your ballot in this election">Vote</a>
        <p v-if="election.viewer_email" class="text-muted small mb-0">You'll need your voting credential, which was sent to <b>{{ election.viewer_email }}</b>.</p>
        <p v-else class="text-muted small mb-0 mt-2">You'll need your voting credential.</p>
        <p v-if="credentialIssuedDisplay" class="text-muted small mb-1">Voting credential issued: {{ credentialIssuedDisplay }} UTC</p>
      </template>

      <template v-else-if="showOpenIneligibleState">
        <p class="mb-2"><strong>You're not eligible to vote in this election.</strong></p>
        <p class="text-muted small mb-2">Eligibility is based on memberships/sponsorships of the AlmaLinux OS Foundation.</p>
        <ul class="text-muted small pl-3 mb-2">
          <li>Hold an active individual membership</li>
          <li>Or be a representative of a sponsoring organization</li>
          <li>And it must have started at least {{ election.eligibility_min_membership_age_days }} day{{ election.eligibility_min_membership_age_days === 1 ? "" : "s" }} before the election start</li>
        </ul>
        <a class="btn btn-outline-primary btn-block" :href="bootstrap.membershipRequestUrl" title="Start a membership request">Request membership</a>
      </template>

      <template v-else-if="showFinishedState">
        <a class="btn btn-outline-primary btn-block" :href="bootstrap.auditLogUrl" title="Review the election audit log">View audit log</a>
        <a class="btn btn-outline-secondary btn-block" :href="bootstrap.publicBallotsUrl" title="Download the ballots ledger (JSON)">Download ballots (JSON)</a>
        <a class="btn btn-outline-secondary btn-block" :href="bootstrap.publicAuditUrl" title="Download the audit log (JSON)">Download audit log (JSON)</a>
      </template>
    </template>
  </div>
</template>
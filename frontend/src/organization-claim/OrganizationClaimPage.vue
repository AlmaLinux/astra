<script setup lang="ts">
import type { OrganizationClaimBootstrap } from "./types";

defineProps<{
  bootstrap: OrganizationClaimBootstrap;
}>();
</script>

<template>
  <div data-organization-claim-vue-root>
    <div class="row">
      <div class="col-lg-8">
        <div class="card">
          <div class="card-body">
            <div v-if="bootstrap.state === 'invalid'" class="alert alert-danger mb-0">This claim link is invalid or has expired.</div>

            <div v-else-if="bootstrap.state === 'already_claimed'" class="alert alert-warning mb-0">
              This organization has already been claimed. If you need access,
              <a :href="`mailto:${bootstrap.membershipCommitteeEmail}`">contact the Membership Committee</a>.
            </div>

            <template v-else>
              <p>
                Claim this organization to become its representative.
                As the representative, you will be able to manage the organization's sponsorship and vote on its behalf.
              </p>

              <dl class="row mb-4">
                <dt class="col-sm-4">Organization</dt>
                <dd class="col-sm-8">{{ bootstrap.organizationName }}</dd>

                <dt class="col-sm-4">Website</dt>
                <dd class="col-sm-8">
                  <a v-if="bootstrap.organizationWebsite" :href="bootstrap.organizationWebsite" rel="noopener noreferrer">{{ bootstrap.organizationWebsite }}</a>
                  <template v-else>—</template>
                </dd>

                <dt class="col-sm-4">Contact email</dt>
                <dd class="col-sm-8">{{ bootstrap.organizationContactEmail || '—' }}</dd>
              </dl>

              <form method="post" :action="bootstrap.formAction">
                <input v-if="bootstrap.csrfToken" type="hidden" name="csrfmiddlewaretoken" :value="bootstrap.csrfToken">
                <button type="submit" class="btn btn-primary">Claim organization</button>
              </form>
            </template>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
<script setup lang="ts">
import { computed } from "vue";

import type { ContactedEmail } from "../types";

const props = defineProps<{
  requestId: number;
  contactedEmail: ContactedEmail;
}>();

const modalId = computed(() => `membership-email-modal-${props.requestId}-${props.contactedEmail.email_id ?? "email"}`);
const headerRows = computed(() => props.contactedEmail.headers ?? []);
const deliveryLogs = computed(() => props.contactedEmail.logs ?? []);
</script>

<template>
  <div class="modal fade" :id="modalId" tabindex="-1" role="dialog" aria-hidden="true" :aria-labelledby="`${modalId}-title`">
    <div class="modal-dialog modal-lg" role="document">
      <div class="modal-content">
        <div class="modal-header">
          <h5 class="modal-title" :id="`${modalId}-title`">Email details</h5>
          <button type="button" class="close" data-dismiss="modal" aria-label="Close" title="Close email details">
            <span aria-hidden="true">&times;</span>
          </button>
        </div>
        <div class="modal-body">
          <dl class="row mb-0">
            <dt class="col-sm-3">To</dt>
            <dd class="col-sm-9">{{ (contactedEmail.to || []).join(', ') }}</dd>
            <dt class="col-sm-3">Subject</dt>
            <dd class="col-sm-9">{{ contactedEmail.subject || '' }}</dd>
            <template v-if="contactedEmail.from_email">
              <dt class="col-sm-3">From</dt>
              <dd class="col-sm-9">{{ contactedEmail.from_email }}</dd>
            </template>
            <template v-if="(contactedEmail.cc || []).length">
              <dt class="col-sm-3">CC</dt>
              <dd class="col-sm-9">{{ (contactedEmail.cc || []).join(', ') }}</dd>
            </template>
            <template v-if="(contactedEmail.bcc || []).length">
              <dt class="col-sm-3">BCC</dt>
              <dd class="col-sm-9">{{ (contactedEmail.bcc || []).join(', ') }}</dd>
            </template>
            <template v-if="contactedEmail.reply_to">
              <dt class="col-sm-3">Reply-To</dt>
              <dd class="col-sm-9">{{ contactedEmail.reply_to }}</dd>
            </template>
            <dt class="col-sm-3">Recipient delivery summary</dt>
            <dd class="col-sm-9">
              {{ contactedEmail.recipient_delivery_summary || '' }}
              <div v-if="contactedEmail.recipient_delivery_summary_note" class="text-muted small">
                {{ contactedEmail.recipient_delivery_summary_note }}
              </div>
            </dd>
          </dl>
          <div v-if="headerRows.length" class="mt-3">
            <div class="text-muted small mb-1">Other headers</div>
            <ul class="small mb-0">
              <li v-for="header in headerRows" :key="header.join(':')">
                <strong>{{ header[0] }}:</strong> {{ header[1] }}
              </li>
            </ul>
          </div>
          <hr>
          <h6 class="text-muted">HTML</h6>
          <iframe
            v-if="contactedEmail.html"
            :srcdoc="contactedEmail.html"
            sandbox
            title="Email HTML preview"
            style="width: 100%; height: 320px; border: 1px solid #dee2e6; border-radius: .25rem;"
          ></iframe>
          <div v-else class="border rounded p-2 bg-light text-muted small">No HTML content.</div>
          <h6 class="text-muted mt-3">Plain text</h6>
          <pre class="border rounded p-2 bg-light mb-0" style="max-height: 260px; overflow: auto; white-space: pre-wrap;">{{ contactedEmail.text || '' }}</pre>
          <hr>
          <h6 class="text-muted">Delivery logs</h6>
          <table v-if="deliveryLogs.length" class="table table-sm">
            <thead>
              <tr>
                <th style="width: 30%;">Time</th>
                <th style="width: 20%;">Status</th>
                <th>Message</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="log in deliveryLogs" :key="`${log.date_display}-${log.status}-${log.message}`">
                <td>{{ log.date_display }}</td>
                <td>{{ log.status }}</td>
                <td>
                  {{ log.message }}
                  <div v-if="log.exception_type" class="text-muted small">{{ log.exception_type }}</div>
                </td>
              </tr>
            </tbody>
          </table>
          <div v-else class="text-muted small">No delivery logs recorded.</div>
        </div>
        <div class="modal-footer">
          <button type="button" class="btn btn-secondary" data-dismiss="modal" title="Close email details">Close</button>
        </div>
      </div>
    </div>
  </div>
</template>
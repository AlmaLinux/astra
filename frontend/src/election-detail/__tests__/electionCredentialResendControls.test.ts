import { mount } from "@vue/test-utils";
import { afterEach, describe, expect, it, vi } from "vitest";
import { nextTick } from "vue";

import ElectionCredentialResendControls from "../ElectionCredentialResendControls.vue";
import type { ElectionCredentialResendBootstrap } from "../types";

const bootstrap: ElectionCredentialResendBootstrap = {
  sendMailCredentialsApiUrl: "/api/v1/elections/1/send-mail-credentials",
  credentialEmailTemplateApiUrl: "/api/v1/elections/1/credential-email-template",
  credentialEmailPreviewUrl: "/elections/1/email/render-preview/",
  electionStatus: "open",
  eligibleUsernames: ["alice", "bob"],
};

const templatePayload = {
  subject: "Your voting credential for {{ election_name }}",
  html_content: "<p>Hi {{ first_name }}, your credential is {{ credential_public_id }}.</p>",
  text_content: "Hi {{ first_name }}, your credential is {{ credential_public_id }}.",
  variables: [
    { name: "first_name", example: "Jane" },
    { name: "election_name", example: "Board Election" },
    { name: "vote_url", example: "https://example.com/vote/1" },
  ],
  template_options: [
    { id: 10, name: "Default credential email" },
    { id: 11, name: "Custom template" },
  ],
  selected_template_id: 10,
};

function flushPromises(): Promise<void> {
  return new Promise((resolve) => {
    setTimeout(resolve, 0);
  });
}

function mockTemplateFetch(): ReturnType<typeof vi.fn> {
  return vi.fn((url: string) => {
    if (url.includes("credential-email-template")) {
      return Promise.resolve(new Response(JSON.stringify(templatePayload), { status: 200 }));
    }
    return Promise.resolve(new Response(JSON.stringify({ ok: true, message: "Queued voting credential email for 1 recipient.", recipient_count: 1 }), { status: 200 }));
  });
}

describe("ElectionCredentialResendControls", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders the resend controls and select options", () => {
    const wrapper = mount(ElectionCredentialResendControls, {
      props: { bootstrap },
    });

    expect(wrapper.text()).toContain("Send reminder to all");
    expect(wrapper.text()).toContain("Send reminder");
    const options = wrapper.findAll("select option");
    expect(options).toHaveLength(3);
    expect(wrapper.find('option[value="alice"]').exists()).toBe(true);
  });

  it("shows 'Send email' labels for closed elections", () => {
    const closedBootstrap: ElectionCredentialResendBootstrap = {
      ...bootstrap,
      electionStatus: "closed",
    };
    const wrapper = mount(ElectionCredentialResendControls, {
      props: { bootstrap: closedBootstrap },
    });

    expect(wrapper.text()).toContain("Send email to all");
    expect(wrapper.text()).toContain("Send email");
    expect(wrapper.text()).not.toContain("Send reminder");
  });

  it("single-user button is disabled when no user is selected", () => {
    const wrapper = mount(ElectionCredentialResendControls, {
      props: { bootstrap },
    });

    const button = wrapper.find<HTMLButtonElement>("[data-testid='send-reminder-single']");
    expect(button.exists()).toBe(true);
    expect(button.attributes("disabled")).toBeDefined();
  });

  it("resend-all button is disabled when there are no eligible usernames", () => {
    const emptyBootstrap: ElectionCredentialResendBootstrap = {
      sendMailCredentialsApiUrl: "/api/v1/elections/1/send-mail-credentials",
      credentialEmailTemplateApiUrl: "/api/v1/elections/1/credential-email-template",
      credentialEmailPreviewUrl: "/elections/1/email/render-preview/",
      electionStatus: "open",
      eligibleUsernames: [],
    };

    const wrapper = mount(ElectionCredentialResendControls, {
      props: { bootstrap: emptyBootstrap },
    });

    const button = wrapper.find<HTMLButtonElement>("[data-testid='send-reminder-all']");
    expect(button.attributes("disabled")).toBeDefined();
  });

  it("opens a compose modal with template content when clicking send-reminder-single", async () => {
    const fetchMock = mockTemplateFetch();
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(ElectionCredentialResendControls, {
      props: { bootstrap },
    });

    await wrapper.get("#resend-credential-username").setValue("alice");
    await wrapper.find("[data-testid='send-reminder-single']").trigger("click");
    await flushPromises();
    await nextTick();

    // Modal should be visible with template content loaded
    expect(wrapper.find(".modal").exists()).toBe(true);
    expect(wrapper.text()).toContain("Send credential reminder to alice");

    // ComposeCard subject field should be populated
    const subjectInput = wrapper.find<HTMLInputElement>('input[name="subject"]');
    expect(subjectInput.element.value).toBe(templatePayload.subject);

    // Variables should be shown
    expect(wrapper.text()).toContain("first_name");
    expect(wrapper.text()).toContain("Jane");
  });

  it("opens a compose modal for all voters when clicking send-reminder-all", async () => {
    const fetchMock = mockTemplateFetch();
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(ElectionCredentialResendControls, {
      props: { bootstrap },
    });

    await wrapper.find("[data-testid='send-reminder-all']").trigger("click");
    await flushPromises();
    await nextTick();

    expect(wrapper.find(".modal").exists()).toBe(true);
    expect(wrapper.text()).toContain("Send credential reminder to all 2 eligible voters");
  });

  it("sends credentials with edited template content on confirm", async () => {
    const fetchMock = mockTemplateFetch();
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(ElectionCredentialResendControls, {
      props: { bootstrap },
    });

    await wrapper.get("#resend-credential-username").setValue("alice");
    await wrapper.find("[data-testid='send-reminder-single']").trigger("click");
    await flushPromises();
    await nextTick();

    // Edit the subject via the ComposeCard input
    await wrapper.find<HTMLInputElement>('input[name="subject"]').setValue("Custom subject");

    // Click send
    await wrapper.find("[data-testid='send-credentials-confirm']").trigger("click");
    await flushPromises();
    await nextTick();

    // Should have made the send call with custom template content
    const sendCall = fetchMock.mock.calls.find(
      (call: unknown[]) => typeof call[0] === "string" && call[0].includes("send-mail-credentials"),
    ) as [string, RequestInit] | undefined;
    expect(sendCall).toBeDefined();

    const body = JSON.parse(sendCall![1].body as string) as Record<string, string>;
    expect(body.username).toBe("alice");
    expect(body.subject_template).toBe("Custom subject");
    expect(body.html_template).toBe(templatePayload.html_content);
    expect(body.text_template).toBe(templatePayload.text_content);

    // Modal should close, success message shown
    expect(wrapper.find(".modal").exists()).toBe(false);
    expect(wrapper.text()).toContain("Queued voting credential email for 1 recipient.");
  });

  it("closes the modal without sending when cancel is clicked", async () => {
    const fetchMock = mockTemplateFetch();
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(ElectionCredentialResendControls, {
      props: { bootstrap },
    });

    await wrapper.get("#resend-credential-username").setValue("alice");
    await wrapper.find("[data-testid='send-reminder-single']").trigger("click");
    await flushPromises();
    await nextTick();

    expect(wrapper.find(".modal").exists()).toBe(true);

    await wrapper.find("[data-testid='send-credentials-cancel']").trigger("click");
    await nextTick();

    expect(wrapper.find(".modal").exists()).toBe(false);

    // Only the template fetch should have been made, not a send
    const sendCalls = fetchMock.mock.calls.filter(
      (call: unknown[]) => typeof call[0] === "string" && call[0].includes("send-mail-credentials"),
    );
    expect(sendCalls).toHaveLength(0);
  });
});
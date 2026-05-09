import { mount } from "@vue/test-utils";
import { afterEach, describe, expect, it, vi } from "vitest";
import { nextTick } from "vue";

import ElectionCredentialResendControls from "../ElectionCredentialResendControls.vue";
import type { ElectionCredentialResendBootstrap } from "../types";

const bootstrap: ElectionCredentialResendBootstrap = {
  sendMailCredentialsApiUrl: "/api/v1/elections/1/send-mail-credentials",
  eligibleUsernames: ["alice", "bob"],
};

function flushPromises(): Promise<void> {
  return new Promise((resolve) => {
    setTimeout(resolve, 0);
  });
}

function deferredResponse(): {
  promise: Promise<Response>;
  resolve: (response: Response) => void;
} {
  let resolvePromise!: (response: Response) => void;
  const promise = new Promise<Response>((resolve) => {
    resolvePromise = resolve;
  });
  return {
    promise,
    resolve: resolvePromise,
  };
}

describe("ElectionCredentialResendControls", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders the existing resend controls and datalist options", () => {
    const wrapper = mount(ElectionCredentialResendControls, {
      props: { bootstrap },
    });

    expect(wrapper.text()).toContain("Resend all credentials");
    expect(wrapper.text()).toContain("Resend voting credential");
    expect(wrapper.findAll("datalist option")).toHaveLength(2);
    expect(wrapper.find('option[value="alice"]').exists()).toBe(true);
  });

  it("submits a single-user resend and shows inline success without redirecting", async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ ok: true, message: "Queued voting credential email for 1 recipient.", recipient_count: 1 }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    const assignMock = vi.fn();
    Object.defineProperty(window, "location", {
      configurable: true,
      value: { assign: assignMock },
    });

    const wrapper = mount(ElectionCredentialResendControls, {
      props: { bootstrap },
    });

    await wrapper.get("#resend-credential-username").setValue("alice");
    await wrapper.findAll("form")[1]?.trigger("submit.prevent");
    await flushPromises();

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const fetchCalls = fetchMock.mock.calls as unknown[][];
    const fetchOptions = fetchCalls[0]?.[1] as RequestInit;
    expect(fetchOptions).toMatchObject({
      method: "POST",
      credentials: "same-origin",
    });
    expect(String(fetchOptions.body)).toContain('"username":"alice"');
    expect(assignMock).not.toHaveBeenCalled();
    expect(wrapper.text()).toContain("Queued voting credential email for 1 recipient.");
  });

  it("renders API errors inline", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify({ ok: false, errors: ["Too many resend attempts. Please try again later."] }), { status: 429 })),
    );

    const wrapper = mount(ElectionCredentialResendControls, {
      props: { bootstrap },
    });

    await wrapper.findAll("form")[0]?.trigger("submit.prevent");
    await flushPromises();

    expect(wrapper.text()).toContain("Too many resend attempts. Please try again later.");
  });

  it("disables resend controls while a single-user request is pending so a second click does not send again", async () => {
    const pending = deferredResponse();
    const fetchMock = vi.fn(() => pending.promise);
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(ElectionCredentialResendControls, {
      props: { bootstrap },
    });

    await wrapper.get("#resend-credential-username").setValue("alice");
    const input = wrapper.get("#resend-credential-username");
    const forms = wrapper.findAll("form");
    const buttons = wrapper.findAll("button");
    const submitSingleButton = buttons[1];
    const submitAllButton = buttons[0];

    await forms[1]!.trigger("submit.prevent");
    await nextTick();

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(input.attributes("disabled")).toBeDefined();
    expect(submitSingleButton.attributes("disabled")).toBeDefined();
    expect(submitAllButton.attributes("disabled")).toBeDefined();

    await forms[1]!.trigger("submit.prevent");
    await nextTick();

    expect(fetchMock).toHaveBeenCalledTimes(1);

    pending.resolve(
      new Response(JSON.stringify({ ok: true, message: "Queued voting credential email for 1 recipient.", recipient_count: 1 }), {
        status: 200,
      }),
    );
    await flushPromises();

    expect(input.attributes("disabled")).toBeUndefined();
    expect(submitSingleButton.attributes("disabled")).toBeUndefined();
    expect(submitAllButton.attributes("disabled")).toBeUndefined();
  });
});
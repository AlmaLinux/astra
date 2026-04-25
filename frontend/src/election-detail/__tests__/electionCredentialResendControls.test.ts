import { mount } from "@vue/test-utils";
import { afterEach, describe, expect, it, vi } from "vitest";

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

  it("submits a single-user resend and redirects to send-mail", async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ ok: true, redirect_url: "/send-mail/?type=csv" }), { status: 200 }));
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
    expect(assignMock).toHaveBeenCalledWith("/send-mail/?type=csv");
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
});
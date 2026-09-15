import { mount } from "@vue/test-utils";
import { afterEach, describe, expect, it, vi } from "vitest";

import ElectionInterruptedStartWarning from "../ElectionInterruptedStartWarning.vue";
import { readElectionInterruptedStartBootstrap, type ElectionInterruptedStartBootstrap } from "../types";

const bootstrap: ElectionInterruptedStartBootstrap = {
  missingCount: 37,
  startRecorded: true,
  completeApiUrl: "/api/v1/elections/1/complete-start",
  progressApiUrl: "/api/v1/elections/1/mail-progress?kind=reminder",
};

function progress(overrides: Record<string, unknown> = {}) {
  return {
    state: "running",
    total: 37,
    processed: 10,
    emailed: 10,
    skipped: 0,
    failures: 0,
    message: "",
    updated_at: 0,
    ...overrides,
  };
}

function flushPromises(): Promise<void> {
  return new Promise((resolve) => {
    setTimeout(resolve, 20);
  });
}

function rootWith(dataset: Record<string, string>): HTMLElement {
  const root = document.createElement("div");
  for (const [key, value] of Object.entries(dataset)) {
    root.dataset[key] = value;
  }
  return root;
}

describe("ElectionInterruptedStartWarning", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("offers an unmistakable button rather than styling it into the alert", () => {
    const wrapper = mount(ElectionInterruptedStartWarning, { props: { bootstrap } });

    const button = wrapper.get("[data-election-interrupted-start-complete]");
    expect(button.classes()).toContain("btn");
    expect(button.classes()).toContain("btn-primary");
    // A yellow button on a yellow alert does not read as a control.
    expect(button.classes()).not.toContain("btn-warning");
    expect(button.text()).toBe("Finish the interrupted start");
  });

  it("names the voters who were never emailed", () => {
    const wrapper = mount(ElectionInterruptedStartWarning, { props: { bootstrap } });

    const alert = wrapper.get(".alert-warning");
    expect(alert.text()).toContain("This election's start did not finish");
    expect(alert.text()).toContain("37 eligible voters");
    expect(alert.text()).toContain("nobody who already received their credentials is emailed again");
    expect(alert.text()).not.toContain("audit log");
  });

  it("names the missing audit entry and announcement when that is what was lost", () => {
    const wrapper = mount(ElectionInterruptedStartWarning, {
      props: { bootstrap: { ...bootstrap, missingCount: 0, startRecorded: false } },
    });

    const alert = wrapper.get(".alert-warning");
    expect(alert.text()).toContain("record the start in the public audit log");
    expect(alert.text()).toContain("never announced");
    expect(alert.text()).not.toContain("eligible voters their voting credentials");
  });

  it("reports both when both were lost", () => {
    const wrapper = mount(ElectionInterruptedStartWarning, {
      props: { bootstrap: { ...bootstrap, missingCount: 2, startRecorded: false } },
    });

    expect(wrapper.findAll(".alert-warning li")).toHaveLength(2);
  });

  it("confirms the start is complete once the run finishes", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(
      JSON.stringify({ ok: true, mail_progress: progress({ state: "done", processed: 37, emailed: 37 }) }),
      { status: 200 },
    )));

    const wrapper = mount(ElectionInterruptedStartWarning, {
      props: { bootstrap: { ...bootstrap, startRecorded: false } },
    });
    await wrapper.get("[data-election-interrupted-start-complete]").trigger("click");
    await flushPromises();

    expect(wrapper.get("[data-mail-progress-counts]").text()).toContain("37 of 37 credential emails sent");
    await wrapper.get("[data-mail-progress-continue]").trigger("click");
    await flushPromises();

    expect(wrapper.find(".alert-warning").exists()).toBe(false);
    expect(wrapper.get(".alert-success").text()).toContain("recorded in the audit log and announced");
  });

  it("keeps warning about whoever the run still could not reach", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(
      JSON.stringify({ ok: true, mail_progress: progress({ state: "done", processed: 37, emailed: 34, skipped: 2, failures: 1 }) }),
      { status: 200 },
    )));

    const wrapper = mount(ElectionInterruptedStartWarning, { props: { bootstrap } });
    await wrapper.get("[data-election-interrupted-start-complete]").trigger("click");
    await flushPromises();
    await wrapper.get("[data-mail-progress-continue]").trigger("click");
    await flushPromises();

    expect(wrapper.find(".alert-success").exists()).toBe(false);
    expect(wrapper.get(".alert-warning").text()).toContain("3 eligible voters");
  });

  it("surfaces a refusal from the server", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(
      JSON.stringify({ ok: false, errors: ["This election's start already finished."] }),
      { status: 400 },
    )));

    const wrapper = mount(ElectionInterruptedStartWarning, { props: { bootstrap } });
    await wrapper.get("[data-election-interrupted-start-complete]").trigger("click");
    await flushPromises();

    expect(wrapper.get(".text-danger").text()).toContain("already finished");
    expect(wrapper.find("[data-mail-progress-counts]").exists()).toBe(false);
  });

  it("ignores repeated clicks while the run is under way", async () => {
    const fetchMock = vi.fn(async () => new Response(
      JSON.stringify({ ok: true, mail_progress: progress() }),
      { status: 200 },
    ));
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(ElectionInterruptedStartWarning, { props: { bootstrap } });
    const button = wrapper.get("[data-election-interrupted-start-complete]");
    await button.trigger("click");
    await flushPromises();
    await button.trigger("click");
    await flushPromises();

    expect(fetchMock.mock.calls.filter((call) => call[0] === bootstrap.completeApiUrl)).toHaveLength(1);
    expect(button.attributes("disabled")).toBeDefined();
  });

  it("does not mount for a start that finished cleanly", () => {
    const dataset = {
      electionInterruptedStartMissingCount: "0",
      electionInterruptedStartRecorded: "true",
      electionInterruptedStartApiUrl: bootstrap.completeApiUrl,
      electionInterruptedStartProgressApiUrl: bootstrap.progressApiUrl,
    };

    expect(readElectionInterruptedStartBootstrap(rootWith(dataset))).toBeNull();
    expect(
      readElectionInterruptedStartBootstrap(rootWith({ ...dataset, electionInterruptedStartRecorded: "false" })),
    ).not.toBeNull();
  });
});

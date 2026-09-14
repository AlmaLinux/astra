import { mount, type VueWrapper } from "@vue/test-utils";
import { afterEach, describe, expect, it, vi } from "vitest";

import ElectionStartAutomation from "../ElectionStartAutomation.vue";
import type { ElectionStartAutomationBootstrap, ElectionStartPreview, ElectionStartProgress } from "../types";

const bootstrap: ElectionStartAutomationBootstrap = {
  startApiUrl: "/api/v1/elections/1/start",
  startPreviewApiUrl: "/api/v1/elections/1/start-preview",
  startProgressApiUrl: "/api/v1/elections/1/start-progress",
  autoStartApiUrl: "/api/v1/elections/1/auto-start",
  autoStartEnabled: false,
};

function preview(overrides: Partial<ElectionStartPreview> = {}): ElectionStartPreview {
  return {
    election_name: "Board election",
    number_of_seats: 1,
    candidate_count: 2,
    eligible_voter_count: 260,
    ...overrides,
  };
}

function progress(overrides: Partial<ElectionStartProgress> = {}): ElectionStartProgress {
  return {
    state: "running",
    total: 200,
    processed: 50,
    emailed: 50,
    skipped: 0,
    failures: 0,
    message: "",
    updated_at: 0,
    ...overrides,
  };
}

function jsonResponse(body: object, status = 200): Response {
  return new Response(JSON.stringify(body), { status });
}

function flushPromises(): Promise<void> {
  return new Promise((resolve) => {
    setTimeout(resolve, 0);
  });
}

/** Answer the preview GET, then every other call with `startBody`. */
function stubFetch(startBody: object, startStatus = 200, previewBody: object = { ok: true, start_preview: preview() }) {
  const fetchMock = vi.fn(async (url: string) => {
    if (url === bootstrap.startPreviewApiUrl) {
      return jsonResponse(previewBody);
    }
    return jsonResponse(startBody, startStatus);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

async function confirmStart(wrapper: VueWrapper): Promise<void> {
  await wrapper.get("button.btn-success").trigger("click");
  await flushPromises();
  await wrapper.get("#start-election-submit").trigger("click");
  await flushPromises();
}

describe("ElectionStartAutomation", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("confirms the start with the electorate size and the vacant seat warning", async () => {
    stubFetch({ ok: true, start_progress: progress() }, 200, {
      ok: true,
      start_preview: preview({ number_of_seats: 3, candidate_count: 1 }),
    });

    const wrapper = mount(ElectionStartAutomation, { props: { bootstrap } });
    await wrapper.get("button.btn-success").trigger("click");
    await flushPromises();

    expect(wrapper.get("#start-election-modal").text()).toContain("This will open the election and email voting credentials to all eligible voters.");
    expect(wrapper.get("#start-election-modal").text()).toContain("so it will finish with 2 vacant seats");
    expect(wrapper.get(".js-eligible-voters-count").text()).toBe("260");
  });

  it("omits the vacant seat warning when every seat has a candidate", async () => {
    stubFetch({ ok: true, start_progress: progress() });

    const wrapper = mount(ElectionStartAutomation, { props: { bootstrap } });
    await wrapper.get("button.btn-success").trigger("click");
    await flushPromises();

    expect(wrapper.get("#start-election-modal").text()).not.toContain("vacant seat");
  });

  it("does nothing until the start is confirmed", async () => {
    const fetchMock = stubFetch({ ok: true, start_progress: progress() });

    const wrapper = mount(ElectionStartAutomation, { props: { bootstrap } });
    await wrapper.get("button.btn-success").trigger("click");
    await flushPromises();

    expect(fetchMock.mock.calls.filter((call) => call[0] === bootstrap.startApiUrl)).toHaveLength(0);

    await wrapper.get(".modal-footer button.btn-outline-secondary").trigger("click");
    expect(wrapper.find("#start-election-modal").exists()).toBe(false);
  });

  it("shows a progress modal with the share of credential emails sent", async () => {
    stubFetch({ ok: true, start_progress: progress() });

    const wrapper = mount(ElectionStartAutomation, { props: { bootstrap } });
    await confirmStart(wrapper);

    expect(wrapper.find("#start-election-modal").exists()).toBe(false);
    expect(wrapper.get(".progress-bar").attributes("style")).toContain("width: 25%");
    expect(wrapper.get("[data-election-start-progress-counts]").text()).toContain("50 of 200 credential emails sent");
  });

  it("ignores repeated clicks while a start is under way", async () => {
    const fetchMock = stubFetch({ ok: true, start_progress: progress() });

    const wrapper = mount(ElectionStartAutomation, { props: { bootstrap } });
    await confirmStart(wrapper);

    const startButton = wrapper.get("button.btn-success");
    await startButton.trigger("click");
    await startButton.trigger("click");
    await flushPromises();

    expect(startButton.attributes("disabled")).toBeDefined();
    expect(fetchMock.mock.calls.filter((call) => call[0] === bootstrap.startApiUrl)).toHaveLength(1);
  });

  it("reports a finished delivery and offers to continue", async () => {
    stubFetch({ ok: true, start_progress: progress({ state: "done", processed: 200, emailed: 198, skipped: 2 }) });

    const wrapper = mount(ElectionStartAutomation, { props: { bootstrap } });
    await confirmStart(wrapper);

    expect(wrapper.get(".modal-title").text()).toBe("Election started");
    expect(wrapper.get(".progress-bar").classes()).toContain("bg-success");
    expect(wrapper.get("[data-election-start-progress-counts]").text()).toContain("2 skipped");
    expect(wrapper.get("#start-election-continue").attributes("disabled")).toBeUndefined();
  });

  it("lists every reason the server rejected the start", async () => {
    stubFetch({ ok: false, errors: ["Candidate is not eligible: alice", "No eligible voters were found for this election."] }, 400);

    const wrapper = mount(ElectionStartAutomation, { props: { bootstrap } });
    await confirmStart(wrapper);

    const modal = wrapper.get("#start-election-modal");
    expect(modal.get(".alert-danger").text()).toContain("Candidate is not eligible: alice");
    expect(modal.get(".alert-danger").text()).toContain("No eligible voters were found for this election.");
    expect(wrapper.get("#start-election-submit").attributes("disabled")).toBeUndefined();
  });

  it("warns when delivery stopped before finishing", async () => {
    stubFetch({ ok: true, start_progress: progress({ state: "stalled", message: "Credential delivery stopped before it finished." }) });

    const wrapper = mount(ElectionStartAutomation, { props: { bootstrap } });
    await confirmStart(wrapper);

    expect(wrapper.get(".progress-bar").classes()).toContain("bg-danger");
    expect(wrapper.get(".alert-warning").text()).toBe("Credential delivery stopped before it finished.");
  });
});

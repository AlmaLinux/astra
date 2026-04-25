import { mount } from "@vue/test-utils";
import { afterEach, describe, expect, it, vi } from "vitest";

import ElectionVoterSearchForm from "../ElectionVoterSearchForm.vue";
import type { ElectionVoterSearchBootstrap } from "../types";

const eligibleBootstrap: ElectionVoterSearchBootstrap = {
  fieldName: "eligible_q",
  value: "ali",
  placeholder: "Search users...",
  ariaLabel: "Search users",
  submitTitle: "Search eligible voters",
  width: "220px",
};

describe("ElectionVoterSearchForm", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("submits the field-specific query on the current pathname", async () => {
    const assign = vi.fn();
    Object.defineProperty(window, "location", {
      value: {
        pathname: "/elections/1/",
        assign,
      },
      configurable: true,
    });

    const wrapper = mount(ElectionVoterSearchForm, { props: { bootstrap: eligibleBootstrap } });
    await wrapper.get('input[name="eligible_q"]').setValue("bob");
    await wrapper.get("form").trigger("submit.prevent");

    expect(assign).toHaveBeenCalledWith("/elections/1/?eligible_q=bob");
  });

  it("clears the query when the clear button is pressed", async () => {
    const assign = vi.fn();
    Object.defineProperty(window, "location", {
      value: {
        pathname: "/elections/1/",
        assign,
      },
      configurable: true,
    });

    const wrapper = mount(ElectionVoterSearchForm, { props: { bootstrap: eligibleBootstrap } });
    await wrapper.get('button[title="Clear search filter"]').trigger("click");

    expect(assign).toHaveBeenCalledWith("/elections/1/");
  });
});
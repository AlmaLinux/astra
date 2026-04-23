import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";

import TableBase from "../components/TableBase.vue";

describe("TableBase", () => {
  const rows = [
    { id: 11, name: "Alice", status: "pending" },
    { id: 12, name: "Bob", status: "pending" },
  ];

  it("renders slot-based row cells and emits bulk-submit with selected IDs", async () => {
    const wrapper = mount(TableBase, {
      props: {
        rows,
        count: 2,
        currentPage: 1,
        totalPages: 1,
        isLoading: false,
        error: "",
        getRowId: (row: { id: number }) => row.id,
        columns: [
          { key: "name", label: "Name" },
          { key: "status", label: "Status" },
        ],
        colspan: 3,
        checkboxClass: "shared-checkbox",
        paginationAriaLabel: "Shared pagination",
        bulkActions: [
          { value: "approve", label: "Approve" },
        ],
      },
      slots: {
        "row-cells": '<td class="name-cell">{{ row.name }}</td><td>{{ row.status }}</td>',
      },
    });

    const firstCheckbox = wrapper.findAll("tbody input[type='checkbox']").at(0);
    await firstCheckbox?.setValue(true);
    await wrapper.find("select[name='bulk_action']").setValue("approve");
    await wrapper.find("form").trigger("submit");

    expect(wrapper.emitted("bulk-submit")?.[0]?.[0]).toEqual({
      action: "approve",
      scope: undefined,
      selectedIds: ["11"],
    });
    expect(wrapper.find(".name-cell").text()).toBe("Alice");
  });

  it("renders loading, error, and empty states inside tbody rows", async () => {
    const loadingWrapper = mount(TableBase, {
      props: {
        rows: [],
        count: 0,
        currentPage: 1,
        totalPages: 1,
        isLoading: true,
        error: "",
        getRowId: (row: { id: number }) => row.id,
        columns: [{ key: "name", label: "Name" }],
        colspan: 2,
        checkboxClass: "shared-checkbox",
        paginationAriaLabel: "Shared pagination",
      },
      slots: {
        "row-cells": "<td></td>",
      },
    });
    expect(loadingWrapper.find("tbody td[colspan='2']").text()).toContain("Loading...");

    await loadingWrapper.setProps({ isLoading: false, error: "boom" });
    expect(loadingWrapper.find("tbody td[colspan='2']").text()).toContain("boom");

    await loadingWrapper.setProps({ error: "" });
    expect(loadingWrapper.find("tbody td[colspan='2']").text()).toContain("No items.");
  });
});

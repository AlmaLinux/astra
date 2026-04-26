import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";

import MembershipCard from "../MembershipCard.vue";

describe("MembershipCard", () => {
  it("renders a shared membership shell with actions, entries, and notes", () => {
    const wrapper = mount(MembershipCard, {
      props: {
        title: "Membership",
        notes: {
          summaryUrl: "/summary",
          detailUrl: "/detail",
          addUrl: "/add",
          csrfToken: "csrf",
          nextUrl: "/next",
          canView: true,
          canWrite: false,
          targetType: "org",
          target: "1",
        },
        requestDetailTemplate: "/membership/request/__request_id__/",
      },
      slots: {
        actions: '<a href="/membership/history/" class="btn btn-sm btn-outline-secondary">History</a>',
        default: '<li class="list-group-item">Membership row</li>',
      },
      global: {
        stubs: {
          MembershipNotesCard: {
            props: ["targetType", "target", "requestDetailTemplate"],
            template: '<div data-test="membership-notes">{{ targetType }}:{{ target }}:{{ requestDetailTemplate }}</div>',
          },
        },
      },
    });

    expect(wrapper.find("[data-membership-card-root]").exists()).toBe(true);
    expect(wrapper.text()).toContain("Membership");
    expect(wrapper.text()).toContain("History");
    expect(wrapper.text()).toContain("Membership row");
    expect(wrapper.find('[data-test="membership-notes"]').text()).toBe("org:1:/membership/request/__request_id__/");
  });
});
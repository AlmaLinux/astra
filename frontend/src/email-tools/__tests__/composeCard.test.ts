import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";

import ComposeCard from "../ComposeCard.vue";

function field(name: string) {
  return { id: `id_${name}`, name, value: "", cssClass: "form-control", attrs: {}, errors: [] };
}

describe("ComposeCard", () => {
  it("includes the notice modal that replaces browser alerts, inside the compose container", () => {
    const wrapper = mount(ComposeCard, {
      props: {
        templateOptions: [],
        selectedTemplateId: null,
        availableVariables: [],
        previewUrl: "/preview/",
        preview: { subject: "", html: "", text: "" },
        subjectField: field("subject"),
        htmlContentField: field("html_content"),
        textContentField: field("text_content"),
      },
    });

    // templated_email.js looks the modal up within the compose container and fills these.
    const notice = wrapper.get('[data-templated-email-compose] [data-compose-modal="notice"] .modal');
    expect(notice.find(".modal-title").exists()).toBe(true);
    expect(notice.find(".modal-body").exists()).toBe(true);
    expect(notice.findAll("button").map((button) => button.text())).toContain("OK");
  });
});

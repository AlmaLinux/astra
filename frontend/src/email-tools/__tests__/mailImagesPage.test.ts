import { mount } from "@vue/test-utils";
import { afterEach, describe, expect, it, vi } from "vitest";

import MailImagesPage from "../MailImagesPage.vue";
import type { MailImagesBootstrap } from "../types";

const bootstrap: MailImagesBootstrap = {
  apiUrl: "/api/v1/email-tools/images/detail",
  submitUrl: "/email-tools/images/",
  csrfToken: "csrf-token",
  initialPayload: {
    mailImagesPrefix: "mail-images/",
    exampleImageUrl: "https://cdn.example/mail-images/path/to/image.png",
    images: [
      {
        key: "mail-images/logo.png",
        relativeKey: "logo.png",
        url: "https://cdn.example/mail-images/logo.png",
        sizeBytes: 123,
        modifiedAt: "2026-01-01T00:00:00+00:00",
      },
    ],
  },
};

describe("MailImagesPage", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders the image instructions, upload form, and stored images from raw data", () => {
    const wrapper = mount(MailImagesPage, {
      props: { bootstrap },
    });

    expect(wrapper.text()).toContain("How To Use Images in Email Templates");
    expect(wrapper.find('form[action="/email-tools/images/"]').exists()).toBe(true);
    expect(wrapper.text()).toContain("mail-images/");
    expect(wrapper.text()).toContain("logo.png");
    expect(wrapper.text()).toContain("123 bytes");
    expect(wrapper.text()).toContain("2026-01-01 00:00:00 UTC");
    expect(wrapper.find('a[href="https://cdn.example/mail-images/logo.png"]').exists()).toBe(true);
  });
});
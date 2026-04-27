import { afterEach, describe, expect, it, vi } from "vitest";

import { mountSendMailPage } from "../../entrypoints/emailTools";

function buildRoot(attributes: Record<string, string>, initialPayload?: object): HTMLDivElement {
  const root = document.createElement("div");
  root.setAttribute("data-send-mail-root", "");
  for (const [key, value] of Object.entries(attributes)) {
    root.setAttribute(key, value);
  }
  if (initialPayload) {
    const script = document.createElement("script");
    script.type = "application/json";
    script.id = "send-mail-initial-payload";
    script.textContent = JSON.stringify(initialPayload);
    root.appendChild(script);
  }
  document.body.appendChild(root);
  return root;
}

describe("sendMail entrypoint", () => {
  afterEach(() => {
    document.body.innerHTML = "";
    vi.restoreAllMocks();
  });

  it("mounts the send-mail shell from embedded initial payload without fetching", () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const root = buildRoot(
      {
        "data-send-mail-api-url": "/api/v1/email-tools/send-mail/detail",
        "data-send-mail-submit-url": "/email-tools/send-mail/",
        "data-send-mail-preview-url": "/email-tools/send-mail/render-preview/",
        "data-send-mail-csrf-token": "csrf",
      },
      {
        selected_recipient_mode: "",
        action_status: "",
        action_notice: "",
        has_saved_csv_recipients: false,
        created_template_id: null,
        templates: [],
        form: { is_bound: false, non_field_errors: [], fields: [] },
        recipient_preview: { variables: [], recipient_count: 0, first_context: {}, skipped_count: 0 },
        compose: { selected_template_id: null, preview: { subject: "", html: "", text: "" } },
      },
    );

    const app = mountSendMailPage(root);

    expect(app).not.toBeNull();
    expect(fetchMock).not.toHaveBeenCalled();
    expect(root.querySelector("[data-send-mail-vue-root]")).not.toBeNull();
  });
});
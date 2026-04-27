import { afterEach, describe, expect, it, vi } from "vitest";

import { mountEmailTemplatesPage, mountEmailTemplateEditorPage, mountMailImagesPage } from "../../entrypoints/emailTools";

function buildRoot(attributeName: string, attributes: Record<string, string>, initialPayload?: object): HTMLDivElement {
  const root = document.createElement("div");
  root.setAttribute(attributeName, "");
  for (const [key, value] of Object.entries(attributes)) {
    root.setAttribute(key, value);
  }
  if (initialPayload) {
    const script = document.createElement("script");
    script.type = "application/json";
    script.id = attributes["data-initial-payload-id"] || "payload";
    script.textContent = JSON.stringify(initialPayload);
    root.appendChild(script);
  }
  document.body.appendChild(root);
  return root;
}

describe("emailTools entrypoints", () => {
  afterEach(() => {
    document.body.innerHTML = "";
    vi.restoreAllMocks();
  });

  it("mounts the templates list shell from embedded initial payload without fetching", () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const root = buildRoot(
      "data-email-templates-root",
      {
        "data-email-templates-api-url": "/api/v1/email-tools/templates/detail",
        "data-email-template-create-url": "/email-tools/templates/new/",
        "data-email-template-edit-url-template": "/email-tools/templates/__template_id__/",
        "data-email-template-delete-url-template": "/email-tools/templates/__template_id__/delete/",
        "data-email-template-csrf-token": "csrf",
        "data-initial-payload-id": "email-templates-initial-payload",
      },
      { templates: [] },
    );

    const app = mountEmailTemplatesPage(root);

    expect(app).not.toBeNull();
    expect(fetchMock).not.toHaveBeenCalled();
    expect(root.querySelector("[data-email-templates-vue-root]")).not.toBeNull();
  });

  it("mounts the editor and images shells when required bootstrap attrs exist", () => {
    const editorRoot = buildRoot("data-email-template-editor-root", {
      "data-email-template-editor-api-url": "/api/v1/email-tools/templates/new/detail",
      "data-email-template-list-url": "/email-tools/templates/",
      "data-email-template-submit-url": "/email-tools/templates/new/",
      "data-email-template-preview-url": "/email-tools/templates/render-preview/",
      "data-email-template-csrf-token": "csrf",
      "data-initial-payload-id": "email-template-editor-initial-payload",
    }, {
      mode: "create",
      template: null,
      form: { is_bound: false, non_field_errors: [], fields: [] },
      compose: { selected_template_id: null, template_options: [], available_variables: [], preview: { subject: "", html: "", text: "" } },
    });
    const imagesRoot = buildRoot("data-mail-images-root", {
      "data-mail-images-api-url": "/api/v1/email-tools/images/detail",
      "data-mail-images-submit-url": "/email-tools/images/",
      "data-mail-images-csrf-token": "csrf",
      "data-initial-payload-id": "mail-images-initial-payload",
    }, {
      mail_images_prefix: "mail-images/",
      example_image_url: "https://cdn.example/mail-images/path/to/image.png",
      images: [],
    });

    expect(mountEmailTemplateEditorPage(editorRoot)).not.toBeNull();
    expect(mountMailImagesPage(imagesRoot)).not.toBeNull();
  });
});
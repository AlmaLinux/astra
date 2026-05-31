import { expect, test, type Locator, type Page } from "@playwright/test";

const tinyPng = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9WfXcS8AAAAASUVORK5CYII=",
  "base64",
);

const existingTemplateName = "password-reset-success";
const existingTemplateSubject = "Your password has been reset";

async function loginViaForm(page: Page, username: string, password: string): Promise<void> {
  await page.goto("/login/");
  await page.getByLabel("Username", { exact: true }).fill(username);
  await page.getByLabel("Password", { exact: true }).fill(password);
  await page.getByRole("button", { name: /log in/i }).click();
}

async function openMailToolsRoute(page: Page, path: string): Promise<void> {
  await page.goto(path);
}

async function fillCodeMirrorEditor(page: Page, index: number, value: string): Promise<void> {
  const editor = page.locator(".CodeMirror").nth(index);
  await editor.click();
  await page.keyboard.press("Control+A");
  await page.keyboard.insertText(value);
}

function rowForTemplate(page: Page, templateName: string): Locator {
  return page.locator("tbody tr").filter({ hasText: templateName }).first();
}

async function openSendMailWorkflow(page: Page): Promise<void> {
  await loginViaForm(page, "admin", "admin-password");
  await openMailToolsRoute(page, "/email-tools/send-mail/");

  await expect(page).toHaveURL(/\/email-tools\/send-mail\/?$/);
  await expect(page.locator("[data-send-mail-root]")).toBeVisible();
  await expect(page.getByText("Loading send mail...")).toHaveCount(0);
  await expect(page.getByText("Unable to load send mail right now.")).toHaveCount(0);
}

async function selectExistingTemplate(page: Page): Promise<void> {
  await page.locator('select[name="email_template_id"]').selectOption({ label: existingTemplateName });
  await expect(page.locator("#id_subject")).toHaveValue(existingTemplateSubject);
  await expect(page.locator("iframe[title='Rendered HTML preview']")).toBeVisible();
  await expect(page.locator("iframe[title='Rendered text preview']")).toBeVisible();
}

async function confirmSendAndExpectQueued(page: Page, recipientCount: number): Promise<void> {
  const plural = recipientCount === 1 ? "" : "s";

  await expect(page.locator("#send-mail-send-btn")).toBeEnabled();
  await page.locator("#send-mail-send-btn").click();
  const sendModal = page.locator("#send-mail-send-confirm-modal");
  await expect(sendModal).toBeVisible();
  await expect(
    sendModal.getByText(
      `Queue ${recipientCount} email${plural} for delivery using the current recipients and message contents?`,
    ),
  ).toBeVisible();
  await expect(sendModal.locator("#send-mail-send-confirm-btn")).toBeVisible();
  await sendModal.locator("#send-mail-send-confirm-btn").click();
  await expect(page.getByText(`Queued ${recipientCount} email${plural}.`, { exact: true })).toBeVisible();
}

async function selectUserRecipients(page: Page, usernames: string[]): Promise<void> {
  await page.locator("#id_user_usernames").evaluate((element, values) => {
    const select = element as HTMLSelectElement;
    for (const option of select.options) {
      option.selected = values.includes(option.value);
    }
    select.dispatchEvent(new Event("change", { bubbles: true }));
  }, usernames);
}

// As a send-mail operator, I can choose recipients by group, users, CSV, or manual addresses.
// As a send-mail operator, I can inspect recipient count and optional headers before sending.
// As a send-mail operator, I can compose a templated message, preview it for the first recipient, and open the save-as template dialog without persisting changes.
// As a send-mail operator, I can explicitly confirm the final send action.
test("mail-tools-send-mail-workflow", async ({ page }) => {
  await openSendMailWorkflow(page);

  await page.getByRole("tab", { name: "Users", exact: true }).click();
  await expect(page.locator("#send-mail-recipients-users")).toHaveClass(/active/);

  await page.getByRole("tab", { name: "Manual", exact: true }).click();
  await expect(page.getByLabel("Manual to:", { exact: true })).toBeVisible();

  await page.getByRole("tab", { name: "CSV file", exact: true }).click();
  await page.locator("#id_csv_file").setInputFiles({
    name: "mail-tools-recipients.csv",
    mimeType: "text/csv",
    buffer: Buffer.from("Email\nregular01@example.test\nregular02@example.test\n", "utf-8"),
  });

  await page.getByRole("button", { name: "Load file", exact: true }).click();

  await expect(page.locator("[data-send-mail-root]")).toBeVisible();
  await expect(page.locator("#send-mail-recipient-count")).toHaveText("2");

  await page.locator("#send-mail-extra-options-toggle").click();
  await expect(page.getByLabel("Cc:", { exact: true })).toBeVisible();
  await page.getByLabel("Cc:", { exact: true }).fill("cc@example.test");
  await page.getByLabel("Bcc:", { exact: true }).fill("bcc@example.test");
  await page.getByLabel("Reply to:", { exact: true }).fill("reply@example.test");

  await page.locator("#id_subject").fill("Wave 7 browser subject");
  await expect(page.locator("iframe[title='Rendered HTML preview']")).toBeVisible();
  await expect(page.locator("iframe[title='Rendered text preview']")).toBeVisible();

  await page.locator('[data-compose-action="save-as"]').click();
  const saveAsModal = page.getByRole("dialog").filter({ hasText: "Save email template as…" }).first();
  await expect(saveAsModal).toBeVisible();
  await expect(saveAsModal.getByText("Create a new template from the current subject and contents?")).toBeVisible();
  await saveAsModal.locator('input[name="name"]').fill(`wave7-save-as-${Date.now()}`);
  await saveAsModal.getByRole("button", { name: "Cancel", exact: true }).click();
  await expect(saveAsModal).not.toBeVisible();

  await expect(page.locator("#send-mail-send-btn")).toBeEnabled();
  await page.locator("#send-mail-send-btn").click();
  const sendModal = page.locator("#send-mail-send-confirm-modal");
  await expect(sendModal).toBeVisible();
  await expect(sendModal.getByText("Queue 2 emails for delivery using the current recipients and message contents?")).toBeVisible();
  await expect(sendModal.locator("#send-mail-send-confirm-btn")).toBeVisible();
  await sendModal.locator("#send-mail-send-confirm-btn").click();
  await expect(page.getByText("Queued 2 emails.", { exact: true })).toBeVisible();
});

// As a send-mail operator, I can queue a templated message to all members of a selected group.
test("mail-tools-send-mail-group-workflow", async ({ page }) => {
  await openSendMailWorkflow(page);

  await page.locator("#id_group_cn").selectOption("admins");

  await expect(page.locator("#send-mail-recipients-group")).toHaveClass(/active/);
  await expect(page.locator("#send-mail-recipient-count")).toHaveText("1");

  await selectExistingTemplate(page);
  await confirmSendAndExpectQueued(page, 1);
});

// As a send-mail operator, I can queue a templated message to explicitly selected users.
test("mail-tools-send-mail-users-workflow", async ({ page }) => {
  await openSendMailWorkflow(page);

  await page.getByRole("tab", { name: "Users", exact: true }).click();
  await expect(page.locator("#id_user_usernames option")).not.toHaveCount(0);
  await selectUserRecipients(page, ["regular01", "regular02"]);

  await expect(page.locator("#send-mail-recipients-users")).toHaveClass(/active/);
  await expect(page.locator("#send-mail-recipient-count")).toHaveText("2");

  await selectExistingTemplate(page);
  await confirmSendAndExpectQueued(page, 2);
});

// As a send-mail operator, I can queue a templated message to manually entered addresses.
test("mail-tools-send-mail-manual-workflow", async ({ page }) => {
  await openSendMailWorkflow(page);

  await page.getByRole("tab", { name: "Manual", exact: true }).click();
  await page.getByLabel("Manual to:", { exact: true }).fill("manual01@example.test, manual02@example.test");

  await expect(page.locator("#send-mail-recipients-manual")).toHaveClass(/active/);
  await expect(page.locator("#send-mail-recipient-count")).toHaveText("2");

  await selectExistingTemplate(page);
  await confirmSendAndExpectQueued(page, 2);
});

// As a template manager, I can list templates, create new ones, edit details/content, preview rendered HTML/text, and delete unlocked templates.
// As a mail operator, I can manage uploaded images used by email templates.
test("mail-tools-template-manager-and-images", async ({ page }) => {
  await loginViaForm(page, "admin", "admin-password");
  await openMailToolsRoute(page, "/email-tools/templates/");

  await expect(page).toHaveURL(/\/email-tools\/templates\/?$/);
  await expect(page.locator("[data-email-templates-root]")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Email Templates", exact: true })).toBeVisible();

  const templateName = `wave7-template-${Date.now()}`;
  await page.getByRole("link", { name: "New template", exact: true }).click();

  await expect(page.locator("[data-email-template-editor-root]")).toBeVisible();
  await page.getByLabel("Name:", { exact: true }).fill(templateName);
  await page.getByLabel("Description:", { exact: true }).fill("Wave 7 browser-created template");
  await page.getByLabel("Subject:", { exact: true }).fill("Wave 7 template subject");
  await expect(page.locator(".CodeMirror")).toHaveCount(2);
  await fillCodeMirrorEditor(page, 0, "<p>Template HTML body</p>");
  await fillCodeMirrorEditor(page, 1, "Template text body");
  await expect(page.locator("iframe[title='Rendered HTML preview']")).toHaveAttribute("srcdoc", /Template HTML body/);

  await page.getByRole("button", { name: "Save", exact: true }).click();

  await expect(page.locator("[data-email-template-editor-root]")).toBeVisible();
  await expect(page.getByLabel("Description:", { exact: true })).toHaveValue("Wave 7 browser-created template");
  await page.getByRole("link", { name: "Back", exact: true }).click();

  await expect(page.locator("[data-email-templates-root]")).toBeVisible();
  const templateRow = rowForTemplate(page, templateName);
  await expect(templateRow).toBeVisible();
  await templateRow.getByRole("button", { name: "Delete", exact: true }).click();

  const deleteTemplateModal = page.locator("#email-template-delete-modal");
  await expect(deleteTemplateModal).toBeVisible();
  await expect(deleteTemplateModal.getByText(templateName)).toBeVisible();
  await deleteTemplateModal.getByRole("button", { name: "Delete", exact: true }).click();
  await expect(rowForTemplate(page, templateName)).toHaveCount(0);

  await openMailToolsRoute(page, "/email-tools/images/");
  await expect(page).toHaveURL(/\/email-tools\/images\/?$/);
  await expect(page.locator("[data-mail-images-root]")).toBeVisible();
  await expect(page.getByRole("heading", { name: "How To Use Images in Email Templates", exact: true })).toBeVisible();
  await expect(page.getByText(/Inline \(embedded\) image/i)).toBeVisible();
  await expect(page.getByText(/External URL/i)).toBeVisible();

  const imageName = `wave7-image-${Date.now()}.png`;
  await page.getByLabel("Folder (optional)", { exact: true }).fill("wave7");
  await page.locator("#mail-images-upload-files").setInputFiles({
    name: imageName,
    mimeType: "image/png",
    buffer: tinyPng,
  });
  await page.locator('label[for="mail-images-overwrite"]').click();
  await expect(page.locator("#mail-images-overwrite")).toBeChecked();

  await page.getByRole("button", { name: "Upload", exact: true }).click();

  const imageRow = page.locator("tbody tr").filter({ hasText: imageName }).first();
  await expect(imageRow).toBeVisible();
  await imageRow.getByRole("button", { name: "Delete", exact: true }).click();

  const deleteImageModal = page.locator("#delete-mail-image-modal");
  await expect(deleteImageModal).toBeVisible();
  await expect(deleteImageModal.getByText(imageName)).toBeVisible();
  await deleteImageModal.getByRole("button", { name: "Delete", exact: true }).click();
  await expect(page.locator("tbody tr").filter({ hasText: imageName })).toHaveCount(0);
});
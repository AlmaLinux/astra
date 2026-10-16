import { afterEach, describe, expect, it, vi } from "vitest";

import { copyToClipboard } from "../clipboard";

describe("copyToClipboard", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: undefined });
  });

  it("uses the Clipboard API when the page may", async () => {
    const writeText = vi.fn(async () => undefined);
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText } });

    expect(await copyToClipboard("abc123")).toBe(true);
    expect(writeText).toHaveBeenCalledWith("abc123");
  });

  it("falls back to a selection copy where the Clipboard API is unavailable", async () => {
    let copied = "";
    const execCommand = vi.fn(() => {
      const textarea = document.querySelector("textarea")!;
      copied = textarea.value.slice(textarea.selectionStart, textarea.selectionEnd);
      return true;
    });
    Object.defineProperty(document, "execCommand", { configurable: true, value: execCommand });

    expect(await copyToClipboard("abc123")).toBe(true);
    expect(execCommand).toHaveBeenCalledWith("copy");
    expect(copied).toBe("abc123");
    // The helper cleans up after itself.
    expect(document.querySelector("textarea")).toBeNull();
  });

  it("reports failure when neither way works", async () => {
    Object.defineProperty(document, "execCommand", { configurable: true, value: vi.fn(() => false) });

    expect(await copyToClipboard("abc123")).toBe(false);
  });
});

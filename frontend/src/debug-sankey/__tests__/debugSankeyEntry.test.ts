import { afterEach, describe, expect, it, vi } from "vitest";

describe("debugSankey entrypoint", () => {
  afterEach(() => {
    document.body.replaceChildren();
    vi.resetModules();
  });

  it("mounts on the debug sankey root", async () => {
    document.body.innerHTML = `
      <div
        data-debug-sankey-root
        data-debug-sankey-flows-id="debug-sankey-data"
        data-debug-sankey-elected-id="debug-sankey-elected"
        data-debug-sankey-eliminated-id="debug-sankey-eliminated"
      ></div>
      <script type="application/json" id="debug-sankey-data">[]</script>
      <script type="application/json" id="debug-sankey-elected">[]</script>
      <script type="application/json" id="debug-sankey-eliminated">[]</script>
    `;

    await import("../../entrypoints/debugSankey");
    await new Promise((resolve) => {
      setTimeout(resolve, 0);
    });

    expect(document.querySelector("[data-debug-sankey-vue-root]")).not.toBeNull();
  });
});
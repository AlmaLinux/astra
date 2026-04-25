import { readdirSync } from "node:fs";
import { basename, resolve } from "node:path";
import { pathToFileURL } from "node:url";

import { describe, expect, it } from "vitest";

import { buildEntrypointInputs } from "../viteEntrypoints";

describe("vite build entrypoints", () => {
  it("includes every entrypoint file in the production manifest inputs", async () => {
    const projectRootUrl = pathToFileURL(`${resolve(__dirname, "../../..")}/`);
    const input = buildEntrypointInputs(projectRootUrl);
    const entrypointDir = resolve(__dirname, "../../entrypoints");
    const entrypoints = readdirSync(entrypointDir)
      .filter((filename) => filename.endsWith(".ts"))
      .map((filename) => basename(filename, ".ts"))
      .sort();

    expect(Object.keys(input).sort()).toEqual(entrypoints);
    expect(input.electionEdit).toContain("src/entrypoints/electionEdit.ts");
  });
});

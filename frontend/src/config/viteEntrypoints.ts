import { readdirSync } from "node:fs";
import { basename } from "node:path";
import { fileURLToPath, URL } from "node:url";

export function buildEntrypointInputs(projectRootUrl: URL): Record<string, string> {
  const entrypointsUrl = new URL("./src/entrypoints/", projectRootUrl);
  return Object.fromEntries(
    readdirSync(fileURLToPath(entrypointsUrl))
      .filter((filename) => filename.endsWith(".ts"))
      .sort()
      .map((filename) => [basename(filename, ".ts"), fileURLToPath(new URL(filename, entrypointsUrl))]),
  );
}

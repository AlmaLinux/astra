import { describe, expect, it } from "vitest";

import parityCases from "./fixtures/chatLinkParityCases.json";
import { buildChatLink } from "../chatLinks";

type ChatLinkExpectation = {
  href: string;
  title: string;
  display: string;
  external: boolean;
};

describe("buildChatLink", () => {
  for (const testCase of parityCases.cases) {
    it(`matches the shared parity fixture for ${testCase.id}`, () => {
      const link = buildChatLink(testCase.raw, {
        kind: testCase.kind,
        config: parityCases.config,
      });

      if (testCase.expected === null) {
        expect(link).toBeNull();
        return;
      }

      expect(link).toEqual(testCase.expected as ChatLinkExpectation);
    });
  }
});
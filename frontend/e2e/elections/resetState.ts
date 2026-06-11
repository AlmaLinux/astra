import { readFileSync } from "node:fs";

type Actor = {
  username: string;
  password: string;
};

type ElectionRouteState = {
  id: number;
  name: string;
  route: string;
};

type ReceiptState = {
  ballot_hash: string;
  verification_state: string;
};

type CredentialState = {
  public_id: string;
  freeipa_username: string;
  weight: number;
  election_alias: string;
};

type ScenarioState = {
  actor: string;
  aliases: string[];
  destructive: boolean;
  route_target: string;
};

export type ElectionsResetState = {
  actors: {
    viewer: Actor;
    manager: Actor;
  };
  elections: Record<string, ElectionRouteState>;
  receipts: Record<string, ReceiptState>;
  credentials: Record<string, CredentialState>;
  routes: {
    algorithm: string;
    audit_tallied: string;
    ballot_verify: string;
    closed_detail: string;
    edit_draft: string;
    open_detail: string;
    open_vote: string;
    tallied_detail: string;
    turnout_report: string;
  };
  scenarios: Record<string, ScenarioState>;
};

export function readElectionsResetState(): ElectionsResetState {
  const resetStatePath = process.env.ASTRA_E2E_ELECTIONS_RESET_STATE_FILE;
  if (!resetStatePath) {
    throw new Error("ASTRA_E2E_ELECTIONS_RESET_STATE_FILE is required for elections Playwright specs.");
  }

  return JSON.parse(readFileSync(resetStatePath, "utf-8")) as ElectionsResetState;
}
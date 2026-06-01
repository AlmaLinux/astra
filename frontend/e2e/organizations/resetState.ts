import { readFileSync } from "node:fs";

export type OrganizationsResetState = {
  actors: {
    representative_observer: {
      username: string;
      password: string;
      organization_aliases: Record<string, number>;
      request_aliases: Record<string, number>;
    };
    claim_happy_actor: {
      username: string;
      password: string;
      organization_aliases: Record<string, number>;
    };
    claim_rejection_actor: {
      username: string;
      password: string;
      organization_aliases: Record<string, number>;
    };
    no_org_actor: {
      username: string;
      password: string;
      organization_aliases: Record<string, number>;
    };
  };
  claim_routes: Record<string, string>;
  organizations: Record<string, {
    organization_id: number;
    name: string;
    status: string;
    detail_url: string;
    business_contact_email: string;
  }>;
  requests: Record<string, {
    request_id: number;
    status: string;
    detail_url: string;
    organization_id: number;
  }>;
  scenarios: Record<string, {
    actor: string;
    aliases: string[];
    destructive: boolean;
    route_target: string;
  }>;
};

export function readOrganizationsResetState(): OrganizationsResetState {
  const resetStatePath = process.env.ASTRA_E2E_RESET_STATE_FILE;
  if (!resetStatePath) {
    throw new Error("ASTRA_E2E_RESET_STATE_FILE is required for organizations Playwright specs.");
  }

  return JSON.parse(readFileSync(resetStatePath, "utf-8")) as OrganizationsResetState;
}
import { readFileSync } from "node:fs";

const REQUIRED_HISTORY_ORDER = [
  "regular03_history_expiry_changed",
  "regular03_history_approved",
  "regular03_history_requested",
] as const;

type SelfServiceActor = {
  username: string;
  password: string;
  profile_route: string;
  settings_membership_route?: string;
  organization_aliases: Record<string, number>;
  request_aliases: Record<string, number>;
  membership_type_codes: string[];
};

type SelfServiceRequestState = {
  alias: string;
  request_id: number;
  detail_route: string;
  actor_username: string;
  browser_state: string;
  target_kind: "user" | "organization";
  target_organization_id: number | null;
};

type SettingsMembershipRow = {
  membership_type_code: string;
  membership_type_name: string;
  action: string;
  action_label: string;
  created_at: string;
};

export type SelfServiceResetState = {
  actors: Record<string, SelfServiceActor>;
  organizations: Record<string, {
    organization_id: number;
    name: string;
    representative_username: string;
    detail_route: string;
    request_route: string;
  }>;
  routes: {
    create: string;
    profiles: Record<string, string>;
    settings_membership: Record<string, string>;
  };
  requests: Record<string, SelfServiceRequestState>;
  settings: {
    membership: {
      actor_username: string;
      route: string;
      active_membership_alias: string;
      active_membership: {
        membership_type_code: string;
        membership_type_name: string;
        created_at: string;
        expires_at: string | null;
        terminate_membership_type_code: string;
        terminate_route: string;
      };
      ordered_history_aliases: string[];
      history_rows: Record<string, SettingsMembershipRow>;
    };
  };
};

function assertSelfServiceContract(state: SelfServiceResetState): void {
  const membershipSettings = state.settings.membership;
  const orderedAliases = membershipSettings.ordered_history_aliases;

  if (!membershipSettings.active_membership_alias) {
    throw new Error("Self-service reset state is missing settings.membership.active_membership_alias.");
  }
  if (new Set(orderedAliases).size !== orderedAliases.length) {
    throw new Error("Self-service reset state contains duplicate settings.membership.ordered_history_aliases.");
  }
  if (JSON.stringify(orderedAliases) !== JSON.stringify(REQUIRED_HISTORY_ORDER)) {
    throw new Error(
      `Self-service reset state must declare ordered history aliases ${REQUIRED_HISTORY_ORDER.join(", ")}.`,
    );
  }

  for (const alias of orderedAliases) {
    if (!membershipSettings.history_rows[alias]) {
      throw new Error(`Self-service reset state is missing settings.membership.history_rows.${alias}.`);
    }
  }
}

export function readSelfServiceResetState(): SelfServiceResetState {
  const resetStatePath = process.env.ASTRA_E2E_SELF_SERVICE_RESET_STATE_FILE;
  if (!resetStatePath) {
    throw new Error(
      "ASTRA_E2E_SELF_SERVICE_RESET_STATE_FILE is required for membership self-service Playwright specs.",
    );
  }

  const state = JSON.parse(readFileSync(resetStatePath, "utf-8")) as SelfServiceResetState;
  assertSelfServiceContract(state);
  return state;
}
import { readFileSync } from "node:fs";

type AuthActor = {
  username: string;
  password: string;
  profile_route: string;
  settings_route?: string;
};

type AgreementRoute = {
  cn: string;
  route: string;
};

export type AuthResetState = {
  scenario: "auth-profile";
  status: "reset";
  actors: Record<string, AuthActor>;
  routes: {
    login: string;
    password_reset_request: string;
    password_reset_confirm: string;
    password_expired: string;
    otp_sync: string;
    register: string;
    register_confirm: string;
    register_activate: string;
    settings_profile: string;
    settings_emails: string;
    settings_keys: string;
    settings_security: string;
    settings_privacy: string;
    settings_agreements: string;
    settings_membership: string;
    settings_email_validate_primary: string;
    settings_email_validate_bugzilla: string;
  };
  agreements: {
    required_coc: AgreementRoute;
    optional_unsigned: AgreementRoute;
  };
};

function assertAuthResetState(state: AuthResetState): void {
  if (state.scenario !== "auth-profile" || state.status !== "reset") {
    throw new Error("Auth reset state is not an auth-profile reset payload.");
  }
  if (!state.actors.regular01 || !state.actors.regular03 || !state.actors.account_setup || !state.actors.admin) {
    throw new Error("Auth reset state is missing one or more required actors.");
  }
  if (
    !state.routes.password_reset_confirm
    || !state.routes.register_confirm
    || !state.routes.register_activate
    || !state.routes.settings_membership
    || !state.routes.settings_email_validate_primary
  ) {
    throw new Error("Auth reset state is missing required tokenized routes.");
  }
}

export function readAuthResetState(): AuthResetState {
  const resetStatePath = process.env.ASTRA_E2E_AUTH_RESET_STATE_FILE;
  if (!resetStatePath) {
    throw new Error("ASTRA_E2E_AUTH_RESET_STATE_FILE is required for auth Playwright specs.");
  }

  const state = JSON.parse(readFileSync(resetStatePath, "utf-8")) as AuthResetState;
  assertAuthResetState(state);
  return state;
}
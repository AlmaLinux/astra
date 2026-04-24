export type OrganizationClaimState = "invalid" | "already_claimed" | "ready";

export interface OrganizationClaimBootstrap {
  state: OrganizationClaimState;
  membershipCommitteeEmail: string;
  organizationName: string;
  organizationWebsite: string;
  organizationContactEmail: string;
  csrfToken: string;
  formAction: string;
}

function readString(root: HTMLElement, name: string): string {
  return root.dataset[name] || "";
}

export function readOrganizationClaimBootstrap(root: HTMLElement): OrganizationClaimBootstrap | null {
  const state = readString(root, "claimState");
  if (state !== "invalid" && state !== "already_claimed" && state !== "ready") {
    return null;
  }

  return {
    state,
    membershipCommitteeEmail: readString(root, "membershipCommitteeEmail"),
    organizationName: readString(root, "organizationName"),
    organizationWebsite: readString(root, "organizationWebsite"),
    organizationContactEmail: readString(root, "organizationContactEmail"),
    csrfToken: readString(root, "csrfToken"),
    formAction: readString(root, "formAction"),
  };
}
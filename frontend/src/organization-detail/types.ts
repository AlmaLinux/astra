export interface OrganizationDetailMembershipBadge {
  label: string;
  class_name: string;
  request_url: string | null;
}

export interface OrganizationDetailRepresentative {
  username: string;
  full_name: string;
}

export interface OrganizationDetailContactGroup {
  key: string;
  label: string;
  name: string;
  email: string;
  phone: string;
}

export interface OrganizationDetailAddress {
  street: string;
  city: string;
  state: string;
  postal_code: string;
  country_code: string;
}

export interface OrganizationDetailOrganization {
  id: number;
  name: string;
  status: string;
  website: string;
  detail_url: string;
  logo_url: string;
  memberships: OrganizationDetailMembershipBadge[];
  representative: OrganizationDetailRepresentative;
  contact_groups: OrganizationDetailContactGroup[];
  address: OrganizationDetailAddress;
}

export interface OrganizationDetailResponse {
  organization: OrganizationDetailOrganization;
}

export interface OrganizationDetailBootstrap {
  apiUrl: string;
}

export function readOrganizationDetailBootstrap(root: HTMLElement): OrganizationDetailBootstrap | null {
  const apiUrl = String(root.dataset.organizationDetailApiUrl || "").trim();
  if (!apiUrl) {
    return null;
  }
  return { apiUrl };
}

export interface GroupFormPayload {
  cn: string;
  description: string;
  fas_url: string;
  fas_mailing_list: string;
  fas_discussion_url: string;
  fas_irc_channels: string[];
}

export interface GroupFormGetResponse {
  group: GroupFormPayload;
}

export interface GroupFormPutResponse {
  ok: boolean;
  group?: GroupFormPayload;
  errors?: Record<string, string[] | string>;
  error?: string;
}

export interface GroupFormBootstrap {
  apiUrl: string;
  detailUrl: string;
}

export function readGroupFormBootstrap(root: HTMLElement): GroupFormBootstrap | null {
  const apiUrl = String(root.dataset.groupFormApiUrl || "").trim();
  const detailUrl = String(root.dataset.groupFormDetailUrl || "").trim();
  if (!apiUrl || !detailUrl) {
    return null;
  }
  return { apiUrl, detailUrl };
}

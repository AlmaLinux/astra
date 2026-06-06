export type Select2ResultItem = {
  id: string;
  text: string;
};

const GROUP_SELECT2_DESCRIPTION_MAX_LENGTH = 72;

function truncateSelect2Text(text: string, maxLength: number): string {
  const normalized = String(text || "").trim();
  if (!normalized || normalized.length <= maxLength) {
    return normalized;
  }

  if (maxLength <= 3) {
    return normalized.slice(0, maxLength);
  }

  return `${normalized.slice(0, maxLength - 3).trimEnd()}...`;
}

function formatGroupSelect2Text(text: string): string {
  const normalized = String(text || "").trim();
  if (!normalized) {
    return "";
  }

  const separator = " — ";
  const separatorIndex = normalized.indexOf(separator);
  if (separatorIndex === -1) {
    return normalized;
  }

  const groupName = normalized.slice(0, separatorIndex).trim();
  const description = normalized.slice(separatorIndex + separator.length).trim();
  if (!description) {
    return groupName;
  }

  return `${groupName}${separator}${truncateSelect2Text(description, GROUP_SELECT2_DESCRIPTION_MAX_LENGTH)}`;
}

export function normalizeSelect2Results(payload: unknown, resultKind: string): { results: Select2ResultItem[] } {
  if (resultKind === "users") {
    const users = (payload as { users?: Array<{ username?: string; full_name?: string }> }).users;
    const results = Array.isArray(users)
      ? users
          .map((user) => {
            const username = String(user?.username || "").trim();
            if (!username) {
              return null;
            }
            const fullName = String(user?.full_name || "").trim();
            return {
              id: username,
              text: fullName ? `${fullName} (${username})` : username,
            } satisfies Select2ResultItem;
          })
          .filter((item): item is Select2ResultItem => item !== null)
      : [];
    return { results };
  }

  const results = (payload as { results?: Array<{ id?: string; text?: string }> }).results;
  return {
    results: Array.isArray(results)
      ? results
          .map((item) => {
            const id = String(item?.id || "").trim();
            if (!id) {
              return null;
            }
            const text = String(item?.text || id).trim() || id;
            return {
              id,
              text: formatGroupSelect2Text(text),
            } satisfies Select2ResultItem;
          })
          .filter((item): item is Select2ResultItem => item !== null)
      : [],
  };
}
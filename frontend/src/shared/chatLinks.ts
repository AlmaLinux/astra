export interface ChatLinkConfig {
  irc: {
    defaultServer: string;
  };
  matrix: {
    defaultServer: string;
  };
  mattermost: {
    defaultServer: string;
    defaultTeam: string;
  };
  matrixToArgs: string;
}

export interface ChatLink {
  href: string;
  title: string;
  display: string;
  external: boolean;
}

type ChatKind = "nickname" | "channel";
type ChatScheme = "irc" | "matrix" | "mattermost";

interface BuildChatLinkOptions {
  kind: ChatKind;
  config: ChatLinkConfig;
  schemeOverride?: string;
}

interface ParsedUrlParts {
  scheme: string;
  host: string;
  path: string;
  fragment: string;
}

interface ParsedChatInput {
  value: string;
  parsedUrl: ParsedUrlParts | null;
  scheme: ChatScheme;
}

interface SchemeDefaults {
  defaultServer: string;
  defaultTeam: string;
}

const IRC_NICK_RE = /^[a-z_[\]\\^{}|`-][a-z0-9_[\]\\^{}|`-]*$/i;
const IRC_CHANNEL_RE = /^#[^\s,]{1,63}$/;
const MATRIX_LOCALPART_RE = /^[a-z0-9.=_/-]+$/i;
const MATTERMOST_NAME_RE = /^[a-z0-9][a-z0-9._-]*$/i;
const SERVER_RE = /^[a-z0-9][a-z0-9.-]*(:[0-9]+)?$/i;

function parseUrlParts(rawValue: string): ParsedUrlParts | null {
  try {
    const parsed = new URL(rawValue);
    return {
      scheme: parsed.protocol.replace(/:$/, "").toLowerCase(),
      host: parsed.host,
      path: parsed.pathname || "",
      fragment: parsed.hash.replace(/^#/, ""),
    };
  } catch {
    const match = rawValue.match(/^([a-z][a-z0-9+.-]*):\/\/([^/?#]*)([^?#]*)(?:#(.*))?$/i);
    if (!match) {
      return null;
    }

    return {
      scheme: match[1].toLowerCase(),
      host: match[2],
      path: match[3] || "",
      fragment: match[4] || "",
    };
  }
}

function normalizeScheme(scheme: string | undefined, kind: ChatKind, rawValue: string): ChatScheme {
  const normalized = String(scheme || "").toLowerCase();
  if (normalized === "irc" || normalized === "ircs") {
    return "irc";
  }
  if (normalized === "matrix" || normalized === "mattermost") {
    return normalized;
  }

  const compact = rawValue.replace(/\s+/g, "");
  if (kind === "channel" && compact.startsWith("~")) {
    return "mattermost";
  }
  return "irc";
}

function isValidServer(value: string): boolean {
  return SERVER_RE.test(value);
}

function appendMatrixArgs(baseHref: string, matrixToArgs: string): string {
  const suffix = String(matrixToArgs || "").trim();
  return suffix ? `${baseHref}?${suffix}` : baseHref;
}

function parseChatInput(rawValue: string, kind: ChatKind, schemeOverride?: string): ParsedChatInput | null {
  const value = String(rawValue || "").trim();
  if (!value) {
    return null;
  }

  const parsedUrl = parseUrlParts(value);
  return {
    value,
    parsedUrl,
    scheme: normalizeScheme(schemeOverride || parsedUrl?.scheme, kind, value),
  };
}

function getSchemeDefaults(config: ChatLinkConfig, scheme: ChatScheme): SchemeDefaults {
  return {
    defaultServer: config[scheme].defaultServer,
    defaultTeam: scheme === "mattermost" ? config.mattermost.defaultTeam : "",
  };
}

function shouldExpandMattermostDisplay(
  server: string,
  team: string,
  defaults: SchemeDefaults,
): boolean {
  return !defaults.defaultServer || !defaults.defaultTeam || server !== defaults.defaultServer || team !== defaults.defaultTeam;
}

function buildNicknameLink(rawValue: string, config: ChatLinkConfig, schemeOverride?: string): ChatLink | null {
  const parsedInput = parseChatInput(rawValue, "nickname", schemeOverride);
  if (!parsedInput) {
    return null;
  }

  const { value, parsedUrl, scheme } = parsedInput;
  const defaults = getSchemeDefaults(config, scheme);

  let nick = "";
  let server = "";
  let team = "";

  if (parsedUrl) {
    const path = parsedUrl.path.replace(/^\//, "");
    const parts = path.split("/").filter(Boolean);
    const serverFromUrl = parsedUrl.host.trim();

    if (scheme === "mattermost") {
      if (parts.length >= 2) {
        team = parts[0] || "";
        if (parts.length >= 3 && parts[1] === "messages" && parts[2]?.startsWith("@")) {
          nick = parts[2].replace(/^@/, "");
        } else {
          nick = parts[1] || "";
        }
        server = serverFromUrl || defaults.defaultServer;
      } else if (parts.length === 1) {
        nick = parts[0] || "";
        server = serverFromUrl || defaults.defaultServer;
      } else {
        nick = serverFromUrl;
        server = defaults.defaultServer;
      }
    } else {
      nick = path;
      server = serverFromUrl || defaults.defaultServer;
    }

    if (!nick && parsedUrl.fragment) {
      nick = parsedUrl.fragment.replace(/^[@#]/, "");
    }
    nick = nick.replace(/^@/, "").trim();
  } else {
    const cleaned = value.replace(/^@/, "").trim();
    if (scheme === "matrix" && cleaned.includes(":")) {
      const index = cleaned.lastIndexOf(":");
      nick = cleaned.slice(0, index);
      server = cleaned.slice(index + 1);
    } else if (scheme === "mattermost" && cleaned.includes(":")) {
      const parts = cleaned.split(":").filter(Boolean);
      if (parts.length >= 3) {
        nick = parts[0] || "";
        team = parts.at(-1) || "";
        server = parts.slice(1, -1).join(":");
      } else {
        const index = cleaned.lastIndexOf(":");
        nick = cleaned.slice(0, index);
        server = cleaned.slice(index + 1);
      }
    } else if (cleaned.includes(":")) {
      const index = cleaned.indexOf(":");
      nick = cleaned.slice(0, index);
      server = cleaned.slice(index + 1);
    } else if (cleaned.includes("@")) {
      const index = cleaned.indexOf("@");
      nick = cleaned.slice(0, index);
      server = cleaned.slice(index + 1);
    } else {
      nick = cleaned;
    }

    server = server.trim() || defaults.defaultServer;
    if (scheme === "mattermost" && !team) {
      team = defaults.defaultTeam;
    }
  }

  nick = nick.trim();
  server = server.trim();
  team = scheme === "mattermost" ? team.trim() : "";

  if (!nick || !server || !isValidServer(server)) {
    return null;
  }

  if (scheme === "irc") {
    if (!IRC_NICK_RE.test(nick)) {
      return null;
    }
    const display = defaults.defaultServer && server === defaults.defaultServer ? nick : `${nick}:${server}`;
    return {
      href: `irc://${server}/${nick},isnick`,
      title: `IRC on ${server}`,
      display,
      external: false,
    };
  }

  if (scheme === "matrix") {
    const localpart = nick.replace(/^@/, "");
    if (!MATRIX_LOCALPART_RE.test(localpart)) {
      return null;
    }
    const display = defaults.defaultServer && server === defaults.defaultServer ? `@${localpart}` : `@${localpart}:${server}`;
    return {
      href: appendMatrixArgs(`https://matrix.to/#/@${localpart}:${server}`, config.matrixToArgs),
      title: `Matrix on ${server}`,
      display,
      external: true,
    };
  }

  if (!MATTERMOST_NAME_RE.test(nick)) {
    return null;
  }
  if (server !== defaults.defaultServer && !team) {
    return null;
  }
  const resolvedTeam = team || defaults.defaultTeam;
  if (!resolvedTeam || !MATTERMOST_NAME_RE.test(resolvedTeam)) {
    return null;
  }
  return {
    href: `https://${server}/${resolvedTeam}/messages/@${nick}`,
    title: `Mattermost on ${server} (${resolvedTeam})`,
    display: shouldExpandMattermostDisplay(server, resolvedTeam, defaults) ? `@${nick}:${server}:${resolvedTeam}` : `@${nick}`,
    external: true,
  };
}

function buildChannelLink(rawValue: string, config: ChatLinkConfig, schemeOverride?: string): ChatLink | null {
  const parsedInput = parseChatInput(rawValue, "channel", schemeOverride);
  if (!parsedInput) {
    return null;
  }

  const { value, parsedUrl } = parsedInput;
  let { scheme } = parsedInput;
  let defaults = getSchemeDefaults(config, scheme);

  let channel = "";
  let server = "";
  let team = "";

  if (parsedUrl) {
    const serverFromUrl = parsedUrl.host.trim();
    server = serverFromUrl || defaults.defaultServer;

    if (scheme === "mattermost") {
      if (serverFromUrl === "channels") {
        server = defaults.defaultServer;
      }
      const parts = parsedUrl.path.replace(/^\//, "").split("/").filter(Boolean);
      if (parts.length >= 3 && parts[1] === "channels") {
        team = parts[0] || "";
        channel = parts[2] || "";
      } else if (parts.length >= 2 && parts[0] === "channels") {
        channel = parts[1] || "";
      } else {
        channel = parts.at(-1) || "";
      }
      channel = channel.replace(/^~/, "").trim();
      if (!team && server === defaults.defaultServer) {
        team = defaults.defaultTeam;
      }
    } else {
      channel = parsedUrl.fragment ? `#${parsedUrl.fragment.trim()}` : parsedUrl.path.replace(/^\//, "").trim();
      if (channel && !channel.startsWith("#")) {
        channel = `#${channel}`;
      }
    }
  } else {
    const compact = value.replace(/ /g, "");
    if (compact.startsWith("~")) {
      scheme = "mattermost";
      defaults = getSchemeDefaults(config, scheme);
      channel = compact.replace(/^~/, "");
      const parts = channel.split(":").filter(Boolean);
      channel = parts[0] || "";
      if (parts.length >= 3) {
        team = parts.at(-1) || "";
        server = parts.slice(1, -1).join(":");
      } else if (parts.length === 2) {
        server = parts[1] || "";
      }
      server = server.trim() || defaults.defaultServer;
      if (!team && server === defaults.defaultServer) {
        team = defaults.defaultTeam;
      }
    } else if (compact.startsWith("#")) {
      channel = compact;
      if (compact.includes(":")) {
        const index = compact.lastIndexOf(":");
        channel = compact.slice(0, index);
        server = compact.slice(index + 1);
      }
      server = server.trim() || defaults.defaultServer;
    } else {
      return null;
    }
  }

  channel = channel.trim();
  server = server.trim();
  team = scheme === "mattermost" ? team.trim() : "";

  if (!channel || !server || !isValidServer(server)) {
    return null;
  }

  if (scheme === "mattermost") {
    if (!MATTERMOST_NAME_RE.test(channel)) {
      return null;
    }
    const resolvedTeam = team || defaults.defaultTeam;
    if (!resolvedTeam || !MATTERMOST_NAME_RE.test(resolvedTeam)) {
      return null;
    }
    return {
      href: `https://${server}/${resolvedTeam}/channels/${channel}`,
      title: `Mattermost channel on ${server} (${resolvedTeam})`,
      display: shouldExpandMattermostDisplay(server, resolvedTeam, defaults) ? `~${channel}:${server}:${resolvedTeam}` : `~${channel}`,
      external: true,
    };
  }

  if (scheme === "irc") {
    if (!IRC_CHANNEL_RE.test(channel)) {
      return null;
    }
    const display = defaults.defaultServer && server === defaults.defaultServer ? channel : `${channel}:${server}`;
    return {
      href: `ircs://${server}/${channel}`,
      title: `IRC channel on ${server}`,
      display,
      external: false,
    };
  }

  const localpart = channel.replace(/^#/, "");
  if (!localpart || !MATRIX_LOCALPART_RE.test(localpart)) {
    return null;
  }
  const display = defaults.defaultServer && server === defaults.defaultServer ? `#${localpart}` : `#${localpart}:${server}`;
  return {
    href: appendMatrixArgs(`https://matrix.to/#/#${localpart}:${server}`, config.matrixToArgs),
    title: `Matrix room alias on ${server}`,
    display,
    external: true,
  };
}

export function buildChatLink(value: string, options: BuildChatLinkOptions): ChatLink | null {
  return options.kind === "nickname"
    ? buildNicknameLink(value, options.config, options.schemeOverride)
    : buildChannelLink(value, options.config, options.schemeOverride);
}
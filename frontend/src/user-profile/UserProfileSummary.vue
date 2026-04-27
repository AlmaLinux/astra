<script setup lang="ts">
import { computed } from "vue";

import type { UserProfileSummaryBootstrap } from "./types";

const props = defineProps<{
  bootstrap: UserProfileSummaryBootstrap;
  currentTimeLabel: string;
  settingsProfileUrl: string;
}>();

const SOCIAL_PLATFORM_COPY: Record<string, { label: string; title: string; icon: string }> = {
  bluesky: { label: "Bluesky", title: "Bluesky URLs", icon: "fab fa-bluesky" },
  mastodon: { label: "Mastodon", title: "Mastodon URLs", icon: "fab fa-mastodon" },
  x: { label: "X (Twitter)", title: "X (Twitter) URLs", icon: "fab fa-x-twitter" },
  linkedin: { label: "LinkedIn", title: "LinkedIn URLs", icon: "fab fa-linkedin" },
  facebook: { label: "Facebook", title: "Facebook URLs", icon: "fab fa-facebook" },
  instagram: { label: "Instagram", title: "Instagram URLs", icon: "fab fa-instagram" },
  youtube: { label: "YouTube", title: "YouTube URLs", icon: "fab fa-youtube" },
  reddit: { label: "Reddit", title: "Reddit URLs", icon: "fab fa-reddit" },
  tiktok: { label: "TikTok", title: "TikTok URLs", icon: "fab fa-tiktok" },
  signal: { label: "Signal", title: "Signal URLs", icon: "fab fa-signal-messenger" },
};

function hostForUrl(url: string): string {
  const value = String(url || "").trim();
  if (!value) {
    return "";
  }

  try {
    return new URL(value).hostname.toLowerCase().replace(/\.$/, "");
  } catch {
    try {
      return new URL(`https://${value}`).hostname.toLowerCase().replace(/\.$/, "");
    } catch {
      return "";
    }
  }
}

function safeExternalHref(url: string): string | null {
  const value = String(url || "").trim();
  if (!value) {
    return null;
  }

  if (value.startsWith("//")) {
    try {
      const parsed = new URL(`https:${value}`);
      return parsed.hostname ? `https:${value}` : null;
    } catch {
      return null;
    }
  }

  try {
    const parsed = new URL(value);
    if ((parsed.protocol === "http:" || parsed.protocol === "https:") && parsed.hostname) {
      return value;
    }
    return null;
  } catch {
    try {
      const parsed = new URL(`https://${value}`);
      return parsed.hostname ? `https://${value}` : null;
    } catch {
      return null;
    }
  }
}

function socialDisplayText(platform: string, url: string): string {
  const value = String(url || "").trim();
  const host = hostForUrl(value);
  const fallback = host || value;
  if (!value) {
    return fallback;
  }

  let parsed: URL | null = null;
  try {
    parsed = new URL(value);
  } catch {
    try {
      parsed = new URL(`https://${value}`);
    } catch {
      parsed = null;
    }
  }

  const segments = (parsed?.pathname || "").split("/").filter(Boolean);

  switch (platform) {
    case "bluesky": {
      const profileIndex = segments.indexOf("profile");
      if (profileIndex !== -1 && segments[profileIndex + 1]) {
        return `@${segments[profileIndex + 1].replace(/^@/, "")}`;
      }
      if (host.endsWith(".bsky.social") && host !== "bsky.social") {
        return `@${host}`;
      }
      return fallback;
    }
    case "x":
    case "instagram":
    case "tiktok":
      return segments[0] ? `@${segments[0].replace(/^@/, "")}` : fallback;
    case "reddit":
      if ((segments[0] === "u" || segments[0] === "user") && segments[1]) {
        return `u/${segments[1]}`;
      }
      if (segments[0] === "r" && segments[1]) {
        return `r/${segments[1]}`;
      }
      return fallback;
    case "mastodon": {
      const atSegment = segments.find((segment) => segment.startsWith("@") && segment.replace(/^@/, ""));
      if (atSegment && host) {
        return `@${atSegment.replace(/^@/, "")}@${host}`;
      }
      return fallback;
    }
    case "signal":
      return host || "Signal link";
    case "youtube":
      return segments[0]?.startsWith("@") ? segments[0] : fallback;
    case "linkedin":
      return segments[0] === "in" && segments[1] ? segments[1] : fallback;
    default:
      return fallback;
  }
}

function countryDisplayName(countryCode: string): string {
  const normalizedCode = String(countryCode || "").trim().toUpperCase();
  if (!normalizedCode) {
    return "Not provided";
  }

  try {
    const displayNames = new Intl.DisplayNames(["en"], { type: "region" });
    return displayNames.of(normalizedCode) || normalizedCode;
  } catch {
    return normalizedCode;
  }
}

const socialProfiles = computed(() => {
  return props.bootstrap.socialProfiles
    .map((profile) => {
      const copy = SOCIAL_PLATFORM_COPY[profile.platform];
      if (!copy) {
        return null;
      }

      return {
        ...copy,
        platform: profile.platform,
        urls: profile.urls.map((url) => ({
          href: safeExternalHref(url),
          text: socialDisplayText(profile.platform, url),
        })),
      };
    })
    .filter((profile): profile is { label: string; title: string; icon: string; platform: string; urls: Array<{ href: string | null; text: string }> } => profile !== null);
});

const websiteLinks = computed(() => props.bootstrap.websiteUrls.map((url) => ({
  href: safeExternalHref(url),
  text: String(url || "").trim(),
})).filter((item) => item.text));

const rssLinks = computed(() => props.bootstrap.rssUrls.map((url) => ({
  href: safeExternalHref(url),
  text: String(url || "").trim(),
})).filter((item) => item.text));

function externalLinkTarget(href: string | null): string | undefined {
  return href ? "_blank" : undefined;
}
</script>

<template>
  <div data-user-profile-summary-vue-root>
    <div class="card">
      <div class="card-body text-center">
        <div class="d-none d-md-block">
          <img
            v-if="bootstrap.avatarUrl"
            :src="bootstrap.avatarUrl"
            class="img-fluid img-circle mb-3"
            style="width:220px;height:220px;object-fit:cover;"
            alt="Avatar"
          >
          <i v-else class="far fa-user" style="font-size:140px;" />
        </div>
        <div class="d-md-none">
          <img
            v-if="bootstrap.avatarUrl"
            :src="bootstrap.avatarUrl"
            class="img-fluid img-circle mb-3"
            style="width:140px;height:140px;object-fit:cover;"
            alt="Avatar"
          >
          <i v-else class="far fa-user" style="font-size:90px;" />
        </div>

        <h3 class="profile-username mb-1">{{ bootstrap.fullName }}</h3>
        <div id="user_username" class="text-muted">{{ bootstrap.username }}</div>
        <div v-if="bootstrap.email" id="user_mail" class="mt-1">
          <a :href="`mailto:${bootstrap.email}`">{{ bootstrap.email }}</a>
        </div>

        <div v-if="bootstrap.isSelf && settingsProfileUrl" class="mt-3">
          <a class="btn btn-primary btn-block" :href="settingsProfileUrl" title="Edit your profile">Edit Profile</a>
        </div>
      </div>

      <ul id="user_attributes" class="list-group list-group-flush">
        <li v-if="bootstrap.viewerIsMembershipCommittee" class="list-group-item d-flex justify-content-between align-items-center">
          <strong class="profile-attr-label" title="Country"><i class="fas fa-flag" /> Country</strong>
          <div class="profile-attr-value text-end">{{ countryDisplayName(bootstrap.countryCode) }}</div>
        </li>

        <li v-if="bootstrap.pronouns" class="list-group-item d-flex justify-content-between align-items-center">
          <strong class="profile-attr-label" title="Pronouns"><i class="fas fa-user-tag" /> Pronouns</strong>
          <div class="profile-attr-value text-end">{{ bootstrap.pronouns }}</div>
        </li>

        <li v-if="bootstrap.locale" class="list-group-item d-flex justify-content-between align-items-center">
          <strong class="profile-attr-label" title="Locale"><i class="fas fa-language" /> Locale</strong>
          <div class="profile-attr-value text-end">{{ bootstrap.locale }}</div>
        </li>

        <template v-if="bootstrap.timezoneName">
          <li class="list-group-item d-flex justify-content-between align-items-center">
            <strong class="profile-attr-label" title="Timezone"><i class="fas fa-globe" /> Timezone</strong>
            <div class="profile-attr-value text-end">{{ bootstrap.timezoneName }}</div>
          </li>
          <li id="user-timezone" class="list-group-item" :data-timezone="bootstrap.timezoneName">
            <div class="d-flex justify-content-between align-items-center">
              <strong class="profile-attr-label" title="Current Time"><i class="far fa-clock" /> Current Time</strong>
              <div id="user-time" class="profile-attr-value text-end">{{ currentTimeLabel }}</div>
            </div>
          </li>
        </template>

        <li v-if="bootstrap.ircNicks.length" class="list-group-item d-flex justify-content-between">
          <strong class="profile-attr-label" title="Chat"><i class="fas fa-comments" /> Chat</strong>
          <div class="profile-attr-value text-end">
            <div v-for="nick in bootstrap.ircNicks" :key="nick" class="mb-0 text-monospace profile-chat-item">{{ nick }}</div>
          </div>
        </li>

        <li v-for="profile in socialProfiles" :key="profile.platform" class="list-group-item d-flex justify-content-between">
          <strong class="profile-attr-label" :title="profile.title"><i :class="profile.icon" /> {{ profile.label }}</strong>
          <div class="profile-attr-value text-end">
            <div v-for="item in profile.urls" :key="`${profile.label}-${item.text}`" class="mb-0 text-monospace">
              <a v-if="item.href" :href="item.href" :target="externalLinkTarget(item.href)" rel="noopener noreferrer">{{ item.text }}</a>
              <template v-else>{{ item.text }}</template>
            </div>
          </div>
        </li>

        <li v-if="websiteLinks.length" class="list-group-item d-flex justify-content-between">
          <strong class="profile-attr-label" title="Website URLs"><i class="fas fa-link" /> Website</strong>
          <div class="profile-attr-value text-end">
            <div v-for="item in websiteLinks" :key="`website-${item.text}`" class="mb-0 text-monospace">
              <a v-if="item.href" :href="item.href" :target="externalLinkTarget(item.href)" rel="noopener noreferrer">{{ item.text }}</a>
              <template v-else>{{ item.text }}</template>
            </div>
          </div>
        </li>

        <li v-if="rssLinks.length" class="list-group-item d-flex justify-content-between">
          <strong class="profile-attr-label" title="RSS URL"><i class="fas fa-rss" /> RSS</strong>
          <div class="profile-attr-value text-end">
            <div v-for="item in rssLinks" :key="`rss-${item.text}`" class="mb-0 text-monospace">
              <a v-if="item.href" :href="item.href" :target="externalLinkTarget(item.href)" rel="noopener noreferrer">{{ item.text }}</a>
              <template v-else>{{ item.text }}</template>
            </div>
          </div>
        </li>

        <li v-if="bootstrap.rhbzEmail" class="list-group-item d-flex justify-content-between align-items-center">
          <strong class="profile-attr-label" title="RHBZ"><i class="fas fa-bug" /> RHBZ</strong>
          <div class="profile-attr-value text-end">{{ bootstrap.rhbzEmail }}</div>
        </li>

        <li v-if="bootstrap.githubUsername" class="list-group-item d-flex justify-content-between align-items-center">
          <strong class="profile-attr-label" title="GitHub"><i class="fab fa-github" /> GitHub</strong>
          <div class="profile-attr-value text-end">
            <a :href="`https://github.com/${bootstrap.githubUsername}`" target="_blank" rel="noopener noreferrer">@{{ bootstrap.githubUsername }}</a>
          </div>
        </li>

        <li v-if="bootstrap.gitlabUsername" class="list-group-item d-flex justify-content-between align-items-center">
          <strong class="profile-attr-label" title="GitLab"><i class="fab fa-gitlab" /> GitLab</strong>
          <div class="profile-attr-value text-end">
            <a :href="`https://gitlab.com/${bootstrap.gitlabUsername}`" target="_blank" rel="noopener noreferrer">@{{ bootstrap.gitlabUsername }}</a>
          </div>
        </li>

        <li v-if="bootstrap.gpgKeys.length" class="list-group-item">
          <strong class="profile-attr-label" title="GPG Keys"><i class="fas fa-key" /> GPG Keys</strong>
          <div class="mt-2 profile-pre-list">
            <pre v-for="key in bootstrap.gpgKeys" :key="key" class="mb-1">{{ key }}</pre>
          </div>
        </li>

        <li v-if="bootstrap.sshKeys.length" class="list-group-item">
          <strong class="profile-attr-label" title="SSH Keys"><i class="fas fa-key" /> SSH Keys</strong>
          <div class="mt-2 profile-pre-list">
            <pre v-for="key in bootstrap.sshKeys" :key="key" class="mb-1">{{ key }}</pre>
          </div>
        </li>
      </ul>
    </div>
  </div>
</template>
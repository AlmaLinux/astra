<script setup lang="ts">
import type { UserProfileLinkItem, UserProfileSummaryBootstrap } from "./types";

defineProps<{
  bootstrap: UserProfileSummaryBootstrap;
}>();

function externalLinkTarget(item: UserProfileLinkItem): string | undefined {
  return item.href ? "_blank" : undefined;
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

        <div v-if="bootstrap.isSelf && bootstrap.profileEditUrl" class="mt-3">
          <a class="btn btn-primary btn-block" :href="bootstrap.profileEditUrl" title="Edit your profile">Edit Profile</a>
        </div>
      </div>

      <ul id="user_attributes" class="list-group list-group-flush">
        <li v-if="bootstrap.viewerIsMembershipCommittee" class="list-group-item d-flex justify-content-between align-items-center">
          <strong class="profile-attr-label" title="Country"><i class="fas fa-flag" /> Country</strong>
          <div class="profile-attr-value text-end">{{ bootstrap.profileCountry }}</div>
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
              <div id="user-time" class="profile-attr-value text-end">{{ bootstrap.currentTimeLabel }}</div>
            </div>
          </li>
        </template>

        <li v-if="bootstrap.ircNicks.length" class="list-group-item d-flex justify-content-between">
          <strong class="profile-attr-label" title="Chat"><i class="fas fa-comments" /> Chat</strong>
          <div class="profile-attr-value text-end">
            <div v-for="nick in bootstrap.ircNicks" :key="nick" class="mb-0 text-monospace profile-chat-item">{{ nick }}</div>
          </div>
        </li>

        <li v-for="profile in bootstrap.socialProfiles" :key="profile.label" class="list-group-item d-flex justify-content-between">
          <strong class="profile-attr-label" :title="profile.title"><i :class="profile.icon" /> {{ profile.label }}</strong>
          <div class="profile-attr-value text-end">
            <div v-for="item in profile.urls" :key="`${profile.label}-${item.text}`" class="mb-0 text-monospace">
              <a v-if="item.href" :href="item.href" :target="externalLinkTarget(item)" rel="noopener noreferrer">{{ item.text }}</a>
              <template v-else>{{ item.text }}</template>
            </div>
          </div>
        </li>

        <li v-if="bootstrap.websiteUrls.length" class="list-group-item d-flex justify-content-between">
          <strong class="profile-attr-label" title="Website URLs"><i class="fas fa-link" /> Website</strong>
          <div class="profile-attr-value text-end">
            <div v-for="item in bootstrap.websiteUrls" :key="`website-${item.text}`" class="mb-0 text-monospace">
              <a v-if="item.href" :href="item.href" :target="externalLinkTarget(item)" rel="noopener noreferrer">{{ item.text }}</a>
              <template v-else>{{ item.text }}</template>
            </div>
          </div>
        </li>

        <li v-if="bootstrap.rssUrls.length" class="list-group-item d-flex justify-content-between">
          <strong class="profile-attr-label" title="RSS URL"><i class="fas fa-rss" /> RSS</strong>
          <div class="profile-attr-value text-end">
            <div v-for="item in bootstrap.rssUrls" :key="`rss-${item.text}`" class="mb-0 text-monospace">
              <a v-if="item.href" :href="item.href" :target="externalLinkTarget(item)" rel="noopener noreferrer">{{ item.text }}</a>
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
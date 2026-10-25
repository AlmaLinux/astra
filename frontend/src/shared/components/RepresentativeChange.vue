<script setup lang="ts">
// Empty usernames mean the organization had no representative (old) or was left without one (new).
const props = defineProps<{
  old: string;
  new: string;
  profileHref: (username: string) => string;
}>();
</script>

<template>
  <!-- `new` is a keyword in template expressions, so read both through props. -->
  <template v-for="(username, index) in [props.old, props.new]" :key="index">
    <template v-if="index"> → </template>
    <a v-if="username" :href="profileHref(username)">{{ username }}</a>
    <template v-else>(none)</template>
  </template>
</template>

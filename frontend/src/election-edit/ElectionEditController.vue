<script setup lang="ts">
import { onMounted } from "vue";

import { initElectionEditController } from "./controller";

onMounted(() => {
  initElectionEditController();
});
</script>

<template>
  <div data-election-edit-vue-root hidden></div>
</template>

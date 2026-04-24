<script setup lang="ts">
const props = defineProps<{
  username: string;
  fullName?: string;
  avatarUrl?: string;
  profileUrl: string;
}>();
</script>

<template>
  <div class="card card-body mb-4 px-2 py-3 card-widget widget-user position-relative">
    <div class="d-flex align-items-center">
      <div class="flex-shrink-0 ml-1 mr-3">
        <a :href="props.profileUrl">
          <img
            v-if="props.avatarUrl"
            :src="props.avatarUrl"
            width="50"
            height="50"
            class="img-circle elevation-2"
            style="object-fit: cover;"
            alt="User Avatar"
          >
          <span
            v-else
            class="img-circle elevation-2 d-inline-flex align-items-center justify-content-center bg-secondary"
            style="width: 50px; height: 50px;"
          >
            <i class="far fa-user" />
          </span>
        </a>
      </div>

      <div class="flex-grow-1 ms-2" style="min-width: 0;">
        <div class="my-0 font-weight-bold">
          <a :href="props.profileUrl">{{ props.username }}</a>
        </div>
        <div v-if="props.fullName" class="text-truncate w-100">{{ props.fullName }}</div>
      </div>
    </div>
  </div>
</template>

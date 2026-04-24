<script setup lang="ts">
import { computed } from "vue";

interface WidgetUserAction {
  key: string;
  ariaLabel: string;
  title: string;
  buttonClass: string;
  iconClass: string;
  disabled?: boolean;
  onClick: () => void;
}

const props = defineProps<{
  username: string;
  fullName?: string;
  avatarUrl?: string;
  dimmed?: boolean;
  secondaryText?: string;
  actions?: WidgetUserAction[];
}>();

const profileUrl = computed(() => `/user/${encodeURIComponent(props.username)}/`);

function actionButtonStyle(index: number): Record<string, string> {
  return {
    top: ".35rem",
    right: index === 0 ? ".35rem" : `${0.35 + index * 2.25}rem`,
    zIndex: "10",
  };
}
</script>

<template>
  <div class="card card-body mb-4 px-2 py-3 card-widget widget-user position-relative">
    <button
      v-for="(action, index) in props.actions || []"
      :key="action.key"
      type="button"
      :class="action.buttonClass"
      class="position-absolute"
      :style="actionButtonStyle(index)"
      :aria-label="action.ariaLabel"
      :title="action.title"
      :disabled="action.disabled"
      @click="action.onClick()"
    >
      <i :class="action.iconClass"></i>
    </button>

    <div class="d-flex align-items-center" :class="{ 'text-muted': props.dimmed }" :style="props.dimmed ? { opacity: '0.55' } : undefined">
      <div class="flex-shrink-0 ml-1 mr-3">
        <a :href="profileUrl">
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
          <a :href="profileUrl">{{ props.username }}</a>
        </div>
        <div v-if="props.fullName" class="text-truncate w-100">{{ props.fullName }}</div>
        <div v-if="props.secondaryText" class="text-truncate w-100 small">{{ props.secondaryText }}</div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
// A button that stays disabled until its action is possible, with a tooltip saying why
// it is not yet. Attributes and listeners (type, class, id, @click...) go to the button;
// the tooltip sits on a wrapper because disabled buttons get no hover events.
defineOptions({ inheritAttrs: false });

withDefaults(
  defineProps<{
    // Why the action is not possible yet; "" when it is.
    blockedReason: string;
    // The tooltip once the action is possible.
    title: string;
    // Disabled without a reason, e.g. while a request is in flight.
    busy?: boolean;
    // Lets the wrapper double as a layout element, e.g. "input-group-append".
    wrapperClass?: string;
  }>(),
  { busy: false, wrapperClass: "d-inline-block" },
);
</script>

<template>
  <span :class="wrapperClass" :title="blockedReason || title">
    <button
      v-bind="$attrs"
      :disabled="Boolean(blockedReason) || busy"
      :style="blockedReason ? 'pointer-events: none;' : undefined"
    >
      <slot />
    </button>
  </span>
</template>

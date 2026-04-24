<script setup lang="ts">
import { computed } from "vue";

interface PaginationPayload {
  count: number;
  page: number;
  num_pages: number;
  page_numbers: number[];
  show_first: boolean;
  show_last: boolean;
  has_previous: boolean;
  has_next: boolean;
  previous_page_number: number | null;
  next_page_number: number | null;
  start_index: number;
  end_index: number;
}

const props = defineProps<{
  items: unknown[];
  isLoading: boolean;
  error: string;
  emptyMessage: string;
  pagination: PaginationPayload | null;
  buildPageHref: (pageNumber: number) => string;
}>();

const emit = defineEmits<{
  (event: "page-change", pageNumber: number): void;
}>();

const hasPagination = computed(() => (props.pagination?.num_pages || 0) > 1);

function onPageLinkClick(event: Event, pageNumber: number, disabled: boolean): void {
  event.preventDefault();
  if (disabled || !props.pagination || pageNumber === props.pagination.page) {
    return;
  }
  emit("page-change", pageNumber);
}
</script>

<template>
  <div>
    <div v-if="error" class="text-muted">{{ error }}</div>
    <div v-else-if="isLoading" class="d-flex align-items-center text-muted">
      <span class="spinner-border spinner-border-sm mr-2" role="status" aria-hidden="true" />
    </div>
    <div v-else-if="items.length === 0" class="text-muted">{{ emptyMessage }}</div>
    <div v-else class="row">
      <div v-for="(item, index) in items" :key="index" class="col-12 col-md-6 col-lg-4 col-xl-3">
        <slot name="item" :item="item" />
      </div>
    </div>

    <div class="mt-2 clearfix">
      <div class="float-left text-muted small" v-if="pagination && pagination.count">
        Showing {{ pagination.start_index }}-{{ pagination.end_index }} of {{ pagination.count }}
      </div>

      <ul v-if="hasPagination" class="pagination pagination-sm m-0 float-right">
        <li class="page-item" :class="{ disabled: !pagination?.has_previous }">
          <a
            class="page-link"
            :href="pagination?.has_previous ? buildPageHref(Number(pagination?.previous_page_number)) : '#'"
            aria-label="Previous"
            @click="onPageLinkClick($event, Number(pagination?.previous_page_number), !pagination?.has_previous)"
          >«</a>
        </li>

        <li v-if="pagination?.show_first" class="page-item">
          <a class="page-link" :href="buildPageHref(1)" @click="onPageLinkClick($event, 1, false)">1</a>
        </li>
        <li v-if="pagination?.show_first" class="page-item disabled"><span class="page-link">…</span></li>

        <li
          v-for="pageNumber in pagination?.page_numbers || []"
          :key="pageNumber"
          class="page-item"
          :class="{ active: pageNumber === pagination?.page }"
        >
          <a class="page-link" :href="buildPageHref(pageNumber)" @click="onPageLinkClick($event, pageNumber, false)">{{ pageNumber }}</a>
        </li>

        <li v-if="pagination?.show_last" class="page-item disabled"><span class="page-link">…</span></li>
        <li v-if="pagination?.show_last" class="page-item">
          <a class="page-link" :href="buildPageHref(Number(pagination?.num_pages))" @click="onPageLinkClick($event, Number(pagination?.num_pages), false)">
            {{ pagination?.num_pages }}
          </a>
        </li>

        <li class="page-item" :class="{ disabled: !pagination?.has_next }">
          <a
            class="page-link"
            :href="pagination?.has_next ? buildPageHref(Number(pagination?.next_page_number)) : '#'"
            aria-label="Next"
            @click="onPageLinkClick($event, Number(pagination?.next_page_number), !pagination?.has_next)"
          >»</a>
        </li>
      </ul>
    </div>
  </div>
</template>

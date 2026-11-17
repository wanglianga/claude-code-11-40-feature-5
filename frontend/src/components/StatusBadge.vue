<script setup lang="ts">
defineProps<{
  status?: string | null
  reviewStatus?: string | null
  label?: string
  dot?: boolean
}>()
</script>

<template>
  <span class="badge" :class="reviewStatus === 'under_review' ? 'under_review' : status || 'neutral'">
    <span v-if="dot !== false" class="dot"></span>
    <slot>{{ label }}</slot>
  </span>
</template>

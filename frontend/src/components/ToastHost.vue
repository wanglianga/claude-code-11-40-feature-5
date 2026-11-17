<script setup lang="ts">
import { toasts } from '../toast'
</script>

<template>
  <div>
    <div v-for="t in toasts" :key="t.id" class="toast" :class="{ error: t.kind === 'error' }">
      {{ t.message }}
    </div>
  </div>
</template>

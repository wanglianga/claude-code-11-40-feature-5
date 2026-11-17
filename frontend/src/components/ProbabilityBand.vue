<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{
  probability: number
  low: number
  high: number
  threshold: number
  label?: string
}>()

const clamp = (v: number) => Math.max(0, Math.min(1, v))
const left = computed(() => clamp(props.low) * 100)
const width = computed(() => Math.max(1.2, (clamp(props.high) - clamp(props.low)) * 100))
const point = computed(() => clamp(props.probability) * 100)
const thresholdPos = computed(() => clamp(props.threshold) * 100)
</script>

<template>
  <div class="prob">
    <div class="prob-band">
      <div class="prob-track"></div>
      <div class="prob-range" :style="{ left: left + '%', width: width + '%' }"></div>
      <div class="prob-point" :style="{ left: point + '%' }"></div>
      <div class="prob-threshold" :style="{ left: thresholdPos + '%' }">
        <span class="prob-threshold-label">阈值 {{ threshold.toFixed(2) }}</span>
      </div>
    </div>
    <div class="prob-axis">
      <span>0</span><span>0.25</span><span>0.5</span><span>0.75</span><span>1</span>
    </div>
    <div v-if="label" class="tiny muted" style="margin-top: 4px">{{ label }}</div>
  </div>
</template>

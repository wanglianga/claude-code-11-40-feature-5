<script setup lang="ts">
/**
 * Minimal SVG map for disclosure partitions.
 *
 * It draws only the GeoJSON the API chooses to release, so a public viewer sees
 * exactly the published blocks and no finer geometry. Pan/zoom is pure SVG
 * transforms over that geometry — there is no per-pixel or coordinate lookup.
 */
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { buildPaths, colorFor, computeBounds, computeViewBox, toShapes } from '../mapGeometry'

const props = defineProps<{
  features: Array<{ geometry: any; properties: Record<string, any> }>
  selected?: string | null
  height?: number
}>()

const emit = defineEmits<{ (event: 'select', code: string): void }>()

const WIDTH = 760
const scale = ref(1)
const offset = ref({ x: 0, y: 0 })
const dragging = ref<{ x: number; y: number; ox: number; oy: number } | null>(null)

const height = computed(() => props.height ?? 420)
const viewBox = computed(() => computeViewBox(computeBounds(props.features), WIDTH, height.value))
const pathFor = computed(() => buildPaths(toShapes(props.features), viewBox.value, WIDTH, height.value))

function onWheel(event: WheelEvent) {
  const factor = event.deltaY < 0 ? 1.15 : 1 / 1.15
  const next = Math.min(12, Math.max(1, scale.value * factor))
  if (next === 1) {
    offset.value = { x: 0, y: 0 }
  }
  scale.value = next
}

function onDown(event: PointerEvent) {
  ;(event.target as Element).setPointerCapture?.(event.pointerId)
  dragging.value = { x: event.clientX, y: event.clientY, ox: offset.value.x, oy: offset.value.y }
}

function onMove(event: PointerEvent) {
  if (!dragging.value) return
  offset.value = {
    x: dragging.value.ox + (event.clientX - dragging.value.x),
    y: dragging.value.oy + (event.clientY - dragging.value.y),
  }
}

function onUp() {
  dragging.value = null
}

function reset() {
  scale.value = 1
  offset.value = { x: 0, y: 0 }
}

watch(
  () => props.features,
  () => reset(),
)
onBeforeUnmount(() => {
  dragging.value = null
})
</script>

<template>
  <div class="spatial-map">
    <svg
      :viewBox="`0 0 ${WIDTH} ${height}`"
      :style="{ height: `${height}px` }"
      role="img"
      aria-label="发布分区地图"
      @wheel.prevent="onWheel"
      @pointerdown="onDown"
      @pointermove="onMove"
      @pointerup="onUp"
      @pointerleave="onUp"
    >
      <g :transform="`translate(${offset.x} ${offset.y}) scale(${scale})`">
        <path
          v-for="shape in pathFor"
          :key="shape.properties.region_code"
          :d="shape.d"
          :fill="colorFor(shape.properties).fill"
          :stroke="
            shape.properties.region_code === selected
              ? 'var(--pine)'
              : colorFor(shape.properties).stroke
          "
          :stroke-width="shape.properties.region_code === selected ? 2.4 : 0.9"
          :opacity="selected && shape.properties.region_code !== selected ? 0.45 : 0.82"
          style="cursor: pointer"
          @click="emit('select', shape.properties.region_code)"
        >
          <title>
            {{ shape.properties.region_code }} · 候选生境
            {{ shape.properties.candidate_habitat_ha }} ha
          </title>
        </path>
      </g>
    </svg>
    <div class="map-tools">
      <button class="btn sm" @click="scale = Math.min(12, scale * 1.3)">放大</button>
      <button class="btn sm" @click="scale = Math.max(1, scale / 1.3)">缩小</button>
      <button class="btn sm" @click="reset">复位</button>
      <span class="tiny muted">滚轮缩放 · 拖动平移</span>
    </div>
  </div>
</template>

<style scoped>
.spatial-map {
  border: 1px solid var(--line);
  border-radius: 9px;
  background: linear-gradient(180deg, #fbfaf5, #f2f0e8);
  overflow: hidden;
  touch-action: none;
}
.spatial-map svg {
  display: block;
  width: 100%;
  user-select: none;
}
.map-tools {
  display: flex;
  gap: 6px;
  align-items: center;
  padding: 6px 8px;
  border-top: 1px solid var(--line);
  background: var(--surface);
}
</style>

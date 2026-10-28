<script setup lang="ts">
import { computed, ref } from 'vue'
import type { MemoryCharacter, MemoryItem } from '@/types/api'

/**
 * 人物关系图：从记忆里每个人物的「关系」描述中找出提到的其他人物，连成一条边。
 * 环形布局，点人物高亮它的关系；下方列出全部关系（表格视图，便于阅读与无障碍）。
 */
const props = defineProps<{ characters: MemoryCharacter[]; archive?: MemoryItem[] }>()

interface Edge { a: string; b: string; labels: string[] }

const SPLIT = /[；;，,。\n]/

const edges = computed<Edge[]>(() => {
  const all = [...props.characters.map((c) => c.name), ...(props.archive ?? []).map((c) => c.name)]
    .filter((n) => n && n.length >= 2)
  const map = new Map<string, Edge>()
  for (const c of props.characters) {
    const parts = (c.relationships || '').split(SPLIT).map((s) => s.trim()).filter(Boolean)
    for (const other of all) {
      if (other === c.name) continue
      const hits = parts.filter((p) => p.includes(other))
      if (!hits.length) continue
      const [a, b] = [c.name, other].sort()
      const key = `${a}\u0000${b}`
      const edge = map.get(key) ?? { a: a!, b: b!, labels: [] }
      for (const h of hits) if (!edge.labels.includes(h)) edge.labels.push(h)
      map.set(key, edge)
    }
  }
  return [...map.values()]
})

const nodes = computed(() => {
  const active = props.characters.map((c) => ({ name: c.name, status: c.status, archived: false }))
  const linked = new Set(edges.value.flatMap((e) => [e.a, e.b]))
  const archived = (props.archive ?? [])
    .filter((c) => linked.has(c.name) && !active.some((a) => a.name === c.name))
    .map((c) => ({ name: c.name, status: c.status, archived: true }))
  const list = [...active, ...archived]
  const W = 640
  const H = 440
  const r = Math.min(W, H) / 2 - 56
  return list.map((n, i) => {
    // 两个人左右摆，三个及以上从正上方开始绕圈
    const angle = (i / Math.max(1, list.length)) * Math.PI * 2 + (list.length === 2 ? Math.PI : -Math.PI / 2)
    return { ...n, x: W / 2 + r * Math.cos(angle), y: H / 2 + r * Math.sin(angle) }
  })
})
const pos = computed(() => Object.fromEntries(nodes.value.map((n) => [n.name, n])))

const selected = ref<string | null>(null)
const hoverEdge = ref<Edge | null>(null)
function related(e: Edge) {
  return !selected.value || e.a === selected.value || e.b === selected.value
}
const visibleEdges = computed(() => edges.value.filter((e) => related(e)))
function shortName(n: string) {
  return n.length > 4 ? n.slice(0, 4) + '…' : n
}
</script>

<template>
  <div class="space-y-4">
    <div v-if="nodes.length < 2" class="rounded-2xl border bg-card p-8 text-center text-sm text-muted-foreground">
      记忆里的人物还太少，写几章、更新记忆后这里会画出人物关系
    </div>
    <template v-else>
      <div class="relative rounded-2xl border bg-card p-3 shadow-sm">
        <p class="px-2 pb-1 text-xs text-muted-foreground">
          点人物只看他的关系；连线来自记忆里「关系」描述中提到的其他人物。虚线圈是近期不活跃的人物。
        </p>
        <svg viewBox="0 0 640 440" class="mx-auto h-auto w-full max-w-2xl text-foreground" role="img" aria-label="人物关系图">
          <g>
            <line
              v-for="e in edges"
              :key="`${e.a}-${e.b}`"
              :x1="pos[e.a]!.x" :y1="pos[e.a]!.y" :x2="pos[e.b]!.x" :y2="pos[e.b]!.y"
              stroke="currentColor"
              :stroke-opacity="related(e) ? (hoverEdge === e ? 0.9 : 0.35) : 0.06"
              :stroke-width="hoverEdge === e ? 2.5 : 1.5"
            />
            <!-- 比线宽得多的透明命中区，方便悬停 -->
            <line
              v-for="e in visibleEdges"
              :key="`hit-${e.a}-${e.b}`"
              :x1="pos[e.a]!.x" :y1="pos[e.a]!.y" :x2="pos[e.b]!.x" :y2="pos[e.b]!.y"
              stroke="transparent" stroke-width="14"
              class="cursor-help"
              @mouseenter="hoverEdge = e" @mouseleave="hoverEdge = null"
            />
          </g>
          <g v-for="n in nodes" :key="n.name" class="cursor-pointer" @click="selected = selected === n.name ? null : n.name">
            <title>{{ n.name }}{{ n.status ? '：' + n.status : '' }}</title>
            <circle
              :cx="n.x" :cy="n.y" r="26"
              class="fill-background"
              stroke="currentColor"
              :stroke-width="selected === n.name ? 2.5 : 1.5"
              :stroke-dasharray="n.archived ? '4 3' : undefined"
              :stroke-opacity="!selected || selected === n.name || edges.some((e) => (e.a === selected && e.b === n.name) || (e.b === selected && e.a === n.name)) ? 1 : 0.25"
            />
            <text :x="n.x" :y="n.y + 4" text-anchor="middle" font-size="12" class="fill-foreground font-medium select-none">
              {{ shortName(n.name) }}
            </text>
          </g>
        </svg>
        <div
          v-if="hoverEdge"
          class="pointer-events-none absolute left-1/2 top-10 z-10 max-w-sm -translate-x-1/2 rounded-lg border bg-popover px-3 py-2 text-xs shadow-md"
        >
          <p class="font-medium">{{ hoverEdge.a }} — {{ hoverEdge.b }}</p>
          <p v-for="(l, i) in hoverEdge.labels" :key="i" class="mt-0.5 text-muted-foreground">{{ l }}</p>
        </div>
      </div>

      <div class="rounded-2xl border bg-card shadow-sm">
        <div class="flex items-center border-b px-5 py-3 text-sm font-medium">
          {{ selected ? `${selected} 的关系` : '全部关系' }}（{{ visibleEdges.length }}）
          <button v-if="selected" type="button" class="ml-auto text-xs text-muted-foreground hover:text-foreground" @click="selected = null">
            看全部
          </button>
        </div>
        <ul class="divide-y">
          <li v-for="e in visibleEdges" :key="`row-${e.a}-${e.b}`" class="px-5 py-2.5 text-sm">
            <span class="font-medium">{{ e.a }} — {{ e.b }}</span>
            <span class="ml-2 text-muted-foreground">{{ e.labels.join('；') }}</span>
          </li>
          <li v-if="!visibleEdges.length" class="px-5 py-4 text-sm text-muted-foreground">没有找到关系描述</li>
        </ul>
      </div>
    </template>
  </div>
</template>

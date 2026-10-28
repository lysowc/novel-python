<script setup lang="ts">
import { computed } from 'vue'
import type { MemoryForeshadowing } from '@/types/api'

/** 伏笔一览：每条伏笔一行，在章节轴上画出「埋下 → 回收」的跨度；未回收的延伸到当前章 */
const props = defineProps<{ items: MemoryForeshadowing[]; currentChapter: number }>()

const maxNo = computed(() =>
  Math.max(
    props.currentChapter || 1,
    ...props.items.map((f) => Math.max(f.planted_chapter || 0, f.resolved_chapter || 0)),
  ),
)
const rows = computed(() =>
  [...props.items].sort((a, b) => {
    if (a.status !== b.status) return a.status === 'open' ? -1 : 1
    return (a.planted_chapter || 0) - (b.planted_chapter || 0)
  }),
)
const openCount = computed(() => props.items.filter((f) => f.status === 'open').length)

function span(f: MemoryForeshadowing) {
  const start = Math.max(1, f.planted_chapter || 1)
  const end = f.status === 'resolved' ? Math.max(start, f.resolved_chapter || start) : Math.max(start, props.currentChapter || start)
  const pct = (n: number) => ((n - 1) / Math.max(1, maxNo.value - 1)) * 100
  return { left: `${pct(start)}%`, width: `${Math.max(1.5, pct(end) - pct(start))}%`, start, end }
}
function age(f: MemoryForeshadowing) {
  return f.status === 'open' && f.planted_chapter ? Math.max(0, (props.currentChapter || 0) - f.planted_chapter) : 0
}
</script>

<template>
  <div class="rounded-2xl border bg-card shadow-sm">
    <div class="flex flex-wrap items-center gap-x-4 gap-y-1 border-b px-5 py-3 text-sm">
      <span class="font-medium">伏笔一览</span>
      <span class="text-muted-foreground">未回收 {{ openCount }} · 已回收 {{ items.length - openCount }} · 当前写到第 {{ currentChapter || '—' }} 章</span>
      <span class="ml-auto flex items-center gap-3 text-xs text-muted-foreground">
        <span class="flex items-center gap-1"><span class="inline-block h-1.5 w-5 rounded-full bg-foreground/80" />已回收</span>
        <span class="flex items-center gap-1"><span class="inline-block h-1.5 w-5 rounded-full border border-dashed border-foreground/60" />未回收</span>
      </span>
    </div>
    <p v-if="!items.length" class="px-5 py-8 text-center text-sm text-muted-foreground">记忆里还没有伏笔</p>
    <ul v-else class="divide-y">
      <li v-for="(f, i) in rows" :key="i" class="grid gap-2 px-5 py-3 sm:grid-cols-[minmax(0,1fr)_minmax(0,1.2fr)] sm:items-center">
        <div class="min-w-0">
          <p class="text-sm" :class="f.status === 'resolved' ? 'text-muted-foreground' : 'font-medium'">{{ f.description }}</p>
          <p class="mt-0.5 text-xs text-muted-foreground">
            {{ f.planted_chapter ? `第 ${f.planted_chapter} 章埋下` : '埋下章节未知' }}
            <template v-if="f.status === 'resolved'"> · 第 {{ f.resolved_chapter || '?' }} 章回收</template>
            <template v-else-if="age(f) >= 20"> · <span class="text-foreground">已经 {{ age(f) }} 章没回收</span></template>
            <template v-else> · 未回收</template>
          </p>
        </div>
        <div class="relative h-5" :title="`第 ${span(f).start} 章 → 第 ${span(f).end} 章`">
          <div class="absolute inset-x-0 top-1/2 h-px -translate-y-1/2 bg-border" />
          <div
            class="absolute top-1/2 h-1.5 -translate-y-1/2 rounded-full"
            :class="f.status === 'resolved' ? 'bg-foreground/80' : 'border border-dashed border-foreground/60 bg-transparent'"
            :style="{ left: span(f).left, width: span(f).width }"
          />
        </div>
      </li>
    </ul>
    <div v-if="items.length" class="flex justify-between px-5 pb-3 text-[11px] text-muted-foreground sm:ml-[45%]">
      <span>第 1 章</span><span>第 {{ maxNo }} 章</span>
    </div>
  </div>
</template>

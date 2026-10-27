<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { Info } from '@lucide/vue'
import PageHeader from '@/components/common/PageHeader.vue'
import LoadingState from '@/components/common/LoadingState.vue'
import EmptyState from '@/components/common/EmptyState.vue'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { fetchUsage } from '@/api'
import { formatNumber } from '@/lib/format'
import type { UsageRow, UsageStats } from '@/types/api'

const loading = ref(true)
const data = ref<UsageStats | null>(null)
const days = ref(30)
const metric = ref<'cost' | 'total_tokens' | 'calls'>('total_tokens')
const tab = ref<'novel' | 'task' | 'model'>('novel')

const RANGES = [7, 30, 90]
const METRICS = [
  { key: 'total_tokens', label: 'tokens' },
  { key: 'cost', label: '费用' },
  { key: 'calls', label: '调用次数' },
] as const

async function load() {
  loading.value = true
  try {
    data.value = await fetchUsage({ days: days.value })
  } catch {
    data.value = null
  } finally {
    loading.value = false
  }
}
watch(days, load)
onMounted(load)

const money = (v: number) => `¥${v < 1 ? v.toFixed(4) : v.toFixed(2)}`
const compact = (v: number) =>
  v >= 1e8 ? `${(v / 1e8).toFixed(2)} 亿` : v >= 1e4 ? `${(v / 1e4).toFixed(1)} 万` : formatNumber(v)
function fmt(v: number, key = metric.value) {
  return key === 'cost' ? money(v) : compact(v)
}

const hasPrice = computed(() => (data.value?.totals.cost ?? 0) > 0)
const tiles = computed(() => {
  const t = data.value?.totals
  if (!t) return []
  return [
    { label: '调用次数', value: formatNumber(t.calls), sub: t.failed ? `失败 ${t.failed}` : '全部成功' },
    { label: '总 tokens', value: compact(t.total_tokens), sub: `输入 ${compact(t.prompt_tokens)} · 输出 ${compact(t.completion_tokens)}` },
    { label: '费用', value: hasPrice.value ? money(t.cost) : '—', sub: hasPrice.value ? '按模型单价计算' : '在「AI 配置」填写模型单价后计算' },
    { label: '日均 tokens', value: compact(Math.round(t.total_tokens / (data.value?.days || 1))), sub: `近 ${data.value?.days} 天` },
  ]
})

// ---- 每日柱状图（单一指标、单一序列：不需要图例，标题即说明） ----
const W = 720
const H = 200
const PAD = { top: 12, right: 8, bottom: 22, left: 8 }
const bars = computed(() => {
  const rows = data.value?.by_day ?? []
  const max = Math.max(...rows.map((r) => Number(r[metric.value]) || 0), 0)
  const innerW = W - PAD.left - PAD.right
  const innerH = H - PAD.top - PAD.bottom
  const step = rows.length ? innerW / rows.length : 0
  const gap = Math.min(2, step * 0.2) // 柱间 2px 留白
  return rows.map((r, i) => {
    const v = Number(r[metric.value]) || 0
    const h = max > 0 ? Math.max(v > 0 ? 2 : 0, (v / max) * innerH) : 0
    return {
      date: r.date, row: r, value: v,
      x: PAD.left + i * step + gap / 2, w: Math.max(1, step - gap),
      y: PAD.top + innerH - h, h,
      hitX: PAD.left + i * step, hitW: step,
    }
  })
})
const maxValue = computed(() => Math.max(...(data.value?.by_day ?? []).map((r) => Number(r[metric.value]) || 0), 0))
const xTicks = computed(() => {
  const b = bars.value
  if (!b.length) return []
  const idx = [0, Math.floor((b.length - 1) / 2), b.length - 1]
  return [...new Set(idx)].map((i) => ({ x: b[i]!.x + b[i]!.w / 2, label: b[i]!.date.slice(5) }))
})
const hover = ref<number | null>(null)
const hovered = computed(() => (hover.value == null ? null : bars.value[hover.value] ?? null))

/** 柱子：顶部 4px 圆角，底部贴住基线保持平直 */
function barPath(b: { x: number; y: number; w: number; h: number }) {
  if (b.h <= 0) return ''
  const r = Math.min(4, b.w / 2, b.h)
  const bottom = b.y + b.h
  return `M${b.x},${bottom} V${b.y + r} Q${b.x},${b.y} ${b.x + r},${b.y} H${b.x + b.w - r} Q${b.x + b.w},${b.y} ${b.x + b.w},${b.y + r} V${bottom} Z`
}

// ---- 分组明细 ----
const groupRows = computed(() => {
  const d = data.value
  if (!d) return [] as { name: string; row: UsageRow }[]
  if (tab.value === 'novel') return d.by_novel.map((r) => ({ name: r.title, row: r }))
  if (tab.value === 'task') return d.by_task_type.map((r) => ({ name: r.task_type_text, row: r }))
  return d.by_model.map((r) => ({ name: r.model, row: r }))
})
const groupTotal = computed(() => groupRows.value.reduce((s, g) => s + (hasPrice.value ? g.row.cost : g.row.total_tokens), 0))
function share(r: UsageRow) {
  const v = hasPrice.value ? r.cost : r.total_tokens
  return groupTotal.value > 0 ? (v / groupTotal.value) * 100 : 0
}
const estimatedCalls = computed(() => data.value?.totals.estimated ?? 0)
</script>

<template>
  <div class="space-y-6">
    <PageHeader title="用量统计" description="AI 调用的 tokens 与费用，按天、按小说、按任务类型和模型汇总" />

    <!-- 筛选：一行 -->
    <div class="flex flex-wrap items-center gap-2">
      <div class="inline-flex rounded-lg border p-0.5">
        <button
          v-for="d in RANGES"
          :key="d"
          type="button"
          class="rounded-md px-3 py-1 text-sm transition-colors"
          :class="days === d ? 'bg-foreground text-background' : 'text-muted-foreground hover:text-foreground'"
          @click="days = d"
        >近 {{ d }} 天</button>
      </div>
    </div>

    <LoadingState v-if="loading" variant="table" :rows="4" />

    <div v-else-if="!data || data.totals.calls === 0" class="rounded-2xl border bg-card">
      <EmptyState title="这段时间没有 AI 调用" description="生成设定、大纲、章节等操作后，这里会显示用量" />
    </div>

    <template v-else>
      <!-- 指标卡 -->
      <div class="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <div v-for="t in tiles" :key="t.label" class="rounded-2xl border bg-card p-5 shadow-sm">
          <p class="text-xs text-muted-foreground">{{ t.label }}</p>
          <p class="mt-2 truncate text-2xl font-bold leading-none tabular-nums">{{ t.value }}</p>
          <p class="mt-2 truncate text-xs text-muted-foreground">{{ t.sub }}</p>
        </div>
      </div>

      <p v-if="estimatedCalls" class="flex items-start gap-1.5 text-xs text-muted-foreground">
        <Info class="mt-0.5 size-3.5 shrink-0" />
        其中 {{ formatNumber(estimatedCalls) }} 次调用的接口没有返回用量，按字数估算（约 1 字 ≈ 1 token）。
      </p>

      <!-- 每日趋势 -->
      <div class="rounded-2xl border bg-card p-5 shadow-sm">
        <div class="mb-3 flex flex-wrap items-center justify-between gap-2">
          <h2 class="text-sm font-semibold">每日{{ metric === 'total_tokens' ? ' tokens' : METRICS.find((m) => m.key === metric)?.label }}</h2>
          <div class="inline-flex rounded-lg border p-0.5">
            <button
              v-for="m in METRICS"
              :key="m.key"
              type="button"
              class="rounded-md px-2.5 py-0.5 text-xs transition-colors"
              :class="metric === m.key ? 'bg-foreground text-background' : 'text-muted-foreground hover:text-foreground'"
              @click="metric = m.key"
            >{{ m.label }}</button>
          </div>
        </div>
        <div class="relative">
          <svg :viewBox="`0 0 ${W} ${H}`" class="h-52 w-full text-foreground" preserveAspectRatio="none" role="img"
               :aria-label="`近 ${data.days} 天每日用量柱状图`" @mouseleave="hover = null">
            <!-- 基线与最大值参考线（弱化） -->
            <line :x1="PAD.left" :x2="W - PAD.right" :y1="H - PAD.bottom" :y2="H - PAD.bottom" class="stroke-border" stroke-width="1" />
            <line :x1="PAD.left" :x2="W - PAD.right" :y1="PAD.top" :y2="PAD.top" class="stroke-border" stroke-width="1" stroke-dasharray="3 3" />
            <path
              v-for="(b, i) in bars"
              :key="b.date"
              :d="barPath(b)"
              fill="currentColor"
              :opacity="hover == null || hover === i ? 0.85 : 0.35"
            />
            <!-- 比柱子更大的悬停区域 -->
            <rect
              v-for="(b, i) in bars"
              :key="`hit-${b.date}`"
              :x="b.hitX" :y="0" :width="b.hitW" :height="H - PAD.bottom"
              fill="transparent"
              @mouseenter="hover = i"
              @touchstart.passive="hover = i"
            />
            <text v-for="t in xTicks" :key="t.label" :x="t.x" :y="H - 6" text-anchor="middle"
                  class="fill-muted-foreground" font-size="11">{{ t.label }}</text>
          </svg>
          <span class="pointer-events-none absolute left-2 top-0 text-[11px] text-muted-foreground tabular-nums">
            最高 {{ fmt(maxValue) }}
          </span>
          <!-- 悬停提示 -->
          <div
            v-if="hovered"
            class="pointer-events-none absolute top-6 z-10 -translate-x-1/2 whitespace-nowrap rounded-lg border bg-popover px-3 py-2 text-xs shadow-md"
            :style="{ left: `${Math.min(88, Math.max(12, ((hovered.x + hovered.w / 2) / W) * 100))}%` }"
          >
            <p class="font-medium">{{ hovered.date }}</p>
            <p class="mt-1 tabular-nums text-muted-foreground">
              {{ formatNumber(hovered.row.calls) }} 次 · {{ compact(hovered.row.total_tokens) }} tokens
              <template v-if="hasPrice"> · {{ money(hovered.row.cost) }}</template>
            </p>
          </div>
        </div>
      </div>

      <!-- 分组明细（表格即可读的数据视图） -->
      <div class="rounded-2xl border bg-card shadow-sm">
        <div class="flex flex-wrap items-center gap-2 border-b px-5 py-3">
          <h2 class="mr-auto text-sm font-semibold">用量构成</h2>
          <div class="inline-flex rounded-lg border p-0.5">
            <button
              v-for="t in [{ k: 'novel', l: '按小说' }, { k: 'task', l: '按任务类型' }, { k: 'model', l: '按模型' }] as const"
              :key="t.k"
              type="button"
              class="rounded-md px-2.5 py-0.5 text-xs transition-colors"
              :class="tab === t.k ? 'bg-foreground text-background' : 'text-muted-foreground hover:text-foreground'"
              @click="tab = t.k"
            >{{ t.l }}</button>
          </div>
        </div>
        <div class="overflow-x-auto">
          <Table class="min-w-[560px]">
            <TableHeader>
              <TableRow class="hover:bg-transparent">
                <TableHead>{{ tab === 'novel' ? '小说' : tab === 'task' ? '任务类型' : '模型' }}</TableHead>
                <TableHead class="text-right">调用</TableHead>
                <TableHead class="text-right">tokens</TableHead>
                <TableHead class="text-right">费用</TableHead>
                <TableHead class="w-40">占比（{{ hasPrice ? '费用' : 'tokens' }}）</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              <TableRow v-for="g in groupRows" :key="g.name">
                <TableCell class="max-w-56 truncate font-medium">{{ g.name }}</TableCell>
                <TableCell class="text-right tabular-nums">
                  {{ formatNumber(g.row.calls) }}
                  <span v-if="g.row.failed" class="text-xs text-muted-foreground">（失败 {{ g.row.failed }}）</span>
                </TableCell>
                <TableCell class="text-right tabular-nums">{{ compact(g.row.total_tokens) }}</TableCell>
                <TableCell class="text-right tabular-nums">{{ hasPrice ? money(g.row.cost) : '—' }}</TableCell>
                <TableCell>
                  <div class="flex items-center gap-2">
                    <div class="h-1.5 flex-1 overflow-hidden rounded-full bg-muted">
                      <div class="h-full rounded-full bg-foreground/80" :style="{ width: `${share(g.row)}%` }" />
                    </div>
                    <span class="w-10 text-right text-xs tabular-nums text-muted-foreground">{{ share(g.row).toFixed(0) }}%</span>
                  </div>
                </TableCell>
              </TableRow>
            </TableBody>
          </Table>
        </div>
      </div>
    </template>
  </div>
</template>

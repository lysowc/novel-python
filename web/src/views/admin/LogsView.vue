<script setup lang="ts">
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog'

import { computed, onMounted, ref } from 'vue'
import { Copy, FileText, LoaderCircle, Search, Trash2, X } from '@lucide/vue'
import { toast } from 'vue-sonner'
import PageHeader from '@/components/common/PageHeader.vue'
import LoadingState from '@/components/common/LoadingState.vue'
import EmptyState from '@/components/common/EmptyState.vue'
import PaginationBar from '@/components/common/PaginationBar.vue'
import ConfirmDialog from '@/components/common/ConfirmDialog.vue'
import { clearLogs, fetchAdminNovels, fetchLogDetail, fetchLogs } from '@/api'
import { formatDate, formatDuration, formatNumber } from '@/lib/format'
import type { AiLog, AiLogDetail, PageResult } from '@/types/api'

const loading = ref(true)
const logs = ref<PageResult<AiLog>>({ list: [], total: 0, page: 1, page_size: 15 })
const clearOpen = ref(false)
const clearing = ref(false)

const taskTypeLabel: Record<string, string> = {
  generate_setting: '生成设定', generate_outline: '生成大纲', generate_chapter: '生成章节',
  continue_chapter: '续写章节', regenerate_chapter: '重新生成', generate_summary: '生成摘要',
  update_memory: '更新记忆', rebuild_memory: '重建记忆', extend_outline: '续写大纲', idea_chat: '点子聊天', idea_save: '点子保存',
  consistency_check: '一致性审校', arc_summary: '分段总结', outline_sync: '大纲校准', quality_check: '质量检查',
}
const reasoningLabel: Record<string, string> = { none: '不思考', low: '轻度', high: '深度', max: '最深' }

// ---------- 筛选 ----------
const keyword = ref('')
const activeKeyword = ref('') // 已提交搜索的关键词（用于高亮）
const novelId = ref<string>('')
const taskType = ref('')
const status = ref('')
const novels = ref<{ id: number; title: string }[]>([])

const hasFilter = computed(() => !!(activeKeyword.value || novelId.value || taskType.value || status.value))

async function load() {
  loading.value = true
  try {
    logs.value = await fetchLogs({
      page: logs.value.page, page_size: logs.value.page_size, keyword: activeKeyword.value,
      novel_id: novelId.value, task_type: taskType.value, status: status.value,
    })
  } finally {
    loading.value = false
  }
}

function search() {
  activeKeyword.value = keyword.value.trim()
  logs.value.page = 1
  load()
}

function resetFilters() {
  keyword.value = ''
  activeKeyword.value = ''
  novelId.value = ''
  taskType.value = ''
  status.value = ''
  logs.value.page = 1
  load()
}

async function loadNovels() {
  try {
    const res = await fetchAdminNovels({ page: 1, page_size: 100 })
    novels.value = res.list.map((n) => ({ id: n.id, title: n.title }))
  } catch {
    // 拿不到小说列表也不影响看日志
  }
}

const novelTitle = (id?: number) => (id ? novels.value.find((n) => n.id === id)?.title || `#${id}` : '')

// ---------- 高亮 ----------
function escapeHtml(text: string) {
  return text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;')
}
function highlight(text: string) {
  const safe = escapeHtml(text || '')
  const k = activeKeyword.value
  if (!k) return safe
  const pattern = escapeHtml(k).replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  return safe.replace(new RegExp(pattern, 'gi'), (m) => `<mark class="rounded bg-yellow-300/70 px-0.5 text-foreground dark:bg-yellow-500/50">${m}</mark>`)
}
function countMatches(text: string) {
  const k = activeKeyword.value.toLowerCase()
  if (!k || !text) return 0
  return text.toLowerCase().split(k).length - 1
}

// ---------- 详情 ----------
const detailOpen = ref(false)
const detailLoading = ref(false)
const detail = ref<AiLogDetail | null>(null)
const detailTab = ref<'input' | 'output' | 'reasoning' | 'prompt'>('output')

const detailTabs = computed(() => {
  const d = detail.value
  if (!d) return []
  return [
    { value: 'output' as const, label: '输出', text: d.output_text },
    { value: 'input' as const, label: '输入', text: d.input_text },
    { value: 'reasoning' as const, label: '思考过程', text: d.reasoning_text },
    { value: 'prompt' as const, label: 'System Prompt', text: d.prompt },
  ].filter((t) => t.text)
})
const currentText = computed(() => detailTabs.value.find((t) => t.value === detailTab.value)?.text || '')

async function openDetail(l: AiLog) {
  detailOpen.value = true
  detailLoading.value = true
  detail.value = null
  try {
    detail.value = await fetchLogDetail(l.id)
    const field = l.match?.field
    const preferred = field === 'input_text' ? 'input' : field === 'reasoning_text' ? 'reasoning' : field === 'prompt' ? 'prompt' : 'output'
    detailTab.value = detailTabs.value.some((t) => t.value === preferred) ? preferred : (detailTabs.value[0]?.value ?? 'output')
  } catch {
    detailOpen.value = false
  } finally {
    detailLoading.value = false
  }
}

async function copyCurrent() {
  try {
    await navigator.clipboard.writeText(currentText.value)
    toast.success('已复制')
  } catch {
    toast.error('复制失败')
  }
}

async function onClear() {
  clearing.value = true
  try {
    await clearLogs()
    toast.success('日志已清空')
    clearOpen.value = false
    logs.value.page = 1
    load()
  } catch {
    // 请求层已提示
  } finally {
    clearing.value = false
  }
}

onMounted(() => {
  load()
  loadNovels()
})
</script>

<template>
  <div>
    <PageHeader title="AI 日志" description="每一次 AI 调用的参数、用量和完整的输入输出，可按关键词搜索">
      <template #actions>
        <Button variant="destructive" class="gap-2" @click="clearOpen = true">
          <Trash2 class="size-4" />
          清空日志
        </Button>
      </template>
    </PageHeader>

    <!-- 筛选 -->
    <form class="mb-4 flex flex-wrap items-center gap-2" @submit.prevent="search">
      <div class="relative min-w-56 flex-1 sm:max-w-sm">
        <Search class="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" />
        <Input v-model="keyword" placeholder="搜输入、输出、思考过程、Prompt、错误" class="pl-8" />
      </div>
      <select v-model="novelId" class="h-9 max-w-48 rounded-md border bg-background px-2 text-sm" @change="search">
        <option value="">全部小说</option>
        <option v-for="n in novels" :key="n.id" :value="String(n.id)">{{ n.title }}</option>
      </select>
      <select v-model="taskType" class="h-9 rounded-md border bg-background px-2 text-sm" @change="search">
        <option value="">全部任务</option>
        <option v-for="(label, key) in taskTypeLabel" :key="key" :value="key">{{ label }}</option>
      </select>
      <select v-model="status" class="h-9 rounded-md border bg-background px-2 text-sm" @change="search">
        <option value="">全部状态</option>
        <option value="success">成功</option>
        <option value="failed">失败</option>
      </select>
      <Button type="submit" size="sm" class="h-9">搜索</Button>
      <Button v-if="hasFilter" type="button" variant="ghost" size="sm" class="h-9 gap-1 text-muted-foreground" @click="resetFilters">
        <X class="size-4" /> 清除
      </Button>
      <span v-if="hasFilter && !loading" class="text-xs text-muted-foreground">找到 {{ formatNumber(logs.total) }} 条</span>
    </form>

    <LoadingState v-if="loading" variant="table" :rows="8" />

    <div v-else-if="logs.list.length === 0" class="rounded-2xl border bg-card">
      <EmptyState :title="hasFilter ? '没有符合条件的日志' : '暂无日志'" :description="hasFilter ? '换个关键词或筛选条件试试' : 'AI 调用记录会显示在这里'" />
    </div>

    <div v-else class="overflow-x-auto rounded-2xl border bg-card shadow-sm">
      <Table class="min-w-[960px]">
        <TableHeader>
          <TableRow class="hover:bg-transparent">
            <TableHead>时间</TableHead>
            <TableHead>任务</TableHead>
            <TableHead>模型</TableHead>
            <TableHead class="text-right">温度</TableHead>
            <TableHead>思考</TableHead>
            <TableHead class="text-right">输入 / 输出 tokens</TableHead>
            <TableHead class="text-right">耗时</TableHead>
            <TableHead>状态</TableHead>
            <TableHead>错误信息</TableHead>
            <TableHead>详情</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          <template v-for="l in logs.list" :key="l.id">
            <TableRow :class="l.match ? 'border-b-0' : ''">
              <TableCell class="whitespace-nowrap text-xs">{{ formatDate(l.created_at, true) }}</TableCell>
              <TableCell class="text-xs">
                <p>{{ taskTypeLabel[l.task_type] || l.task_type }}</p>
                <p v-if="l.novel_id" class="max-w-36 truncate text-[11px] text-muted-foreground">{{ novelTitle(l.novel_id) }}</p>
              </TableCell>
              <TableCell>
                <p class="text-xs font-medium">{{ l.model }}</p>
                <p class="text-[11px] text-muted-foreground">{{ l.provider }}</p>
              </TableCell>
              <TableCell class="text-right text-xs">{{ l.temperature ?? '—' }}</TableCell>
              <TableCell class="text-xs">{{ l.reasoning ? (reasoningLabel[l.reasoning] || l.reasoning) : '—' }}</TableCell>
              <TableCell class="text-right text-xs whitespace-nowrap">
                {{ formatNumber(l.prompt_tokens) }} / {{ formatNumber(l.completion_tokens) }}
              </TableCell>
              <TableCell class="text-right text-xs">{{ formatDuration(l.duration) }}</TableCell>
              <TableCell>
                <span
                  class="rounded-full px-2 py-0.5 text-[11px] font-medium"
                  :class="l.status === 'success' ? 'bg-foreground/10 text-foreground' : 'bg-destructive/10 text-destructive'"
                >
                  {{ l.status === 'success' ? '成功' : '失败' }}
                </span>
              </TableCell>
              <TableCell class="max-w-52">
                <p class="line-clamp-1 text-xs text-destructive" :title="l.error_message">{{ l.error_message || '—' }}</p>
              </TableCell>
              <TableCell>
                <Button v-if="l.has_io || l.has_prompt" variant="ghost" size="icon" class="size-8" title="查看输入输出" @click="openDetail(l)">
                  <FileText class="size-4" />
                </Button>
                <span v-else class="text-xs text-muted-foreground" title="旧日志没有记录输入输出">—</span>
              </TableCell>
            </TableRow>
            <TableRow v-if="l.match" class="hover:bg-transparent">
              <TableCell colspan="10" class="pt-0 pb-3">
                <button type="button" class="w-full rounded-lg bg-muted/50 px-3 py-2 text-left text-xs leading-relaxed hover:bg-muted" @click="openDetail(l)">
                  <span class="mr-2 rounded bg-background px-1.5 py-0.5 text-[11px] text-muted-foreground">{{ l.match.label }}</span>
                  <!-- eslint-disable-next-line vue/no-v-html -->
                  <span v-html="highlight(l.match.text)" />
                </button>
              </TableCell>
            </TableRow>
          </template>
        </TableBody>
      </Table>
    </div>

    <PaginationBar
      v-if="!loading && logs.total > 0"
      v-model:page="logs.page"
      :page-size="logs.page_size"
      :total="logs.total"
      @update:page="load"
    />

    <ConfirmDialog
      :open="clearOpen"
      title="清空日志"
      description="确定要清空全部 AI 调用日志吗？此操作无法撤销。"
      confirm-text="清空"
      :loading="clearing"
      @update:open="(v: boolean) => (clearOpen = v)"
      @confirm="onClear"
    />

    <!-- 详情：完整输入 / 输出 / 思考过程 / System Prompt -->
    <Dialog v-model:open="detailOpen">
      <DialogContent class="flex max-h-[90vh] flex-col sm:max-w-4xl">
        <DialogHeader>
          <DialogTitle>
            {{ detail ? (taskTypeLabel[detail.task_type] || detail.task_type) : 'AI 调用详情' }}
            <span v-if="detail?.novel_id" class="ml-1 text-sm font-normal text-muted-foreground">· {{ novelTitle(detail.novel_id) }}</span>
          </DialogTitle>
          <DialogDescription v-if="detail">
            {{ formatDate(detail.created_at, true) }} · {{ detail.model }} · 温度 {{ detail.temperature ?? '未记录' }} ·
            思考 {{ detail.reasoning ? (reasoningLabel[detail.reasoning] || detail.reasoning) : '未传' }} ·
            输出上限 {{ detail.max_tokens ? formatNumber(detail.max_tokens) : '—' }} ·
            {{ formatNumber(detail.prompt_tokens) }} / {{ formatNumber(detail.completion_tokens) }} tokens · {{ formatDuration(detail.duration) }}
          </DialogDescription>
        </DialogHeader>

        <div v-if="detailLoading" class="flex items-center py-10 text-sm text-muted-foreground">
          <LoaderCircle class="mr-2 size-4 animate-spin" /> 加载中…
        </div>
        <template v-else-if="detail">
          <div class="flex flex-wrap items-center gap-2">
            <Button
              v-for="t in detailTabs"
              :key="t.value"
              size="sm"
              :variant="detailTab === t.value ? 'default' : 'outline'"
              class="h-8"
              @click="detailTab = t.value"
            >
              {{ t.label }}
              <span class="text-[11px] opacity-70">{{ formatNumber(t.text.length) }} 字</span>
              <span v-if="countMatches(t.text)" class="rounded bg-yellow-300/80 px-1 text-[11px] text-foreground">{{ countMatches(t.text) }}</span>
            </Button>
            <Button size="sm" variant="ghost" class="ml-auto h-8 gap-1.5" :disabled="!currentText" @click="copyCurrent">
              <Copy class="size-4" /> 复制
            </Button>
          </div>
          <p v-if="detail.error_message" class="rounded-lg bg-destructive/10 px-3 py-2 text-xs text-destructive">{{ detail.error_message }}</p>
          <p v-if="!detailTabs.length" class="py-8 text-center text-sm text-muted-foreground">这条是旧日志，没有记录输入输出</p>
          <!-- eslint-disable-next-line vue/no-v-html -->
          <pre v-else class="min-h-0 flex-1 overflow-y-auto rounded-lg bg-muted/50 p-4 font-mono text-xs leading-relaxed whitespace-pre-wrap" v-html="highlight(currentText)" />
        </template>
      </DialogContent>
    </Dialog>
  </div>
</template>

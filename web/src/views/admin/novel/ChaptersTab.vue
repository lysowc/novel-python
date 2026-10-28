<script setup lang="ts">import { Textarea } from '@/components/ui/textarea'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { Button } from '@/components/ui/button'

import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { ArchiveRestore, Brain, Eye, MoreHorizontal, Send, FilePlus2, History, LoaderCircle, Pencil, Plus, RefreshCw, RotateCw, Sparkles, Trash2, TriangleAlert, Wand2 } from '@lucide/vue'
import { toast } from 'vue-sonner'
import LoadingState from '@/components/common/LoadingState.vue'
import EmptyState from '@/components/common/EmptyState.vue'
import ConfirmDialog from '@/components/common/ConfirmDialog.vue'
import StreamDialog from '@/components/common/StreamDialog.vue'
import ChapterVersionsDialog from '@/components/common/ChapterVersionsDialog.vue'
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import {
  cancelTask, createChapter, deleteChapter, fetchAdminChapters, fetchChapter, fetchOutlineStatus, fetchTasks,
  publishChapters, retryTask, updateChapter,
} from '@/api'
import { countWords, formatNumber, formatRelative } from '@/lib/format'
import type { AiTask, Chapter, OutlineStatus } from '@/types/api'

const props = defineProps<{ novelId: number }>()

type AiChapterTask = 'generate_chapter' | 'continue_chapter' | 'regenerate_chapter' | 'generate_summary' | 'update_memory'

const loading = ref(true)
const chapters = ref<Chapter[]>([])

// 编辑/新增章节对话框
const editOpen = ref(false)
const editing = ref<Chapter | null>(null)
const saving = ref(false)
const chapterForm = ref({ title: '', content: '', summary: '' })

// 删除
const deleting = ref<Chapter | null>(null)
const deleteLoading = ref(false)

// AI 流式任务
const aiOpen = ref(false)
const aiTask = ref<{ type: AiChapterTask; no?: number; mode: 'stream' | 'poll'; remaining?: number } | null>(null)
const aiTitle = ref('')

// 连续续写
const batchCount = ref(5)

// 历史版本 / 已删除章节
const versionsOpen = ref(false)
const versionsMode = ref<'versions' | 'deleted'>('versions')
const versionsChapter = ref<Chapter | null>(null)
function openVersions(c: Chapter) {
  versionsMode.value = 'versions'
  versionsChapter.value = c
  versionsOpen.value = true
}
function openDeleted() {
  versionsMode.value = 'deleted'
  versionsChapter.value = null
  versionsOpen.value = true
}

// 查看章节内容（只读）
const viewOpen = ref(false)
const viewing = ref<Chapter | null>(null)

// 进行中的任务（查看进度）
const runningTask = ref<AiTask | null>(null)
const progressOpen = ref(false)

// 失败任务（可重试）
const recentTasks = ref<AiTask[]>([])
const retrying = ref<number | null>(null)
const DISMISS_KEY = `novel:${props.novelId}:dismissedTasks`
function readDismissed(): number[] {
  try {
    return JSON.parse(localStorage.getItem(DISMISS_KEY) || '[]')
  } catch {
    return []
  }
}
const dismissed = ref<number[]>(readDismissed())

/** 需要处理的失败任务：章节已保存待补做的、连续续写被停止的，以及最近一个任务失败的 */
const failedTasks = computed(() =>
  recentTasks.value.filter((t, i) => {
    if (dismissed.value.includes(t.id)) return false
    if (t.status === 'cancelled') return !!t.params?.resume
    if (t.status !== 'failed') return false
    return !!t.params?.resume || !!t.params?.chain_stopped || i === 0
  }),
)

function retryLabel(t: AiTask) {
  if (t.params?.resume) return '补做摘要/记忆'
  if (t.params?.chain_stopped) return '重试并继续续写'
  return '重试'
}

async function retry(t: AiTask) {
  if (openProgressIfRunning()) return
  retrying.value = t.id
  try {
    runningTask.value = await retryTask(t.id)
    toast.success('已重新入队')
    progressOpen.value = true
    loadRunningTask()
  } catch {
    // 请求层已提示
  } finally {
    retrying.value = null
  }
}

const cancelling = ref(false)
async function cancelRunning(afterCurrent = false) {
  if (!runningTask.value) return
  cancelling.value = true
  try {
    await cancelTask(runningTask.value.id, afterCurrent)
    toast.success(afterCurrent ? '写完本章后将停止连续续写' : '已请求取消')
    loadRunningTask()
  } catch {
    // 请求层已提示
  } finally {
    cancelling.value = false
  }
}

function dismiss(id: number) {
  dismissed.value = [...dismissed.value, id].slice(-100)
  try {
    localStorage.setItem(DISMISS_KEY, JSON.stringify(dismissed.value))
  } catch {
    // 无痕模式等场景下存储不可用，仅本次生效
  }
}

// 大纲余量提醒
const outline = ref<OutlineStatus | null>(null)
const extendOpen = ref(false)

// 质量提醒：自动重写后仍有问题的章节
const qualityTasks = computed(() =>
  recentTasks.value.filter((t) => t.status === 'success' && t.params?.quality_warning && !dismissed.value.includes(t.id)),
)
function qualityChapterNo(t: AiTask): number | undefined {
  const r = t.params?.resume as { chapter_no?: number } | undefined
  return r?.chapter_no
}

const drafts = computed(() => chapters.value.filter((c) => c.status === 'draft'))
const publishing = ref(false)
async function publish(ids?: number[]) {
  publishing.value = true
  try {
    const res = await publishChapters(props.novelId, ids)
    toast.success(res.count ? `已发布 ${res.count} 章` : '没有需要发布的草稿')
    load()
  } catch {
    // 请求层已提示
  } finally {
    publishing.value = false
  }
}

const latestNo = computed(() =>
  chapters.value.length ? Math.max(...chapters.value.map((c) => c.chapter_no)) : 0,
)

async function load() {
  loading.value = true
  try {
    chapters.value = await fetchAdminChapters(props.novelId)
    await loadRunningTask()
    fetchOutlineStatus(props.novelId).then((o) => (outline.value = o)).catch(() => {})
  } finally {
    loading.value = false
  }
}

async function loadRunningTask() {
  try {
    const res = await fetchTasks({ novel_id: props.novelId, page_size: 30 })
    recentTasks.value = res.list
    runningTask.value = res.list.find((t) => t.status === 'pending' || t.status === 'running') ?? null
  } catch {
    runningTask.value = null
  }
}

// 轮询进行中任务，让横幅随任务开始/结束自动出现、消失
let pollTimer: ReturnType<typeof setInterval> | null = null
function startPolling() {
  stopPolling()
  pollTimer = setInterval(loadRunningTask, 3000)
}
function stopPolling() {
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}

function openCreate() {
  editing.value = null
  chapterForm.value = { title: `第 ${latestNo.value + 1} 章`, content: '', summary: '' }
  editOpen.value = true
}

async function openEdit(c: Chapter) {
  try {
    const full = await fetchChapter(c.id)
    editing.value = full
    chapterForm.value = { title: full.title, content: full.content, summary: full.summary || '' }
    editOpen.value = true
  } catch {
    // 请求层已提示
  }
}

async function openView(c: Chapter) {
  viewing.value = c
  viewOpen.value = true
  try {
    viewing.value = await fetchChapter(c.id)
  } catch {
    // 请求层已提示
  }
}

async function saveChapter() {
  if (!chapterForm.value.title.trim()) {
    toast.error('请填写章节标题')
    return
  }
  if (!chapterForm.value.content.trim()) {
    toast.error('章节内容不能为空')
    return
  }
  saving.value = true
  try {
    if (editing.value) {
      await updateChapter(editing.value.id, {
        title: chapterForm.value.title,
        content: chapterForm.value.content,
        summary: chapterForm.value.summary,
      })
      toast.success('章节已更新')
    } else {
      await createChapter(props.novelId, { ...chapterForm.value })
      toast.success('章节已创建')
    }
    editOpen.value = false
    load()
  } catch {
    // 请求层已提示
  } finally {
    saving.value = false
  }
}

async function confirmDelete() {
  if (!deleting.value) return
  deleteLoading.value = true
  try {
    await deleteChapter(deleting.value.id)
    toast.success('章节已删除')
    deleting.value = null
    load()
  } catch {
    // 请求层已提示
  } finally {
    deleteLoading.value = false
  }
}

/** 若已有任务在跑，则打开进度回放并返回 true（阻止另起任务） */
function openProgressIfRunning(): boolean {
  if (runningTask.value) {
    toast.info('已有任务进行中，为你打开进度查看')
    progressOpen.value = true
    return true
  }
  return false
}

function startAi(type: AiChapterTask, c?: Chapter) {
  if (openProgressIfRunning()) return
  // generate_chapter 表示"生成下一章"，章号 = 当前最大章号 + 1
  const no = type === 'generate_chapter' && !c
    ? latestNo.value + 1
    : (c?.chapter_no ?? latestNo.value)
  if (type !== 'generate_chapter' && !no) {
    toast.error('还没有章节，请先创建或生成第一章')
    return
  }
  aiTask.value = { type, no: no || undefined, mode: type === 'generate_summary' || type === 'update_memory' ? 'poll' : 'stream' }
  aiTitle.value =
    type === 'generate_chapter' ? 'AI 生成章节'
      : type === 'continue_chapter' ? `AI 续写 · 第 ${no} 章之后`
      : type === 'regenerate_chapter' ? `AI 重新生成 · 第 ${no} 章`
      : type === 'update_memory' ? `更新记忆 · 第 ${no} 章`
      : `AI 生成摘要 · 第 ${no} 章`
  aiOpen.value = true
}

function aiParams() {
  const p: Record<string, unknown> = {}
  // continue_chapter 总是"在最大章号之后续写下一章"，不能传章号（传了会覆盖最后一章）
  if (aiTask.value?.no && aiTask.value.type !== 'continue_chapter') p.chapter_no = aiTask.value.no
  if (aiTask.value?.type === 'generate_chapter') p.target_words = 3000
  if (aiTask.value?.type === 'continue_chapter') p.target_words = 3000
  if (aiTask.value?.remaining) p.remaining = aiTask.value.remaining
  return p
}

/** 连续续写 N 章（首章流式展示，后续自动排队） */
function startBatch() {
  if (openProgressIfRunning()) return
  if (chapters.value.length === 0) {
    toast.error('还没有章节，请先创建或生成第一章')
    return
  }
  const n = Math.max(1, batchCount.value)
  aiTask.value = { type: 'continue_chapter', mode: 'stream', remaining: n - 1 }
  aiTitle.value = `连续续写 · 共 ${n} 章`
  aiOpen.value = true
}

function openProgress() {
  progressOpen.value = true
}

onMounted(() => {
  load()
  startPolling()
})
onBeforeUnmount(stopPolling)
</script>

<template>
  <div>
    <!-- 操作栏 -->
    <div class="mb-4 flex flex-wrap items-center justify-between gap-2">
      <p class="text-sm text-muted-foreground">共 {{ chapters.length }} 章</p>
      <div class="flex flex-wrap items-center gap-2">
        <Button class="gap-2" :disabled="chapters.length === 0" @click="startAi('continue_chapter')">
          <Wand2 class="size-4" />
          AI 续写
        </Button>
        <div v-if="chapters.length > 0" class="flex items-center gap-1.5">
          <select
            v-model.number="batchCount"
            class="h-9 rounded-lg border bg-background px-2 text-sm text-muted-foreground"
          >
            <option :value="3">3 章</option>
            <option :value="5">5 章</option>
            <option :value="10">10 章</option>
          </select>
          <Button variant="secondary" class="gap-2" @click="startBatch">
            <Sparkles class="size-4" />
            连续续写
          </Button>
        </div>
        <Button variant="outline" class="gap-2" @click="startAi('generate_chapter')">
          <Sparkles class="size-4" />
          AI 生成新章节
        </Button>
        <Button variant="secondary" class="gap-2" @click="openCreate">
          <FilePlus2 class="size-4" />
          新增章节
        </Button>
        <Button v-if="drafts.length" variant="outline" class="gap-2" :disabled="publishing" @click="publish()">
          <Send class="size-4" />
          发布全部草稿（{{ drafts.length }}）
        </Button>
        <Button variant="ghost" class="gap-2 text-muted-foreground" title="找回删除的章节" @click="openDeleted">
          <ArchiveRestore class="size-4" />
          已删除
        </Button>
      </div>
    </div>

    <!-- 进行中任务横幅 -->
    <div
      v-if="runningTask"
      class="mb-4 flex flex-wrap items-center gap-3 rounded-xl border border-primary/30 bg-primary/5 px-4 py-2.5"
    >
      <LoaderCircle class="size-4 animate-spin text-primary" />
      <p class="min-w-0 flex-1 text-sm">
        有任务进行中：<span class="font-medium">{{ runningTask.task_type_text || runningTask.task_type }}</span>
        <span class="text-muted-foreground">（任务 #{{ runningTask.id }}）</span>
      </p>
      <Button size="sm" variant="outline" class="gap-1.5" @click="openProgress">
        <Eye class="size-3.5" />
        查看进度
      </Button>
      <Button
        v-if="runningTask.task_type === 'continue_chapter' && Number(runningTask.params?.remaining ?? 0) > 0"
        size="sm"
        variant="ghost"
        :disabled="cancelling"
        @click="cancelRunning(true)"
      >
        写完本章后停止
      </Button>
      <Button size="sm" variant="ghost" class="text-destructive" :disabled="cancelling" @click="cancelRunning(false)">
        取消
      </Button>
    </div>

    <!-- 大纲快用完 -->
    <div
      v-if="outline?.warn"
      class="mb-4 flex flex-wrap items-center gap-3 rounded-xl border border-dashed border-foreground/30 px-4 py-2.5 text-sm"
    >
      <span class="min-w-0 flex-1">
        大纲{{ outline.remaining > 0 ? `只剩 ${outline.remaining} 章` : '已经写完了' }}（规划到第 {{ outline.outline_max }} 章）。
        <span class="text-muted-foreground">
          {{ outline.auto_extend_outline ? '已开启自动续写大纲，写下一章前会自动补上。' : '可以续写大纲，或在「自动化」里开启自动续写。' }}
        </span>
      </span>
      <Button size="sm" variant="outline" :disabled="!!runningTask" @click="extendOpen = true">续写大纲</Button>
    </div>
    <StreamDialog
      v-if="extendOpen"
      v-model:open="extendOpen"
      title="续写大纲"
      task-type="extend_outline"
      :novel-id="novelId"
      :params="{ chapters: 10 }"
      mode="stream"
      @done="() => { toast.success('大纲已续写'); load() }"
    />

    <!-- 质量提醒 -->
    <div v-if="qualityTasks.length" class="mb-4 space-y-2 rounded-xl border border-dashed border-foreground/30 px-4 py-3">
      <p class="text-sm font-medium">质量提醒：这些章节自动重写后仍有问题，建议看一下</p>
      <div v-for="t in qualityTasks" :key="t.id" class="flex flex-wrap items-start gap-2 text-sm">
        <p class="min-w-0 flex-1 text-muted-foreground">
          <span class="text-foreground">{{ qualityChapterNo(t) ? `第 ${qualityChapterNo(t)} 章` : `任务 #${t.id}` }}</span>：{{ t.params?.quality_warning }}
        </p>
        <Button
          v-if="qualityChapterNo(t)"
          size="sm"
          variant="outline"
          :disabled="!!runningTask"
          @click="startAi('regenerate_chapter', chapters.find((c) => c.chapter_no === qualityChapterNo(t)))"
        >重新生成</Button>
        <Button size="sm" variant="ghost" class="text-muted-foreground" @click="dismiss(t.id)">忽略</Button>
      </div>
    </div>

    <!-- 失败任务（可重试） -->
    <div
      v-if="failedTasks.length"
      class="mb-4 space-y-2.5 rounded-xl border border-destructive/30 bg-destructive/5 px-4 py-3"
    >
      <p class="flex items-center gap-2 text-sm font-medium text-destructive">
        <TriangleAlert class="size-4" />
        有 {{ failedTasks.length }} 个任务失败
      </p>
      <div v-for="t in failedTasks" :key="t.id" class="flex flex-wrap items-start gap-2 sm:flex-nowrap">
        <div class="min-w-0 flex-1 basis-full sm:basis-auto">
          <p class="text-sm font-medium">
            {{ t.task_type_text || t.task_type }}
            <span class="font-normal text-muted-foreground">（任务 #{{ t.id }} · {{ formatRelative(t.updated_at) }}）</span>
          </p>
          <p class="line-clamp-2 break-all text-xs text-muted-foreground">{{ t.error_message || '未知错误' }}</p>
        </div>
        <Button
          size="sm"
          variant="outline"
          class="gap-1.5"
          :disabled="!!runningTask || retrying === t.id"
          @click="retry(t)"
        >
          <LoaderCircle v-if="retrying === t.id" class="size-3.5 animate-spin" />
          <RotateCw v-else class="size-3.5" />
          {{ retryLabel(t) }}
        </Button>
        <Button size="sm" variant="ghost" class="text-muted-foreground" @click="dismiss(t.id)">忽略</Button>
      </div>
    </div>

    <LoadingState v-if="loading" variant="table" :rows="5" />

    <div v-else-if="chapters.length === 0" class="rounded-2xl border bg-card">
      <EmptyState title="还没有章节" description="用 AI 生成或手动添加第一章吧">
        <div class="flex gap-2">
          <Button size="sm" class="gap-2" @click="startAi('generate_chapter')">
            <Sparkles class="size-4" /> AI 生成
          </Button>
          <Button size="sm" variant="outline" class="gap-2" @click="openCreate">
            <Plus class="size-4" /> 手动添加
          </Button>
        </div>
      </EmptyState>
    </div>

    <div v-else class="overflow-hidden rounded-2xl border bg-card shadow-sm">
      <Table>
        <TableHeader>
          <TableRow class="hover:bg-transparent">
            <TableHead class="w-16">章号</TableHead>
            <TableHead>标题</TableHead>
            <TableHead class="hidden sm:table-cell">字数</TableHead>
            <TableHead class="hidden md:table-cell">摘要</TableHead>
            <TableHead class="hidden lg:table-cell">更新时间</TableHead>
            <TableHead class="text-right">操作</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          <TableRow v-for="c in chapters" :key="c.id" class="group">
            <TableCell class="font-medium">{{ c.chapter_no }}</TableCell>
            <TableCell>
              <p class="line-clamp-1 font-medium">
                <span
                  v-if="c.status === 'draft'"
                  class="mr-1.5 rounded border border-dashed border-foreground/40 px-1.5 py-px align-middle text-[10px] font-medium text-muted-foreground"
                  title="草稿：读者不可见，审阅后点「发布」"
                >草稿</span>{{ c.title }}
              </p>
              <p class="text-[11px] text-muted-foreground sm:hidden">{{ formatNumber(c.word_count) }} 字</p>
            </TableCell>
            <TableCell class="hidden sm:table-cell">{{ formatNumber(c.word_count) }}</TableCell>
            <TableCell class="hidden max-w-52 md:table-cell">
              <p v-if="c.summary" class="line-clamp-1 text-xs text-muted-foreground">{{ c.summary }}</p>
              <span
                v-else
                class="rounded-full border border-dashed border-foreground/30 px-2 py-0.5 text-[11px] font-medium text-muted-foreground"
                title="可点右侧「AI 补摘要」生成"
              >缺摘要</span>
            </TableCell>
            <TableCell class="hidden lg:table-cell text-xs text-muted-foreground">{{ formatRelative(c.updated_at) }}</TableCell>
            <TableCell class="text-right">
              <div class="flex justify-end gap-1 lg:opacity-0 lg:transition-opacity lg:group-hover:opacity-100">
                <Button variant="ghost" size="icon" class="size-8" title="查看内容" @click="openView(c)">
                  <Eye class="size-4" />
                </Button>
                <Button v-if="c.status === 'draft'" variant="ghost" size="icon" class="size-8" title="发布（读者可见）" :disabled="publishing" @click="publish([c.id])">
                  <Send class="size-4" />
                </Button>
                <Button variant="ghost" size="icon" class="size-8" title="编辑" @click="openEdit(c)">
                  <Pencil class="size-4" />
                </Button>
                <Button variant="ghost" size="icon" class="size-8 text-primary" title="AI 重新生成" @click="startAi('regenerate_chapter', c)">
                  <RefreshCw class="size-4" />
                </Button>
                <DropdownMenu>
                  <DropdownMenuTrigger as-child>
                    <Button variant="ghost" size="icon" class="size-8 text-muted-foreground hover:text-foreground" title="更多操作">
                      <MoreHorizontal class="size-4" />
                    </Button>
                  </DropdownMenuTrigger>
                  <DropdownMenuContent align="end" class="w-44">
                    <DropdownMenuItem @select="startAi('generate_summary', c)">
                      <Wand2 class="size-4" /> AI 补摘要
                    </DropdownMenuItem>
                    <DropdownMenuItem @select="startAi('update_memory', c)">
                      <Brain class="size-4" /> 更新本章记忆
                    </DropdownMenuItem>
                    <DropdownMenuItem @select="openVersions(c)">
                      <History class="size-4" /> 历史版本
                    </DropdownMenuItem>
                    <DropdownMenuSeparator />
                    <DropdownMenuItem class="text-destructive focus:text-destructive" @select="deleting = c">
                      <Trash2 class="size-4" /> 删除
                    </DropdownMenuItem>
                  </DropdownMenuContent>
                </DropdownMenu>
              </div>
            </TableCell>
          </TableRow>
        </TableBody>
      </Table>
    </div>

    <!-- 章节编辑对话框 -->
    <Dialog v-model:open="editOpen">
      <DialogContent class="max-h-[90vh] max-w-2xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{{ editing ? `编辑章节 · 第 ${editing.chapter_no} 章` : '新增章节' }}</DialogTitle>
          <DialogDescription>{{ editing ? '修改章节标题、正文与摘要' : '手动创建新章节，章号自动递增' }}</DialogDescription>
        </DialogHeader>
        <div class="space-y-4 py-2">
          <div class="space-y-1.5">
            <Label>标题 <span class="text-destructive">*</span></Label>
            <Input v-model="chapterForm.title" />
          </div>
          <div class="space-y-1.5">
            <Label>正文 <span class="text-destructive">*</span>（{{ countWords(chapterForm.content) }} 字）</Label>
            <Textarea
              v-model="chapterForm.content"
              rows="16"
              class="resize-y font-serif leading-relaxed"
              placeholder="请输入章节正文…"
            />
          </div>
          <div class="space-y-1.5">
            <Label>摘要</Label>
            <Textarea v-model="chapterForm.summary" rows="2" placeholder="一句话摘要，展示在目录中（可选）" />
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" @click="editOpen = false">取消</Button>
          <Button :disabled="saving" @click="saveChapter">
            <LoaderCircle v-if="saving" class="size-4 animate-spin" />
            保存
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>

    <!-- 查看章节内容（只读） -->
    <Dialog v-model:open="viewOpen">
      <DialogContent class="max-h-[85vh] max-w-2xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle>第 {{ viewing?.chapter_no }} 章 {{ viewing?.title }}</DialogTitle>
          <DialogDescription v-if="viewing?.summary">摘要：{{ viewing.summary }}</DialogDescription>
        </DialogHeader>
        <div class="py-2">
          <p class="whitespace-pre-wrap font-serif text-[15px] leading-8">{{ viewing?.content }}</p>
        </div>
        <DialogFooter>
          <Button variant="outline" @click="viewOpen = false">关闭</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>

    <!-- 删除确认 -->
    <ConfirmDialog
      :open="!!deleting"
      title="删除章节"
      :description="`确定要删除「第 ${deleting?.chapter_no} 章 ${deleting?.title ?? ''}」吗？此操作无法撤销。`"
      confirm-text="删除"
      :loading="deleteLoading"
      @update:open="(v: boolean) => !v && (deleting = null)"
      @confirm="confirmDelete"
    />

    <!-- 历史版本 / 已删除章节 -->
    <ChapterVersionsDialog
      v-model:open="versionsOpen"
      :mode="versionsMode"
      :novel-id="novelId"
      :chapter="versionsChapter"
      @restored="load"
    />

    <!-- AI 流式对话框 -->
    <StreamDialog
      v-if="aiTask"
      v-model:open="aiOpen"
      :title="aiTitle"
      :task-type="aiTask.type"
      :novel-id="novelId"
      :params="aiParams()"
      :mode="aiTask.mode"
      @done="() => { toast.success('AI 任务完成'); load() }"
      @update:open="(v: boolean) => { if (!v) { aiTask = null; loadRunningTask() } }"
    />

    <!-- 查看进行中任务进度（订阅已有任务，不新建） -->
    <StreamDialog
      v-if="runningTask"
      v-model:open="progressOpen"
      :title="`查看进度 · 任务 #${runningTask.id}`"
      :task-type="runningTask.task_type"
      :novel-id="novelId"
      mode="stream"
      :attach-task-id="runningTask.id"
      @done="load"
      @update:open="(v: boolean) => { if (!v) load() }"
    />
  </div>
</template>

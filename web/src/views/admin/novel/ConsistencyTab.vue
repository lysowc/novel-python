<script setup lang="ts">import { Button } from '@/components/ui/button'

import { onMounted, ref } from 'vue'
import { ClipboardCheck, ListChecks, LoaderCircle, RefreshCw, ShieldAlert, Sparkles } from '@lucide/vue'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import StreamDialog from '@/components/common/StreamDialog.vue'
import { toast } from 'vue-sonner'
import LoadingState from '@/components/common/LoadingState.vue'
import EmptyState from '@/components/common/EmptyState.vue'
import { fetchAdminChapters, fetchChapter, fetchConsistencyReports } from '@/api'
import { usePollingTask } from '@/composables/useAiTask'
import { formatRelative } from '@/lib/format'
import type { Chapter, ConsistencyIssue, ConsistencyReport, ConsistencyStatus } from '@/types/api'

const props = defineProps<{ novelId: number }>()

const loading = ref(true)
const reports = ref<ConsistencyReport[]>([])

const { running, run } = usePollingTask()

const statusMap: Record<ConsistencyStatus, { label: string; cls: string }> = {
  ok: { label: '通过', cls: 'bg-muted text-muted-foreground' },
  warning: { label: '需关注', cls: 'border border-foreground/25 text-foreground' },
  critical: { label: '严重问题', cls: 'bg-foreground text-background' },
}

const severityMap: Record<string, { label: string; dot: string }> = {
  minor: { label: '轻微', dot: 'bg-foreground/25' },
  major: { label: '重要', dot: 'bg-foreground/60' },
  critical: { label: '严重', dot: 'bg-foreground' },
}

const typeLabels: Record<string, string> = {
  outline_drift: '剧情偏离',
  contradiction: '前后矛盾',
  foreshadowing_dropped: '伏笔遗忘',
  character_inconsistency: '人物失据',
  timeline_conflict: '时间线冲突',
  other: '其他',
}

async function load() {
  loading.value = true
  try {
    reports.value = await fetchConsistencyReports(props.novelId)
  } finally {
    loading.value = false
  }
}

async function runCheck() {
  await run({
    taskType: 'consistency_check',
    novelId: props.novelId,
    onSuccess: () => {
      load()
      toast.success('审校完成')
    },
  })
}

function issueLabel(issue: ConsistencyIssue) {
  return `${typeLabels[issue.type] ?? issue.type} · ${severityMap[issue.severity]?.label ?? issue.severity}`
}

// 定位到章节：点章号查看正文，可按审校建议重写这一章（旧版本自动进入历史版本）
const viewOpen = ref(false)
const viewLoading = ref(false)
const viewing = ref<Chapter | null>(null)
const viewingIssue = ref<ConsistencyIssue | null>(null)
let chapterIds: Record<number, number> | null = null

async function openChapter(no: number, issue: ConsistencyIssue) {
  viewingIssue.value = issue
  viewing.value = null
  viewOpen.value = true
  viewLoading.value = true
  try {
    if (!chapterIds) {
      const list = await fetchAdminChapters(props.novelId)
      chapterIds = Object.fromEntries(list.map((c) => [c.chapter_no, c.id]))
    }
    const id = chapterIds[no]
    if (!id) {
      toast.error(`第 ${no} 章不存在（可能已删除）`)
      viewOpen.value = false
      return
    }
    viewing.value = await fetchChapter(id)
  } catch {
    viewOpen.value = false
  } finally {
    viewLoading.value = false
  }
}

const fixOpen = ref(false)
const fixParams = ref<Record<string, unknown>>({})
function fixWithSuggestion() {
  if (!viewing.value || !viewingIssue.value) return
  const issue = viewingIssue.value
  fixParams.value = {
    chapter_no: viewing.value.chapter_no,
    instruction:
      `一致性审校发现本章存在问题：${issue.description}\n` +
      (issue.suggestion ? `修改建议：${issue.suggestion}\n` : '') +
      '请在保持本章主要情节和篇幅的前提下修正上述问题。',
  }
  viewOpen.value = false
  fixOpen.value = true
}

onMounted(load)
</script>

<template>
  <div>
    <div class="mb-4 flex flex-wrap items-center justify-between gap-2">
      <p class="text-sm text-muted-foreground">
        对照大纲、已写章节与小说记忆，检测剧情偏离、前后矛盾与伏笔遗忘
      </p>
      <Button variant="secondary" class="gap-2" :disabled="running" @click="runCheck">
        <Sparkles class="size-4" />
        {{ running ? '审校中…' : '运行审校' }}
      </Button>
    </div>

    <LoadingState v-if="loading" variant="list" :rows="2" />

    <EmptyState
      v-else-if="reports.length === 0"
      title="还没有审校报告"
      description="点击「运行审校」，AI 会对照大纲检查已写章节的一致性（每章生成完成后也会按设置自动审校）"
    >
      <Button size="sm" class="gap-2" @click="runCheck">
        <Sparkles class="size-4" /> 运行审校
      </Button>
    </EmptyState>

    <div v-else class="space-y-4">
      <div v-for="r in reports" :key="r.id" class="rounded-2xl border bg-card shadow-sm">
        <div class="flex flex-wrap items-center gap-2 border-b px-5 py-3">
          <ClipboardCheck class="size-4 text-primary" />
          <span class="text-sm font-medium">审校报告 #{{ r.id }}</span>
          <span class="rounded-full px-2 py-0.5 text-[11px] font-medium" :class="statusMap[r.status]?.cls">
            {{ statusMap[r.status]?.label }}
          </span>
          <span class="text-xs text-muted-foreground">覆盖至第 {{ r.chapter_no }} 章</span>
          <span class="ml-auto text-xs text-muted-foreground">{{ formatRelative(r.created_at) }}</span>
        </div>

        <div class="space-y-4 p-5">
          <p v-if="r.report.summary" class="text-sm leading-relaxed">{{ r.report.summary }}</p>

          <div v-if="r.report.issues.length === 0" class="flex items-center gap-2 text-sm text-muted-foreground">
            <ShieldAlert class="size-4" />
            未发现明显问题
          </div>

          <div v-else class="space-y-3">
            <div
              v-for="(issue, i) in r.report.issues"
              :key="i"
              class="rounded-xl border border-dashed p-3.5"
            >
              <div class="flex items-start gap-2.5">
                <span class="mt-1.5 size-2 shrink-0 rounded-full" :class="severityMap[issue.severity]?.dot" />
                <div class="min-w-0 flex-1">
                  <p class="text-xs font-medium text-muted-foreground">{{ issueLabel(issue) }}</p>
                  <p class="mt-1 text-sm leading-relaxed">{{ issue.description }}</p>
                  <p v-if="issue.suggestion" class="mt-1.5 text-sm text-muted-foreground">
                    <ListChecks class="mr-1 inline size-3.5" />
                    建议：{{ issue.suggestion }}
                  </p>
                  <div v-if="issue.related_chapters?.length" class="mt-2 flex flex-wrap gap-1.5">
                    <button
                      v-for="no in issue.related_chapters"
                      :key="no"
                      type="button"
                      class="rounded-full bg-foreground/10 px-2 py-0.5 text-[11px] transition-colors hover:bg-foreground hover:text-background"
                      title="查看这一章，可按建议重写"
                      @click="openChapter(no, issue)"
                    >
                      第 {{ no }} 章
                    </button>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- 查看相关章节 -->
    <Dialog v-model:open="viewOpen">
      <DialogContent class="max-h-[85vh] max-w-2xl overflow-y-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>{{ viewing ? (/^第.+章/.test(viewing.title) ? viewing.title : `第 ${viewing.chapter_no} 章 ${viewing.title}`) : '加载中…' }}</DialogTitle>
          <DialogDescription v-if="viewingIssue">
            问题：{{ viewingIssue.description }}
            <template v-if="viewingIssue.suggestion"><br />建议：{{ viewingIssue.suggestion }}</template>
          </DialogDescription>
        </DialogHeader>
        <div v-if="viewLoading" class="flex items-center py-8 text-sm text-muted-foreground">
          <LoaderCircle class="mr-2 size-4 animate-spin" /> 加载中…
        </div>
        <p v-else class="whitespace-pre-wrap py-2 font-serif text-[15px] leading-8">{{ viewing?.content }}</p>
        <DialogFooter class="sticky -bottom-6 -mx-6 -mb-6 gap-2 border-t bg-background px-6 py-3">
          <Button variant="outline" @click="viewOpen = false">关闭</Button>
          <Button class="gap-1.5" :disabled="!viewing" @click="fixWithSuggestion">
            <RefreshCw class="size-4" />
            按建议重写本章
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>

    <StreamDialog
      v-if="fixOpen"
      v-model:open="fixOpen"
      :title="`按审校建议重写 · 第 ${fixParams.chapter_no} 章`"
      task-type="regenerate_chapter"
      :novel-id="novelId"
      :params="fixParams"
      mode="stream"
      @done="() => { toast.success('已重写，旧版本可在章节的「历史版本」里找回'); chapterIds = null }"
    />
  </div>
</template>

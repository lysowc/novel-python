<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { ArchiveRestore, GitCompare, History, LoaderCircle } from '@lucide/vue'
import { toast } from 'vue-sonner'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import EmptyState from '@/components/common/EmptyState.vue'
import ConfirmDialog from '@/components/common/ConfirmDialog.vue'
import {
  fetchChapter, fetchChapterVersion, fetchChapterVersions, fetchDeletedChapters, restoreChapterVersion,
} from '@/api'
import { diffParagraphs } from '@/lib/diff'
import { formatNumber, formatRelative } from '@/lib/format'
import type { Chapter, ChapterVersion } from '@/types/api'

/**
 * 章节历史版本 / 已删除章节
 * - mode="versions"：某一章的历史版本，可与当前正文对比、恢复
 * - mode="deleted"：本小说已删除的章节，可恢复
 */
const props = defineProps<{
  open: boolean
  mode: 'versions' | 'deleted'
  novelId: number
  chapter?: Chapter | null
}>()
const emit = defineEmits<{
  (e: 'update:open', v: boolean): void
  (e: 'restored', chapter: Chapter): void
}>()

const loading = ref(false)
const versions = ref<ChapterVersion[]>([])
const selected = ref<ChapterVersion | null>(null)
const selectedLoading = ref(false)
const current = ref<Chapter | null>(null)
const compare = ref(false)
const confirmOpen = ref(false)
const restoring = ref(false)

const title = computed(() =>
  props.mode === 'deleted'
    ? '已删除的章节'
    : `历史版本 · 第 ${props.chapter?.chapter_no ?? ''} 章`,
)

async function load() {
  loading.value = true
  selected.value = null
  compare.value = false
  try {
    if (props.mode === 'deleted') {
      versions.value = await fetchDeletedChapters(props.novelId)
      current.value = null
    } else if (props.chapter) {
      const [list, full] = await Promise.all([
        fetchChapterVersions(props.chapter.id),
        fetchChapter(props.chapter.id),
      ])
      versions.value = list
      current.value = full
    }
    if (versions.value.length) await select(versions.value[0]!)
  } catch {
    versions.value = []
  } finally {
    loading.value = false
  }
}

async function select(v: ChapterVersion) {
  selectedLoading.value = true
  try {
    selected.value = await fetchChapterVersion(v.id)
  } catch {
    // 请求层已提示
  } finally {
    selectedLoading.value = false
  }
}

const diff = computed(() =>
  compare.value && selected.value && current.value
    ? diffParagraphs(selected.value.content ?? '', current.value.content ?? '')
    : [],
)
const diffStats = computed(() => ({
  del: diff.value.filter((d) => d.type === 'del').length,
  add: diff.value.filter((d) => d.type === 'add').length,
}))

async function restore() {
  if (!selected.value) return
  restoring.value = true
  try {
    const chapter = await restoreChapterVersion(selected.value.id)
    toast.success(
      props.mode === 'deleted' ? `第 ${chapter.chapter_no} 章已恢复` : '已恢复到所选版本（当前内容已存为新的历史版本）',
      { description: '如果后续剧情依赖这一章，可在章节操作里「更新本章记忆」' },
    )
    confirmOpen.value = false
    emit('restored', chapter)
    emit('update:open', false)
  } catch {
    // 请求层已提示（例如有任务正在执行）
  } finally {
    restoring.value = false
  }
}

watch(
  () => props.open,
  (v) => v && load(),
  { immediate: true },
)
</script>

<template>
  <Dialog :open="open" @update:open="(v: boolean) => emit('update:open', v)">
    <DialogContent class="flex h-[85vh] max-w-4xl flex-col gap-0 overflow-hidden p-0 sm:max-w-4xl">
      <DialogHeader class="border-b px-5 py-4">
        <DialogTitle class="flex items-center gap-2">
          <History class="size-4" />
          {{ title }}
        </DialogTitle>
        <DialogDescription>
          {{ mode === 'deleted'
            ? '删除章节前会自动保存一份，可在这里找回'
            : '重新生成、手动编辑、恢复和删除前都会自动保存旧版本（每章保留最近 20 个）' }}
        </DialogDescription>
      </DialogHeader>

      <div v-if="loading" class="flex items-center justify-center py-16 text-sm text-muted-foreground">
        <LoaderCircle class="mr-2 size-4 animate-spin" /> 加载中…
      </div>

      <div v-else-if="versions.length === 0" class="py-6">
        <EmptyState
          :title="mode === 'deleted' ? '没有已删除的章节' : '还没有历史版本'"
          :description="mode === 'deleted' ? '删除的章节会出现在这里' : '这一章还没有被重新生成或修改过'"
        />
      </div>

      <div v-else class="flex min-h-0 flex-1 flex-col md:flex-row">
        <!-- 版本列表 -->
        <ul class="max-h-48 shrink-0 overflow-y-auto border-b md:max-h-none md:w-64 md:border-b-0 md:border-r">
          <li v-for="v in versions" :key="v.id">
            <button
              type="button"
              class="w-full border-l-2 px-4 py-3 text-left transition-colors hover:bg-muted/60"
              :class="selected?.id === v.id ? 'border-foreground bg-muted' : 'border-transparent'"
              @click="select(v)"
            >
              <p class="text-sm font-medium">
                <template v-if="mode === 'deleted'">第 {{ v.chapter_no }} 章 · </template>{{ v.title }}
              </p>
              <p class="mt-0.5 text-xs text-muted-foreground">
                {{ v.source_text }} · {{ formatRelative(v.created_at) }} · {{ formatNumber(v.word_count) }} 字
              </p>
              <p class="mt-1 line-clamp-2 text-xs text-muted-foreground/80">{{ v.preview }}</p>
            </button>
          </li>
        </ul>

        <!-- 内容 / 对比 -->
        <div class="flex min-h-0 min-w-0 flex-1 flex-col">
          <div class="flex flex-wrap items-center gap-2 border-b px-5 py-2.5">
            <p class="min-w-0 flex-1 truncate text-xs text-muted-foreground">
              <template v-if="selected">保存于 {{ selected.created_at }}</template>
            </p>
            <Button
              v-if="mode === 'versions' && current"
              size="sm"
              :variant="compare ? 'secondary' : 'outline'"
              class="gap-1.5"
              @click="compare = !compare"
            >
              <GitCompare class="size-3.5" />
              {{ compare ? '查看全文' : '与当前对比' }}
            </Button>
            <Button size="sm" class="gap-1.5" :disabled="!selected" @click="confirmOpen = true">
              <ArchiveRestore class="size-3.5" />
              恢复此版本
            </Button>
          </div>

          <div class="min-h-0 flex-1 overflow-y-auto px-5 py-4">
            <div v-if="selectedLoading" class="flex items-center text-sm text-muted-foreground">
              <LoaderCircle class="mr-2 size-4 animate-spin" /> 加载中…
            </div>
            <template v-else-if="selected">
              <template v-if="compare">
                <p class="mb-3 text-xs text-muted-foreground">
                  <span class="mr-3"><span class="line-through">删除线</span>：只在此版本中（{{ diffStats.del }} 段）</span>
                  <span><span class="rounded bg-foreground/10 px-1">底色</span>：只在当前正文中（{{ diffStats.add }} 段）</span>
                </p>
                <p v-if="diffStats.del === 0 && diffStats.add === 0" class="text-sm text-muted-foreground">与当前正文相同</p>
                <div class="space-y-3 font-serif text-[15px] leading-8">
                  <p
                    v-for="(d, i) in diff"
                    :key="i"
                    :class="{
                      'text-muted-foreground line-through decoration-foreground/40': d.type === 'del',
                      'rounded bg-foreground/10 px-1.5': d.type === 'add',
                    }"
                  >{{ d.text }}</p>
                </div>
              </template>
              <template v-else>
                <p v-if="selected.summary" class="mb-3 text-xs text-muted-foreground">摘要：{{ selected.summary }}</p>
                <p class="whitespace-pre-wrap font-serif text-[15px] leading-8">{{ selected.content }}</p>
              </template>
            </template>
          </div>
        </div>
      </div>
    </DialogContent>
  </Dialog>

  <ConfirmDialog
    :open="confirmOpen"
    title="恢复此版本"
    :description="mode === 'deleted'
      ? `将恢复第 ${selected?.chapter_no} 章「${selected?.title ?? ''}」。`
      : '当前正文会先自动保存为一个历史版本，然后替换为所选版本，可以再恢复回来。'"
    confirm-text="恢复"
    :loading="restoring"
    @update:open="(v: boolean) => (confirmOpen = v)"
    @confirm="restore"
  />
</template>

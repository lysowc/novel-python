<script setup lang="ts">
import { ref, watch } from 'vue'
import { ArchiveRestore, LoaderCircle, Trash2 } from '@lucide/vue'
import { toast } from 'vue-sonner'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import EmptyState from '@/components/common/EmptyState.vue'
import ConfirmDialog from '@/components/common/ConfirmDialog.vue'
import { fetchTrashNovels, purgeNovel, restoreNovel } from '@/api'
import { formatNumber, formatRelative } from '@/lib/format'
import type { Novel } from '@/types/api'

/** 小说回收站：删除的小说先放这里，可恢复；超过保留天数自动彻底删除 */
const props = defineProps<{ open: boolean }>()
const emit = defineEmits<{ (e: 'update:open', v: boolean): void; (e: 'changed'): void }>()

const loading = ref(false)
const list = ref<Novel[]>([])
const retention = ref(30)
const busyId = ref<number | null>(null)
const purging = ref<Novel | null>(null)

async function load() {
  loading.value = true
  try {
    const res = await fetchTrashNovels({ page_size: 100 })
    list.value = res.list
    retention.value = res.trash_retention_days
  } finally {
    loading.value = false
  }
}

function daysLeft(n: Novel) {
  if (!n.deleted_at) return retention.value
  const passed = (Date.now() - new Date(n.deleted_at).getTime()) / 86400000
  return Math.max(0, Math.ceil(retention.value - passed))
}

async function restore(n: Novel) {
  busyId.value = n.id
  try {
    await restoreNovel(n.id)
    toast.success(`《${n.title}》已恢复`)
    await load()
    emit('changed')
  } catch {
    // 请求层已提示
  } finally {
    busyId.value = null
  }
}

async function confirmPurge() {
  const n = purging.value
  if (!n) return
  busyId.value = n.id
  try {
    await purgeNovel(n.id)
    toast.success(`《${n.title}》已彻底删除`)
    purging.value = null
    await load()
    emit('changed')
  } catch {
    // 请求层已提示
  } finally {
    busyId.value = null
  }
}

watch(() => props.open, (v) => v && load(), { immediate: true })
</script>

<template>
  <Dialog :open="open" @update:open="(v: boolean) => emit('update:open', v)">
    <DialogContent class="max-h-[85vh] max-w-2xl overflow-y-auto sm:max-w-2xl">
      <DialogHeader>
        <DialogTitle class="flex items-center gap-2"><Trash2 class="size-4" /> 回收站</DialogTitle>
        <DialogDescription>删除的小说会在这里保留 {{ retention }} 天，期间可以恢复；到期后自动彻底删除。</DialogDescription>
      </DialogHeader>

      <div v-if="loading" class="flex items-center py-10 text-sm text-muted-foreground">
        <LoaderCircle class="mr-2 size-4 animate-spin" /> 加载中…
      </div>
      <EmptyState v-else-if="list.length === 0" title="回收站是空的" description="删除的小说会先放到这里" />
      <ul v-else class="divide-y rounded-xl border">
        <li v-for="n in list" :key="n.id" class="flex flex-wrap items-center gap-3 px-4 py-3">
          <div class="min-w-0 flex-1">
            <p class="truncate text-sm font-medium">《{{ n.title }}》</p>
            <p class="mt-0.5 text-xs text-muted-foreground">
              {{ formatNumber(n.chapter_count) }} 章 · {{ formatNumber(n.word_count) }} 字 ·
              删除于 {{ formatRelative(n.deleted_at) }} · {{ daysLeft(n) }} 天后自动清除
            </p>
          </div>
          <Button size="sm" variant="outline" class="gap-1.5" :disabled="busyId === n.id" @click="restore(n)">
            <ArchiveRestore class="size-3.5" /> 恢复
          </Button>
          <Button size="sm" variant="ghost" class="text-destructive" :disabled="busyId === n.id" @click="purging = n">
            彻底删除
          </Button>
        </li>
      </ul>
    </DialogContent>
  </Dialog>

  <ConfirmDialog
    :open="!!purging"
    title="彻底删除"
    :description="`《${purging?.title ?? ''}》的章节、设定、记忆、历史版本将全部清除，无法恢复。`"
    confirm-text="彻底删除"
    :loading="busyId === purging?.id"
    @update:open="(v: boolean) => !v && (purging = null)"
    @confirm="confirmPurge"
  />
</template>

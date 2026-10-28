<script setup lang="ts">import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Button } from '@/components/ui/button'

import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ArrowLeft, Download, ExternalLink, LoaderCircle } from '@lucide/vue'
import { toast } from 'vue-sonner'
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuLabel, DropdownMenuSeparator, DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import CoverArt from '@/components/common/CoverArt.vue'
import LoadingState from '@/components/common/LoadingState.vue'
import InfoTab from './novel/InfoTab.vue'
import SettingTab from './novel/SettingTab.vue'
import OutlineTab from './novel/OutlineTab.vue'
import ChaptersTab from './novel/ChaptersTab.vue'
import MemoryTab from './novel/MemoryTab.vue'
import ConsistencyTab from './novel/ConsistencyTab.vue'
import AutomationTab from './novel/AutomationTab.vue'
import BibleTab from './novel/BibleTab.vue'
import { exportNovel, fetchAdminNovel, restoreNovel } from '@/api'
import { formatNumber } from '@/lib/format'
import type { NovelDetail } from '@/types/api'

const route = useRoute()
const router = useRouter()
const id = computed(() => String(route.params.id))

const loading = ref(true)
const novel = ref<NovelDetail | null>(null)
const active = ref('info')

const tabItems = [
  { value: 'info', label: '信息' },
  { value: 'setting', label: '设定' },
  { value: 'outline', label: '大纲' },
  { value: 'chapters', label: '章节' },
  { value: 'memory', label: '记忆' },
  { value: 'bible', label: '资料库' },
  { value: 'consistency', label: '审校' },
  { value: 'automation', label: '自动化' },
]

async function reload() {
  novel.value = await fetchAdminNovel(id.value)
}

async function restore() {
  try {
    await restoreNovel(id.value)
    toast.success('已恢复')
    reload()
  } catch {
    // 请求层已提示
  }
}

onMounted(async () => {
  try {
    novel.value = await fetchAdminNovel(id.value)
  } finally {
    loading.value = false
  }
})

// 导出
const exporting = ref(false)
const includeDrafts = ref(false)
async function doExport(format: 'txt' | 'md' | 'epub') {
  exporting.value = true
  try {
    await exportNovel(id.value, format, includeDrafts.value)
  } catch (e) {
    toast.error((e as Error).message || '导出失败')
  } finally {
    exporting.value = false
  }
}
</script>

<template>
  <div>
    <div class="mb-5 flex flex-wrap items-center justify-between gap-3">
      <Button variant="ghost" size="sm" class="-ml-2 text-muted-foreground" @click="router.push('/admin/novels')">
        <ArrowLeft class="size-4" />
        返回列表
      </Button>
      <div class="flex items-center gap-2">
        <DropdownMenu>
          <DropdownMenuTrigger as-child>
            <Button variant="outline" size="sm" class="gap-1.5" :disabled="!novel || exporting">
              <LoaderCircle v-if="exporting" class="size-4 animate-spin" />
              <Download v-else class="size-4" />
              导出
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" class="w-52">
            <DropdownMenuLabel class="text-xs font-normal text-muted-foreground">导出整本小说</DropdownMenuLabel>
            <DropdownMenuItem @select="doExport('txt')">TXT 文本</DropdownMenuItem>
            <DropdownMenuItem @select="doExport('epub')">EPUB 电子书</DropdownMenuItem>
            <DropdownMenuItem @select="doExport('md')">Markdown</DropdownMenuItem>
            <DropdownMenuSeparator />
            <DropdownMenuItem @select.prevent="includeDrafts = !includeDrafts">
              <span class="flex-1">包含草稿章节</span>
              <span class="text-xs text-muted-foreground">{{ includeDrafts ? '是' : '否' }}</span>
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
        <Button variant="outline" size="sm" class="gap-1.5" :disabled="!novel" @click="router.push(`/novel/${id}`)">
          <ExternalLink class="size-4" />
          前台预览
        </Button>
      </div>
    </div>

    <LoadingState v-if="loading" variant="list" :rows="2" />

    <template v-else-if="novel">
      <!-- 头部信息 -->
      <div class="mb-6 flex items-center gap-4 rounded-2xl border bg-card p-5 shadow-sm">
        <CoverArt :title="novel.title" :cover="novel.cover" aspect="portrait" title-size="md" class="w-20 shrink-0" />
        <div class="min-w-0 flex-1">
          <h1 class="truncate text-xl font-bold">{{ novel.title }}</h1>
          <div class="mt-1.5 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground">
            <span>{{ novel.category_name || '未分类' }}</span>
            <span>{{ formatNumber(novel.word_count) }} 字</span>
            <span>{{ novel.chapter_count }} 章</span>
            <span>{{ novel.is_public ? '公开' : '私密' }}</span>
          </div>
          <p class="mt-2 line-clamp-1 text-sm text-muted-foreground">{{ novel.description || '暂无简介' }}</p>
        </div>
      </div>

      <div
        v-if="novel.deleted_at"
        class="mb-5 flex flex-wrap items-center gap-3 rounded-xl border border-destructive/30 bg-destructive/5 px-4 py-3 text-sm"
      >
        <span class="flex-1">这本小说在回收站里，读者看不到，也不能使用 AI。</span>
        <Button size="sm" variant="outline" @click="restore">恢复</Button>
      </div>

      <Tabs v-model="active" class="w-full">
        <div class="-mx-1 overflow-x-auto px-1 pb-1">
          <TabsList class="inline-flex w-max min-w-full sm:min-w-0">
          <TabsTrigger v-for="t in tabItems" :key="t.value" :value="t.value">
            {{ t.label }}
          </TabsTrigger>
          </TabsList>
        </div>
        <TabsContent value="info" class="mt-5">
          <InfoTab :novel-id="Number(id)" :novel="novel" @saved="reload" />
        </TabsContent>
        <TabsContent value="setting" class="mt-5">
          <SettingTab :novel-id="Number(id)" />
        </TabsContent>
        <TabsContent value="outline" class="mt-5">
          <OutlineTab :novel-id="Number(id)" />
        </TabsContent>
        <TabsContent value="chapters" class="mt-5">
          <ChaptersTab :novel-id="Number(id)" />
        </TabsContent>
        <TabsContent value="memory" class="mt-5">
          <MemoryTab :novel-id="Number(id)" />
        </TabsContent>
        <TabsContent value="bible" class="mt-5">
          <BibleTab :novel-id="Number(id)" :deleted="!!novel.deleted_at" />
        </TabsContent>
        <TabsContent value="consistency" class="mt-5">
          <ConsistencyTab :novel-id="Number(id)" />
        </TabsContent>
        <TabsContent value="automation" class="mt-5">
          <AutomationTab :novel-id="Number(id)" :deleted="!!novel.deleted_at" />
        </TabsContent>
      </Tabs>
    </template>
  </div>
</template>

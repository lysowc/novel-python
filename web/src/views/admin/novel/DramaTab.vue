<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { Clapperboard, FileText, Film, Pencil, RefreshCw, Save, Scissors, Trash2, TriangleAlert } from '@lucide/vue'
import { toast } from 'vue-sonner'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { Label } from '@/components/ui/label'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import ConfirmDialog from '@/components/common/ConfirmDialog.vue'
import StreamDialog from '@/components/common/StreamDialog.vue'
import LoadingState from '@/components/common/LoadingState.vue'
import { batchDeleteDrama, clearDrama, fetchDrama, fetchDramaEpisode, updateDramaEpisode } from '@/api'
import type { DramaEpisode, DramaOverview, TaskType } from '@/types/api'

/** 短剧：已写章节 → 分集方案 → 每集剧本 → 分镜 */
const props = defineProps<{ novelId: number; deleted?: boolean }>()

const loading = ref(true)
const data = ref<DramaOverview | null>(null)

async function load() {
  try {
    data.value = await fetchDrama(props.novelId)
    const ids = new Set(data.value.episodes.map((e) => e.id))
    picked.value = picked.value.filter((id) => ids.has(id))
  } finally {
    loading.value = false
  }
}
onMounted(load)

const episodes = computed(() => data.value?.episodes ?? [])
const pendingChapters = computed(() => {
  const d = data.value
  return d ? Math.max(0, d.written_until - d.covered_until) : 0
})
const missingScripts = computed(() => episodes.value.filter((e) => !e.has_script).length)
const missingBoards = computed(() => episodes.value.filter((e) => e.has_script && !e.shot_count).length)
const totalSeconds = computed(() => episodes.value.reduce((s, e) => s + (e.duration || 0), 0))

function chapterRange(e: DramaEpisode) {
  return e.chapter_from === e.chapter_to ? `第 ${e.chapter_from} 章` : `第 ${e.chapter_from}-${e.chapter_to} 章`
}
function fmtSeconds(s: number) {
  const m = Math.floor(s / 60)
  const r = Math.round(s % 60)
  return m ? `${m} 分 ${r} 秒` : `${r} 秒`
}

// ---------- AI 任务 ----------
const task = ref<{ title: string; type: TaskType; params?: Record<string, unknown> } | null>(null)
const taskOpen = computed({
  get: () => !!task.value,
  set: (v: boolean) => { if (!v) task.value = null },
})
function runPlan() {
  const d = data.value!
  task.value = { title: `切分集 · 第 ${d.covered_until + 1}-${d.written_until} 章`, type: 'drama_plan' }
}
function runScripts(ids?: number[]) {
  task.value = {
    title: ids ? `写剧本 · ${ids.length} 集` : `补写剧本 · ${missingScripts.value} 集`,
    type: 'drama_script',
    params: ids ? { episode_ids: ids } : undefined,
  }
}
function runBoards(ids?: number[]) {
  task.value = {
    title: ids ? `出分镜 · ${ids.length} 集` : `补出分镜 · ${missingBoards.value} 集`,
    type: 'drama_storyboard',
    params: ids ? { episode_ids: ids } : undefined,
  }
}
async function onTaskDone() {
  await load()
  if (detail.value) await openDetail(detail.value.id, detailTab.value)
}

// ---------- 多选 / 删除 ----------
const picked = ref<number[]>([])
const allPicked = computed(() => episodes.value.length > 0 && picked.value.length === episodes.value.length)
function pickAll() {
  picked.value = allPicked.value ? [] : episodes.value.map((e) => e.id)
}
function togglePick(id: number) {
  picked.value = picked.value.includes(id) ? picked.value.filter((x) => x !== id) : [...picked.value, id]
}
const bulk = ref<{ title: string; desc: string; clear?: boolean } | null>(null)
const bulkRunning = ref(false)
function askDelete() {
  bulk.value = {
    title: `删除选中的 ${picked.value.length} 集？`,
    desc: '剧本和分镜一起删除，后面的集会重新编号。删掉的是最后几集的话，再点「切分集」会把那几章重新改编。',
  }
}
function askClear() {
  bulk.value = { title: '清空全部短剧分集？', desc: '所有分集方案、剧本、分镜都会删除，小说本身不受影响。', clear: true }
}
async function runBulk() {
  if (!bulk.value) return
  bulkRunning.value = true
  try {
    const r = bulk.value.clear ? await clearDrama(props.novelId) : await batchDeleteDrama(props.novelId, picked.value)
    toast.success(`已删除 ${r.deleted} 集`)
    picked.value = []
    bulk.value = null
    await load()
  } finally {
    bulkRunning.value = false
  }
}

// ---------- 单集详情 ----------
type DetailTab = 'plan' | 'script' | 'board'
const detail = ref<DramaEpisode | null>(null)
const detailOpen = computed({
  get: () => !!detail.value,
  set: (v: boolean) => { if (!v) detail.value = null },
})
const detailTab = ref<DetailTab>('plan')
const form = ref({ title: '', summary: '', hook: '', cliffhanger: '', script: '' })
const saving = ref(false)

async function openDetail(id: number, tab: DetailTab = 'plan') {
  const ep = await fetchDramaEpisode(id)
  detail.value = ep
  detailTab.value = tab
  form.value = { title: ep.title, summary: ep.summary, hook: ep.hook, cliffhanger: ep.cliffhanger, script: ep.script ?? '' }
}
async function saveDetail(keys: (keyof typeof form.value)[]) {
  if (!detail.value) return
  saving.value = true
  try {
    const body = Object.fromEntries(keys.map((k) => [k, form.value[k]]))
    detail.value = await updateDramaEpisode(detail.value.id, body)
    toast.success('已保存')
    await load()
  } finally {
    saving.value = false
  }
}
const busy = computed(() => !!task.value || !!props.deleted)
</script>

<template>
  <LoadingState v-if="loading" />
  <div v-else-if="data" class="space-y-4">
    <!-- 概况 + 操作 -->
    <section class="rounded-2xl border bg-card p-4 shadow-sm">
      <div class="flex flex-wrap items-baseline gap-x-5 gap-y-1 text-sm text-muted-foreground">
        <span>小说写到第 <b class="text-foreground">{{ data.written_until }}</b> 章</span>
        <span>已改编到第 <b class="text-foreground">{{ data.covered_until }}</b> 章</span>
        <span>共 <b class="text-foreground">{{ episodes.length }}</b> 集</span>
        <span v-if="episodes.length">剧本 {{ episodes.length - missingScripts }} / {{ episodes.length }}</span>
        <span v-if="totalSeconds">分镜合计约 {{ fmtSeconds(totalSeconds) }}</span>
        <span>每集约 {{ data.episode_seconds }} 秒</span>
      </div>
      <div class="mt-3 flex flex-wrap gap-2">
        <Button size="sm" class="gap-1.5" :disabled="busy || !pendingChapters" @click="runPlan">
          <Scissors class="size-4" />
          {{ pendingChapters ? `切分集（第 ${data.covered_until + 1}-${data.written_until} 章）` : '没有新章节可改编' }}
        </Button>
        <Button size="sm" variant="outline" class="gap-1.5" :disabled="busy || !missingScripts" @click="runScripts()">
          <FileText class="size-4" /> 补写剧本{{ missingScripts ? `（${missingScripts} 集）` : '' }}
        </Button>
        <Button size="sm" variant="outline" class="gap-1.5" :disabled="busy || !missingBoards" @click="runBoards()">
          <Clapperboard class="size-4" /> 补出分镜{{ missingBoards ? `（${missingBoards} 集）` : '' }}
        </Button>
      </div>
      <p class="mt-3 text-xs leading-relaxed text-muted-foreground">
        顺序：先「切分集」把写好的章节切成一集一集，再「补写剧本」，最后「补出分镜」。点开某一集可以改方案、改剧本、看分镜。
        小说写了新章节后，再点「切分集」会接着往后改编。
      </p>
    </section>

    <p v-if="!episodes.length" class="rounded-xl border border-dashed px-4 py-10 text-center text-sm text-muted-foreground">
      还没有分集。小说写了几章之后，点上面的「切分集」开始改编。
    </p>

    <template v-else>
      <div class="flex flex-wrap items-center gap-2 text-sm">
        <label class="flex items-center gap-2 text-muted-foreground">
          <input type="checkbox" class="size-4 accent-foreground" :checked="allPicked" @change="pickAll" />
          全选
        </label>
        <template v-if="picked.length">
          <Button size="sm" variant="outline" class="h-8 gap-1.5" :disabled="busy" @click="runScripts(picked)">
            <RefreshCw class="size-3.5" /> 重写剧本（{{ picked.length }}）
          </Button>
          <Button size="sm" variant="outline" class="h-8 gap-1.5" :disabled="busy" @click="runBoards(picked)">
            <RefreshCw class="size-3.5" /> 重出分镜（{{ picked.length }}）
          </Button>
          <Button size="sm" variant="destructive" class="h-8 gap-1.5" @click="askDelete">
            <Trash2 class="size-3.5" /> 删除选中（{{ picked.length }}）
          </Button>
        </template>
        <Button size="sm" variant="ghost" class="ml-auto h-8 text-muted-foreground" @click="askClear">清空全部</Button>
      </div>

      <div class="space-y-2">
        <article
          v-for="e in episodes"
          :key="e.id"
          class="flex gap-3 rounded-2xl border bg-card p-4 shadow-sm transition-colors hover:bg-muted/40"
        >
          <input
            type="checkbox"
            class="mt-1 size-4 shrink-0 accent-foreground"
            :checked="picked.includes(e.id)"
            @change="togglePick(e.id)"
          />
          <button class="min-w-0 flex-1 text-left" @click="openDetail(e.id, e.has_script ? 'script' : 'plan')">
            <h3 class="flex flex-wrap items-baseline gap-x-2 font-semibold">
              第 {{ e.episode_no }} 集 · {{ e.title }}
              <span class="text-xs font-normal text-muted-foreground">{{ chapterRange(e) }}</span>
            </h3>
            <p class="mt-1 line-clamp-2 text-sm text-muted-foreground">{{ e.summary }}</p>
            <div class="mt-2 flex flex-wrap gap-1.5 text-xs">
              <span class="rounded-full px-2 py-0.5" :class="e.has_script ? 'bg-muted' : 'border border-dashed text-muted-foreground'">
                {{ e.has_script ? `剧本 ${e.script_words} 字` : '未写剧本' }}
              </span>
              <span class="rounded-full px-2 py-0.5" :class="e.shot_count ? 'bg-muted' : 'border border-dashed text-muted-foreground'">
                {{ e.shot_count ? `分镜 ${e.shot_count} 个镜头 · 约 ${fmtSeconds(e.duration)}` : '未出分镜' }}
              </span>
              <span v-if="e.source_changed" class="flex items-center gap-1 rounded-full border border-foreground/30 px-2 py-0.5">
                <TriangleAlert class="size-3" /> 原著改过，建议重写剧本
              </span>
            </div>
          </button>
        </article>
      </div>
    </template>

    <!-- 单集详情 -->
    <Dialog v-model:open="detailOpen">
      <DialogContent v-if="detail" class="max-h-[92vh] overflow-y-auto sm:max-w-4xl">
        <DialogHeader>
          <DialogTitle>第 {{ detail.episode_no }} 集 · {{ detail.title }}</DialogTitle>
          <DialogDescription>
            改编自{{ chapterRange(detail) }}
            <span v-if="detail.source_changed">· 原著这几章在剧本写完后改过</span>
          </DialogDescription>
        </DialogHeader>

        <div class="flex gap-1 border-b">
          <button
            v-for="t in ([['plan', '分集方案', Pencil], ['script', '剧本', FileText], ['board', '分镜', Film]] as const)"
            :key="t[0]"
            class="-mb-px flex items-center gap-1.5 border-b-2 px-3 py-2 text-sm"
            :class="detailTab === t[0] ? 'border-foreground font-medium' : 'border-transparent text-muted-foreground'"
            @click="detailTab = t[0]"
          >
            <component :is="t[2]" class="size-4" /> {{ t[1] }}
          </button>
        </div>

        <!-- 分集方案 -->
        <div v-if="detailTab === 'plan'" class="space-y-4 py-2">
          <div class="space-y-1.5">
            <Label>集名</Label>
            <Input v-model="form.title" maxlength="255" />
          </div>
          <div class="space-y-1.5">
            <Label>这一集演什么</Label>
            <Textarea v-model="form.summary" rows="4" />
          </div>
          <div class="space-y-1.5">
            <Label>开头钩子（前 3 秒）</Label>
            <Textarea v-model="form.hook" rows="2" />
          </div>
          <div class="space-y-1.5">
            <Label>结尾钩子</Label>
            <Textarea v-model="form.cliffhanger" rows="2" />
          </div>
          <div class="flex justify-end">
            <Button size="sm" class="gap-1.5" :disabled="saving" @click="saveDetail(['title', 'summary', 'hook', 'cliffhanger'])">
              <Save class="size-4" /> 保存方案
            </Button>
          </div>
          <p class="text-xs text-muted-foreground">改了方案后，到「剧本」页点「重写本集剧本」，AI 会按新方案写。</p>
        </div>

        <!-- 剧本 -->
        <div v-else-if="detailTab === 'script'" class="space-y-3 py-2">
          <p v-if="!detail.has_script && !form.script" class="rounded-xl border border-dashed px-4 py-8 text-center text-sm text-muted-foreground">
            这一集还没有剧本。
          </p>
          <Textarea v-else v-model="form.script" rows="22" class="font-mono text-sm leading-relaxed" />
          <div class="flex flex-wrap justify-end gap-2">
            <Button size="sm" variant="outline" class="gap-1.5" :disabled="busy" @click="runScripts([detail.id])">
              <RefreshCw class="size-4" /> {{ detail.has_script ? '重写本集剧本' : '写本集剧本' }}
            </Button>
            <Button v-if="detail.has_script || form.script" size="sm" class="gap-1.5" :disabled="saving" @click="saveDetail(['script'])">
              <Save class="size-4" /> 保存剧本
            </Button>
          </div>
        </div>

        <!-- 分镜 -->
        <div v-else class="space-y-3 py-2">
          <p v-if="!detail.storyboard?.length" class="rounded-xl border border-dashed px-4 py-8 text-center text-sm text-muted-foreground">
            {{ detail.has_script ? '这一集还没有分镜。' : '要先有剧本，才能出分镜。' }}
          </p>
          <div v-else class="overflow-x-auto rounded-xl border">
            <table class="w-full min-w-[720px] text-sm">
              <thead class="bg-muted/60 text-left text-xs text-muted-foreground">
                <tr>
                  <th class="px-2 py-2 font-medium">#</th>
                  <th class="px-2 py-2 font-medium">镜头</th>
                  <th class="px-2 py-2 font-medium">画面</th>
                  <th class="px-2 py-2 font-medium">台词</th>
                  <th class="px-2 py-2 font-medium">声音 / 屏幕字</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="s in detail.storyboard" :key="s.no" class="border-t align-top">
                  <td class="px-2 py-2 text-muted-foreground">{{ s.no }}</td>
                  <td class="px-2 py-2 whitespace-nowrap">
                    {{ s.shot }}<span v-if="s.camera" class="text-muted-foreground"> · {{ s.camera }}</span>
                    <div class="text-xs text-muted-foreground">{{ s.duration }} 秒 · {{ s.scene }}</div>
                  </td>
                  <td class="px-2 py-2">{{ s.visual }}</td>
                  <td class="px-2 py-2">{{ s.line }}</td>
                  <td class="px-2 py-2 text-xs text-muted-foreground">
                    <div v-if="s.sound">♪ {{ s.sound }}</div>
                    <div v-if="s.subtitle">【屏幕】{{ s.subtitle }}</div>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
          <div class="flex items-center justify-between gap-2">
            <span class="text-xs text-muted-foreground">
              <template v-if="detail.storyboard?.length">{{ detail.shot_count }} 个镜头，合计约 {{ fmtSeconds(detail.duration) }}</template>
            </span>
            <Button size="sm" variant="outline" class="gap-1.5" :disabled="busy || !detail.has_script" @click="runBoards([detail.id])">
              <RefreshCw class="size-4" /> {{ detail.shot_count ? '重出本集分镜' : '出本集分镜' }}
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>

    <ConfirmDialog
      :open="!!bulk"
      :title="bulk?.title ?? ''"
      :description="bulk?.desc ?? ''"
      confirm-text="删除"
      :loading="bulkRunning"
      @update:open="(v: boolean) => { if (!v) bulk = null }"
      @confirm="runBulk"
    />

    <StreamDialog
      v-if="task"
      v-model:open="taskOpen"
      :title="task.title"
      :task-type="task.type"
      :novel-id="novelId"
      :params="task.params"
      mode="stream"
      @done="onTaskDone"
      @error="load"
    />
  </div>
</template>

<script setup lang="ts">import { Textarea } from '@/components/ui/textarea'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'

import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  ArrowLeft, BookOpenCheck, Lightbulb, ListChecks, LoaderCircle, Save, Send, Sparkles, Square,
} from '@lucide/vue'
import { toast } from 'vue-sonner'
import {
  createIdea, createNovelFromIdea, extractIdeas, fetchIdea, fetchIdeaMessages, fetchModels, fetchProviders,
  saveIdea, streamIdeaChat, updateIdea,
} from '@/api'
import { formatRelative } from '@/lib/format'
import type { Idea, IdeaMessage } from '@/types/api'

const route = useRoute()
const router = useRouter()

const ideaId = computed(() => String(route.params.id))

const loading = ref(true)
const idea = ref<Idea | null>(null)
const messages = ref<IdeaMessage[]>([])
const input = ref('')
const sending = ref(false)
const streaming = ref(false)
const scrollRef = ref<HTMLElement | null>(null)
const controller = ref<AbortController | null>(null)

const streamingText = ref('')
const streamingMsg = ref<IdeaMessage | null>(null)
const chatError = ref('')
const noProvider = ref(false)

async function loadIdea() {
  loading.value = true
  try {
    idea.value = await fetchIdea(ideaId.value)
    messages.value = await fetchIdeaMessages(ideaId.value)
    await checkAiAvailability()
  } finally {
    loading.value = false
    await scrollToBottom()
  }
}

/** 检查是否有可用的 Provider + Model，没有则给出常驻提示 */
async function checkAiAvailability() {
  try {
    const [providers, models] = await Promise.all([fetchProviders(), fetchModels()])
    noProvider.value = !providers.some((p) => Number(p.status) === 1)
      || !models.some((m) => Number(m.status) === 1)
  } catch {
    noProvider.value = false
  }
}

async function scrollToBottom() {
  await nextTick()
  const el = scrollRef.value
  if (el) el.scrollTop = el.scrollHeight
}

async function send(preset?: string) {
  const text = (preset ?? input.value).trim()
  if (!text || sending.value) return
  if (preset === undefined) input.value = ''
  sending.value = true
  chatError.value = ''

  // 立即追加用户消息
  messages.value.push({
    id: -Date.now(),
    idea_id: Number(ideaId.value),
    role: 'user',
    content: text,
    created_at: new Date().toISOString().slice(0, 19),
  })
  await scrollToBottom()

  // 准备流式 AI 消息
  streaming.value = true
  streamingText.value = ''
  streamingMsg.value = {
    id: -Date.now() - 1,
    idea_id: Number(ideaId.value),
    role: 'assistant',
    content: '',
    created_at: new Date().toISOString().slice(0, 19),
  }
  messages.value.push(streamingMsg.value!)
  controller.value = new AbortController()

  try {
    await streamIdeaChat(
      ideaId.value,
      text,
      {
        onDelta: (t) => {
          streamingText.value += t
          if (streamingMsg.value) streamingMsg.value.content = streamingText.value
          scrollToBottom()
        },
        onDone: async () => {
          streaming.value = false
          controller.value = null
          chatError.value = ''
          // 结束后刷新正式消息列表
          messages.value = await fetchIdeaMessages(ideaId.value)
          await scrollToBottom()
        },
        onError: (msg) => {
          streaming.value = false
          controller.value = null
          if (streamingMsg.value && !streamingMsg.value.content) {
            messages.value = messages.value.filter((m) => m.id !== streamingMsg.value!.id)
          }
          chatError.value = msg
          toast.error(msg)
        },
      },
      controller.value.signal,
    )
  } finally {
    sending.value = false
  }
}

function stop() {
  controller.value?.abort()
  controller.value = null
  streaming.value = false
  if (streamingMsg.value && !streamingMsg.value.content) {
    messages.value = messages.value.filter((m) => m.id !== streamingMsg.value!.id)
  }
}

function transcript(): string {
  return messages.value
    .map((m) => `${m.role === 'user' ? '用户' : 'AI'}：${m.content}`)
    .join('\n\n')
}

async function onSave() {
  try {
    await saveIdea(ideaId.value, { content: transcript() })
    toast.success('点子已保存')
  } catch {
    // 请求层已提示
  }
}

async function onCreateNovel() {
  try {
    const res = await createNovelFromIdea(ideaId.value)
    toast.success('小说已创建，设定生成任务已入队')
    router.push(`/admin/novels/${res.novel_id}`)
  } catch {
    // 请求层已提示
  }
}

// ---------- 聊法：随便聊聊 / 打磨点子 ----------
const isExplore = computed(() => idea.value?.mode === 'explore')

async function switchMode(mode: 'refine' | 'explore') {
  if (!idea.value || idea.value.mode === mode) return
  try {
    idea.value = await updateIdea(idea.value.id, { mode })
    toast.success(mode === 'explore' ? '换成随便聊聊：AI 会多抛脑洞，不急着定方案' : '换成打磨点子：AI 会围绕这个点子追问细节')
  } catch {
    // 请求层已提示
  }
}

// 没话说的时候点一下就开头：send 直接发出去，fill 填进输入框让你接着写
const starters: { label: string; send?: string; fill?: string }[] = [
  { label: '给我几个随机脑洞', send: '我现在没什么想法，随便给我几个脑洞吧，方向越不一样越好。' },
  { label: '我想写点 ___ 感觉的东西', fill: '我想写点这种感觉的东西：' },
  { label: '从一个画面开始', fill: '我脑子里有个画面：' },
  { label: '把老套路反过来写', send: '挑几个大家写烂了的老套路，各给一个反过来写的脑洞。' },
  { label: '从冷门职业 / 冷知识找灵感', send: '随便挑几个冷门职业或者冷知识，各变成一个小说脑洞。' },
]

async function useStarter(s: { send?: string; fill?: string }) {
  if (s.send) {
    send(s.send)
    return
  }
  input.value = s.fill || ''
  await nextTick()
  const el = document.querySelector<HTMLTextAreaElement>('textarea[data-idea-input]')
  if (el) {
    el.focus()
    el.setSelectionRange(el.value.length, el.value.length)
  }
}

// ---------- 提炼点子（随便聊聊里可能聊出好几个方向） ----------
const extractOpen = ref(false)
const extracting = ref(false)
const extracted = ref<{ title: string; content: string; picked: boolean }[]>([])
const savingPicked = ref(false)
const pickedCount = computed(() => extracted.value.filter((x) => x.picked).length)

async function onExtract() {
  extractOpen.value = true
  extracting.value = true
  extracted.value = []
  try {
    const res = await extractIdeas(ideaId.value)
    extracted.value = res.ideas.map((x) => ({ ...x, picked: true }))
  } catch {
    extractOpen.value = false
  } finally {
    extracting.value = false
  }
}

async function savePicked() {
  const picked = extracted.value.filter((x) => x.picked && x.title.trim())
  if (!picked.length) return
  savingPicked.value = true
  try {
    for (const x of picked) {
      await createIdea({ title: x.title.trim(), content: x.content.trim(), category_id: idea.value?.category_id || 0, mode: 'refine' })
    }
    toast.success(`已保存 ${picked.length} 个点子，在「AI 点子」列表里，可以分别进去接着打磨`)
    extractOpen.value = false
  } catch {
    // 请求层已提示
  } finally {
    savingPicked.value = false
  }
}

onMounted(loadIdea)
onBeforeUnmount(() => controller.value?.abort())
</script>

<template>
  <div class="flex min-h-0 flex-1 flex-col bg-background">
    <!-- 顶部栏 -->
    <header class="flex shrink-0 items-center gap-3 border-b bg-background px-4 py-3 sm:px-6">
      <Button variant="ghost" size="icon" class="size-9 shrink-0" @click="router.push('/admin/ideas')">
        <ArrowLeft class="size-4.5" />
      </Button>
      <div class="min-w-0 flex-1">
        <p class="truncate text-[15px] font-semibold">{{ idea?.title || '点子聊天' }}</p>
        <p class="flex items-center gap-1.5 text-xs text-muted-foreground">
          <Lightbulb class="size-3" />
          {{ idea?.category_name || '未分类' }}
          <span v-if="idea">· {{ formatRelative(idea.updated_at) }}</span>
        </p>
      </div>
      <div class="flex shrink-0 gap-2">
        <template v-if="isExplore">
          <Button size="sm" class="gap-1.5" :disabled="!messages.length || streaming" @click="onExtract">
            <ListChecks class="size-3.5" />
            提炼点子
          </Button>
        </template>
        <template v-else>
          <Button variant="outline" size="sm" class="gap-1.5" @click="onSave">
            <Save class="size-3.5" />
            保存点子
          </Button>
          <Button size="sm" class="gap-1.5" @click="onCreateNovel">
            <BookOpenCheck class="size-3.5" />
            创建小说
          </Button>
        </template>
      </div>
    </header>

    <!-- 聊法切换 -->
    <div v-if="idea" class="flex shrink-0 items-center justify-center gap-1 border-b bg-background px-4 py-2 text-xs">
      <span class="mr-1 text-muted-foreground">聊法：</span>
      <button
        type="button"
        class="rounded-full px-3 py-1 transition-colors"
        :class="isExplore ? 'bg-foreground text-background' : 'text-muted-foreground hover:bg-muted'"
        title="还没有明确点子：AI 多抛脑洞、顺着你的兴趣聊，不急着定方案"
        @click="switchMode('explore')"
      >随便聊聊</button>
      <button
        type="button"
        class="rounded-full px-3 py-1 transition-colors"
        :class="!isExplore ? 'bg-foreground text-background' : 'text-muted-foreground hover:bg-muted'"
        title="已经有个大概想法：AI 围绕它追问题材、主角、冲突、卖点"
        @click="switchMode('refine')"
      >打磨点子</button>
    </div>

    <!-- 未启用 AI 的常驻提示 -->
    <div
      v-if="noProvider"
      class="shrink-0 border-b bg-destructive/10 px-4 py-2 text-center text-xs text-destructive"
    >
      当前没有可用的 AI Provider/Model，聊天无法回复——请先到「AI 配置」里启用（或添加）后使用
    </div>

    <!-- 消息区（与页面同底色，无边界感） -->
    <div ref="scrollRef" class="min-h-0 flex-1 overflow-y-auto">
      <div v-if="loading" class="flex h-full items-center justify-center gap-2 text-sm text-muted-foreground">
        <LoaderCircle class="size-5 animate-spin" />
        加载中…
      </div>

      <div v-else class="mx-auto w-full max-w-3xl px-4 py-8 sm:px-6">
        <div v-if="messages.length === 0" class="flex flex-col items-center pt-16 text-center">
          <span class="flex size-14 items-center justify-center rounded-full bg-foreground/5">
            <Lightbulb class="size-6 text-foreground/60" />
          </span>
          <template v-if="isExplore">
            <p class="mt-4 text-base font-medium">还没想好写什么？随便聊聊</p>
            <p class="mt-1.5 text-sm text-muted-foreground">说说最近爱看什么、想要什么感觉，或者点下面的按钮开个头。聊出方向后点右上角「提炼点子」。</p>
          </template>
          <template v-else>
            <p class="mt-4 text-base font-medium">和 AI 聊聊这个点子吧</p>
            <p class="mt-1.5 text-sm text-muted-foreground">展开设定、寻找冲突、完善人物，让灵感长成故事。</p>
          </template>
        </div>

        <div v-else class="space-y-7">
          <template v-for="m in messages" :key="m.id">
            <!-- 用户消息：黑色气泡 -->
            <div v-if="m.role === 'user'" class="flex justify-end">
              <div class="max-w-[75%] whitespace-pre-wrap rounded-2xl rounded-br-md bg-foreground px-4 py-2.5 text-sm leading-relaxed text-background">
                {{ m.content }}
              </div>
            </div>
            <!-- AI 消息：无框纯文本 -->
            <div v-else class="flex gap-3">
              <span class="mt-1 flex size-7 shrink-0 items-center justify-center rounded-full bg-foreground text-background">
                <Sparkles class="size-3.5" />
              </span>
              <div class="min-w-0 flex-1">
                <p class="whitespace-pre-wrap text-[15px] leading-7">
                  {{ m.content }}
                  <span
                    v-if="streaming && m.id === streamingMsg?.id"
                    class="ml-0.5 inline-block h-4 w-1.5 animate-pulse rounded bg-foreground align-middle"
                  />
                </p>
                <p class="mt-1.5 text-xs text-muted-foreground/60">{{ formatRelative(m.created_at) }}</p>
              </div>
            </div>
          </template>
        </div>
      </div>
    </div>

    <!-- 输入区 -->
    <div class="shrink-0 border-t bg-background px-4 pb-4 pt-3 sm:px-6">
      <!-- 发送失败的内联错误提示（比 toast 更醒目、常驻） -->
      <div
        v-if="chatError"
        class="mx-auto mb-2 flex w-full max-w-3xl items-start gap-2 rounded-lg border border-destructive/30 bg-destructive/5 px-3 py-2 text-sm text-destructive"
      >
        <span class="mt-0.5 font-medium">发送失败：</span>
        <span class="flex-1">{{ chatError }}</span>
      </div>
      <div v-if="isExplore && !streaming" class="mx-auto mb-2 flex w-full max-w-3xl gap-1.5 overflow-x-auto pb-1">
        <button
          v-for="st in starters"
          :key="st.label"
          type="button"
          class="shrink-0 rounded-full border px-3 py-1 text-xs text-muted-foreground transition-colors hover:bg-muted hover:text-foreground disabled:opacity-50"
          :disabled="sending"
          @click="useStarter(st)"
        >{{ st.label }}</button>
      </div>
      <div class="mx-auto flex w-full max-w-3xl items-end gap-2">
        <Textarea
          data-idea-input
          v-model="input"
          :rows="1"
          class="max-h-32 min-h-11 flex-1 resize-none rounded-2xl"
          :placeholder="isExplore ? '随便说点什么，想到哪说到哪…' : '和 AI 聊聊你的点子…'"
          :disabled="sending"
          @keydown.enter.exact.prevent="send()"
        />
        <Button
          v-if="streaming"
          variant="secondary"
          class="h-11 gap-1.5"
          @click="stop"
        >
          <Square class="size-4" />
          停止
        </Button>
        <Button
          v-else
          class="h-11 gap-1.5 px-5"
          :disabled="!input.trim() || sending"
          @click="send()"
        >
          <Send class="size-4" />
          发送
        </Button>
      </div>
      <p class="mx-auto mt-2 w-full max-w-3xl text-center text-[11px] text-muted-foreground/60">
        Enter 发送 · Shift+Enter 换行
      </p>
    </div>

    <!-- 提炼点子 -->
    <Dialog v-model:open="extractOpen">
      <DialogContent class="flex max-h-[90vh] flex-col sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>从讨论里提炼出的点子</DialogTitle>
          <DialogDescription>勾选想留下的，标题和内容都可以直接改。保存后每个都是单独的点子，可以分别进去接着打磨、创建小说。</DialogDescription>
        </DialogHeader>
        <div v-if="extracting" class="flex items-center justify-center gap-2 py-12 text-sm text-muted-foreground">
          <LoaderCircle class="size-4 animate-spin" /> AI 正在整理讨论记录…
        </div>
        <div v-else class="min-h-0 flex-1 space-y-3 overflow-y-auto pr-1">
          <label
            v-for="(x, i) in extracted"
            :key="i"
            class="flex gap-3 rounded-xl border p-3 transition-colors"
            :class="x.picked ? 'bg-card' : 'bg-muted/40 opacity-60'"
          >
            <input v-model="x.picked" type="checkbox" class="mt-2.5 size-4 shrink-0 accent-foreground" />
            <div class="min-w-0 flex-1 space-y-2">
              <Input v-model="x.title" class="h-9 font-medium" maxlength="60" />
              <Textarea v-model="x.content" :rows="4" class="resize-y text-sm leading-relaxed" />
            </div>
          </label>
        </div>
        <DialogFooter>
          <Button variant="outline" @click="extractOpen = false">取消</Button>
          <Button :disabled="extracting || savingPicked || !pickedCount" @click="savePicked">
            <LoaderCircle v-if="savingPicked" class="size-4 animate-spin" />
            保存选中的 {{ pickedCount }} 个
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  </div>
</template>

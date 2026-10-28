<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { CalendarClock, ListPlus, LoaderCircle, Save } from '@lucide/vue'
import { toast } from 'vue-sonner'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import StreamDialog from '@/components/common/StreamDialog.vue'
import { fetchNovelAutomation, fetchOutlineStatus, saveNovelAutomation } from '@/api'
import type { NovelAutomation, OutlineStatus } from '@/types/api'

/** 自动化：定时续写 + 大纲快用完时自动续写大纲 */
const props = defineProps<{ novelId: number; deleted?: boolean }>()

const loading = ref(true)
const saving = ref(false)
const form = ref<NovelAutomation | null>(null)
const outline = ref<OutlineStatus | null>(null)
const extendOpen = ref(false)

async function load() {
  loading.value = true
  try {
    const [a, o] = await Promise.all([fetchNovelAutomation(props.novelId), fetchOutlineStatus(props.novelId)])
    form.value = a
    outline.value = o
  } finally {
    loading.value = false
  }
}

async function save() {
  if (!form.value) return
  saving.value = true
  try {
    form.value = await saveNovelAutomation(props.novelId, {
      schedule_enabled: form.value.schedule_enabled ? 1 : 0,
      schedule_time: form.value.schedule_time,
      chapters_per_run: Number(form.value.chapters_per_run) || 1,
      target_words: Number(form.value.target_words) || 0,
      auto_extend_outline: form.value.auto_extend_outline ? 1 : 0,
      extend_chapters: Number(form.value.extend_chapters) || 10,
    })
    toast.success('自动化设置已保存')
  } catch {
    // 请求层已提示
  } finally {
    saving.value = false
  }
}

const outlineText = computed(() => {
  const o = outline.value
  if (!o) return ''
  if (!o.outline_max) return '还没有大纲，请先在「大纲」页生成'
  if (o.remaining <= 0) return `大纲规划到第 ${o.outline_max} 章，已经写完了`
  return `大纲规划到第 ${o.outline_max} 章，已写到第 ${o.written_max} 章，还剩 ${o.remaining} 章`
})

onMounted(load)
</script>

<template>
  <div v-if="loading" class="flex items-center py-10 text-sm text-muted-foreground">
    <LoaderCircle class="mr-2 size-4 animate-spin" /> 加载中…
  </div>
  <div v-else-if="form" class="max-w-2xl space-y-5">
    <p v-if="deleted" class="rounded-xl border border-dashed px-4 py-3 text-sm text-muted-foreground">
      这本小说在回收站里，恢复后才能开启定时续写。
    </p>

    <!-- 定时续写 -->
    <section class="rounded-2xl border bg-card shadow-sm">
      <h2 class="flex items-center gap-2 border-b px-5 py-3 text-sm font-semibold">
        <CalendarClock class="size-4" /> 定时续写
      </h2>
      <div class="space-y-4 p-5">
        <label class="flex items-center gap-3 text-sm">
          <input v-model="form.schedule_enabled" type="checkbox" :true-value="1" :false-value="0" class="size-4 accent-foreground" :disabled="deleted" />
          每天自动续写
        </label>
        <div class="grid gap-4 sm:grid-cols-3">
          <label class="space-y-1.5 text-sm">
            <span class="font-medium">时间</span>
            <Input v-model="form.schedule_time" type="time" />
          </label>
          <label class="space-y-1.5 text-sm">
            <span class="font-medium">每次写几章</span>
            <Input v-model.number="form.chapters_per_run" type="number" min="1" max="20" />
          </label>
          <label class="space-y-1.5 text-sm">
            <span class="font-medium">每章字数</span>
            <Input v-model.number="form.target_words" type="number" min="0" step="500" placeholder="0 = 用系统设置" />
          </label>
        </div>
        <p class="text-xs text-muted-foreground">
          按 {{ form.timezone }} 时间触发，每天一次。到点时如果这本小说正有任务在跑，会等它结束后再开始；超过费用上限当天跳过。
        </p>
        <p v-if="form.last_run_message" class="rounded-lg bg-muted/60 px-3 py-2 text-xs">最近一次：{{ form.last_run_message }}</p>
      </div>
    </section>

    <!-- 大纲 -->
    <section class="rounded-2xl border bg-card shadow-sm">
      <h2 class="flex items-center gap-2 border-b px-5 py-3 text-sm font-semibold">
        <ListPlus class="size-4" /> 大纲续写
      </h2>
      <div class="space-y-4 p-5">
        <p class="text-sm" :class="outline?.warn ? 'font-medium' : 'text-muted-foreground'">{{ outlineText }}</p>
        <label class="flex items-center gap-3 text-sm">
          <input v-model="form.auto_extend_outline" type="checkbox" :true-value="1" :false-value="0" class="size-4 accent-foreground" />
          大纲快用完时自动往后续写
        </label>
        <label class="block max-w-48 space-y-1.5 text-sm">
          <span class="font-medium">每次续写几章大纲</span>
          <Input v-model.number="form.extend_chapters" type="number" min="1" max="50" />
        </label>
        <p class="text-xs text-muted-foreground">
          剩余章数少于「系统设置 → 大纲剩余几章时提醒」时，在写下一章之前自动续写大纲，连续续写不会写着写着没了方向。
        </p>
        <Button variant="outline" size="sm" class="gap-1.5" :disabled="!outline?.outline_max || deleted" @click="extendOpen = true">
          <ListPlus class="size-4" /> 现在就续写 {{ form.extend_chapters || 10 }} 章大纲
        </Button>
      </div>
    </section>

    <div class="flex justify-end">
      <Button class="gap-2" :disabled="saving" @click="save">
        <Save class="size-4" /> 保存设置
      </Button>
    </div>

    <StreamDialog
      v-if="extendOpen"
      v-model:open="extendOpen"
      title="续写大纲"
      task-type="extend_outline"
      :novel-id="novelId"
      :params="{ chapters: Number(form.extend_chapters) || 10 }"
      mode="stream"
      @done="() => { toast.success('大纲已续写，可在「大纲」页查看'); load() }"
    />
  </div>
</template>

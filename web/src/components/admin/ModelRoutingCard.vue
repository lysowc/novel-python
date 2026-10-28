<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { Save, Split } from '@lucide/vue'
import { toast } from 'vue-sonner'
import { Button } from '@/components/ui/button'
import { fetchConfig, saveConfig } from '@/api'
import type { AiModel } from '@/types/api'

/** 模型分工：写正文用好模型，摘要/记忆/审校/质检这类辅助调用用便宜模型 */
defineProps<{ models: AiModel[] }>()

const writing = ref('0')
const auxiliary = ref('0')
const saving = ref(false)

async function load() {
  try {
    const cfg = await fetchConfig()
    writing.value = String(cfg.model_writing_id ?? '0')
    auxiliary.value = String(cfg.model_auxiliary_id ?? '0')
  } catch {
    // 请求层已提示
  }
}

async function save() {
  saving.value = true
  try {
    await saveConfig({ model_writing_id: writing.value, model_auxiliary_id: auxiliary.value })
    toast.success('模型分工已保存')
  } catch {
    // 请求层已提示
  } finally {
    saving.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="rounded-2xl border bg-card shadow-sm">
    <div class="flex items-center gap-2 border-b px-5 py-3">
      <Split class="size-4" />
      <h2 class="text-sm font-semibold">模型分工</h2>
      <span class="text-xs text-muted-foreground">每写一章，约一半的调用是摘要、记忆这类辅助工作，交给便宜的模型能省不少钱</span>
    </div>
    <div class="grid gap-4 p-5 sm:grid-cols-[1fr_1fr_auto] sm:items-end">
      <label class="space-y-1.5 text-sm">
        <span class="font-medium">写作模型</span>
        <span class="block text-xs text-muted-foreground">正文、设定、大纲、点子聊天</span>
        <select v-model="writing" class="h-9 w-full rounded-lg border bg-background px-2 text-sm">
          <option value="0">默认模型</option>
          <option v-for="m in models" :key="m.id" :value="String(m.id)" :disabled="!m.status">
            {{ m.display_name || m.name }}（{{ m.provider_name }}）{{ m.status ? '' : '· 已停用' }}
          </option>
        </select>
      </label>
      <label class="space-y-1.5 text-sm">
        <span class="font-medium">辅助模型</span>
        <span class="block text-xs text-muted-foreground">摘要、记忆、审校、质量检查</span>
        <select v-model="auxiliary" class="h-9 w-full rounded-lg border bg-background px-2 text-sm">
          <option value="0">默认模型</option>
          <option v-for="m in models" :key="m.id" :value="String(m.id)" :disabled="!m.status">
            {{ m.display_name || m.name }}（{{ m.provider_name }}）{{ m.status ? '' : '· 已停用' }}
          </option>
        </select>
      </label>
      <Button class="gap-1.5" :disabled="saving" @click="save">
        <Save class="size-4" /> 保存
      </Button>
    </div>
  </div>
</template>

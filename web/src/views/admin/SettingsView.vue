<script setup lang="ts">import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'

import { onMounted, ref } from 'vue'
import { Save } from '@lucide/vue'
import { toast } from 'vue-sonner'
import PageHeader from '@/components/common/PageHeader.vue'
import LoadingState from '@/components/common/LoadingState.vue'
import { fetchConfig, saveConfig } from '@/api'
import type { SystemConfig } from '@/types/api'

const loading = ref(true)
const saving = ref(false)
const form = ref<SystemConfig>({})

type Field = { key: string; label: string; type: 'text' | 'number'; placeholder: string; hint: string; step?: number }

const groups: { title: string; fields: Field[] }[] = [
  {
    title: '基础',
    fields: [
      { key: 'site_name', label: '站点名称', type: 'text', placeholder: '拾光小说', hint: '前台导航与页脚展示的站点名' },
      { key: 'ai_temperature', label: 'AI 温度', type: 'number', placeholder: '0.8', hint: '创作随机性，0-2，越高越发散', step: 0.1 },
      { key: 'ai_http_timeout', label: 'AI HTTP 超时（秒）', type: 'number', placeholder: '120', hint: '调用模型接口的超时上限', step: 1 },
    ],
  },
  {
    title: '章节生成',
    fields: [
      { key: 'chapter_target_words', label: '章节目标字数', type: 'number', placeholder: '3000', hint: 'AI 生成章节的默认目标字数', step: 1 },
      { key: 'chapter_auto_publish', label: 'AI 新章节自动发布', type: 'number', placeholder: '1', hint: '1=生成后直接发布；0=先存为草稿，读者不可见，在章节页审阅后发布', step: 1 },
      { key: 'context_max_recent_chapters', label: '最近章节数', type: 'number', placeholder: '5', hint: '生成章节时带入的最近章节正文数量', step: 1 },
      { key: 'context_summary_max_chars', label: '摘要字符上限', type: 'number', placeholder: '12000', hint: '上下文摘要的最大字符数', step: 1 },
      { key: 'context_max_chars', label: '上下文总字符上限', type: 'number', placeholder: '60000', hint: '发给 AI 的上下文总长度上限，超出时依次压缩最近正文、召回章节和摘要；在「AI 配置」填写模型上下文窗口后会自动取更小的值', step: 1000 },
      { key: 'retrieval_enabled', label: '相关章节检索', type: 'number', placeholder: '1', hint: '生成章节时按相关性召回历史章节和原文片段（1=启用，0=关闭）', step: 1 },
      { key: 'retrieval_max_chapters', label: '召回章节数', type: 'number', placeholder: '5', hint: '每章生成时召回的相关历史章节数量', step: 1 },
    ],
  },
  {
    title: '质量与记忆',
    fields: [
      { key: 'quality_check_enabled', label: '生成后自动检查质量', type: 'number', placeholder: '1', hint: '1=开启：字数明显不足、写到一半断掉、和上一章大段重复、段落重复时自动重写一次；0=关闭', step: 1 },
      { key: 'quality_min_ratio', label: '字数不足判定比例', type: 'number', placeholder: '0.5', hint: '正文字数低于「目标字数 × 这个比例」算字数不足，默认 0.5', step: 0.1 },
      { key: 'auto_rebuild_memory', label: '重写中间章节后自动重建记忆', type: 'number', placeholder: '1', hint: '1=重写中间章节后自动逐章重算后续章节的记忆（每章一次 AI 调用）；0=只更新这一章', step: 1 },
      { key: 'consistency_auto_interval', label: '自动审校间隔（章）', type: 'number', placeholder: '10', hint: '每 N 章自动运行一致性审校（0=关闭）', step: 1 },
      { key: 'arc_summary_size', label: '分段总结间隔（章）', type: 'number', placeholder: '20', hint: '每写满 N 章自动生成一段前情总结，长篇写到后面也不会忘掉早期剧情（0=关闭）', step: 1 },
      { key: 'repetition_check_enabled', label: '检测重复桥段', type: 'number', placeholder: '1', hint: '1=每章写完和前 30 章的摘要比对，情节太像时在章节页提醒；0=关闭', step: 1 },
      { key: 'outline_warn_remaining', label: '大纲剩余几章时提醒', type: 'number', placeholder: '3', hint: '大纲只剩这么多章没写时提醒；小说开启了「自动续写大纲」时会自动往后规划', step: 1 },
    ],
  },
  {
    title: '思考强度（DeepSeek）',
    fields: [
      { key: 'reasoning_writing', label: '写正文 / 设定 / 大纲', type: 'text', placeholder: 'high', hint: 'none=不思考直接写（「AI 温度」才生效）；low / high / max=先思考再写，越高越慢越贵。默认 high' },
      { key: 'reasoning_auxiliary', label: '摘要 / 记忆 / 分段总结', type: 'text', placeholder: 'none', hint: '整理类任务，默认 none（不思考）：更快更省，也不会出现「思考用光额度、没给出结果」' },
      { key: 'reasoning_review', label: '一致性审校', type: 'text', placeholder: 'low', hint: '找前后矛盾需要一点推理，默认 low。填 off=不传这个参数，用模型自己的默认（非 DeepSeek 模型不受影响）' },
    ],
  },
  {
    title: '费用上限',
    fields: [
      { key: 'budget_daily', label: '每日 AI 费用上限（元）', type: 'number', placeholder: '0', hint: '今天所有 AI 调用加起来超过这个数就不再发起新任务，第二天自动恢复（0=不限；需要在「AI 配置」填写模型单价）', step: 1 },
      { key: 'budget_novel_daily', label: '每本小说每日费用上限（元）', type: 'number', placeholder: '0', hint: '单本小说今天的花费超过这个数就不再续写（0=不限）', step: 1 },
    ],
  },
  {
    title: '回收站',
    fields: [
      { key: 'trash_retention_days', label: '回收站保留天数', type: 'number', placeholder: '30', hint: '删除的小说在回收站里保留的天数，到期自动彻底删除', step: 1 },
    ],
  },
]

async function load() {
  loading.value = true
  try {
    form.value = await fetchConfig()
  } finally {
    loading.value = false
  }
}

async function save() {
  saving.value = true
  try {
    await saveConfig({ ...form.value })
    toast.success('系统设置已保存')
  } catch {
    // 请求层已提示
  } finally {
    saving.value = false
  }
}

onMounted(load)
</script>

<template>
  <div>
    <PageHeader title="系统设置" description="配置站点与 AI 创作参数" />

    <LoadingState v-if="loading" variant="table" :rows="3" />

    <div v-else class="max-w-2xl space-y-5">
      <section v-for="g in groups" :key="g.title" class="rounded-2xl border bg-card shadow-sm">
        <h2 class="border-b px-6 py-3 text-sm font-semibold">{{ g.title }}</h2>
        <div class="space-y-4 p-6">
          <div v-for="f in g.fields" :key="f.key" class="space-y-1.5">
            <Label>{{ f.label }}</Label>
            <Input
              :type="f.type"
              :step="f.step"
              v-model="form[f.key]"
              :placeholder="f.placeholder"
            />
            <p class="text-xs text-muted-foreground">{{ f.hint }}</p>
          </div>
        </div>
      </section>
      <div class="sticky bottom-4 flex justify-end">
        <Button class="gap-2 shadow-md" :disabled="saving" @click="save">
          <Save class="size-4" />
          保存设置
        </Button>
      </div>
    </div>
  </div>
</template>

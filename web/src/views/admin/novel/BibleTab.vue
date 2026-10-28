<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { BookMarked, Layers, Lock, LockOpen, Pencil, Plus, RefreshCw, Save, Search, Sparkles, Trash2, Users } from '@lucide/vue'
import { toast } from 'vue-sonner'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { Label } from '@/components/ui/label'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import ConfirmDialog from '@/components/common/ConfirmDialog.vue'
import StreamDialog from '@/components/common/StreamDialog.vue'
import LoadingState from '@/components/common/LoadingState.vue'
import {
  createProfile, createTerm, deleteProfile, deleteTerm, fetchBible, updateArc, updateProfile, updateTerm,
} from '@/api'
import type { ArcSummary, CharacterProfile, StoryBible, StoryTerm } from '@/types/api'

/** 长篇资料库：人物档案、名词表、分段总结。AI 每章写完自动维护，作者可修改并锁定 */
const props = defineProps<{ novelId: number; deleted?: boolean }>()

const loading = ref(true)
const bible = ref<StoryBible | null>(null)
const section = ref<'profiles' | 'terms' | 'arcs'>('profiles')

async function load() {
  try {
    bible.value = await fetchBible(props.novelId)
  } finally {
    loading.value = false
  }
}
onMounted(load)

const sections = computed(() => [
  { value: 'profiles' as const, label: '人物档案', icon: Users, count: bible.value?.profiles.length ?? 0 },
  { value: 'terms' as const, label: '名词表', icon: BookMarked, count: bible.value?.terms.length ?? 0 },
  { value: 'arcs' as const, label: '分段总结', icon: Layers, count: bible.value?.arcs.length ?? 0 },
])

// ---------- 人物档案 ----------
const profileKeyword = ref('')
const profiles = computed(() => {
  const list = bible.value?.profiles ?? []
  const k = profileKeyword.value.trim()
  if (!k) return list
  return list.filter((p) => [p.name, p.aliases, p.role, p.realm].some((v) => v.includes(k)))
})

type ProfileForm = Omit<CharacterProfile, 'id' | 'updated_at' | 'last_chapter'> & { id?: number }
const emptyProfile = (): ProfileForm => ({
  name: '', aliases: '', role: '', appearance: '', personality: '', abilities: '', realm: '',
  realm_history: [], relationships: '', status: '', notes: '', first_chapter: 0, locked: 1,
})
const profileOpen = ref(false)
const profileForm = ref<ProfileForm>(emptyProfile())
const profileSaving = ref(false)

function editProfile(p?: CharacterProfile) {
  profileForm.value = p ? { ...p, realm_history: [...p.realm_history] } : emptyProfile()
  profileOpen.value = true
}

async function saveProfile() {
  const f = profileForm.value
  if (!f.name.trim()) {
    toast.error('姓名不能为空')
    return
  }
  profileSaving.value = true
  try {
    const body = {
      name: f.name, aliases: f.aliases, role: f.role, appearance: f.appearance, personality: f.personality,
      abilities: f.abilities, realm: f.realm, relationships: f.relationships, status: f.status, notes: f.notes,
      locked: f.locked ? 1 : 0, realm_history: f.realm_history,
    }
    if (f.id) {
      // 境界改了的话后端会自动记一笔历史，这里不重复传历史
      const old = bible.value?.profiles.find((p) => p.id === f.id)
      if (old && old.realm !== f.realm) delete (body as Partial<typeof body>).realm_history
      await updateProfile(f.id, body)
    } else {
      await createProfile(props.novelId, { ...body, first_chapter: Number(f.first_chapter) || 0 })
    }
    toast.success('档案已保存')
    profileOpen.value = false
    load()
  } catch {
    // 请求层已提示
  } finally {
    profileSaving.value = false
  }
}

function removeRealm(i: number) {
  profileForm.value.realm_history = profileForm.value.realm_history.filter((_, idx) => idx !== i)
}

async function toggleProfileLock(p: CharacterProfile) {
  try {
    await updateProfile(p.id, { locked: p.locked ? 0 : 1 })
    p.locked = p.locked ? 0 : 1
    toast.success(p.locked ? '已锁定：AI 不会再改这个人物' : '已解锁：AI 会随剧情更新这个人物')
  } catch {
    // 请求层已提示
  }
}

// ---------- 名词表 ----------
const termKeyword = ref('')
const terms = computed(() => {
  const list = bible.value?.terms ?? []
  const k = termKeyword.value.trim()
  if (!k) return list
  return list.filter((t) => [t.term, t.type, t.description].some((v) => v.includes(k)))
})
type TermForm = { id?: number; term: string; type: string; description: string; locked: number }
const termOpen = ref(false)
const termForm = ref<TermForm>({ term: '', type: '', description: '', locked: 1 })
const termSaving = ref(false)

function editTerm(t?: StoryTerm) {
  termForm.value = t
    ? { id: t.id, term: t.term, type: t.type, description: t.description, locked: t.locked }
    : { term: '', type: '', description: '', locked: 1 }
  termOpen.value = true
}

async function saveTerm() {
  const f = termForm.value
  if (!f.term.trim()) {
    toast.error('名词不能为空')
    return
  }
  termSaving.value = true
  try {
    const body = { term: f.term, type: f.type, description: f.description, locked: f.locked ? 1 : 0 }
    if (f.id) await updateTerm(f.id, body)
    else await createTerm(props.novelId, body)
    toast.success('名词已保存')
    termOpen.value = false
    load()
  } catch {
    // 请求层已提示
  } finally {
    termSaving.value = false
  }
}

// ---------- 删除确认（档案 / 名词共用） ----------
const pendingDelete = ref<{ kind: 'profile' | 'term'; id: number; name: string } | null>(null)
const deleting = ref(false)
async function confirmDelete() {
  const d = pendingDelete.value
  if (!d) return
  deleting.value = true
  try {
    if (d.kind === 'profile') await deleteProfile(d.id)
    else await deleteTerm(d.id)
    toast.success('已删除')
    pendingDelete.value = null
    load()
  } catch {
    // 请求层已提示
  } finally {
    deleting.value = false
  }
}

// ---------- 分段总结 ----------
const arcEditing = ref<number | null>(null)
const arcDraft = ref('')
const arcSaving = ref(false)
function editArc(a: ArcSummary) {
  arcEditing.value = a.id
  arcDraft.value = a.content
}
async function saveArc(a: ArcSummary) {
  arcSaving.value = true
  try {
    const res = await updateArc(a.id, { content: arcDraft.value })
    a.content = res.content
    a.updated_at = res.updated_at
    arcEditing.value = null
    toast.success('分段总结已保存')
  } catch {
    // 请求层已提示
  } finally {
    arcSaving.value = false
  }
}

const arcTask = ref<{ title: string; params?: Record<string, unknown> } | null>(null)
const arcTaskOpen = computed({
  get: () => !!arcTask.value,
  set: (v: boolean) => { if (!v) arcTask.value = null },
})
function regenerateArc(a: ArcSummary) {
  arcTask.value = { title: `重新生成 · 第 ${a.start_no}-${a.end_no} 章分段总结`, params: { chapter_no: a.start_no } }
}
function fillArcs() {
  arcTask.value = { title: '补齐分段总结', params: undefined }
}
function onArcDone() {
  toast.success('分段总结已更新')
  load()
}
</script>

<template>
  <LoadingState v-if="loading" variant="list" :rows="3" />
  <div v-else-if="bible" class="space-y-5">
    <p class="text-sm text-muted-foreground">
      资料库帮 AI 在长篇里不串人设、不忘前情：每章写完 AI 会自动更新这里，写新章节时只带上和本章相关的部分。
      你可以修改任何条目；<span class="text-foreground">锁定</span>的条目 AI 不会再改。
    </p>

    <!-- 分区切换 -->
    <div class="flex gap-2 overflow-x-auto pb-1">
      <Button
        v-for="s in sections"
        :key="s.value"
        size="sm"
        :variant="section === s.value ? 'default' : 'outline'"
        class="shrink-0 gap-1.5"
        @click="section = s.value"
      >
        <component :is="s.icon" class="size-4" />
        {{ s.label }}
        <span class="text-xs opacity-70">{{ s.count }}</span>
      </Button>
    </div>

    <!-- 人物档案 -->
    <section v-if="section === 'profiles'" class="space-y-3">
      <div class="flex flex-wrap items-center gap-2">
        <div class="relative min-w-48 flex-1 sm:max-w-xs">
          <Search class="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input v-model="profileKeyword" placeholder="搜姓名、别名、身份、境界" class="pl-8" />
        </div>
        <Button size="sm" class="gap-1.5" :disabled="deleted" @click="editProfile()">
          <Plus class="size-4" /> 新建档案
        </Button>
      </div>

      <p v-if="!bible.profiles.length" class="rounded-xl border border-dashed px-4 py-8 text-center text-sm text-muted-foreground">
        还没有人物档案。写完新章节后 AI 会自动建档；已有章节可以到「记忆」页「从第 1 章重建记忆」一次性补出来。
      </p>
      <p v-else-if="!profiles.length" class="py-6 text-center text-sm text-muted-foreground">没有匹配的人物</p>

      <div class="grid gap-3 md:grid-cols-2">
        <article v-for="p in profiles" :key="p.id" class="flex flex-col rounded-2xl border bg-card p-4 shadow-sm">
          <header class="flex items-start gap-2">
            <div class="min-w-0 flex-1">
              <h3 class="flex flex-wrap items-baseline gap-x-2 font-semibold">
                {{ p.name }}
                <span v-if="p.role" class="text-xs font-normal text-muted-foreground">{{ p.role }}</span>
              </h3>
              <p v-if="p.aliases" class="truncate text-xs text-muted-foreground">又称：{{ p.aliases }}</p>
            </div>
            <Button
              size="icon"
              variant="ghost"
              class="size-8 shrink-0"
              :title="p.locked ? '已锁定，点击解锁' : '点击锁定，AI 不再修改'"
              @click="toggleProfileLock(p)"
            >
              <Lock v-if="p.locked" class="size-4" />
              <LockOpen v-else class="size-4 text-muted-foreground" />
            </Button>
            <Button size="icon" variant="ghost" class="size-8 shrink-0" title="编辑" @click="editProfile(p)">
              <Pencil class="size-4" />
            </Button>
            <Button
              size="icon"
              variant="ghost"
              class="size-8 shrink-0 text-muted-foreground hover:text-destructive"
              title="删除"
              @click="pendingDelete = { kind: 'profile', id: p.id, name: p.name }"
            >
              <Trash2 class="size-4" />
            </Button>
          </header>
          <dl class="mt-3 space-y-1.5 text-sm">
            <div v-if="p.realm" class="flex gap-2"><dt class="w-10 shrink-0 text-muted-foreground">境界</dt><dd>{{ p.realm }}</dd></div>
            <div v-if="p.status" class="flex gap-2"><dt class="w-10 shrink-0 text-muted-foreground">现状</dt><dd>{{ p.status }}</dd></div>
            <div v-if="p.appearance" class="flex gap-2"><dt class="w-10 shrink-0 text-muted-foreground">外貌</dt><dd class="line-clamp-2">{{ p.appearance }}</dd></div>
            <div v-if="p.personality" class="flex gap-2"><dt class="w-10 shrink-0 text-muted-foreground">性格</dt><dd class="line-clamp-2">{{ p.personality }}</dd></div>
          </dl>
          <div v-if="p.realm_history.length > 1" class="mt-3 flex flex-wrap items-center gap-1 text-xs text-muted-foreground">
            <template v-for="(h, i) in p.realm_history" :key="i">
              <span v-if="i" aria-hidden="true">→</span>
              <span class="rounded bg-muted px-1.5 py-0.5">{{ h.chapter ? `第${h.chapter}章 ` : '' }}{{ h.realm }}</span>
            </template>
          </div>
          <footer class="mt-auto pt-3 text-xs text-muted-foreground">
            <span v-if="p.first_chapter">第 {{ p.first_chapter }} 章登场</span>
            <span v-if="p.last_chapter" class="ml-3">最近出场第 {{ p.last_chapter }} 章</span>
          </footer>
        </article>
      </div>
    </section>

    <!-- 名词表 -->
    <section v-else-if="section === 'terms'" class="space-y-3">
      <div class="flex flex-wrap items-center gap-2">
        <div class="relative min-w-48 flex-1 sm:max-w-xs">
          <Search class="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input v-model="termKeyword" placeholder="搜名词、类别、说明" class="pl-8" />
        </div>
        <Button size="sm" class="gap-1.5" :disabled="deleted" @click="editTerm()">
          <Plus class="size-4" /> 新建名词
        </Button>
      </div>
      <p v-if="!bible.terms.length" class="rounded-xl border border-dashed px-4 py-8 text-center text-sm text-muted-foreground">
        还没有名词。地名、功法、物品、势力等专有名词会在写章节时由 AI 自动收录，统一写法。
      </p>
      <p v-else-if="!terms.length" class="py-6 text-center text-sm text-muted-foreground">没有匹配的名词</p>
      <div v-if="terms.length" class="divide-y rounded-2xl border bg-card shadow-sm">
        <div v-for="t in terms" :key="t.id" class="flex items-start gap-3 px-4 py-3">
          <div class="min-w-0 flex-1">
            <p class="flex flex-wrap items-baseline gap-x-2 text-sm font-medium">
              {{ t.term }}
              <span v-if="t.type" class="rounded bg-muted px-1.5 py-0.5 text-xs font-normal text-muted-foreground">{{ t.type }}</span>
              <Lock v-if="t.locked" class="size-3 self-center text-muted-foreground" />
              <span v-if="t.first_chapter" class="text-xs font-normal text-muted-foreground">第 {{ t.first_chapter }} 章</span>
            </p>
            <p v-if="t.description" class="mt-0.5 text-sm text-muted-foreground">{{ t.description }}</p>
          </div>
          <Button size="icon" variant="ghost" class="size-8 shrink-0" title="编辑" @click="editTerm(t)">
            <Pencil class="size-4" />
          </Button>
          <Button
            size="icon"
            variant="ghost"
            class="size-8 shrink-0 text-muted-foreground hover:text-destructive"
            title="删除"
            @click="pendingDelete = { kind: 'term', id: t.id, name: t.term }"
          >
            <Trash2 class="size-4" />
          </Button>
        </div>
      </div>
    </section>

    <!-- 分段总结 -->
    <section v-else class="space-y-3">
      <div class="flex flex-wrap items-center gap-3">
        <p class="flex-1 text-sm text-muted-foreground">
          <template v-if="bible.arc_size > 0">每 {{ bible.arc_size }} 章一段，写满一段自动生成，作为早期剧情的"前情提要"带给 AI。</template>
          <template v-else>分段总结已关闭（系统设置 → 分段总结间隔为 0）。</template>
        </p>
        <Button
          v-if="bible.missing_arcs.length"
          size="sm"
          variant="outline"
          class="gap-1.5"
          :disabled="deleted || bible.arc_size <= 0"
          @click="fillArcs"
        >
          <Sparkles class="size-4" /> 补齐缺失的 {{ bible.missing_arcs.length }} 段
        </Button>
      </div>
      <p v-if="bible.missing_arcs.length" class="text-xs text-muted-foreground">
        缺少：{{ bible.missing_arcs.map((m) => `第${m.start_no}-${m.end_no}章`).join('、') }}
      </p>
      <p v-if="!bible.arcs.length" class="rounded-xl border border-dashed px-4 py-8 text-center text-sm text-muted-foreground">
        还没有分段总结。写满 {{ bible.arc_size || 20 }} 章后会自动生成第一段。
      </p>
      <article v-for="a in bible.arcs" :key="a.id" class="rounded-2xl border bg-card shadow-sm">
        <header class="flex flex-wrap items-center gap-2 border-b px-4 py-2.5">
          <h3 class="flex-1 text-sm font-semibold">第 {{ a.start_no }}-{{ a.end_no }} 章</h3>
          <span class="text-xs text-muted-foreground">{{ a.updated_at }}</span>
          <template v-if="arcEditing !== a.id">
            <Button size="sm" variant="ghost" class="h-8 gap-1.5" @click="editArc(a)"><Pencil class="size-3.5" /> 编辑</Button>
            <Button size="sm" variant="ghost" class="h-8 gap-1.5" :disabled="deleted" @click="regenerateArc(a)">
              <RefreshCw class="size-3.5" /> 重新生成
            </Button>
          </template>
        </header>
        <div class="p-4">
          <template v-if="arcEditing === a.id">
            <Textarea v-model="arcDraft" rows="10" class="text-sm leading-relaxed" />
            <div class="mt-3 flex justify-end gap-2">
              <Button size="sm" variant="outline" @click="arcEditing = null">取消</Button>
              <Button size="sm" class="gap-1.5" :disabled="arcSaving" @click="saveArc(a)"><Save class="size-4" /> 保存</Button>
            </div>
          </template>
          <p v-else class="text-sm leading-relaxed whitespace-pre-wrap">{{ a.content || '（空）' }}</p>
        </div>
      </article>
    </section>

    <!-- 档案编辑 -->
    <Dialog v-model:open="profileOpen">
      <DialogContent class="max-h-[90vh] overflow-y-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>{{ profileForm.id ? `编辑档案 · ${profileForm.name}` : '新建人物档案' }}</DialogTitle>
          <DialogDescription>写清楚长期不变的设定，AI 写到这个人时会参照。</DialogDescription>
        </DialogHeader>
        <div class="grid gap-4 py-2 sm:grid-cols-2">
          <div class="space-y-1.5">
            <Label>姓名 <span class="text-destructive">*</span></Label>
            <Input v-model="profileForm.name" maxlength="64" />
          </div>
          <div class="space-y-1.5">
            <Label>别名 / 称呼</Label>
            <Input v-model="profileForm.aliases" maxlength="255" placeholder="用逗号分隔，如：小七，七公子" />
          </div>
          <div class="space-y-1.5">
            <Label>身份</Label>
            <Input v-model="profileForm.role" maxlength="32" placeholder="主角 / 配角 / 反派…" />
          </div>
          <div class="space-y-1.5">
            <Label>境界 / 实力</Label>
            <Input v-model="profileForm.realm" maxlength="128" />
          </div>
          <div class="space-y-1.5 sm:col-span-2">
            <Label>现状</Label>
            <Input v-model="profileForm.status" maxlength="255" placeholder="如：重伤闭关、下落不明" />
          </div>
          <div class="space-y-1.5 sm:col-span-2">
            <Label>外貌</Label>
            <Textarea v-model="profileForm.appearance" rows="2" />
          </div>
          <div class="space-y-1.5 sm:col-span-2">
            <Label>性格</Label>
            <Textarea v-model="profileForm.personality" rows="2" />
          </div>
          <div class="space-y-1.5 sm:col-span-2">
            <Label>能力 / 功法 / 物品</Label>
            <Textarea v-model="profileForm.abilities" rows="2" />
          </div>
          <div class="space-y-1.5 sm:col-span-2">
            <Label>人物关系</Label>
            <Textarea v-model="profileForm.relationships" rows="2" />
          </div>
          <div class="space-y-1.5 sm:col-span-2">
            <Label>备注</Label>
            <Textarea v-model="profileForm.notes" rows="2" />
          </div>
          <div v-if="profileForm.realm_history.length" class="space-y-1.5 sm:col-span-2">
            <Label>境界变化记录</Label>
            <div class="flex flex-wrap gap-1.5">
              <span
                v-for="(h, i) in profileForm.realm_history"
                :key="i"
                class="inline-flex items-center gap-1 rounded-md bg-muted px-2 py-1 text-xs"
              >
                {{ h.chapter ? `第${h.chapter}章` : '初始' }}：{{ h.realm }}
                <button type="button" class="text-muted-foreground hover:text-destructive" title="删掉这条" @click="removeRealm(i)">×</button>
              </span>
            </div>
            <p class="text-xs text-muted-foreground">重写中间章节时，AI 会按这里取当时的境界。改上面的境界会自动记一笔。</p>
          </div>
          <label class="flex items-start gap-3 rounded-xl border p-3 text-sm sm:col-span-2">
            <input v-model="profileForm.locked" type="checkbox" :true-value="1" :false-value="0" class="mt-0.5 size-4 accent-foreground" />
            <span>
              <span class="font-medium">锁定</span>
              <span class="block text-xs text-muted-foreground">锁定后 AI 写新章节时不会改这份档案，只读不写。</span>
            </span>
          </label>
        </div>
        <DialogFooter>
          <Button variant="outline" @click="profileOpen = false">取消</Button>
          <Button :disabled="profileSaving" @click="saveProfile">保存</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>

    <!-- 名词编辑 -->
    <Dialog v-model:open="termOpen">
      <DialogContent class="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{{ termForm.id ? '编辑名词' : '新建名词' }}</DialogTitle>
          <DialogDescription>统一专有名词的写法，AI 会照这里写。</DialogDescription>
        </DialogHeader>
        <div class="space-y-4 py-2">
          <div class="space-y-1.5">
            <Label>名词 <span class="text-destructive">*</span></Label>
            <Input v-model="termForm.term" maxlength="64" />
          </div>
          <div class="space-y-1.5">
            <Label>类别</Label>
            <Input v-model="termForm.type" maxlength="32" placeholder="地名 / 功法 / 物品 / 势力…" />
          </div>
          <div class="space-y-1.5">
            <Label>说明</Label>
            <Textarea v-model="termForm.description" rows="3" maxlength="500" />
          </div>
          <label class="flex items-center gap-3 text-sm">
            <input v-model="termForm.locked" type="checkbox" :true-value="1" :false-value="0" class="size-4 accent-foreground" />
            锁定（AI 不再修改）
          </label>
        </div>
        <DialogFooter>
          <Button variant="outline" @click="termOpen = false">取消</Button>
          <Button :disabled="termSaving" @click="saveTerm">保存</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>

    <ConfirmDialog
      :open="!!pendingDelete"
      :title="`删除「${pendingDelete?.name ?? ''}」？`"
      :description="pendingDelete?.kind === 'profile' ? '删除后 AI 写到这个人物时不再参照这份档案；如果没锁定，之后出场时 AI 可能会重新建档。' : '删除后 AI 不再参照这个名词的写法。'"
      confirm-text="删除"
      :loading="deleting"
      @update:open="(v: boolean) => { if (!v) pendingDelete = null }"
      @confirm="confirmDelete"
    />

    <StreamDialog
      v-if="arcTask"
      v-model:open="arcTaskOpen"
      :title="arcTask.title"
      task-type="arc_summary"
      :novel-id="novelId"
      :params="arcTask.params"
      mode="poll"
      @done="onArcDone"
    />
  </div>
</template>

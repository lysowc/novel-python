// 与后端契约一致的数据类型定义

export interface ApiResponse<T = unknown> {
  code: number
  msg: string
  data: T
}

export interface PageResult<T> {
  list: T[]
  total: number
  page: number
  page_size: number
}

export interface Category {
  id: number
  name: string
  description: string
  sort: number
  status: number
  novel_count?: number
  created_at: string
  updated_at: string
}

export type NovelStatus = 'draft' | 'published' | 'finished'

export interface Novel {
  id: number
  category_id: number
  title: string
  cover: string
  description: string
  tags: string // 逗号分隔
  status: NovelStatus
  is_public: 0 | 1
  word_count: number
  chapter_count: number
  category_name?: string
  created_at: string
  updated_at: string
  /** 非空 = 在回收站里 */
  deleted_at?: string | null
}

export interface NovelDetail extends Novel {
  first_no: number
}

export interface ChapterListItem {
  chapter_no: number
  title: string
  word_count: number
  updated_at: string
}

export interface Chapter {
  id: number
  novel_id: number
  chapter_no: number
  title: string
  summary: string
  content: string
  word_count: number
  /** published = 已发布；draft = 草稿（读者不可见） */
  status: 'published' | 'draft' | string
  created_at: string
  updated_at: string
}

export interface ChapterVersion {
  id: number
  novel_id: number
  chapter_no: number
  title: string
  word_count: number
  source: string
  source_text: string
  preview: string
  created_at: string
  content?: string
  summary?: string
}

export interface ChapterRead {
  chapter: Chapter
  prev_no: number | null
  next_no: number | null
}

export type IdeaStatus = 'unused' | 'used'

export interface Idea {
  id: number
  category_id: number
  title: string
  content: string
  status: IdeaStatus
  category_name?: string
  created_at: string
  updated_at: string
}

export type IdeaRole = 'user' | 'assistant'

export interface IdeaMessage {
  id: number
  idea_id: number
  role: IdeaRole
  content: string
  created_at: string
}

export interface AiProvider {
  id: number
  name: string
  base_url: string
  api_key: string // 回显时打码
  status: number
  is_default: number
  created_at: string
}

export interface AiModel {
  id: number
  provider_id: number
  provider_name?: string
  name: string
  display_name: string
  max_tokens: number
  /** 模型上下文窗口（tokens），0 = 未填写 */
  context_window?: number
  temperature: number
  /** 每百万输入 / 输出 tokens 的价格（元），用于费用统计 */
  price_input?: number
  price_output?: number
  status: number
  is_default: number
}

export interface UsageRow {
  calls: number
  prompt_tokens: number
  completion_tokens: number
  total_tokens: number
  cost: number
  failed: number
  estimated: number
}

export interface UsageStats {
  days: number
  since: string
  totals: UsageRow
  by_day: (UsageRow & { date: string })[]
  by_task_type: (UsageRow & { task_type: string; task_type_text: string })[]
  by_model: (UsageRow & { model: string })[]
  by_novel: (UsageRow & { novel_id: number; title: string })[]
}

export type PromptType =
  | 'idea_chat'
  | 'novel_setting'
  | 'outline'
  | 'chapter_generate'
  | 'chapter_summary'
  | 'memory_update'
  | 'chapter_continue'
  | 'consistency_check'
  | 'memory_patch'
  | 'arc_summary'

export interface Prompt {
  id: number
  type: PromptType
  name: string
  description: string
  content: string
  updated_at: string
}

export type TaskStatus = 'pending' | 'running' | 'success' | 'failed' | 'cancelled'

export type TaskType =
  | 'generate_setting'
  | 'generate_outline'
  | 'generate_chapter'
  | 'continue_chapter'
  | 'regenerate_chapter'
  | 'generate_summary'
  | 'update_memory'
  | 'rebuild_memory'
  | 'extend_outline'
  | 'consistency_check'
  | 'arc_summary'

export interface AiTask {
  id: number
  task_type: TaskType
  task_type_text?: string
  ref_id: number
  ref_type?: string
  status: TaskStatus
  error_message: string
  /** 任务参数；resume = 章节已保存待补做摘要/记忆，chain_stopped = 连续续写因错误停止 */
  params?: Record<string, unknown> | null
  created_at: string
  updated_at: string
}

export interface AiLog {
  id: number
  novel_id?: number
  provider: string
  model: string
  task_type: string
  /** 列表接口不返回（has_prompt 表示有），详情接口返回 */
  prompt?: string
  prompt_tokens: number
  completion_tokens: number
  total_tokens: number
  cost?: number
  duration: number // 毫秒
  status: 'success' | 'failed'
  error_message: string
  /** 本次调用的温度；null = 旧日志没记录 */
  temperature?: number | null
  /** 思考强度：none / low / high / max；空 = 没传（旧日志或非 DeepSeek） */
  reasoning?: string
  max_tokens?: number
  has_io?: number
  has_prompt?: number
  /** 搜索时：关键词所在位置的前后文 */
  match?: { field: string; label: string; text: string }
  created_at: string
}

export interface AiLogDetail extends AiLog {
  prompt: string
  input_text: string
  output_text: string
  reasoning_text: string
}

export interface NovelSetting {
  id: number
  novel_id: number
  world_view: string
  characters: string
  factions: string
  conflicts: string
  main_plot: string
  style: string
}

// Outline 是 novel 上的 JSON 字符串
export interface OutlineChapter {
  no: number
  title: string
  summary: string
}

export interface OutlineVolume {
  title: string
  chapters: OutlineChapter[]
}

export interface Outline {
  volumes: OutlineVolume[]
}

export interface Memory {
  id: number
  novel_id: number
  content: string // JSON 字符串（v2 结构化记忆槽 / 旧格式兼容）
  updated_at: string
}

// ============ 结构化记忆 v2 ============

export interface MemoryCurrentState {
  location: string
  time: string
  plot_progress: string
}

export interface MemoryCharacter {
  name: string
  status: string
  relationships: string
  goals: string
}

export interface MemoryForeshadowing {
  description: string
  planted_chapter: number
  status: 'open' | 'resolved'
  resolved_chapter: number
}

export interface MemoryTimelineEvent {
  chapter: number
  event: string
}

export interface MemoryItem {
  name: string
  status: string
}

export interface StructuredMemory {
  schema: 'v2'
  current_state: MemoryCurrentState
  characters: MemoryCharacter[]
  foreshadowing: MemoryForeshadowing[]
  world_facts: string[]
  timeline: MemoryTimelineEvent[]
  unresolved_events: string[]
  important_items: MemoryItem[]
  style_notes: string
  /** 已不在「人物状态」中的登场人物（姓名 + 最后状态），由后端合并时自动维护 */
  character_archive?: MemoryItem[]
}

// ============ 一致性审校 ============

export type ConsistencyStatus = 'ok' | 'warning' | 'critical'

export interface ConsistencyIssue {
  severity: 'minor' | 'major' | 'critical'
  type: string
  description: string
  suggestion: string
  related_chapters: number[]
}

export interface ConsistencyReport {
  id: number
  novel_id: number
  chapter_no: number
  status: ConsistencyStatus
  report: {
    status: ConsistencyStatus
    summary: string
    issues: ConsistencyIssue[]
  }
  created_at: string
}

export interface Dashboard {
  novel_count: number
  chapter_count: number
  total_words: number
  idea_count: number
  running_tasks: number
  today_chapters: number
  recent_tasks: AiTask[]
  recent_logs: AiLog[]
  /** 需要管理员处理的安全配置提醒 */
  security_warnings?: string[]
  /** 大纲快用完的小说 */
  outline_warnings?: (OutlineStatus & { novel_id: number; title: string })[]
}

export interface OutlineStatus {
  outline_max: number
  written_max: number
  remaining: number
  threshold: number
  warn: boolean
  auto_extend_outline?: number
}

export interface NovelAutomation {
  novel_id: number
  schedule_enabled: number
  schedule_time: string
  chapters_per_run: number
  target_words: number
  last_run_date: string | null
  last_run_message: string
  auto_extend_outline: number
  extend_chapters: number
  timezone: string
}

export interface OperationLog {
  id: number
  admin_id: number
  username: string
  action: string
  summary: string
  method: string
  path: string
  target_type: string
  target_id: number
  success: number
  message: string
  ip: string
  created_at: string
}

export interface HomeData {
  site_name?: string
  recent_updates: Novel[]
  categories: { id: number; name: string; novel_count: number }[]
}

export interface SystemConfig {
  [key: string]: string | number
}

// SSE 事件
export type StreamEventType = 'chunk' | 'delta' | 'status' | 'reset' | 'done' | 'error'

export interface StreamEvent {
  type: StreamEventType
  content?: string
  msg?: string
  /** 任务状态事件：status(pending/running/success/failed) / stage / message */
  status?: string
  stage?: string
  message?: string
  error_message?: string
  data?: unknown
}

export interface LoginResult {
  username: string
}

export interface CreateNovelResult {
  novel_id: number
}

// ============ 长篇资料库 ============

export interface RealmChange {
  chapter: number
  realm: string
}

export interface CharacterProfile {
  id: number
  name: string
  aliases: string
  role: string
  appearance: string
  personality: string
  abilities: string
  realm: string
  realm_history: RealmChange[]
  relationships: string
  status: string
  notes: string
  first_chapter: number
  last_chapter: number
  locked: number
  updated_at: string
}

export interface StoryTerm {
  id: number
  term: string
  type: string
  description: string
  first_chapter: number
  locked: number
  updated_at: string
}

export interface ArcSummary {
  id: number
  start_no: number
  end_no: number
  content: string
  updated_at: string
}

export interface StoryBible {
  profiles: CharacterProfile[]
  terms: StoryTerm[]
  arcs: ArcSummary[]
  arc_size: number
  missing_arcs: { start_no: number; end_no: number }[]
}

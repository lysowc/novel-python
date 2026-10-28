import { defineStore } from 'pinia'

export type ReaderWidth = 'narrow' | 'medium' | 'wide'

interface ReaderSettings {
  fontSize: number
  width: ReaderWidth
}

export interface ReadingProgress {
  no: number
  percent: number
  scrollTop: number
  updatedAt: number
}

/** 书签：存在读者自己的浏览器里（不需要登录） */
export interface Bookmark {
  id: string
  novelId: number
  novelTitle: string
  no: number
  chapterTitle: string
  percent: number
  scrollTop: number
  excerpt: string
  createdAt: number
}

const SETTINGS_KEY = 'reader-settings'
const PROGRESS_KEY = 'reader-progress'
const BOOKMARKS_KEY = 'reader-bookmarks'
const MAX_BOOKMARKS = 300

function readJson<T>(key: string, fallback: T): T {
  try {
    const raw = localStorage.getItem(key)
    return raw ? (JSON.parse(raw) as T) : fallback
  } catch {
    return fallback
  }
}

function writeJson(key: string, value: unknown) {
  try {
    localStorage.setItem(key, JSON.stringify(value))
  } catch {
    // 无痕模式或存储已满：本次会话内仍然有效
  }
}

function loadSettings(): ReaderSettings {
  try {
    const raw = localStorage.getItem(SETTINGS_KEY)
    if (raw) {
      const s = JSON.parse(raw) as Partial<ReaderSettings>
      return {
        fontSize: Math.min(24, Math.max(14, Number(s.fontSize) || 18)),
        width: ['narrow', 'medium', 'wide'].includes(s.width as string)
          ? (s.width as ReaderWidth)
          : 'medium',
      }
    }
  } catch {
    // ignore
  }
  return { fontSize: 18, width: 'medium' }
}

function loadProgress(): Record<number, ReadingProgress> {
  return readJson<Record<number, ReadingProgress>>(PROGRESS_KEY, {})
}

export const WIDTH_STYLES: Record<ReaderWidth, string> = {
  narrow: 'max-w-2xl',
  medium: 'max-w-3xl',
  wide: 'max-w-4xl',
}

export const useReaderStore = defineStore('reader', {
  state: () => ({
    fontSize: 18,
    width: 'medium' as ReaderWidth,
    progress: {} as Record<number, ReadingProgress>,
    bookmarks: [] as Bookmark[],
    settingsLoaded: false,
  }),
  actions: {
    init() {
      if (this.settingsLoaded) return
      const s = loadSettings()
      this.fontSize = s.fontSize
      this.width = s.width
      this.progress = loadProgress()
      const saved = readJson<Bookmark[]>(BOOKMARKS_KEY, [])
      this.bookmarks = Array.isArray(saved) ? saved : []
      this.settingsLoaded = true
    },
    setFontSize(size: number) {
      this.fontSize = Math.min(24, Math.max(14, size))
      this.persistSettings()
    },
    setWidth(w: ReaderWidth) {
      this.width = w
      this.persistSettings()
    },
    persistSettings() {
      writeJson(SETTINGS_KEY, { fontSize: this.fontSize, width: this.width })
    },
    /** 保存阅读进度（节流由调用方处理） */
    saveProgress(novelId: number, p: ReadingProgress) {
      this.progress[novelId] = p
      writeJson(PROGRESS_KEY, this.progress)
    },
    getProgress(novelId: number): ReadingProgress | undefined {
      return this.progress[novelId]
    },
    clearProgress(novelId: number) {
      delete this.progress[novelId]
      writeJson(PROGRESS_KEY, this.progress)
    },
    addBookmark(b: Omit<Bookmark, 'id' | 'createdAt'>): Bookmark {
      const bookmark: Bookmark = { ...b, id: `${Date.now()}-${Math.random().toString(36).slice(2, 7)}`, createdAt: Date.now() }
      this.bookmarks = [bookmark, ...this.bookmarks].slice(0, MAX_BOOKMARKS)
      writeJson(BOOKMARKS_KEY, this.bookmarks)
      return bookmark
    },
    removeBookmark(id: string) {
      this.bookmarks = this.bookmarks.filter((b) => b.id !== id)
      writeJson(BOOKMARKS_KEY, this.bookmarks)
    },
    bookmarksOf(novelId: number): Bookmark[] {
      return this.bookmarks.filter((b) => b.novelId === novelId)
    },
  },
})

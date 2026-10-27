/** 段落级文本对比（LCS），用于章节版本对比 */
export interface DiffPart {
  type: 'same' | 'add' | 'del'
  text: string
}

function splitParagraphs(text: string): string[] {
  return (text || '')
    .split(/\n+/)
    .map((p) => p.trim())
    .filter(Boolean)
}

/**
 * 对比 oldText → newText：del = 只在旧文本里，add = 只在新文本里。
 * 段落数过多时（避免卡顿）退化为整体替换。
 */
export function diffParagraphs(oldText: string, newText: string): DiffPart[] {
  const a = splitParagraphs(oldText)
  const b = splitParagraphs(newText)
  const n = a.length
  const m = b.length
  if (n * m > 4_000_000) {
    return [...a.map((text) => ({ type: 'del' as const, text })), ...b.map((text) => ({ type: 'add' as const, text }))]
  }
  // dp[i][j] = a[i..] 与 b[j..] 的最长公共子序列长度
  const dp: Uint32Array[] = Array.from({ length: n + 1 }, () => new Uint32Array(m + 1))
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      dp[i]![j] = a[i] === b[j] ? dp[i + 1]![j + 1]! + 1 : Math.max(dp[i + 1]![j]!, dp[i]![j + 1]!)
    }
  }
  const out: DiffPart[] = []
  let i = 0
  let j = 0
  while (i < n && j < m) {
    if (a[i] === b[j]) {
      out.push({ type: 'same', text: a[i]! })
      i++
      j++
    } else if (dp[i + 1]![j]! >= dp[i]![j + 1]!) {
      out.push({ type: 'del', text: a[i++]! })
    } else {
      out.push({ type: 'add', text: b[j++]! })
    }
  }
  while (i < n) out.push({ type: 'del', text: a[i++]! })
  while (j < m) out.push({ type: 'add', text: b[j++]! })
  return out
}

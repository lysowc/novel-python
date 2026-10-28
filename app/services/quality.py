"""生成质量检查：字数明显不足、写到一半断掉、和上一章大段重复、段落自我重复"""
import re

END_CHARS = set("。！？!?…”」』》）)】~～—.\"'*")
SHINGLE = 10            # 判定重复用的连续字数
DUPLICATE_RATIO = 0.3   # 和上一章重复片段占比超过这个值算重复
MIN_PARAGRAPH = 20      # 参与自我重复判定的最短段落


def _shingles(text: str) -> set[str]:
    t = re.sub(r"\s+", "", text or "")
    return {t[i:i + SHINGLE] for i in range(0, max(0, len(t) - SHINGLE + 1), 3)}


def overlap_ratio(text: str, previous: str) -> float:
    a, b = _shingles(text), _shingles(previous)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a)


def check(text: str, target_words: int, previous: str = "", finish_reason: str = "",
          min_ratio: float = 0.5) -> list[str]:
    """返回问题列表（空列表 = 通过）"""
    issues = []
    body = (text or "").strip()
    words = len(re.sub(r"\s+", "", body))
    if target_words > 0 and words < target_words * min_ratio:
        issues.append(f"字数明显不足（{words} 字，目标 {target_words} 字）")
    if finish_reason == "length":
        issues.append("输出达到长度上限被截断")
    elif finish_reason == "interrupted" or (body and body[-1] not in END_CHARS):
        issues.append("结尾不完整，疑似写到一半断掉")
    if previous:
        ratio = overlap_ratio(body, previous)
        if ratio > DUPLICATE_RATIO:
            issues.append(f"和上一章大段重复（约 {ratio:.0%}）")
    paragraphs = [p.strip() for p in body.splitlines() if len(p.strip()) >= MIN_PARAGRAPH]
    if paragraphs:
        repeated = len(paragraphs) - len(set(paragraphs))
        if repeated >= 2 and repeated / len(paragraphs) > 0.15:
            issues.append(f"有 {repeated} 段内容重复出现")
    return issues


def score(issues: list[str], text: str) -> tuple[int, int]:
    """挑更好的一版：问题少的优先，其次字数多的"""
    return (-len(issues), len(re.sub(r"\s+", "", text or "")))

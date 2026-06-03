"""
日志文件对比引擎
支持文本 diff 和 AI 智能对比两种模式
"""

import difflib
import json
from openai import OpenAI


def text_diff(path1: str, path2: str, name1: str = "", name2: str = ""):
    """逐行文本对比，返回 TextDiffResult 兼容的 dict"""
    with open(path1, encoding="utf-8", errors="replace") as f:
        lines1 = f.readlines()
    with open(path2, encoding="utf-8", errors="replace") as f:
        lines2 = f.readlines()

    diff_lines = list(difflib.unified_diff(
        lines1, lines2,
        fromfile=name1 or "文件1",
        tofile=name2 or "文件2",
        lineterm=""
    ))

    # 统计
    added = sum(1 for l in diff_lines if l.startswith("+") and not l.startswith("+++"))
    removed = sum(1 for l in diff_lines if l.startswith("-") and not l.startswith("---"))

    # 截断过长 diff
    diff_text = "\n".join(diff_lines[:2000])

    return {
        "added_lines": added,
        "removed_lines": removed,
        "total_diffs": added + removed,
        "unified_diff": diff_text,
    }


COMPARE_SYSTEM_PROMPT = """你是一名资深的 Android 系统日志分析专家。
你的任务是比较两份 logcat 日志的预处理摘要，找出关键差异。

请遵循以下原则：
1. 关注 Error、Fatal、ANR、Crash、OOM 等严重指标的变化
2. 标注哪些问题是文件2新增的，哪些在文件2中消失了
3. 给出可操作的修复建议

你必须严格按照 JSON 格式返回分析结果，不要包含任何其他文字。
返回格式如下：
{
  "summary": "对比一句话总结",
  "differences": [
    {"category": "error/crash/anr/other", "detail": "差异描述", "severity": "new/resolved/increased/decreased"}
  ],
  "new_issues": ["文件2新增的问题1", "问题2"],
  "resolved_issues": ["文件1中存在但文件2已消失的问题"],
  "recommendations": ["综合修复建议1", "建议2"]
}"""


def ai_compare(pr1, pr2, api_key: str, model: str = "gpt-4o-mini", base_url: str = "") -> dict:
    """
    调用 LLM 对比两份预处理结果。

    Args:
        pr1: 文件1的 PreprocessResult
        pr2: 文件2的 PreprocessResult
        api_key: API key
        model: 模型名称
        base_url: API 地址

    Returns:
        AI 分析结果 dict
    """

    def _summarize(pr, label: str) -> str:
        """将预处理结果转为 LLM 友好的摘要"""
        parts = [f"## {label}"]
        parts.append(f"总行数: {pr.total_lines}")
        parts.append(f"时间范围: {pr.first_timestamp} ~ {pr.last_timestamp}")
        parts.append(f"日志级别: F={pr.level_counts.get('F',0)}, E={pr.level_counts.get('E',0)}, "
                     f"W={pr.level_counts.get('W',0)}, I={pr.level_counts.get('I',0)}, "
                     f"D={pr.level_counts.get('D',0)}")
        parts.append(f"ANR块: {len(pr.anr_blocks)}, Java异常: {len(pr.java_crashes)}, "
                     f"Tombstone: {len(pr.native_tombstones)}, OOM: {len(pr.oom_events)}")
        parts.append(f"Binder异常: {len(pr.binder_issues)}, GC事件: {len(pr.gc_events)}, "
                     f"WakeLock: {len(pr.wakelock_issues)}, 丢帧: {len(pr.choreographer_issues)}")

        if pr.tag_counter:
            top = pr.tag_counter.most_common(5)
            parts.append(f"Top 5 TAG: {', '.join(f'{t}({c})' for t,c in top)}")

        if pr.error_lines:
            parts.append(f"\n### Error 行 (前20条)")
            for l in pr.error_lines[:20]:
                parts.append(l[:300])

        if pr.fatal_lines:
            parts.append(f"\n### Fatal 行")
            for l in pr.fatal_lines[:10]:
                parts.append(l[:300])

        if pr.anr_blocks:
            parts.append(f"\n### ANR 摘要")
            for a in pr.anr_blocks[:3]:
                parts.append(a[:500])

        if pr.java_crashes:
            parts.append(f"\n### Java Crash 摘要")
            for c in pr.java_crashes[:5]:
                parts.append(str(c)[:500])

        return "\n".join(parts)

    prompt = _summarize(pr1, "文件1") + "\n\n---\n\n" + _summarize(pr2, "文件2")

    client_kwargs = {"api_key": api_key}
    if base_url:
        client_kwargs["base_url"] = base_url
    client = OpenAI(**client_kwargs)

    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": COMPARE_SYSTEM_PROMPT},
            {"role": "user", "content": prompt[:60000]},  # 限制 token
        ],
        temperature=0.3,
    )

    content = response.choices[0].message.content
    if not content:
        raise ValueError("LLM 返回空结果")

    content = content.strip()
    if content.startswith("```"):
        lines = content.split("\n")
        content = "\n".join(lines[1:]) if len(lines) > 1 else content
    if content.endswith("```"):
        content = content[:content.rfind("```")].strip()

    return json.loads(content)

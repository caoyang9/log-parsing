"""
调用 LLM API 分析预处理后的日志数据
支持 OpenAI 及兼容接口（DeepSeek 等）
"""

import json
import os
from openai import OpenAI

from models import (
    ParseReport, CriticalIssue, CrashDetail, ANRDetail,
)

SYSTEM_PROMPT = """你是一名资深的 Android 系统日志分析专家。
你的任务是根据提供的 logcat 日志提取数据，进行深度分析。

请遵循以下原则：
1. 重点关注 FATAL、ANR、Java Exception、Native Crash、OOM、Watchdog 等严重问题
2. 分析问题的根因（是哪个进程/组件引起的）
3. 给出可操作的修复建议
4. 如果存在多个问题，按严重程度排序

你必须严格按照 JSON 格式返回分析结果，不要包含任何其他文字。
返回格式如下：
{
  "summary": "一句话总结日志中最重要的发现",
  "critical_issues": [
    {
      "severity": "CRITICAL",
      "title": "问题标题",
      "description": "问题详细描述",
      "suggestion": "修复建议"
    }
  ],
  "crashes": [
    {
      "package": "崩溃包名",
      "exception": "异常类型",
      "count": 次数,
      "sample": "关键堆栈前几行"
    }
  ],
  "anrs": [
    {
      "package": "ANR包名",
      "reason": "ANR原因",
      "count": 次数
    }
  ],
  "recommendations": ["修复建议1（按优先级排序）", "建议2"]
}"""


def analyze_log(
    llm_input: str,
    api_key: str,
    model: str = "gpt-4o-mini",
    base_url: str = "",
) -> dict:
    """
    调用 LLM API 分析日志。

    Args:
        llm_input: 预处理后的日志文本
        api_key: API key
        model: 模型名称
        base_url: API 地址（空则用 OpenAI 默认）

    Returns:
        解析后的 JSON dict
    """
    client_kwargs = {"api_key": api_key}
    if base_url:
        client_kwargs["base_url"] = base_url
    client = OpenAI(**client_kwargs)

    # DeepSeek 等兼容接口不一定支持 response_format，改用 prompt 约束
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": llm_input},
        ],
        temperature=0.3,
    )

    content = response.choices[0].message.content
    if not content:
        raise ValueError("LLM 返回空结果")

    # 清理可能的 markdown 代码块包裹
    content = content.strip()
    if content.startswith("```"):
        lines = content.split("\n")
        content = "\n".join(lines[1:]) if len(lines) > 1 else content
    if content.endswith("```"):
        content = content[: content.rfind("```")].strip()

    return json.loads(content)


def build_report(
    file_id: str,
    file_name: str,
    preprocess_result,
    ai_result: dict,
) -> ParseReport:
    """
    将预处理结果和 AI 分析结果合并为统一的 ParseReport。
    """
    from preprocess import PreprocessResult
    pr: PreprocessResult = preprocess_result

    time_range = f"{pr.first_timestamp} ~ {pr.last_timestamp}" if pr.first_timestamp else "未知"
    top_tags = [{"tag": t, "count": c} for t, c in pr.tag_counter.most_common(20)]

    crash_pkgs: list[dict] = []
    if pr.java_crashes:
        crash_pkgs = [{"package": p, "count": c} for p, c in pr.crash_package_counter.most_common(10)]

    return ParseReport(
        file_id=file_id,
        file_name=file_name,
        total_lines=pr.total_lines,
        time_range=time_range,
        level_counts=pr.level_counts,
        top_tags=top_tags,
        top_crashing_packages=crash_pkgs,
        summary=ai_result.get("summary", ""),
        critical_issues=[
            CriticalIssue(**ci) for ci in ai_result.get("critical_issues", [])
        ],
        crashes=[
            CrashDetail(**c) for c in ai_result.get("crashes", [])
        ],
        anrs=[
            ANRDetail(**a) for a in ai_result.get("anrs", [])
        ],
        recommendations=ai_result.get("recommendations", []),
    )

"""
Android Logcat MCP Server
通过 stdio JSON-RPC 与 Codex 通信，提供日志分析工具
"""

import sys
import json
import os
from pathlib import Path

# 确保能找到同目录下的模块
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))

from dotenv import load_dotenv
from preprocess import preprocess
from ai_parser import analyze_log

load_dotenv(str(Path(__file__).resolve().parent.parent / ".env"))

SERVER_NAME = "android-logcat-parser"
SERVER_VERSION = "0.1.0"

# ---- MCP 协议处理 ----

def send_response(request_id, result):
    """发送 JSON-RPC 响应到 stdout"""
    msg = json.dumps({"jsonrpc": "2.0", "id": request_id, "result": result}, ensure_ascii=False)
    sys.stdout.write(msg + "\n")
    sys.stdout.flush()

def send_error(request_id, code, message):
    """发送 JSON-RPC 错误到 stdout"""
    msg = json.dumps({"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}, ensure_ascii=False)
    sys.stdout.write(msg + "\n")
    sys.stdout.flush()

def log_stderr(msg):
    """写日志到 stderr（不干扰 stdio 协议通道）"""
    print(f"[mcp-server] {msg}", file=sys.stderr, flush=True)


# ---- Tool 定义 ----

TOOLS = [
    {
        "name": "analyze_logcat",
        "description": "分析 Android logcat 日志文件。自动提取 ANR、Crash、OOM、Binder异常、GC问题、WakeLock 等，并由 AI 生成结构化分析报告。适用文件：bugreport、长时间 logcat 抓取。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "日志文件的绝对路径，例如 /Users/gnayoac/logs/bugreport.log"
                }
            },
            "required": ["path"]
        }
    },
    {
        "name": "quick_stats",
        "description": "快速获取 logcat 日志的统计概览（不调用 AI，秒级返回）。返回总行数、各级别计数、Top TAG、ANR/Crash 数量。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "日志文件的绝对路径"
                }
            },
            "required": ["path"]
        }
    },
    {
        "name": "search_log",
        "description": "在 logcat 日志中搜索匹配特定关键词或正则表达式的行，返回上下文。用于定位特定错误或事件。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "日志文件的绝对路径"
                },
                "keyword": {
                    "type": "string",
                    "description": "搜索关键词或正则表达式"
                },
                "context_lines": {
                    "type": "integer",
                    "description": "每条匹配行的上下文行数（前后各N行），默认2",
                    "default": 2
                },
                "max_results": {
                    "type": "integer",
                    "description": "最大返回条数，默认50",
                    "default": 50
                }
            },
            "required": ["path", "keyword"]
        }
    }
]


# ---- Tool 实现 ----

def handle_analyze_logcat(args: dict) -> str:
    """analyze_logcat tool 实现"""
    path = args["path"]
    if not os.path.exists(path):
        return f"错误：文件不存在 — {path}"

    log_stderr(f"开始分析: {path}")

    # 1. 预处理
    try:
        pr = preprocess(path)
    except Exception as e:
        return f"预处理失败: {str(e)}"

    log_stderr(f"预处理完成: {pr.total_lines} 行, ANR={len(pr.anr_blocks)}, Crash={len(pr.java_crashes)}")

    # 2. AI 分析
    api_key = os.getenv("OPENAI_API_KEY", "")
    if not api_key:
        # 无 API key 时返回预处理摘要
        return _build_stats_report(pr, path)

    model = os.getenv("OPENAI_MODEL", "deepseek-chat")
    base_url = os.getenv("OPENAI_BASE_URL", "")

    try:
        ai_result = analyze_log(pr.llm_input, api_key, model, base_url)
    except Exception as e:
        log_stderr(f"AI 分析失败: {e}")
        # AI 失败时返回预处理摘要
        stats = _build_stats_report(pr, path)
        return f"{stats}\n\n⚠️ AI 分析失败: {str(e)}"

    # 3. 构建报告
    from ai_parser import build_report
    report = build_report(os.path.basename(path), os.path.basename(path), pr, ai_result)

    lines = [
        f"## 📊 {os.path.basename(path)} 分析报告",
        f"",
        f"**总行数**: {report.total_lines:,}  |  **时间范围**: {report.time_range}",
        f"**Fatal**: {report.level_counts.get('F',0)}  |  **Error**: {report.level_counts.get('E',0)}  |  **Warning**: {report.level_counts.get('W',0)}",
        f"**ANR**: {len(report.anrs)}  |  **崩溃**: {len(report.crashes)}  |  **关键问题**: {len(report.critical_issues)}",
        f"",
        f"### 总结",
        report.summary,
    ]

    if report.critical_issues:
        lines.append("")
        lines.append("### 🚨 关键问题")
        for ci in report.critical_issues:
            lines.append(f"- **[{ci.severity}] {ci.title}**: {ci.description}")
            lines.append(f"  → {ci.suggestion}")

    if report.crashes:
        lines.append("")
        lines.append("### 💥 崩溃详情")
        for c in report.crashes:
            lines.append(f"- **{c.package}** — {c.exception} (×{c.count})")

    if report.anrs:
        lines.append("")
        lines.append("### ⏱ ANR 详情")
        for a in report.anrs:
            lines.append(f"- **{a.package}**: {a.reason} (×{a.count})")

    if report.recommendations:
        lines.append("")
        lines.append("### 🔧 修复建议")
        for i, r in enumerate(report.recommendations, 1):
            lines.append(f"{i}. {r}")

    return "\n".join(lines)


def handle_quick_stats(args: dict) -> str:
    """quick_stats tool 实现"""
    path = args["path"]
    if not os.path.exists(path):
        return f"错误：文件不存在 — {path}"

    pr = preprocess(path)

    lines = [
        f"## 📋 {os.path.basename(path)} 统计概览",
        f"",
        f"| 指标 | 数值 |",
        f"|------|------|",
        f"| 总行数 | {pr.total_lines:,} |",
        f"| 时间范围 | {pr.first_timestamp} ~ {pr.last_timestamp} |",
        f"| Fatal (F) | {pr.level_counts['F']:,} |",
        f"| Error (E) | {pr.level_counts['E']:,} |",
        f"| Warning (W) | {pr.level_counts['W']:,} |",
        f"| Info (I) | {pr.level_counts['I']:,} |",
        f"| Debug (D) | {pr.level_counts['D']:,} |",
        f"| Verbose (V) | {pr.level_counts['V']:,} |",
        f"| ANR 块 | {len(pr.anr_blocks)} |",
        f"| Java 异常 | {len(pr.java_crashes)} |",
        f"| Native Tombstone | {len(pr.native_tombstones)} |",
        f"| OOM 事件 | {len(pr.oom_events)} |",
        f"| Binder 异常 | {len(pr.binder_issues)} |",
        f"| GC 事件 | {len(pr.gc_events)} |",
        f"| WakeLock 问题 | {len(pr.wakelock_issues)} |",
        f"| 丢帧/卡顿 | {len(pr.choreographer_issues)} |",
        f"",
        f"### Top 10 TAG",
    ]
    for tag, cnt in pr.tag_counter.most_common(10):
        lines.append(f"- `{tag}`: {cnt:,}")

    return "\n".join(lines)


def handle_search_log(args: dict) -> str:
    """search_log tool 实现"""
    import re
    path = args["path"]
    keyword = args["keyword"]
    context = int(args.get("context_lines", 2))
    max_results = int(args.get("max_results", 50))

    if not os.path.exists(path):
        return f"错误：文件不存在 — {path}"

    try:
        pattern = re.compile(keyword, re.IGNORECASE)
    except re.error as e:
        return f"正则表达式错误: {e}"

    matches: list[tuple[int, str]] = []
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for i, line in enumerate(f):
            if pattern.search(line):
                matches.append((i, line.rstrip()))
            if len(matches) >= max_results:
                break

    if not matches:
        return f"未找到匹配 `{keyword}` 的行"

    # 读上下文
    lines_map: dict[int, str] = {}
    line_indices = set()
    for idx, _ in matches:
        for offset in range(-context, context + 1):
            line_indices.add(idx + offset)

    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for i, line in enumerate(f):
            if i in line_indices:
                lines_map[i] = line.rstrip()
            if i > max(line_indices, default=0):
                break

    result_lines = [f"## 🔍 搜索: `{keyword}`（共 {len(matches)} 条结果）\n"]
    last_idx = -999
    for idx, _ in matches:
        if idx - last_idx > context * 2:
            result_lines.append("---")
        for offset in range(-context, context + 1):
            ln = idx + offset
            if ln in lines_map:
                prefix = ">>>" if ln == idx else "   "
                result_lines.append(f"{prefix} L{ln+1}: {lines_map[ln]}")
        last_idx = idx

    return "\n".join(result_lines)


def _build_stats_report(pr, path: str) -> str:
    """无 AI 时的兜底统计报告"""
    lines = [
        f"## 📋 {os.path.basename(path)} 预处理报告（未启用 AI 分析）",
        f"",
        f"**总行数**: {pr.total_lines:,}  |  **时间范围**: {pr.first_timestamp} ~ {pr.last_timestamp}",
        f"**Fatal**: {pr.level_counts['F']}  |  **Error**: {pr.level_counts['E']}  |  **Warning**: {pr.level_counts['W']}",
        f"**ANR**: {len(pr.anr_blocks)}  |  **崩溃**: {len(pr.java_crashes)}  |  **Tombstone**: {len(pr.native_tombstones)}",
        f"**OOM**: {len(pr.oom_events)}  |  **Binder**: {len(pr.binder_issues)}  |  **GC**: {len(pr.gc_events)}",
    ]
    return "\n".join(lines)


# ---- 主循环 ----

TOOL_HANDLERS = {
    "analyze_logcat": handle_analyze_logcat,
    "quick_stats": handle_quick_stats,
    "search_log": handle_search_log,
}


def handle_request(request: dict):
    """处理单个 JSON-RPC 请求"""
    req_id = request.get("id")
    method = request.get("method", "")

    if method == "initialize":
        return send_response(req_id, {
            "protocolVersion": "2024-11-05",
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            "capabilities": {"tools": {}}
        })

    elif method == "notifications/initialized":
        # 不需要回复
        pass

    elif method == "tools/list":
        return send_response(req_id, {"tools": TOOLS})

    elif method == "tools/call":
        params = request.get("params", {})
        tool_name = params.get("name", "")
        tool_args = params.get("arguments", {})

        handler = TOOL_HANDLERS.get(tool_name)
        if not handler:
            return send_error(req_id, -32601, f"未知工具: {tool_name}")

        try:
            result_text = handler(tool_args)
            return send_response(req_id, {
                "content": [{"type": "text", "text": result_text}]
            })
        except Exception as e:
            log_stderr(f"工具执行错误 [{tool_name}]: {e}")
            return send_error(req_id, -32603, str(e))

    else:
        return send_error(req_id, -32601, f"未知方法: {method}")


def main():
    log_stderr(f"{SERVER_NAME} v{SERVER_VERSION} 启动")
    log_stderr("等待 Codex 连接...")

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
            handle_request(request)
        except json.JSONDecodeError as e:
            log_stderr(f"JSON 解析错误: {e}")
        except Exception as e:
            log_stderr(f"未处理错误: {e}")


if __name__ == "__main__":
    main()

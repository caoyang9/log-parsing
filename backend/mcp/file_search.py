#!/usr/bin/env python3
"""MCP Server: 文本/代码搜索，返回匹配行及上下文."""
import sys, json, re, os
from pathlib import Path

def send(msg):
    sys.stdout.write(json.dumps(msg, ensure_ascii=False) + "\n")
    sys.stdout.flush()

TOOLS = [{
    "name": "grep_files",
    "description": "在指定目录下搜索匹配模式的文件内容，返回文件名、行号和上下文。",
    "inputSchema": {
        "type": "object",
        "properties": {
            "directory": {"type": "string", "description": "搜索目录路径"},
            "pattern": {"type": "string", "description": "正则表达式"},
            "file_glob": {"type": "string", "description": "文件名匹配，如 *.py", "default": "*"},
            "max_results": {"type": "integer", "description": "最大结果数", "default": 20}
        },
        "required": ["directory", "pattern"]
    }
}]

for line in sys.stdin:
    req = json.loads(line.strip())
    rid, method = req.get("id"), req.get("method", "")

    if method == "initialize":
        send({"jsonrpc":"2.0","id":rid,"result":{"protocolVersion":"2024-11-05","serverInfo":{"name":"file-search","version":"0.1"},"capabilities":{"tools":{}}}})
    elif method == "tools/list":
        send({"jsonrpc":"2.0","id":rid,"result":{"tools":TOOLS}})
    elif method == "tools/call":
        args = req["params"]["arguments"]
        directory = args["directory"]
        pattern = args["pattern"]
        file_glob = args.get("file_glob", "*")
        max_results = args.get("max_results", 20)

        if not os.path.isdir(directory):
            send({"jsonrpc":"2.0","id":rid,"result":{"content":[{"type":"text","text":f"错误：目录不存在 — {directory}"}]}})
            continue

        try:
            regex = re.compile(pattern)
        except re.error as e:
            send({"jsonrpc":"2.0","id":rid,"result":{"content":[{"type":"text","text":f"正则错误: {e}"}]}})
            continue

        results = []
        for fpath in Path(directory).rglob(file_glob):
            if not fpath.is_file():
                continue
            try:
                with open(fpath, errors='replace') as f:
                    for i, ln in enumerate(f, 1):
                        if regex.search(ln):
                            results.append(f"{fpath}:{i}: {ln.rstrip()}")
                            if len(results) >= max_results:
                                break
                if len(results) >= max_results:
                    break
            except:
                continue

        text = f"搜索 '{pattern}' 在 {directory}（{file_glob}）: {len(results)} 条结果\n\n" + "\n".join(results) if results else f"未找到匹配 '{pattern}'"
        send({"jsonrpc":"2.0","id":rid,"result":{"content":[{"type":"text","text":text}]}})

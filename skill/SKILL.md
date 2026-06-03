---
name: log-parsing
description: Android/HarmonyOS logcat analysis. Use when analyzing log files (.log, .txt, bugreport). Detects ANR, Crash, OOM, Binder, GC, WakeLock issues and generates AI-powered structured reports.
---

# Log Parsing Skill

## Analyze a Log File

```bash
/Library/Frameworks/Python.framework/Versions/3.8/bin/python3 \
  /Users/gnayoac/codex/log-parsing/skill/scripts/analyze.py \
  /path/to/logfile.log
```

This runs preprocessing + AI analysis in one command. Output is Markdown to stdout, also saved as `<filename>.report.md`.

## Quick Stats (no AI)

For a fast overview without AI, use the MCP server:

```bash
/Users/gnayoac/codex/log-parsing/backend/mcp_call.sh \
  /Users/gnayoac/codex/log-parsing/backend/mcp_wrapper.sh \
  quick_stats path=/path/to/logfile.log
```

## Search Logs

```bash
/Users/gnayoac/codex/log-parsing/backend/mcp_call.sh \
  /Users/gnayoac/codex/log-parsing/backend/mcp_wrapper.sh \
  search_log path=/path/to/logfile.log keyword=Error max_results=10
```

## Backend Web UI (optional)

Start the FastAPI server for browser-based upload and analysis:

```bash
cd /Users/gnayoac/codex/log-parsing/backend && \
  /Library/Frameworks/Python.framework/Versions/3.8/bin/python3 -m uvicorn main:app --host 0.0.0.0 --port 8080 &
```

Then open http://localhost:8080 in the browser.

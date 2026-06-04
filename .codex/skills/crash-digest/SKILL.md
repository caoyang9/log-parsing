---
name: crash-digest
description: Extract crash/ANR/tombstone summaries from log files without AI. Use when user needs quick crash overview, stack trace extraction, or crash statistics. Triggered by "crash digest", "crash summary", "崩溃概览", "崩溃统计", "提取堆栈".
---

# Crash Digest

Quick crash/ANR extraction from log files, no AI involved.

## Usage

```bash
/Library/Frameworks/Python.framework/Versions/3.8/bin/python3 \
  /Users/gnayoac/codex/log-parsing/.codex/skills/crash-digest/scripts/digest.py \
  /path/to/logfile.log
```

Output is a Markdown table of all crashes, ANRs, and tombstones with stack trace samples.

#!/usr/bin/env python3
"""Crash Digest — 快速提取日志中的崩溃/ANR/Tombstone，不调 AI。"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..', 'backend', 'core'))

from preprocess import preprocess

def digest(filepath: str) -> str:
    if not os.path.exists(filepath):
        return f"❌ 文件不存在: {filepath}"

    pr = preprocess(filepath)
    lines: list[str] = []
    fname = os.path.basename(filepath)

    fatal = len(pr.fatal_lines)
    crashes = pr.java_crashes
    anrs = pr.anr_blocks
    tombs = pr.native_tombstones
    total = len(crashes) + len(anrs) + len(tombs) + fatal

    lines.append(f"## 💥 Crash Digest: {fname}")
    lines.append(f"**总行数**: {pr.total_lines:,}  |  **时间**: {pr.first_timestamp} ~ {pr.last_timestamp}")
    lines.append("")

    if total == 0:
        lines.append("✅ **未发现崩溃/ANR/Tombstone/Fatal**")
        return "\n".join(lines)

    # 统计表格
    lines.append("| 类型 | 包 | 异常 / 原因 | 次数 |")
    lines.append("|------|-----|-------------|------|")

    # Count crashes by exception + package
    crash_counts: dict[str, dict] = {}
    for c in crashes:
        exc = c.get("exception", "Unknown")
        pkg = c.get("package", "unknown")
        key = f"{pkg}|{exc}"
        if key not in crash_counts:
            crash_counts[key] = {"package": pkg, "exception": exc, "count": 0, "sample": c.get("lines", "")[:300]}
        crash_counts[key]["count"] += 1

    for c in sorted(crash_counts.values(), key=lambda x: x["count"], reverse=True):
        lines.append(f"| Java | {c['package']} | {c['exception']} | {c['count']} |")

    # Count ANRs
    anr_counts: dict[str, dict] = {}
    for a in anrs:
        # Extract package from ANR block
        import re
        m = re.search(r'in (\S+)', a)
        pkg = m.group(1) if m else "unknown"
        m2 = re.search(r'reason:?\s*(.+?)(?:\n|$)', a, re.IGNORECASE)
        reason = m2.group(1).strip() if m2 else "unknown"
        if pkg not in anr_counts:
            anr_counts[pkg] = {"package": pkg, "reason": reason, "count": 0}
        anr_counts[pkg]["count"] += 1

    for a in sorted(anr_counts.values(), key=lambda x: x["count"], reverse=True):
        lines.append(f"| ANR | {a['package']} | {a['reason']} | {a['count']} |")

    # Tombstones
    if tombs:
        lines.append(f"| Tombstone | - | Native crash (signal) | {len(tombs)} |")

    # Fatal lines
    if fatal:
        lines.append(f"| Fatal | - | Fatal log lines | {fatal} |")

    lines.append("")

    # 关键堆栈详情
    if crash_counts:
        lines.append("### 📋 Java 异常堆栈")
        for c in sorted(crash_counts.values(), key=lambda x: x["count"], reverse=True):
            lines.append(f"**{c['package']}** — {c['exception']} (×{c['count']})")
            lines.append("```")
            sample_lines = c["sample"].strip().split("\n")[:6]
            for sl in sample_lines:
                lines.append(f"  {sl.strip()[:200]}")
            lines.append("```")
            lines.append("")

    if anr_counts:
        lines.append("### ⏱ ANR 详情")
        for a in sorted(anr_counts.values(), key=lambda x: x["count"], reverse=True):
            snippet = a.get("reason", "unknown")[:200]
            lines.append(f"- **{a['package']}**: {snippet} (×{a['count']})")
        lines.append("")

    if fatal:
        lines.append(f"### 🔴 Fatal 行 (前 5 条)")
        for fl in pr.fatal_lines[:5]:
            lines.append(f"- `{fl[:300]}`")
        lines.append("")

    lines.append(f"---")
    lines.append(f"⏱ 无 AI 分析  |  {pr.total_lines:,} 行  |  {total} 个问题")

    return "\n".join(lines)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python3 digest.py <日志路径>")
        sys.exit(1)
    print(digest(sys.argv[1]))

#!/usr/bin/env python3
"""分析日志文件并输出 Markdown 报告。用法: python3 analyze.py <log_path>"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'backend', 'core'))

from pathlib import Path
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), '..', '..', 'backend', '.env'))

from preprocess import preprocess
from ai_parser import analyze_log, build_report

def main():
    if len(sys.argv) < 2:
        print("用法: python3 analyze.py <日志文件路径>")
        sys.exit(1)

    path = sys.argv[1]
    if not os.path.exists(path):
        print(f"错误：文件不存在 — {path}")
        sys.exit(1)

    print(f"预处理中...", file=sys.stderr)
    pr = preprocess(path)
    print(f"完成: {pr.total_lines} 行, ANR={len(pr.anr_blocks)}, Crash={len(pr.java_crashes)}", file=sys.stderr)

    api_key = os.getenv("OPENAI_API_KEY", "")
    if not api_key:
        print("错误：未配置 OPENAI_API_KEY")
        sys.exit(1)

    model = os.getenv("OPENAI_MODEL", "deepseek-chat")
    base_url = os.getenv("OPENAI_BASE_URL", "")

    print(f"AI 分析中...", file=sys.stderr)
    try:
        ai_result = analyze_log(pr.llm_input, api_key, model, base_url)
    except Exception as e:
        print(f"AI 错误: {e}", file=sys.stderr)
        sys.exit(1)

    report = build_report(os.path.basename(path), os.path.basename(path), pr, ai_result)

    # 输出 Markdown
    print(f"# {os.path.basename(path)} 分析报告\n")
    print(f"**总行数**: {report.total_lines:,}  |  **时间范围**: {report.time_range}")
    print(f"**Fatal**: {report.level_counts.get('F',0)}  |  **Error**: {report.level_counts.get('E',0)}  |  **Warning**: {report.level_counts.get('W',0)}")
    print(f"**ANR**: {len(report.anrs)}  |  **崩溃**: {len(report.crashes)}\n")

    print(f"## 总结\n{report.summary}\n")

    if report.critical_issues:
        print("## 关键问题")
        for ci in report.critical_issues:
            print(f"- **[{ci.severity}] {ci.title}**")
            print(f"  {ci.description}")
            print(f"  → {ci.suggestion}\n")

    if report.crashes:
        print("## 崩溃详情")
        for c in report.crashes:
            print(f"- **{c.package}**: {c.exception} (×{c.count})")

    if report.recommendations:
        print("## 修复建议")
        for i, r in enumerate(report.recommendations, 1):
            print(f"{i}. {r}")

    # 保存到文件
    out_path = path + ".report.md"
    with open(out_path, "w") as f:
        f.write(f"# {os.path.basename(path)} 分析报告\n\n")
        f.write(report.summary + "\n")
    print(f"\n报告已保存: {out_path}", file=sys.stderr)

if __name__ == "__main__":
    main()

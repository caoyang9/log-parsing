from __future__ import annotations
"""
Android Logcat 日志解析服务
FastAPI 后端入口，同时 serve 前端静态文件
"""

import os
import uuid
import shutil
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))

from dotenv import load_dotenv

load_dotenv(str(Path(__file__).resolve().parent.parent / ".env"))

from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, StreamingResponse

from models import UploadResponse, ParseReport, ParseStatus, CompareRequest, TextDiffResult, AICompareResult, DiffItem
from preprocess import preprocess
from ai_parser import analyze_log, build_report
from comparer import text_diff, ai_compare


app = FastAPI(title="Android Logcat Parser", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# 目录
BASE_DIR = Path(os.path.dirname(os.path.abspath(__file__))).parent
UPLOAD_DIR = BASE_DIR / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)
import sys

FRONTEND_DIR = BASE_DIR.parent / "frontend"

# 缓存
report_cache: dict[str, ParseReport] = {}
parse_status: dict[str, ParseStatus] = {}


@app.get("/")
def serve_frontend():
    """Serve 前端页面"""
    return FileResponse(FRONTEND_DIR / "index.html")


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.post("/api/upload", response_model=UploadResponse)
async def upload_file(file: UploadFile = File(...)):
    """上传日志文件"""
    if not file.filename:
        raise HTTPException(400, "文件名不能为空")

    file_id = uuid.uuid4().hex[:12]
    ext = Path(file.filename).suffix or ".log"
    save_name = f"{file_id}{ext}"
    save_path = UPLOAD_DIR / save_name

    with open(save_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    file_size = save_path.stat().st_size

    parse_status[file_id] = ParseStatus(
        file_id=file_id, status="pending", progress="文件已上传，等待解析"
    )

    return UploadResponse(file_id=file_id, file_name=file.filename, file_size=file_size)


@app.post("/api/parse/{file_id}", response_model=ParseReport)
def parse_log(file_id: str):
    """解析日志文件"""
    matches = list(UPLOAD_DIR.glob(f"{file_id}.*"))
    if not matches:
        raise HTTPException(404, f"文件 {file_id} 不存在")
    filepath = str(matches[0])
    filename = matches[0].name

    parse_status[file_id] = ParseStatus(
        file_id=file_id, status="processing", progress="正在预处理日志..."
    )

    # 1. 预处理
    pr = preprocess(filepath)

    parse_status[file_id] = ParseStatus(
        file_id=file_id,
        status="processing",
        progress=f"预处理完成（{pr.total_lines} 行），正在 AI 分析..."
    )

    # 2. AI 分析
    api_key = os.getenv("OPENAI_API_KEY", "")
    if not api_key:
        parse_status[file_id] = ParseStatus(
            file_id=file_id, status="error", progress="未配置 OPENAI_API_KEY"
        )
        raise HTTPException(500, "未配置 OPENAI_API_KEY，请在 backend/.env 文件中设置")

    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    base_url = os.getenv("OPENAI_BASE_URL", "")

    try:
        ai_result = analyze_log(pr.llm_input, api_key, model, base_url)
    except Exception as e:
        parse_status[file_id] = ParseStatus(
            file_id=file_id, status="error", progress=f"AI 分析失败: {str(e)}"
        )
        raise HTTPException(500, f"AI 分析失败: {str(e)}")

    # 3. 构建报告
    report = build_report(file_id, filename, pr, ai_result)
    report_cache[file_id] = report

    parse_status[file_id] = ParseStatus(
        file_id=file_id, status="done", progress="分析完成"
    )

    return report


@app.get("/api/report/{file_id}", response_model=ParseReport)
def get_report(file_id: str):
    """获取缓存的报告"""
    if file_id not in report_cache:
        raise HTTPException(404, f"报告 {file_id} 不存在")
    return report_cache[file_id]


@app.get("/api/status/{file_id}", response_model=ParseStatus)
def get_status(file_id: str):
    """获取处理状态"""
    if file_id not in parse_status:
        raise HTTPException(404, f"状态不存在")
    return parse_status[file_id]


def _report_to_markdown(report: ParseReport) -> str:
    """将 ParseReport 转换为 Markdown 文本，供下载"""
    lines = []
    lines.append(f"# Android Logcat 分析报告")
    lines.append(f"**文件**: {report.file_name}")
    lines.append(f"**总行数**: {report.total_lines}")
    lines.append(f"**时间范围**: {report.time_range}")
    lines.append("")
    lines.append(f"## 日志概览")
    lines.append(f"| 级别 | 数量 |")
    lines.append(f"|------|------|")
    for lv in ["F", "E", "W", "I", "D", "V"]:
        lines.append(f"| {lv} | {report.level_counts.get(lv, 0)} |")
    lines.append("")
    lines.append(f"## 分析总结")
    lines.append(report.summary)
    lines.append("")

    if report.critical_issues:
        lines.append("## 关键问题")
        for ci in report.critical_issues:
            lines.append(f"### [{ci.severity}] {ci.title}")
            lines.append(f"- **描述**: {ci.description}")
            lines.append(f"- **建议**: {ci.suggestion}")
            lines.append("")

    if report.crashes:
        lines.append("## 崩溃详情")
        for c in report.crashes:
            lines.append(f"### {c.package} — {c.exception} (×{c.count})")
            lines.append("```")
            lines.append(c.sample)
            lines.append("```")
            lines.append("")

    if report.anrs:
        lines.append("## ANR 详情")
        for a in report.anrs:
            lines.append(f"- **{a.package}**: {a.reason} (×{a.count})")
        lines.append("")

    if report.recommendations:
        lines.append("## 修复建议")
        for i, r in enumerate(report.recommendations, 1):
            lines.append(f"{i}. {r}")
        lines.append("")

    if report.top_tags:
        lines.append("## Top 10 TAG")
        lines.append("| TAG | 数量 |")
        lines.append("|-----|------|")
        for t in report.top_tags[:10]:
            lines.append(f"| {t['tag']} | {t['count']} |")
        lines.append("")

    return "\n".join(lines)


@app.get("/api/report/{file_id}/download")
def download_report(file_id: str):
    """下载分析报告为 Markdown 文件"""
    if file_id not in report_cache:
        raise HTTPException(404, f"报告 {file_id} 不存在")
    report = report_cache[file_id]
    md = _report_to_markdown(report)
    safe_name = report.file_name.rsplit(".", 1)[0]
    return StreamingResponse(
        iter([md]),
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{safe_name}_report.md"'},
    )



# === 文件对比 ===

@app.post("/api/compare/text", response_model=TextDiffResult)
def compare_text(req: CompareRequest):
    """逐行文本对比两个日志文件"""
    m1 = list(UPLOAD_DIR.glob(f"{req.file_id_1}.*"))
    m2 = list(UPLOAD_DIR.glob(f"{req.file_id_2}.*"))
    if not m1:
        raise HTTPException(404, f"文件 {req.file_id_1} 不存在")
    if not m2:
        raise HTTPException(404, f"文件 {req.file_id_2} 不存在")

    diff = text_diff(str(m1[0]), str(m2[0]), m1[0].name, m2[0].name)
    return TextDiffResult(
        file_1_name=m1[0].name,
        file_2_name=m2[0].name,
        **diff,
    )


@app.post("/api/compare/ai", response_model=AICompareResult)
def compare_ai(req: CompareRequest):
    """AI 智能对比两个日志文件"""
    m1 = list(UPLOAD_DIR.glob(f"{req.file_id_1}.*"))
    m2 = list(UPLOAD_DIR.glob(f"{req.file_id_2}.*"))
    if not m1:
        raise HTTPException(404, f"文件 {req.file_id_1} 不存在")
    if not m2:
        raise HTTPException(404, f"文件 {req.file_id_2} 不存在")

    # 预处理两个文件
    pr1 = preprocess(str(m1[0]))
    pr2 = preprocess(str(m2[0]))

    # AI 分析
    api_key = os.getenv("OPENAI_API_KEY", "")
    if not api_key:
        raise HTTPException(500, "未配置 OPENAI_API_KEY")

    model = os.getenv("OPENAI_MODEL", "deepseek-chat")
    base_url = os.getenv("OPENAI_BASE_URL", "")

    try:
        result = ai_compare(pr1, pr2, api_key, model, base_url)
    except Exception as e:
        raise HTTPException(500, f"AI 对比失败: {str(e)}")

    return AICompareResult(
        summary=result.get("summary", ""),
        differences=[DiffItem(**d) for d in result.get("differences", [])],
        new_issues=result.get("new_issues", []),
        resolved_issues=result.get("resolved_issues", []),
        recommendations=result.get("recommendations", []),
    )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)

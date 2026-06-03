from pydantic import BaseModel, Field
from typing import List, Dict, Optional


class CriticalIssue(BaseModel):
    severity: str = Field(description="CRITICAL / HIGH / MEDIUM / LOW")
    title: str
    description: str
    suggestion: str


class CrashDetail(BaseModel):
    package: str
    exception: str
    count: int
    sample: str = Field(description="样例堆栈前N行")


class ANRDetail(BaseModel):
    package: str
    reason: str
    count: int


class ParseReport(BaseModel):
    file_id: str
    file_name: str
    total_lines: int
    time_range: str
    level_counts: Dict[str, int] = Field(description="{F: n, E: n, W: n, I: n, D: n, V: n}")
    top_tags: List[Dict[str, object]] = Field(description="[{tag, count}, ...]")
    top_crashing_packages: List[Dict[str, object]] = Field(description="[{package, count}, ...]")
    summary: str = Field(description="AI 一句话总结")
    critical_issues: List[CriticalIssue] = []
    crashes: List[CrashDetail] = []
    anrs: List[ANRDetail] = []
    recommendations: List[str] = []


class UploadResponse(BaseModel):
    file_id: str
    file_name: str
    file_size: int


class ParseStatus(BaseModel):
    file_id: str
    status: str = Field(description="pending / processing / done / error")
    progress: str = ""


# === 对比功能 ===

class CompareRequest(BaseModel):
    file_id_1: str
    file_id_2: str


class TextDiffResult(BaseModel):
    file_1_name: str
    file_2_name: str
    added_lines: int
    removed_lines: int
    total_diffs: int
    unified_diff: str


class DiffItem(BaseModel):
    category: str    # "error", "warning", "crash", "anr", "other"
    detail: str
    severity: str    # "increased", "decreased", "new", "resolved"


class AICompareResult(BaseModel):
    summary: str
    differences: List[DiffItem] = []
    new_issues: List[str] = []
    resolved_issues: List[str] = []
    recommendations: List[str] = []

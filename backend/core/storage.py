"""
持久化存储层
使用 JSON 文件存储文件列表和分析报告，服务重启后数据不丢失。
"""

import json
import os
import time
from pathlib import Path
from threading import Lock
from typing import Optional


STORAGE_DIR = Path(__file__).resolve().parent.parent / "storage"
FILES_JSON = STORAGE_DIR / "files.json"
REPORTS_DIR = STORAGE_DIR / "reports"
MAX_FILES = 100


class Storage:
    """单例持久化存储"""

    _instance = None
    _lock = Lock()

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True

        STORAGE_DIR.mkdir(parents=True, exist_ok=True)
        REPORTS_DIR.mkdir(exist_ok=True)

        # 文件索引: {file_id: {file_name, file_size, uploaded_at, status, progress}}
        self._files: dict = {}
        # 报告缓存: {file_id: ParseReport dict}
        self._reports: dict = {}
        # 解析状态缓存
        self._status: dict = {}

        self._load()

    def _load(self):
        """从磁盘加载已有数据"""
        if FILES_JSON.exists():
            try:
                with open(FILES_JSON) as f:
                    self._files = json.load(f)
            except (json.JSONDecodeError, IOError):
                self._files = {}

        # 加载已有报告
        if REPORTS_DIR.exists():
            for rp in REPORTS_DIR.glob("*.json"):
                try:
                    with open(rp) as f:
                        report = json.load(f)
                    fid = rp.stem
                    self._reports[fid] = report
                    if fid in self._files:
                        self._files[fid]["status"] = "done"
                except (json.JSONDecodeError, IOError):
                    pass

    def _save_files(self):
        """保存文件索引到磁盘"""
        with open(FILES_JSON, "w") as f:
            json.dump(self._files, f, ensure_ascii=False, indent=2)

    def _save_report(self, file_id: str):
        """保存单个报告到磁盘"""
        if file_id in self._reports:
            with open(REPORTS_DIR / f"{file_id}.json", "w") as f:
                json.dump(self._reports[file_id], f, ensure_ascii=False, indent=2, default=str)

    # ---- 文件管理 ----

    def add_file(self, file_id: str, file_name: str, file_size: int):
        """记录新上传的文件"""
        with self._lock:
            self._files[file_id] = {
                "file_id": file_id,
                "file_name": file_name,
                "file_size": file_size,
                "uploaded_at": time.time(),
                "status": "pending",
                "progress": "",
            }
            self._save_files()
            self._auto_cleanup()

    def list_files(self, sort_by: str = "uploaded_at") -> list:
        """返回文件列表，按时间倒序"""
        with self._lock:
            files = list(self._files.values())
            files.sort(key=lambda x: x.get(sort_by, 0), reverse=True)
            return files

    def get_file(self, file_id: str) -> Optional[dict]:
        """获取单个文件信息"""
        return self._files.get(file_id)

    def remove_file(self, file_id: str):
        """删除文件及相关数据"""
        with self._lock:
            self._files.pop(file_id, None)
            self._reports.pop(file_id, None)
            self._status.pop(file_id, None)
            self._save_files()
            # 删磁盘文件
            rp = REPORTS_DIR / f"{file_id}.json"
            if rp.exists():
                rp.unlink()

    def _auto_cleanup(self):
        """超过最大文件数时删除最旧的"""
        if len(self._files) <= MAX_FILES:
            return
        sorted_ids = sorted(
            self._files.keys(),
            key=lambda fid: self._files[fid].get("uploaded_at", 0)
        )
        to_remove = sorted_ids[: len(sorted_ids) - MAX_FILES]
        for fid in to_remove:
            self._files.pop(fid, None)
            self._reports.pop(fid, None)
            self._status.pop(fid, None)
            (REPORTS_DIR / f"{fid}.json").unlink(missing_ok=True)

    # ---- 状态管理 ----

    def set_status(self, file_id: str, status: str, progress: str = ""):
        """更新解析状态"""
        with self._lock:
            if file_id in self._files:
                self._files[file_id]["status"] = status
                self._files[file_id]["progress"] = progress
            self._status[file_id] = {"file_id": file_id, "status": status, "progress": progress}
            self._save_files()

    def get_status(self, file_id: str) -> Optional[dict]:
        """获取解析状态"""
        if file_id in self._status:
            return self._status[file_id]
        if file_id in self._files:
            return {
                "file_id": file_id,
                "status": self._files[file_id].get("status", "pending"),
                "progress": self._files[file_id].get("progress", ""),
            }
        return None

    # ---- 报告管理 ----

    def save_report(self, file_id: str, report: dict):
        """保存分析报告"""
        with self._lock:
            self._reports[file_id] = report
            if file_id in self._files:
                self._files[file_id]["status"] = "done"
            self._save_report(file_id)
            self._save_files()

    def get_report(self, file_id: str) -> Optional[dict]:
        """获取分析报告（优先内存，回退磁盘）"""
        if file_id in self._reports:
            return self._reports[file_id]
        rp = REPORTS_DIR / f"{file_id}.json"
        if rp.exists():
            try:
                with open(rp) as f:
                    report = json.load(f)
                self._reports[file_id] = report
                return report
            except (json.JSONDecodeError, IOError):
                pass
        return None

    def list_reports(self) -> list:
        """返回所有已完成的报告摘要"""
        with self._lock:
            results = []
            for fid, r in self._reports.items():
                finfo = self._files.get(fid, {})
                results.append({
                    "file_id": fid,
                    "file_name": finfo.get("file_name", r.get("file_name", "")),
                    "total_lines": r.get("total_lines", 0),
                    "summary": r.get("summary", "")[:200],
                    "critical_count": len(r.get("critical_issues", [])),
                    "analyzed_at": finfo.get("uploaded_at", 0),
                })
            results.sort(key=lambda x: x["analyzed_at"], reverse=True)
            return results


# 全局单例
storage = Storage()

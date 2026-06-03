from __future__ import annotations
"""
Android logcat 日志预处理管道
流式读取大文件，提取关键信息，智能采样供 LLM 分析
"""

import re
from collections import Counter
from dataclasses import dataclass, field


# logcat 标准格式: MM-DD HH:MM:SS.mmm  PID  TID LEVEL TAG: MESSAGE
LOGCAT_LINE_RE = re.compile(
    r"^(\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}\.\d+)\s+(\d+)\s+(\d+)\s+([FEWIDV])\s+(.+?)\s*:\s*(.*)$"
)

# === 致命异常 Fatal / Critical ===
ANR_START_RE = re.compile(r"ANR\s+in\s+(\S+)", re.IGNORECASE)
JAVA_EXCEPTION_RE = re.compile(r"(java\.[\w.]+(?:Exception|Error))(?::\s*(.*))?")
TOMBSTONE_MARKER = "*** *** ***"

# === 系统服务 System Services ===
WATCHDOG_RE = re.compile(r"(?:WATCHDOG|Watchdog)", re.IGNORECASE)
SYSTEM_SERVER_CRASH_RE = re.compile(r"system_server", re.IGNORECASE)

# === 内存 Memory ===
OOM_RE = re.compile(r"(?:Out\s+of\s+memory|OOM\s+killer|lowmemorykiller)", re.IGNORECASE)
GC_EVENT_RE = re.compile(r"GC_(?:CONCURRENT|FOR_ALLOC|EXPLICIT|BACKGROUND)", re.IGNORECASE)
EXCESSIVE_GC_RE = re.compile(r"Excessive.*GC|GC.*excessive", re.IGNORECASE)
HEAP_LIMIT_RE = re.compile(r"(?:dalvik|art).*heap.*limit|heap.*limit", re.IGNORECASE)
PROC_OOM_RE = re.compile(r"proc.*has died.*oom|kill.*oom", re.IGNORECASE)

# === Binder IPC Binder通信 ===
BINDER_FAIL_RE = re.compile(r"Binder.*transaction.*fail|FAILED.*BINDER.*TRANSACTION", re.IGNORECASE)
BINDER_DEAD_RE = re.compile(r"(?:Binder.*dead|FAILED.*REPLY|DEAD_OBJECT)", re.IGNORECASE)

# === 电量 Power / Battery ===
WAKELOCK_RE = re.compile(r"WakeLock.*(?:held for|acquired|timeout|released)", re.IGNORECASE)
ALARM_RE = re.compile(r"AlarmManager", re.IGNORECASE)

# === 显示 Display / Choreographer ===
CHOREOGRAPHER_RE = re.compile(r"Choreographer.*skipped\s+(\d+)\s+frames", re.IGNORECASE)
JANK_RE = re.compile(r"Jank", re.IGNORECASE)

# === Native 崩溃 ===
NATIVE_CRASH_RE = re.compile(r"(?:Native\s+crash|signal\s+\d+\s+\(SIG)", re.IGNORECASE)


@dataclass
class PreprocessResult:
    total_lines: int = 0
    level_counts: dict = field(default_factory=lambda: {"F": 0, "E": 0, "W": 0, "I": 0, "D": 0, "V": 0})
    error_lines: list[str] = field(default_factory=list)
    fatal_lines: list[str] = field(default_factory=list)
    anr_blocks: list[str] = field(default_factory=list)          # ANR 块
    java_crashes: list[dict] = field(default_factory=list)       # Java 异常堆栈
    native_tombstones: list[str] = field(default_factory=list)   # Native tombstone
    oom_events: list[str] = field(default_factory=list)          # OOM 事件
    watchdog_events: list[str] = field(default_factory=list)     # Watchdog 事件
    binder_issues: list[str] = field(default_factory=list)       # Binder 通信异常
    gc_events: list[str] = field(default_factory=list)           # GC 事件
    wakelock_issues: list[str] = field(default_factory=list)     # WakeLock 相关问题
    choreographer_issues: list[str] = field(default_factory=list) # 丢帧/卡顿
    system_server_lines: list[str] = field(default_factory=list) # system_server 相关行
    tag_counter: Counter = field(default_factory=Counter)
    crash_package_counter: Counter = field(default_factory=Counter)
    first_timestamp: str = ""
    last_timestamp: str = ""
    raw_lines: int = 0
    llm_input: str = ""


def preprocess(filepath: str, max_llm_chars: int = 80000) -> PreprocessResult:
    """
    流式读取 logcat 文件，提取关键信息。
    max_llm_chars: LLM 输入的最大字符数（约 40K tokens）
    """
    result = PreprocessResult()

    in_anr = False
    in_java_exception = False
    in_tombstone = False
    current_block: list[str] = []
    current_exception_name = ""

    # 收集上限，防止超大日志撑爆内存
    MAX_ERROR_LINES = 5000
    MAX_FATAL_LINES = 500
    MAX_ANR_BLOCKS = 100
    MAX_JAVA_CRASHES = 200
    MAX_TOMBSTONES = 50
    MAX_OOM = 200
    MAX_WATCHDOG = 200
    MAX_BINDER = 200
    MAX_GC = 200
    MAX_WAKELOCK = 200
    MAX_CHOREOGRAPHER = 200
    MAX_SYSTEM_SERVER = 500

    with open(filepath, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.rstrip("\n\r")
            result.total_lines += 1

            m = LOGCAT_LINE_RE.match(line)
            if m:
                ts, pid, tid, level, tag, msg = m.groups()
                if not result.first_timestamp:
                    result.first_timestamp = ts
                result.last_timestamp = ts

                if level in result.level_counts:
                    result.level_counts[level] += 1

                result.tag_counter[tag.strip()] += 1

                # --- ERROR / FATAL 收集 ---
                if level == "F" and len(result.fatal_lines) < MAX_FATAL_LINES:
                    result.fatal_lines.append(line)
                elif level == "E" and len(result.error_lines) < MAX_ERROR_LINES:
                    result.error_lines.append(line)

                # --- ANR 检测 ---
                if ANR_START_RE.search(msg):
                    if in_java_exception:
                        _flush_java_exception(result, current_block, current_exception_name)
                        in_java_exception = False
                    if in_tombstone:
                        _flush_tombstone(result, current_block)
                        in_tombstone = False
                    in_anr = True
                    current_block = [line]
                    pkg_match = ANR_START_RE.search(msg)
                    if pkg_match:
                        result.crash_package_counter[pkg_match.group(1)] += 1
                elif in_anr:
                    current_block.append(line)
                    if not line.strip():
                        _flush_anr(result, current_block)
                        in_anr = False
                        current_block = []

                # --- Java 异常检测 ---
                exc_match = JAVA_EXCEPTION_RE.search(msg)
                if exc_match and not in_anr:
                    if in_java_exception:
                        _flush_java_exception(result, current_block, current_exception_name)
                    in_java_exception = True
                    current_exception_name = exc_match.group(1)
                    current_block = [line]
                elif in_java_exception:
                    stripped_msg = msg.strip()
                    if stripped_msg.startswith("at ") or stripped_msg.startswith("Caused by:"):
                        current_block.append(line)
                    elif not stripped_msg:
                        if current_block:
                            _flush_java_exception(result, current_block, current_exception_name)
                        in_java_exception = False
                        current_block = []
                    else:
                        _flush_java_exception(result, current_block, current_exception_name)
                        in_java_exception = False
                        current_block = []

                # --- 新增：各类事件检测（在 logcat 行内） ---

                # Binder 通信异常
                if (BINDER_FAIL_RE.search(msg) or BINDER_DEAD_RE.search(msg)) and len(result.binder_issues) < MAX_BINDER:
                    result.binder_issues.append(line)

                # GC 事件
                if GC_EVENT_RE.search(msg) and len(result.gc_events) < MAX_GC:
                    result.gc_events.append(line)
                if EXCESSIVE_GC_RE.search(msg) and len(result.gc_events) < MAX_GC:
                    result.gc_events.append(line)

                # WakeLock 问题
                if WAKELOCK_RE.search(msg) and len(result.wakelock_issues) < MAX_WAKELOCK:
                    result.wakelock_issues.append(line)

                # Choreographer 丢帧
                if CHOREOGRAPHER_RE.search(msg) and len(result.choreographer_issues) < MAX_CHOREOGRAPHER:
                    result.choreographer_issues.append(line)

                # system_server 相关
                if SYSTEM_SERVER_CRASH_RE.search(tag) and len(result.system_server_lines) < MAX_SYSTEM_SERVER:
                    result.system_server_lines.append(line)

                # OOM / Watchdog / Heap Limit
                if OOM_RE.search(msg) and len(result.oom_events) < MAX_OOM:
                    result.oom_events.append(line)
                if HEAP_LIMIT_RE.search(msg) and len(result.oom_events) < MAX_OOM:
                    result.oom_events.append(line)
                if PROC_OOM_RE.search(msg) and len(result.oom_events) < MAX_OOM:
                    result.oom_events.append(line)
                if WATCHDOG_RE.search(msg) and len(result.watchdog_events) < MAX_WATCHDOG:
                    result.watchdog_events.append(line)

            else:
                # 非标准 logcat 行
                result.raw_lines += 1

                # Native tombstone
                if TOMBSTONE_MARKER in line:
                    if in_java_exception:
                        _flush_java_exception(result, current_block, current_exception_name)
                        in_java_exception = False
                    in_tombstone = True
                    current_block = [line]
                elif in_tombstone:
                    current_block.append(line)
                    if not line.strip() and len(current_block) > 3:
                        _flush_tombstone(result, current_block)
                        in_tombstone = False
                        current_block = []

                # Native crash 信号行
                if NATIVE_CRASH_RE.search(line) and len(result.native_tombstones) < MAX_TOMBSTONES:
                    result.native_tombstones.append(line)

    # 刷新尾部未完成块
    if in_anr and current_block:
        _flush_anr(result, current_block)
    if in_java_exception and current_block:
        _flush_java_exception(result, current_block, current_exception_name)
    if in_tombstone and current_block:
        _flush_tombstone(result, current_block)

    result.llm_input = _build_llm_input(result, max_llm_chars)
    return result


def _flush_anr(result: PreprocessResult, block: list[str]) -> None:
    if len(result.anr_blocks) < 100:
        result.anr_blocks.append("\n".join(block))


def _flush_java_exception(result: PreprocessResult, block: list[str], exc_name: str) -> None:
    if len(result.java_crashes) < 200:
        result.java_crashes.append({
            "exception": exc_name,
            "lines": "\n".join(block),
        })


def _flush_tombstone(result: PreprocessResult, block: list[str]) -> None:
    if len(result.native_tombstones) < 50:
        result.native_tombstones.append("\n".join(block))


def _build_llm_input(result: PreprocessResult, max_chars: int) -> str:
    """智能采样，构建给 LLM 的输入文本"""
    parts: list[str] = []
    used = 0

    def add(text: str) -> bool:
        nonlocal used
        if used + len(text) > max_chars:
            if used < max_chars - 20:
                parts.append(text[:max_chars - used - 20] + "\n... [截断]")
                used = max_chars
            return False
        parts.append(text)
        used += len(text)
        return True

    time_range = f"{result.first_timestamp} ~ {result.last_timestamp}" if result.first_timestamp else "未知"
    overview = f"""## 日志概览
总行数: {result.total_lines}
时间范围: {time_range}
各级别: F={result.level_counts['F']} E={result.level_counts['E']} W={result.level_counts['W']} I={result.level_counts['I']} D={result.level_counts['D']} V={result.level_counts['V']}
ANR: {len(result.anr_blocks)} | Java异常: {len(result.java_crashes)} | Native Tombstone: {len(result.native_tombstones)}
OOM: {len(result.oom_events)} | Watchdog: {len(result.watchdog_events)} | Binder异常: {len(result.binder_issues)}
GC事件: {len(result.gc_events)} | WakeLock: {len(result.wakelock_issues)} | 丢帧: {len(result.choreographer_issues)}
原始格式行: {result.raw_lines}
"""
    add(overview)

    # Top TAG
    top_tags = result.tag_counter.most_common(20)
    if top_tags:
        add("\n## Top 20 TAG\n")
        for tag, cnt in top_tags:
            add(f"  {tag}: {cnt}\n")

    # FATAL 行（全部保留）
    if result.fatal_lines:
        add(f"\n## FATAL 行 ({len(result.fatal_lines)})\n")
        for fl in result.fatal_lines:
            if not add(fl + "\n"):
                break

    # ANR 块（全部保留）
    if result.anr_blocks:
        add(f"\n## ANR 块 ({len(result.anr_blocks)})\n")
        for block in result.anr_blocks:
            if not add(block + "\n---\n"):
                break

    # Java 异常（全部保留）
    if result.java_crashes:
        add(f"\n## Java 异常 ({len(result.java_crashes)})\n")
        for jc in result.java_crashes:
            if not add(jc["lines"] + "\n---\n"):
                break

    # Native Tombstone（全部保留）
    if result.native_tombstones:
        add(f"\n## Native Tombstone ({len(result.native_tombstones)})\n")
        for tb in result.native_tombstones:
            if not add(tb + "\n---\n"):
                break

    # Binder 通信异常
    if result.binder_issues:
        add(f"\n## Binder 通信异常 ({len(result.binder_issues)})\n")
        for ev in result.binder_issues[:50]:
            if not add(ev + "\n"):
                break

    # system_server 相关
    if result.system_server_lines:
        add(f"\n## system_server 相关行 ({len(result.system_server_lines)})\n")
        for ev in result.system_server_lines[:30]:
            if not add(ev + "\n"):
                break

    # OOM / Watchdog
    if result.oom_events:
        add(f"\n## OOM / 内存事件 ({len(result.oom_events)})\n")
        for ev in result.oom_events[:50]:
            if not add(ev + "\n"):
                break
    if result.watchdog_events:
        add(f"\n## Watchdog 事件 ({len(result.watchdog_events)})\n")
        for ev in result.watchdog_events[:50]:
            if not add(ev + "\n"):
                break

    # GC 事件（采样）
    if result.gc_events:
        sample_n = min(len(result.gc_events), 30)
        add(f"\n## GC 事件采样 ({sample_n}/{len(result.gc_events)})\n")
        for ev in result.gc_events[:sample_n]:
            if not add(ev + "\n"):
                break

    # WakeLock 问题
    if result.wakelock_issues:
        add(f"\n## WakeLock 问题 ({len(result.wakelock_issues)})\n")
        for ev in result.wakelock_issues[:30]:
            if not add(ev + "\n"):
                break

    # Choreographer 丢帧
    if result.choreographer_issues:
        add(f"\n## 丢帧/卡顿 ({len(result.choreographer_issues)})\n")
        for ev in result.choreographer_issues[:30]:
            if not add(ev + "\n"):
                break

    # ERROR 行（去重采样）
    if result.error_lines:
        remaining = max_chars - used
        if remaining > 500:
            seen = set()
            sampled: list[str] = []
            for el in result.error_lines:
                key = el[:80]
                if key not in seen:
                    seen.add(key)
                    sampled.append(el)
                if len(sampled) >= 100:
                    break
            add(f"\n## ERROR 行采样 ({len(sampled)} 条去重后)\n")
            for el in sampled:
                if not add(el + "\n"):
                    break

    return "".join(parts)

"""VTT 字幕解析:去除 YouTube 自動字幕的滾動重複,輸出帶時間戳的純文字。"""
import re
from pathlib import Path

_TS_LINE = re.compile(
    r"(\d{2}):(\d{2}):(\d{2})[.,](\d{3})\s*-->\s*\d{2}:\d{2}:\d{2}[.,]\d{3}"
)
_TAG = re.compile(r"<[^>]+>")


def _fmt_ts(seconds: float) -> str:
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h:d}:{m:02d}:{sec:02d}" if h else f"{m:d}:{sec:02d}"


def parse_vtt(path: Path) -> list[tuple[float, str]]:
    """解析 VTT → [(開始秒數, 文字)],已去重。

    保留 cue 內的行結構再逐行去重:YouTube 滾動字幕的典型格式是
    每個 cue 帶「上一行 + 新的一行」,必須按行比對才能去掉重複。"""
    cues: list[tuple[float, list[str]]] = []
    start: float | None = None
    buf: list[str] = []

    def flush():
        nonlocal start, buf
        if start is not None and buf:
            lines = [l for l in buf if l.strip()]
            if lines:
                cues.append((start, lines))
        start, buf = None, []

    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        m = _TS_LINE.match(line)
        if m:
            flush()
            h, mnt, s, ms = (int(x) for x in m.groups())
            start = h * 3600 + mnt * 60 + s + ms / 1000
            continue
        if not line or line == "WEBVTT" or line.startswith(("NOTE", "Kind:", "Language:", "STYLE")):
            continue
        if start is None:
            continue
        text = _TAG.sub("", line).strip()
        if text:
            buf.append(text)
    flush()

    # YouTube 自動字幕會把上一句重複帶到下一個 cue,逐行去重
    deduped: list[tuple[float, str]] = []
    prev_lines: set[str] = set()
    for ts, lines in cues:
        fresh = [l for l in lines if l not in prev_lines]
        prev_lines = set(lines)
        if fresh:
            joined = " ".join(fresh).strip()
            if deduped and deduped[-1][1] == joined:
                continue
            deduped.append((ts, joined))
    return deduped


def to_transcript(cues: list[tuple[float, str]], marker_interval: int = 30) -> str:
    """輸出純文字逐字稿,每隔 marker_interval 秒插入一個 [時間] 標記。"""
    out: list[str] = []
    next_marker = 0.0
    for ts, text in cues:
        if ts >= next_marker:
            out.append(f"\n[{_fmt_ts(ts)}]")
            next_marker = ts + marker_interval
        out.append(text)
    return " ".join(out).replace("\n ", "\n").strip()

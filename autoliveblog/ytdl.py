"""yt-dlp 封裝:影片資訊、字幕、音訊下載、直播串流網址。"""
import random
import tempfile
import threading
import time
from pathlib import Path

import yt_dlp

from . import config

SUB_LANGS = ["zh-TW", "zh-Hant", "zh", "zh-Hans", "en", "ja"]

# 全域節流:所有 yt-dlp 操作至少間隔 2~3 秒,降低被 YouTube 限流的風險
_THROTTLE_LOCK = threading.Lock()
_last_call = 0.0


def _throttle():
    global _last_call
    with _THROTTLE_LOCK:
        wait = _last_call + 2 + random.random() - time.time()
        if wait > 0:
            time.sleep(wait)
        _last_call = time.time()


def _base_opts(cookies_from_browser: str | None = None) -> dict:
    opts = {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
    }
    if config.FFMPEG:
        opts["ffmpeg_location"] = str(Path(config.FFMPEG).parent)
    if cookies_from_browser:
        opts["cookiesfrombrowser"] = (cookies_from_browser,)
    return opts


def get_info(url: str, cookies_from_browser: str | None = None) -> dict:
    """抓取影片/直播的 metadata(不下載)。"""
    _throttle()
    with yt_dlp.YoutubeDL(_base_opts(cookies_from_browser)) as ydl:
        return ydl.extract_info(url, download=False)


def download_subtitles(url: str, out_dir: Path,
                       cookies_from_browser: str | None = None) -> Path | None:
    """下載官方或自動字幕(vtt),回傳檔案路徑;沒有字幕回傳 None。

    挑選順序:影片原文軌 → 人工上傳字幕 → 自動(機翻)字幕。
    這個順序很重要:YouTube 為每支影片產生 150+ 個機翻軌,若照偏好語言排序,
    英文影片會被抓成機翻中文軌,內容先被機器翻譯糟蹋一次才送進模型。
    另外逐語言個別嘗試:機翻軌常被 429 限流,一條失敗不能毀掉全部。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    info = get_info(url, cookies_from_browser)
    vid = info.get("id", "")
    manual = set(info.get("subtitles") or {}) - {"live_chat"}
    auto = set(info.get("automatic_captions") or {}) - {"live_chat"}
    available = manual | auto

    def prefer(pool: set[str]) -> list[str]:
        """池內先照偏好語言排,其餘依字母序墊底。"""
        head = [l for l in SUB_LANGS if l in pool]
        return head + sorted(pool - set(head))

    # -orig 是影片原本的語言軌(非翻譯),永遠優先
    orig = sorted(k for k in available if k.endswith("-orig"))
    candidates = orig + prefer(manual - set(orig)) + prefer(auto - set(orig))
    # 去重但保留順序
    seen: set[str] = set()
    candidates = [c for c in candidates if not (c in seen or seen.add(c))]
    for lang in candidates:
        opts = _base_opts(cookies_from_browser) | {
            "skip_download": True,
            "writesubtitles": True,
            "writeautomaticsub": True,
            "subtitleslangs": [lang],
            "subtitlesformat": "vtt",
            "outtmpl": str(out_dir / "%(id)s.%(ext)s"),
        }
        try:
            _throttle()
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.extract_info(url, download=True)
        except yt_dlp.utils.DownloadError as e:
            print(f"  字幕軌 {lang} 下載失敗({str(e)[-60:]}),換下一個…")
            continue
        hits = sorted(out_dir.glob(f"{vid}.{lang}*.vtt"))
        if hits:
            return hits[0]
    return None


def download_audio(url: str, out_dir: Path,
                   cookies_from_browser: str | None = None) -> Path:
    """下載最佳音訊(優先 m4a,不需轉檔)。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    opts = _base_opts(cookies_from_browser) | {
        "format": "bestaudio[ext=m4a]/bestaudio/best",
        "outtmpl": str(out_dir / "%(id)s.%(ext)s"),
    }
    _throttle()
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        return Path(ydl.prepare_filename(info))


def get_live_audio_url(url: str,
                       cookies_from_browser: str | None = None,
                       want_video: bool = False) -> tuple[str, dict]:
    """取得直播的串流網址。預設優先純音訊;want_video=True 時
    選低解析度的影音混流(供畫面截圖用)。"""
    opts = _base_opts(cookies_from_browser) | {
        "format": "best[height<=480]/best" if want_video else "bestaudio/best",
    }
    _throttle()
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
    stream_url = info.get("url")
    # 若解析成影像+音訊分離的格式,優先挑有音軌的那一路
    for fmt in info.get("requested_formats") or []:
        if fmt.get("acodec") not in (None, "none"):
            stream_url = fmt.get("url")
            break
    if not stream_url:
        raise RuntimeError("無法取得直播串流網址")
    return stream_url, info


def make_temp_dir(prefix: str) -> Path:
    return Path(tempfile.mkdtemp(prefix=prefix))

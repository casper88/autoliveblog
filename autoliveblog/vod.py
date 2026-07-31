"""VOD / Podcast 總結流程:字幕優先,無字幕則下載音訊給 Gemini 聽。"""
import re
import shutil
from datetime import datetime
from pathlib import Path

from . import config, export, glossary, subtitles, ytdl
from .i18n import t
from .summarizer import make_summarizer


def _safe_name(s: str, limit: int = 60) -> str:
    return re.sub(r'[\\/:*?"<>|\s]+', "_", s).strip("_")[:limit]


_AUDIO_EXT = (".mp3", ".m4a", ".aac", ".ogg", ".opus", ".wav", ".flac", ".mp4")


def _download_direct(audio_url: str, out_dir: Path) -> Path:
    """下載 Podcast enclosure 音檔(RSS 來源不經過 yt-dlp)。

    有大小與時間上限:enclosure 可能指向無限長的網路電台,不設限會塞爆磁碟。
    """
    import time
    from urllib.parse import urlparse

    import requests

    parsed = urlparse(audio_url)
    if parsed.scheme not in ("http", "https"):
        raise RuntimeError(t("run.bad_audio_url", url=audio_url[:80]))
    # 副檔名只接受已知音訊格式,避免奇怪路徑產生無效檔名或錯誤 MIME
    ext = Path(parsed.path).suffix.lower()
    if ext not in _AUDIO_EXT:
        ext = ".mp3"
    dst = out_dir / f"episode{ext}"

    limit = config.MAX_AUDIO_MB * 1024 * 1024
    deadline = time.monotonic() + config.MAX_DOWNLOAD_SECONDS
    written = 0
    with requests.get(audio_url, stream=True, timeout=(15, 60),
                      headers={"User-Agent": "autoliveblog/0.1"}) as r:
        r.raise_for_status()
        declared = int(r.headers.get("Content-Length") or 0)
        if declared and declared > limit:
            raise RuntimeError(t("run.audio_too_large",
                                 mb=f"{declared / 1024 / 1024:.0f}",
                                 limit=config.MAX_AUDIO_MB))
        with open(dst, "wb") as f:
            for chunk in r.iter_content(1 << 16):
                f.write(chunk)
                written += len(chunk)
                # Content-Length 可能不存在(chunked/直播串流),逐塊檢查才擋得住
                if written > limit:
                    f.close()
                    dst.unlink(missing_ok=True)
                    raise RuntimeError(t("run.audio_limit_hit",
                                         limit=config.MAX_AUDIO_MB))
                if time.monotonic() > deadline:
                    f.close()
                    dst.unlink(missing_ok=True)
                    raise RuntimeError(t("run.download_timeout",
                                         mins=config.MAX_DOWNLOAD_SECONDS // 60))
    return dst


def run(url: str, info: dict, lang: str | None = None, model: str | None = None,
        dry_run: bool = False, provider: str | None = None, on_event=None,
        cookies_from_browser: str | None = None) -> Path | None:
    emit = on_event or (lambda e: None)
    title = info.get("title", "(無標題)")
    channel = info.get("uploader") or info.get("channel") or ""
    vid = info.get("id", "video")
    duration = info.get("duration") or 0
    print(t("run.video_header", title=title))
    print(t("run.video_meta", channel=channel, mins=duration // 60,
            secs=duration % 60))
    emit({"type": "started", "title": title, "video_id": vid, "duration": duration})

    # Podcast RSS:info 已帶直接音檔網址,沒有字幕可抓,跳過字幕階段
    direct_audio = info.get("_direct_audio_url")

    tmp = ytdl.make_temp_dir("autoliveblog_vod_")
    try:
        transcript = None
        vtt = None
        if not direct_audio:
            print(t("run.trying_subs"))
            emit({"type": "status", "status": "subtitles"})
            try:
                vtt = ytdl.download_subtitles(url, tmp, cookies_from_browser)
            except Exception as e:
                print("  " + t("run.subs_failed", err=e))
                vtt = None
        if vtt:
            cues = subtitles.parse_vtt(vtt)
            if cues:
                transcript = subtitles.to_transcript(cues)
                print("  " + t("run.got_subs", name=vtt.name, n=len(transcript)))

        if dry_run:
            if transcript:
                print("\n" + t("run.transcript_preview"))
                print(transcript[:1500])
            else:
                print(t("run.no_subs_preview"))
            return None

        summarizer = make_summarizer(provider=provider, model=model, lang=lang)
        terms = glossary.terms_for(channel)
        if terms and hasattr(summarizer, "set_glossary"):
            summarizer.set_glossary(terms)
        if transcript:
            print(t("run.summarizing_subs"))
            emit({"type": "status", "status": "summarizing",
                  "source": "subtitles", "chars": len(transcript)})
            summary = summarizer.summarize_text(title, channel, transcript)
        else:
            print(t("run.no_subs_audio") if not direct_audio
                  else t("run.downloading_podcast"))
            emit({"type": "status", "status": "downloading_audio"})
            if direct_audio:
                audio = _download_direct(direct_audio, tmp)
            else:
                audio = ytdl.download_audio(url, tmp, cookies_from_browser)
            size_mb = audio.stat().st_size / 1024 / 1024
            print("  " + t("run.audio_ready", name=audio.name,
                           mb=f"{size_mb:.1f}"))
            emit({"type": "status", "status": "summarizing", "source": "audio",
                  "size_mb": round(size_mb, 1)})
            summary = summarizer.summarize_audio(audio, title, channel)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    ch_dir = config.OUTPUT_DIR / (_safe_name(channel) or "未知頻道")
    ch_dir.mkdir(parents=True, exist_ok=True)
    out = ch_dir / f"{datetime.now():%Y%m%d}_{_safe_name(title)}_{vid}.md"
    header = (f"# {title}\n\n"
              f"- {t('md.channel')}: {channel}\n- {t('md.url')}: {url}\n"
              f"- {t('md.summarized_at')}: {datetime.now():%Y-%m-%d %H:%M}\n\n---\n\n")
    out.write_text(header + summary + "\n", encoding="utf-8")
    print("\n" + "=" * 60)
    print(summary)
    print("=" * 60)
    export.copy_to_obsidian(out)
    print("\n" + t("run.saved", path=out))
    emit({"type": "final", "summary": summary, "md_path": str(out)})
    return out

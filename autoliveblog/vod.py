"""VOD / Podcast 總結流程:字幕優先,無字幕則下載音訊給 Gemini 聽。"""
import re
import shutil
from datetime import datetime
from pathlib import Path

from . import config, export, glossary, subtitles, ytdl
from .summarizer import make_summarizer


def _safe_name(s: str, limit: int = 60) -> str:
    return re.sub(r'[\\/:*?"<>|\s]+', "_", s).strip("_")[:limit]


def run(url: str, info: dict, lang: str | None = None, model: str | None = None,
        dry_run: bool = False, provider: str | None = None, on_event=None,
        cookies_from_browser: str | None = None) -> Path | None:
    emit = on_event or (lambda e: None)
    title = info.get("title", "(無標題)")
    channel = info.get("uploader") or info.get("channel") or ""
    vid = info.get("id", "video")
    duration = info.get("duration") or 0
    print(f"影片:{title}")
    print(f"頻道:{channel}  長度:{duration // 60} 分 {duration % 60} 秒")
    emit({"type": "started", "title": title, "video_id": vid, "duration": duration})

    tmp = ytdl.make_temp_dir("autoliveblog_vod_")
    try:
        transcript = None
        print("嘗試下載字幕…")
        emit({"type": "status", "status": "subtitles"})
        try:
            vtt = ytdl.download_subtitles(url, tmp, cookies_from_browser)
        except Exception as e:
            print(f"  字幕下載失敗:{e}")
            vtt = None
        if vtt:
            cues = subtitles.parse_vtt(vtt)
            if cues:
                transcript = subtitles.to_transcript(cues)
                print(f"  取得字幕({vtt.name},{len(transcript)} 字)")

        if dry_run:
            if transcript:
                print("\n--- 逐字稿預覽(前 1500 字)---")
                print(transcript[:1500])
            else:
                print("沒有字幕;正式執行時會下載音訊交給 Gemini 聽。")
            return None

        summarizer = make_summarizer(provider=provider, model=model, lang=lang)
        terms = glossary.terms_for(channel)
        if terms and hasattr(summarizer, "set_glossary"):
            summarizer.set_glossary(terms)
        if transcript:
            print("以字幕逐字稿總結中…")
            emit({"type": "status", "status": "summarizing",
                  "source": "subtitles", "chars": len(transcript)})
            summary = summarizer.summarize_text(title, channel, transcript)
        else:
            print("沒有字幕,下載音訊中…")
            emit({"type": "status", "status": "downloading_audio"})
            audio = ytdl.download_audio(url, tmp, cookies_from_browser)
            size_mb = audio.stat().st_size / 1024 / 1024
            print(f"  音訊下載完成({audio.name},{size_mb:.1f} MB),交給 Gemini 聆聽總結中…")
            emit({"type": "status", "status": "summarizing", "source": "audio",
                  "size_mb": round(size_mb, 1)})
            summary = summarizer.summarize_audio(audio, title, channel)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    ch_dir = config.OUTPUT_DIR / (_safe_name(channel) or "未知頻道")
    ch_dir.mkdir(parents=True, exist_ok=True)
    out = ch_dir / f"{datetime.now():%Y%m%d}_{_safe_name(title)}_{vid}.md"
    header = (f"# {title}\n\n"
              f"- 頻道:{channel}\n- 網址:{url}\n"
              f"- 總結時間:{datetime.now():%Y-%m-%d %H:%M}\n\n---\n\n")
    out.write_text(header + summary + "\n", encoding="utf-8")
    print("\n" + "=" * 60)
    print(summary)
    print("=" * 60)
    export.copy_to_obsidian(out)
    print(f"\n已存檔:{out}")
    emit({"type": "final", "summary": summary, "md_path": str(out)})
    return out

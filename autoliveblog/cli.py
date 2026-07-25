"""命令列入口:自動判斷直播或 VOD。"""
import argparse
import sys

from . import config, feeds, live, vod, ytdl


def _force_utf8_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            # line_buffering:重新導向到檔案時也逐行輸出(背景模式即時可讀)
            stream.reconfigure(encoding="utf-8", errors="replace",
                               line_buffering=True)
        except Exception:
            pass


def main(argv: list[str] | None = None) -> int:
    _force_utf8_console()
    parser = argparse.ArgumentParser(
        prog="autoliveblog",
        description="直播即時總結 / 影片與 Podcast 總結(支援 YouTube、Twitch、Podcast RSS 等)")
    parser.add_argument("url", help="YouTube 影片、直播或其他 yt-dlp 支援的網址")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--live", action="store_true", help="強制使用直播模式")
    mode.add_argument("--vod", action="store_true", help="強制使用影片模式")
    parser.add_argument("--lang", default=None, help=f"總結語言(預設:{config.LANG})")
    parser.add_argument("--provider", choices=["auto", "gemini", "openai"],
                        default=None,
                        help="AI 引擎(預設 auto:Gemini 優先,額度耗盡自動切 OpenAI)")
    parser.add_argument("--model", default=None, help=f"Gemini 模型(預設:{config.MODEL})")
    parser.add_argument("--chunk", type=int, default=None,
                        help=f"直播每幾秒總結一次(預設:{config.CHUNK_SECONDS})")
    parser.add_argument("--duration", type=int, default=None, metavar="分鐘",
                        help="直播看滿幾分鐘後自動停止並產出最終總結(預設:直到直播結束)")
    parser.add_argument("--frames", type=int, default=0, metavar="秒",
                        help="直播每幾秒擷取一張畫面給 Gemini 看(讀盤面/字卡;0=關閉,建議 30)")
    parser.add_argument("--smart", action="store_true",
                        help="智慧補看:本地每 10 秒密集抽圖,模型聽到「看這張圖」等"
                             "會自主要求加看該時間點的畫面(未指定 --frames 時自動設 30)")
    parser.add_argument("--from-start", action="store_true", dest="from_start",
                        help="補課模式:從直播開頭開始總結(快速消化歷史後接上即時;"
                             "時間軸改為直播開始起算,畫面截圖自動關閉)")
    parser.add_argument("--no-toast", action="store_true", help="關閉桌面通知")
    parser.add_argument("--dry-run", action="store_true",
                        help="只抓字幕/資訊,不呼叫 Gemini(測試用)")
    parser.add_argument("--cookies-from-browser", default=None, metavar="BROWSER",
                        help="會員限定內容時從瀏覽器讀 cookie,例如 chrome、edge")
    args = parser.parse_args(argv)

    print("讀取影片資訊中…")
    try:
        # feed 網址走 RSS 解析,其餘交給 yt-dlp
        info = feeds.get_info_any(args.url, args.cookies_from_browser)
    except Exception as e:
        print(f"無法讀取網址:{e}")
        return 1

    is_live = args.live or (info.get("is_live", False) and not args.vod)
    try:
        if is_live:
            if args.dry_run:
                print(f"這是直播:{info.get('title')}(--dry-run 不進入監看)")
                return 0
            chunk_s = args.chunk or config.CHUNK_SECONDS
            max_chunks = None
            if args.duration:
                max_chunks = max(1, (args.duration * 60) // chunk_s)
            frames = args.frames or (30 if args.smart else 0)
            live.run(args.url, info, lang=args.lang, model=args.model,
                     chunk_seconds=args.chunk, no_toast=args.no_toast,
                     max_chunks=max_chunks, frames_interval=frames,
                     smart_frames=args.smart, provider=args.provider,
                     from_start=args.from_start,
                     cookies_from_browser=args.cookies_from_browser)
        else:
            vod.run(args.url, info, lang=args.lang, model=args.model,
                    dry_run=args.dry_run, provider=args.provider,
                    cookies_from_browser=args.cookies_from_browser)
    except KeyboardInterrupt:
        print("\n已中止。")
        return 130
    except RuntimeError as e:
        print(f"錯誤:{e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

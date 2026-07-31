"""命令列入口:自動判斷直播或 VOD。"""
import argparse
import sys

from . import config, feeds, live, vod, ytdl
from .i18n import t


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
        description=t("cli.description"))
    parser.add_argument("url", help=t("cli.url_help"))
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--live", action="store_true", help=t("cli.live_help"))
    mode.add_argument("--vod", action="store_true", help=t("cli.vod_help"))
    parser.add_argument("--lang", default=None,
                        help=t("cli.lang_help", default=config.LANG))
    parser.add_argument("--provider", choices=["auto", "gemini", "openai"],
                        default=None,
                        help=t("cli.provider_help"))
    parser.add_argument("--model", default=None,
                        help=t("cli.model_help", default=config.MODEL))
    parser.add_argument("--chunk", type=int, default=None,
                        help=t("cli.chunk_help", default=config.CHUNK_SECONDS))
    parser.add_argument("--duration", type=int, default=None, metavar="MINUTES",
                        help=t("cli.duration_help"))
    parser.add_argument("--frames", type=int, default=0, metavar="SECONDS",
                        help=t("cli.frames_help"))
    parser.add_argument("--smart", action="store_true",
                        help=t("cli.smart_help"))
    parser.add_argument("--from-start", action="store_true", dest="from_start",
                        help=t("cli.from_start_help"))
    parser.add_argument("--no-toast", action="store_true", help=t("cli.no_toast_help"))
    parser.add_argument("--dry-run", action="store_true",
                        help=t("cli.dry_run_help"))
    parser.add_argument("--cookies-from-browser", default=None, metavar="BROWSER",
                        help=t("cli.cookies_help"))
    args = parser.parse_args(argv)

    print(t("cli.reading"))
    try:
        # feed 網址走 RSS 解析,其餘交給 yt-dlp
        info = feeds.get_info_any(args.url, args.cookies_from_browser)
    except Exception as e:
        print(t("cli.cannot_read", err=e))
        return 1

    is_live = args.live or (info.get("is_live", False) and not args.vod)
    try:
        if is_live:
            if args.dry_run:
                print(t("cli.is_live_dry", title=info.get("title")))
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
        print("\n" + t("cli.aborted"))
        return 130
    except RuntimeError as e:
        print(t("cli.error", err=e))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

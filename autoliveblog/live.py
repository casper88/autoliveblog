"""直播即時總結:ffmpeg 切段錄音 → Gemini 滾動總結 → Markdown + 桌面通知。"""
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from . import config, export, glossary, notify, ytdl
from .summarizer import LiveState, make_summarizer


# 連續幾段總結失敗就放棄:額度耗盡這類錯誤不會自己好轉
_MAX_SUMMARY_FAILURES = 3


def _safe_name(s: str, limit: int = 60) -> str:
    return re.sub(r'[\\/:*?"<>|\s]+', "_", s).strip("_")[:limit]


def _fmt_elapsed(seconds: int) -> str:
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _start_ffmpeg(stream_url: str, out_dir: Path, start_index: int,
                  chunk_seconds: int, frames_interval: int = 0) -> subprocess.Popen:
    cmd = [
        config.FFMPEG, "-hide_banner", "-loglevel", "warning", "-nostdin",
        "-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "30",
        "-i", stream_url,
        "-vn", "-ac", "1", "-ar", "16000", "-c:a", "libmp3lame", "-b:a", "48k",
        "-f", "segment", "-segment_time", str(chunk_seconds),
        "-segment_start_number", str(start_index),
        "-reset_timestamps", "1",
        str(out_dir / "chunk_%06d.mp3"),
    ]
    if frames_interval:
        frames_dir = out_dir / "frames"
        frames_dir.mkdir(exist_ok=True)
        frame_start = start_index * chunk_seconds // frames_interval
        cmd += [
            "-map", "0:v", "-vf", f"fps=1/{frames_interval},scale=640:-2",
            "-q:v", "4", "-start_number", str(frame_start),
            str(frames_dir / "frame_%06d.jpg"),
        ]
    # stderr 寫檔而非 PIPE:長時間執行時 PIPE 塞滿會讓 ffmpeg 凍結
    log = open(out_dir / "ffmpeg.log", "ab")
    return subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=log)


def _start_from_start_pipeline(url: str, out_dir: Path, chunk_seconds: int,
                               cookies_from_browser: str | None = None):
    """補課模式:yt-dlp --live-from-start 從直播開頭下載,管線餵給 ffmpeg 切段。
    回傳 (yt-dlp Popen, ffmpeg Popen)。"""
    log = open(out_dir / "ffmpeg.log", "ab")
    ytdlp_cmd = [sys.executable, "-m", "yt_dlp", "--live-from-start",
                 "-f", "bestaudio/best", "--quiet", "--no-warnings", "-o", "-"]
    if config.FFMPEG:
        ytdlp_cmd += ["--ffmpeg-location", str(Path(config.FFMPEG).parent)]
    if cookies_from_browser:
        ytdlp_cmd += ["--cookies-from-browser", cookies_from_browser]
    ytdlp_cmd.append(url)
    p_dl = subprocess.Popen(ytdlp_cmd, stdout=subprocess.PIPE, stderr=log)
    ff_cmd = [
        config.FFMPEG, "-hide_banner", "-loglevel", "error", "-nostdin",
        "-i", "pipe:0",
        "-vn", "-ac", "1", "-ar", "16000", "-c:a", "libmp3lame", "-b:a", "48k",
        "-f", "segment", "-segment_time", str(chunk_seconds),
        "-reset_timestamps", "1",
        str(out_dir / "chunk_%06d.mp3"),
    ]
    p_ff = subprocess.Popen(ff_cmd, stdin=p_dl.stdout,
                            stdout=subprocess.DEVNULL, stderr=log)
    p_dl.stdout.close()  # 讓 ffmpeg 結束時 yt-dlp 收到 broken pipe
    return p_dl, p_ff


def _write_live_md(path: Path, title: str, url: str, state: LiveState,
                   started: datetime, final_summary: str | None = None,
                   from_start: bool = False) -> None:
    ts_note = ("時間軸為直播開始起算" if from_start
               else "時間軸為監看起算的相對時間")
    parts = [
        f"# 🔴 直播即時總結:{title}\n",
        f"- 網址:{url}",
        f"- 開始監看:{started:%Y-%m-%d %H:%M}({ts_note})",
        f"- 最後更新:{datetime.now():%H:%M:%S}\n",
    ]
    if final_summary:
        parts += ["---\n", "## ✅ 最終總結\n", final_summary, "\n"]
    parts += [
        "---\n",
        f"## 目前話題\n\n**{state.current_topic or '(等待第一段音訊…)'}**\n",
        f"## 滾動摘要\n\n{state.rolling_summary or '(尚無)'}\n",
    ]
    if state.media:
        # md 位於頻道子資料夾,media 在 summaries/media → 相對路徑要往上一層
        parts += ["## 🔍 重要畫面(模型自主補看)\n"]
        parts += [f"[{el}] ![{el}](../{rp})" for el, rp in state.media[-12:]]
        parts += [""]
    parts += [
        "## 時間軸\n",
        "\n\n".join(state.timeline) if state.timeline else "(尚無)",
        "",
    ]
    path.write_text("\n".join(parts), encoding="utf-8")


def run(url: str, info: dict, lang: str | None = None, model: str | None = None,
        chunk_seconds: int | None = None, no_toast: bool = False,
        max_chunks: int | None = None, frames_interval: int = 0,
        smart_frames: bool = False, provider: str | None = None,
        from_start: bool = False, keywords: list[str] | None = None,
        on_event=None, stop_event=None,
        cookies_from_browser: str | None = None) -> Path:
    """on_event:選用 callable(dict),Web 介面用來接收即時事件。
    stop_event:選用 threading.Event,設起來等同按 Ctrl+C(收尾並產最終總結)。"""
    if not config.FFMPEG:
        raise RuntimeError("直播模式需要 ffmpeg。請安裝:winget install Gyan.FFmpeg.Essentials")
    emit = on_event or (lambda e: None)
    chunk_seconds = chunk_seconds or config.CHUNK_SECONDS
    title = info.get("title", "(無標題)")
    vid = info.get("id", "live")
    started = datetime.now()

    channel = info.get("uploader") or info.get("channel") or "未知頻道"
    ch_dir = config.OUTPUT_DIR / (_safe_name(channel) or "未知頻道")
    ch_dir.mkdir(parents=True, exist_ok=True)
    md_path = ch_dir / f"live_{started:%Y%m%d_%H%M}_{_safe_name(title)}_{vid}.md"

    # 時間軸基準:補課模式=0(本來就從頭);即時模式從開播時間推算進場偏移,
    # 讓時間軸顯示「直播總時間」,與 YouTube 進度條對齊
    base_offset = 0
    stream_timeline = from_start
    if not from_start:
        rts = info.get("release_timestamp") or 0
        offset = int(time.time() - rts) if rts else 0
        # 超過 12 小時(DVR 上限)多半是 24 小時台,總時間無意義 → 退回監看起算
        if 0 < offset <= 12 * 3600:
            base_offset = offset
            stream_timeline = True
            print(f"直播已進行 {_fmt_elapsed(base_offset)},時間軸以直播開始起算。")

    summarizer = make_summarizer(provider=provider, model=model, lang=lang)
    terms = glossary.terms_for(channel)
    if terms and hasattr(summarizer, "set_glossary"):
        summarizer.set_glossary(terms)
        print(f"已套用「{channel}」專有名詞辭典({len(terms)} 詞)")
    state = LiveState()
    _write_live_md(md_path, title, url, state, started, from_start=stream_timeline)

    print(f"🔴 直播:{title}")
    print(f"每 {chunk_seconds} 秒總結一次;即時結果寫入:{md_path}")
    if max_chunks:
        print(f"預計監看 {max_chunks * chunk_seconds // 60} 分鐘({max_chunks} 段)後自動收尾。")
    print("按 Ctrl+C 停止並產出最終總結。\n")
    emit({"type": "started", "title": title, "video_id": vid,
          "md_path": str(md_path), "chunk_seconds": chunk_seconds})

    tmp = ytdl.make_temp_dir("autoliveblog_live_")
    if from_start and (frames_interval or smart_frames):
        print("補課模式下畫面截圖自動關閉(歷史音訊下載速度快於即時,截圖無法對齊)")
        frames_interval, smart_frames = 0, False
    # 智慧模式:本地每 10 秒密集抽圖(不花 API),先只送稀疏的那幾張,
    # 模型要求加看時再從密集圖庫撈
    capture_interval = min(10, frames_interval) if smart_frames and frames_interval \
        else frames_interval
    want_video = capture_interval > 0
    p_dl = None
    if from_start:
        print("補課模式:從直播開頭下載,快速消化歷史段落後接上即時進度…")
        p_dl, proc = _start_from_start_pipeline(url, tmp, chunk_seconds,
                                                cookies_from_browser)
    else:
        stream_url, _ = ytdl.get_live_audio_url(url, cookies_from_browser,
                                                want_video=want_video)
        proc = _start_ffmpeg(stream_url, tmp, 0, chunk_seconds, capture_interval)

    next_index = 0          # 下一個待處理的 chunk 編號
    total_started = 0       # 已開出的 chunk 總數(重啟 ffmpeg 時的起始編號)
    consecutive_failures = 0
    summary_failures = 0    # 連續總結失敗次數(與 ffmpeg 失敗分開計)
    abort_reason: str | None = None

    # 停滯看門狗:ffmpeg 活著但長時間沒有新資料(例如等待室、斷流)就強制重連
    stall_limit = chunk_seconds * 2 + 60
    last_progress = time.time()
    last_sig: tuple = ()

    def _progress_sig() -> tuple:
        files = sorted(tmp.glob("chunk_*.mp3"))
        if not files:
            return ("", -1)
        try:
            return (files[-1].name, files[-1].stat().st_size)
        except OSError:
            return (files[-1].name, -1)

    def chunk_path(i: int) -> Path:
        return tmp / f"chunk_{i:06d}.mp3"

    def chunk_frame_files(i: int) -> dict[int, Path]:
        """第 i 段涵蓋的截圖:{段內相對秒數: 檔案路徑}。"""
        if not capture_interval:
            return {}
        lo = i * chunk_seconds // capture_interval
        hi = (i + 1) * chunk_seconds // capture_interval
        base = i * chunk_seconds
        out: dict[int, Path] = {}
        for m in range(lo, hi):
            f = tmp / "frames" / f"frame_{m:06d}.jpg"
            if f.exists():
                out[m * capture_interval - base] = f
        return out

    def process_ready_chunks(include_last: bool = False) -> None:
        """處理已完成的 chunk。chunk N 在 chunk N+1 出現後才算完成;
        include_last=True 時(ffmpeg 已退出)連最後一個也處理。"""
        nonlocal next_index, consecutive_failures, summary_failures, abort_reason
        while True:
            # 使用者要求停止時立刻中斷積壓處理,直接進入收尾
            if stop_event is not None and stop_event.is_set() and not include_last:
                break
            if max_chunks and next_index >= max_chunks:
                break
            cur = chunk_path(next_index)
            if not cur.exists():
                break
            if not include_last and not chunk_path(next_index + 1).exists():
                break
            if cur.stat().st_size < 10_000:  # 太小的殘片跳過
                cur.unlink(missing_ok=True)
                next_index += 1
                continue
            elapsed = _fmt_elapsed(base_offset + next_index * chunk_seconds)
            frames_map = chunk_frame_files(next_index)
            # 稀疏送圖:每個 frames_interval 視窗取第一張
            sent_offsets = [o for o in sorted(frames_map)
                            if o % frames_interval < capture_interval]
            coarse = [frames_map[o].read_bytes() for o in sent_offsets]
            dense_lookup = None
            picked_store: dict[int, Path] = {}  # 模型補看過的截圖(=重要畫面)
            if smart_frames and frames_map:
                def dense_lookup(secs, _fm=frames_map, _sent=set(sent_offsets),
                                 _store=picked_store):
                    picked: dict[int, Path] = {}
                    for s in secs:
                        near = sorted(_fm, key=lambda o: abs(o - s))[:2]
                        for o in near:
                            if abs(o - s) <= 20 and o not in _sent:
                                picked[o] = _fm[o]
                    _store.update(picked)
                    return [p.read_bytes() for _, p in sorted(picked.items())[:6]]
            try:
                data = summarizer.live_update(cur.read_bytes(), "audio/mpeg",
                                              state, title, elapsed,
                                              images=coarse,
                                              dense_lookup=dense_lookup,
                                              chunk_seconds=chunk_seconds,
                                              topics=keywords)
                consecutive_failures = 0
                topic = data.get("current_topic", "")
                points = data.get("new_points") or []
                print(f"[{elapsed}] 🗣 {topic}")
                if data.get("_requested_frames"):
                    print(f"         🔍 模型自主要求加看 "
                          f"{data['_requested_frames']} 秒處的畫面並精修")
                for p in points:
                    print(f"         - {p}")
                # 模型補看過的截圖 = 它自己認定重要的畫面 → 保留下來
                images_rel: list[str] = []
                if data.get("_requested_frames") and picked_store:
                    mdir = (config.OUTPUT_DIR / "media"
                            / f"{vid}_{started:%m%d_%H%M}")
                    mdir.mkdir(parents=True, exist_ok=True)
                    for off, src in sorted(picked_store.items())[:4]:
                        dst = mdir / f"{next_index:04d}_{off:03d}s.jpg"
                        try:
                            shutil.copy2(src, dst)
                            rel = dst.relative_to(config.OUTPUT_DIR)
                            images_rel.append(str(rel).replace("\\", "/"))
                        except OSError:
                            pass
                    for rel in images_rel:
                        state.media.append((elapsed, rel))
                _write_live_md(md_path, title, url, state, started, from_start=from_start)
                emit({"type": "chunk", "elapsed": elapsed,
                      "seconds": base_offset + next_index * chunk_seconds,
                      "topic": topic, "points": points,
                      "topic_changed": bool(data.get("topic_changed")),
                      "requested_frames": data.get("_requested_frames") or [],
                      "images": images_rel,
                      "topic_hits": data.get("topic_hits") or [],
                      "rolling_summary": state.rolling_summary})
                if data.get("topic_changed") and points and not no_toast:
                    notify.toast(f"話題轉換:{topic}", points[0])
            except Exception as e:
                consecutive_failures += 1
                summary_failures += 1
                print(f"[{elapsed}] ⚠ 總結失敗:{e}")
                emit({"type": "chunk_error", "elapsed": elapsed,
                      "message": str(e), "consecutive": summary_failures})
                # 額度耗盡這類錯誤不會自己好轉,繼續空轉只是浪費時間與磁碟
                if summary_failures >= _MAX_SUMMARY_FAILURES:
                    abort_reason = (
                        f"連續 {summary_failures} 段總結失敗,停止監看。"
                        f"最後的錯誤:{str(e)[:200]}")
                    return
            cur.unlink(missing_ok=True)
            for f in frames_map.values():
                f.unlink(missing_ok=True)
            next_index += 1

    try:
        while True:
            process_ready_chunks()
            if abort_reason:
                print(f"\n⚠ {abort_reason}")
                emit({"type": "error", "message": abort_reason})
                break
            if stop_event is not None and stop_event.is_set():
                print("\n收到停止指令,收尾中…")
                break
            if max_chunks and next_index >= max_chunks:
                print(f"\n已看滿 {max_chunks} 段,收尾中…")
                break
            sig = _progress_sig()
            if sig != last_sig:
                last_sig, last_progress = sig, time.time()
            elif proc.poll() is None and \
                    time.time() - last_progress > stall_limit:
                print(f"⚠ 串流停滯超過 {stall_limit} 秒(可能還沒開播或斷流),強制重連…")
                emit({"type": "status", "status": "stalled"})
                proc.kill()
                if p_dl and p_dl.poll() is None:
                    p_dl.kill()
                last_progress = time.time()
            if proc.poll() is not None:
                try:
                    stderr = (tmp / "ffmpeg.log").read_text(
                        encoding="utf-8", errors="replace").strip()
                except OSError:
                    stderr = ""
                process_ready_chunks(include_last=True)
                # 判斷直播是否仍在進行(串流網址過期時需重新解析)
                try:
                    fresh = ytdl.get_info(url, cookies_from_browser)
                    still_live = fresh.get("is_live", False)
                except Exception:
                    still_live = False
                if not still_live:
                    print("\n直播已結束。")
                    emit({"type": "stream_ended"})
                    break
                consecutive_failures += 1
                if consecutive_failures > 5:
                    print(f"\nffmpeg 連續失敗過多,停止監看。最後錯誤:{stderr[-300:]}")
                    break
                if from_start:
                    # 補課下載中斷:重來會從頭重抓,改從最新進度續看
                    print("補課下載中斷,改從最新進度續看(中間可能有缺口)…")
                    from_start = False
                    if p_dl and p_dl.poll() is None:
                        p_dl.kill()
                    p_dl = None
                else:
                    print("串流中斷,重新連線中…")
                total_started = next_index
                stream_url, _ = ytdl.get_live_audio_url(url, cookies_from_browser,
                                                        want_video=want_video)
                proc = _start_ffmpeg(stream_url, tmp, total_started, chunk_seconds,
                                     capture_interval)
            time.sleep(5)
    except KeyboardInterrupt:
        print("\n收到停止指令,產出最終總結中…")
    finally:
        for p in (proc, p_dl):
            if p and p.poll() is None:
                p.terminate()
                try:
                    p.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    p.kill()

    try:
        # 使用者主動停止時不再處理積壓,直接收尾
        if stop_event is None or not stop_event.is_set():
            process_ready_chunks(include_last=True)
    except Exception:
        pass

    final = None
    if state.timeline or state.rolling_summary:
        try:
            emit({"type": "status", "status": "finalizing"})
            final = summarizer.finalize_live(state, title)
            print("\n" + "=" * 60)
            print(final)
            print("=" * 60)
        except Exception as e:
            print(f"最終總結失敗:{e}")
            emit({"type": "error", "message": f"最終總結失敗:{e}"})
    _write_live_md(md_path, title, url, state, started, final_summary=final, from_start=from_start)
    export.copy_to_obsidian(md_path)
    shutil.rmtree(tmp, ignore_errors=True)
    print(f"\n已存檔:{md_path}")
    emit({"type": "final", "summary": final or "", "md_path": str(md_path)})
    return md_path

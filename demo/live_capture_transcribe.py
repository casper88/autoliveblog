"""示範:直播擷取 + 本地 Whisper 轉錄(不需任何 API 金鑰)。

擷取 N 段直播音訊,每段轉錄成 demo_work/transcript_XXX.txt,
由外部(Claude / Gemini)讀取逐字稿做即時總結。
"""
import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # 專案根目錄
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from autoliveblog import ytdl
from autoliveblog.live import _start_ffmpeg


def transcribe(model, path: Path) -> str:
    segments, info = model.transcribe(
        str(path), language="zh", beam_size=1, vad_filter=True,
        initial_prompt="以下是台灣財經新聞直播的內容,請使用繁體中文。")
    return "".join(seg.text for seg in segments).strip()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("url")
    ap.add_argument("--chunks", type=int, default=4, help="擷取幾段後結束")
    ap.add_argument("--chunk-seconds", type=int, default=120)
    ap.add_argument("--model", default="small")
    ap.add_argument("--out", default=str(Path(__file__).parent / "demo_work"))
    args = ap.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"載入 Whisper 模型({args.model},CPU int8)…", flush=True)
    from faster_whisper import WhisperModel
    model = WhisperModel(args.model, device="cpu", compute_type="int8")

    print("解析直播串流網址…", flush=True)
    stream_url, info = ytdl.get_live_audio_url(args.url)
    (out_dir / "stream_info.txt").write_text(
        f"{info.get('title', '')}\n{args.url}\n", encoding="utf-8")
    print(f"直播:{info.get('title', '')}", flush=True)

    proc = _start_ffmpeg(stream_url, out_dir, 0, args.chunk_seconds)
    print(f"ffmpeg 開始錄音,每段 {args.chunk_seconds} 秒,共 {args.chunks} 段…", flush=True)

    done = 0
    try:
        while done < args.chunks:
            cur = out_dir / f"chunk_{done:06d}.mp3"
            nxt = out_dir / f"chunk_{done + 1:06d}.mp3"
            if not (cur.exists() and (nxt.exists() or proc.poll() is not None)):
                if proc.poll() is not None and not cur.exists():
                    print("ffmpeg 已結束,串流中斷。", flush=True)
                    break
                time.sleep(5)
                continue
            t0 = time.time()
            print(f"[chunk {done}] 轉錄中…", flush=True)
            text = transcribe(model, cur)
            dt = time.time() - t0
            tf = out_dir / f"transcript_{done:03d}.txt"
            tf.write_text(text, encoding="utf-8")
            print(f"[chunk {done}] 完成({dt:.0f}s,{len(text)} 字)→ {tf.name}", flush=True)
            cur.unlink(missing_ok=True)
            done += 1
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except Exception:
                proc.kill()
    print("DONE", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

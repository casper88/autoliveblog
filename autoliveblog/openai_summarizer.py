"""OpenAI 引擎:gpt-4o-mini-transcribe 轉錄 + gpt-4o-mini 總結。

與 GeminiSummarizer 介面相同,可互換。差異:OpenAI 的對話模型不直接吃音訊,
所以先轉錄成文字再總結;畫面截圖以 vision(低解析)附上。
"""
import base64
import io
import json
import subprocess
import time
from pathlib import Path

from . import config, stats
from .i18n import t
from .summarizer import LiveState, _parse_seconds_list

_QA_RULES = ("If the record does not contain the information, say it was not "
             "mentioned; never make anything up.")


def _safe_stem(s: str, limit: int = 80) -> str:
    """把「頻道_標題」轉成安全的檔名主幹。"""
    import re
    return re.sub(r'[\\/:*?"<>|\s]+', "_", s).strip("_")[:limit]


class OpenAISummarizer:
    provider = "openai"

    def __init__(self, api_key: str | None = None, model: str | None = None,
                 lang: str | None = None):
        key = api_key or config.OPENAI_API_KEY
        if not key:
            raise RuntimeError(t("engine.no_openai_key"))
        from openai import OpenAI
        self.client = OpenAI(api_key=key, timeout=120)
        self.model = model or config.OPENAI_MODEL
        self.stt_model = config.OPENAI_STT_MODEL
        self.lang = lang or config.LANG
        self.retries = 3
        self.glossary: list[str] = []

    def set_glossary(self, terms: list[str]) -> None:
        self.glossary = terms or []

    def _gloss_note(self) -> str:
        if not self.glossary:
            return ""
        return ("\nKnown proper nouns (always use these spellings): "
                + ", ".join(self.glossary))

    # ---------- 基礎 ----------

    def _chat(self, messages: list, json_mode: bool = False,
              retries: int | None = None) -> str:
        retries = retries or self.retries
        last_err = None
        for attempt in range(retries):
            try:
                stats.record_call("openai")
                kwargs = {"model": self.model, "messages": messages}
                if json_mode:
                    kwargs["response_format"] = {"type": "json_object"}
                resp = self.client.chat.completions.create(**kwargs)
                if resp.usage:
                    stats.record_usage("openai",
                                       in_tokens=resp.usage.prompt_tokens,
                                       out_tokens=resp.usage.completion_tokens)
                return (resp.choices[0].message.content or "").strip()
            except Exception as e:
                last_err = e
                msg = str(e)
                if any(k in msg for k in ("invalid_api_key", "401", "403")):
                    stats.record_failure()
                    raise RuntimeError(
                        t("engine.openai_key_invalid", err=msg)) from e
                stats.record_retry(msg)
                wait = 10 * (attempt + 1)
                print("  " + t("engine.openai_retry", wait=wait,
                               attempt=attempt + 1, retries=retries, err=e))
                time.sleep(wait)
        stats.record_failure()
        raise RuntimeError(t("engine.openai_failed", err=last_err))

    def transcribe(self, audio_bytes: bytes, name: str = "chunk.mp3",
                   seconds: float | None = None) -> str:
        if config.STT_PROVIDER == "local":
            return self._transcribe_local(audio_bytes)
        stats.record_call("openai")
        f = io.BytesIO(audio_bytes)
        f.name = name
        kwargs = {}
        if config.STT_LANG:  # 未設定則自動偵測語言(硬編 zh 會毀掉外語內容)
            kwargs["language"] = config.STT_LANG
        if self.glossary:
            # 詞彙偏置:只餵詞彙本身,不加說明句 —— Whisper 會跟著提示的語言走,
            # 中文說明句會讓外語音訊被轉成中文
            kwargs["prompt"] = ", ".join(self.glossary[:20])
        resp = self.client.audio.transcriptions.create(
            model=self.stt_model, file=f, **kwargs)
        # 轉錄計費以音長估算;呼叫端沒給就用 48kbps mp3 反推
        if seconds is None:
            seconds = len(audio_bytes) * 8 / 48_000
        stats.record_usage("openai", audio_seconds=seconds)
        return (resp.text or "").strip()

    _local_model = None

    def _transcribe_local(self, audio_bytes: bytes) -> str:
        """本地 faster-whisper 轉錄:免費、離線,速度取決於 CPU。"""
        if OpenAISummarizer._local_model is None:
            from faster_whisper import WhisperModel
            print("[stt] " + t("engine.local_whisper_loading"))
            OpenAISummarizer._local_model = WhisperModel(
                "small", device="cpu", compute_type="int8")
        segments, _ = OpenAISummarizer._local_model.transcribe(
            io.BytesIO(audio_bytes),
            language=(config.STT_LANG or None), beam_size=1, vad_filter=True,
            # 只餵詞彙,不加中文連接號:提示的語言會影響轉錄輸出的語言
            initial_prompt=(", ".join(self.glossary[:20])
                            if self.glossary else None))
        return "".join(s.text for s in segments).strip()

    @staticmethod
    def _img_part(img: bytes, detail: str = "low") -> dict:
        b64 = base64.b64encode(img).decode()
        return {"type": "image_url", "image_url": {
            "url": f"data:image/jpeg;base64,{b64}", "detail": detail}}

    # ---------- VOD ----------

    def summarize_text(self, title: str, channel: str, transcript: str) -> str:
        # gpt-4o-mini context 128k tokens,中文字≈1 token 以上 → 80k 字就要分段
        if len(transcript) > 80_000:
            size = 80_000
            parts = [transcript[i:i + size]
                     for i in range(0, len(transcript), size)]
            partials = []
            for i, part in enumerate(parts, 1):
                p = (f"This is part {i} of {len(parts)} of the transcript of "
                     f"“{title}”. Write the key points of this part in "
                     f"{self.lang}, keeping every timestamp exactly as it "
                     f"appears:\n\n{part}")
                partials.append(self._chat([{"role": "user", "content": p}]))
            transcript = "\n\n".join(
                f"=== KEY POINTS OF PART {i} ===\n{s}"
                for i, s in enumerate(partials, 1))
        from .summarizer import _transcript_span
        prompt = (self._vod_prompt(title, channel, _transcript_span(transcript))
                  + "\n\n=== TRANSCRIPT ===\n" + transcript)
        return self._chat([{"role": "user", "content": prompt}])

    def summarize_audio(self, path: Path, title: str, channel: str) -> str:
        from .summarizer import _audio_duration
        dur = _audio_duration(path)
        # ffprobe 讀不到時長時不能靜默放行(那會讓花費護欄形同虛設),
        # 改用檔案大小以 48kbps 保守反推分鐘數
        if dur <= 0:
            dur = path.stat().st_size * 8 / 48_000
        # 本地 Whisper 不花錢,花費護欄不該擋它
        if config.STT_PROVIDER != "local":
            est = dur / 60 * 0.003
            if est > config.MAX_AUTO_SPEND_USD:
                raise RuntimeError(t("engine.spend_guard",
                                     mins=f"{dur / 60:.0f}", cost=f"{est:.2f}",
                                     cap=config.MAX_AUTO_SPEND_USD))
        transcript = self._transcribe_file(path)
        # 付費轉錄的逐字稿永久保存,避免重複花費。檔名帶節目、標題與音檔名:
        # 只用標題會在長標題被截斷時碰撞,把先前付費買到的逐字稿蓋掉
        tdir = config.OUTPUT_DIR / "transcripts"
        tdir.mkdir(parents=True, exist_ok=True)
        stem = f"{_safe_stem(f'{channel}_{title}', 70)}_{path.stem}".strip("_")
        (tdir / f"{stem}.txt").write_text(transcript, encoding="utf-8")
        return self.summarize_text(title, channel, transcript)

    def _transcribe_file(self, path: Path) -> str:
        limit = 24 * 1024 * 1024
        if path.stat().st_size <= limit:
            return self.transcribe(path.read_bytes(), path.name)
        if not config.FFMPEG:
            raise RuntimeError(t("engine.audio_needs_ffmpeg"))
        from .summarizer import _hms
        seg_seconds = 900
        seg_dir = path.parent / "stt_segments"
        seg_dir.mkdir(exist_ok=True)
        subprocess.run([
            config.FFMPEG, "-hide_banner", "-loglevel", "error", "-i", str(path),
            "-vn", "-ac", "1", "-ar", "16000", "-c:a", "libmp3lame", "-b:a", "48k",
            "-f", "segment", "-segment_time", str(seg_seconds),
            str(seg_dir / "seg_%03d.mp3")], check=True)
        texts = []
        for i, seg in enumerate(sorted(seg_dir.glob("seg_*.mp3"))):
            # 時間標記要和總結用的 [HH:MM:SS] 一致:_transcript_span 靠它推算總長,
            # 用中文寫會讓它回傳 0,涵蓋全程的要求就悄悄失效
            texts.append(f"[{_hms(i * seg_seconds)}]\n" +
                         self.transcribe(seg.read_bytes(), seg.name))
        return "\n\n".join(texts)

    # ---------- 直播 ----------

    def live_update(self, audio_bytes: bytes, mime: str, state: LiveState,
                    title: str, elapsed_label: str,
                    images: list[bytes] | None = None,
                    dense_lookup=None, chunk_seconds: int = 180,
                    topics: list[str] | None = None) -> dict:
        transcript = self.transcribe(audio_bytes, seconds=float(chunk_seconds))
        context = ""
        if state.rolling_summary:
            context = (f"\n=== SUMMARY SO FAR ===\n{state.rolling_summary}\n"
                       f"=== TOPIC OF THE PREVIOUS SEGMENT ==="
                       f"\n{state.current_topic}\n")
        visual_note = (f"\nAlso attached are {len(images)} screenshots taken "
                       "during this segment, in chronological order; read the "
                       "information on screen (share prices, charts, captions) "
                       "and fold it into the key points."
                       if images else "")
        smart_field = smart_note = ""
        if dense_lookup:
            smart_field = (',\n  "need_frames": [seconds you want extra frames '
                           f'for (0-{chunk_seconds}), at most 3; give an empty '
                           'array if you do not need any]')
            smart_note = ("\nIf the transcript shows the speaker explaining "
                          "something on screen (“look at this chart” and the "
                          "like) and the attached frames are not enough, use "
                          "need_frames to ask for more.")
        topic_field = ""
        if topics:
            topic_field = (',\n  "topic_hits": [if the content is semantically '
                           'related to any of these topics (they need not appear '
                           f'literally), list the ones that match: {topics}; '
                           'otherwise an empty array]')
        prompt = f"""You are following the live stream “{title}” as it happens. Below is the transcript of the segment from around {elapsed_label} into the stream:
=== TRANSCRIPT ===
{transcript or "(no intelligible speech in this segment)"}
{context}{visual_note}{self._gloss_note()}
Reply with JSON, values written in {self.lang}. Format:
{{
  "current_topic": "one sentence describing the current topic",
  "topic_changed": true or false,
  "new_points": ["1 to 4 concrete points, including names, numbers and conclusions"],
  "rolling_summary": "the overall summary with the earlier one merged in, 300 characters or fewer"{smart_field}{topic_field}
}}{smart_note}
If there is no real content, give an empty new_points array and say so in current_topic."""
        content: list = [{"type": "text", "text": prompt}]
        for img in images or []:
            content.append(self._img_part(img))
        data = self._parse_json(
            self._chat([{"role": "user", "content": content}], json_mode=True),
            state)

        wanted = _parse_seconds_list(data.get("need_frames"), chunk_seconds)
        if dense_lookup and wanted:
            extra = dense_lookup(wanted)
            if extra:
                data["_requested_frames"] = wanted
                refine = (f"This was your analysis: {json.dumps({k: v for k, v in data.items() if not k.startswith('_')}, ensure_ascii=False)}\n"
                          f"Here are {len(extra)} screenshots taken around the "
                          f"moments you asked for. Output JSON again in the same "
                          f"format (without need_frames), in {self.lang}, folding "
                          "the concrete information on screen into the key points; "
                          "where a name or number differs, trust the screen.\n"
                          f"(the transcript, as before: {transcript[:2000]})")
                content2: list = [{"type": "text", "text": refine}]
                for img in extra:
                    # 精修時用高解析讀圖:字卡上的公司名/代碼要看得清楚
                    content2.append(self._img_part(img, detail="high"))
                refined = self._parse_json(
                    self._chat([{"role": "user", "content": content2}],
                               json_mode=True), state)
                refined["_requested_frames"] = wanted
                data = refined

        state.current_topic = data.get("current_topic", state.current_topic)
        state.rolling_summary = data.get("rolling_summary", state.rolling_summary)
        points = data.get("new_points") or []
        if points:
            state.timeline.append(
                f"[{elapsed_label}] {state.current_topic}\n" +
                "\n".join(f"  - {p}" for p in points))
        return data

    def finalize_live(self, state: LiveState, title: str) -> str:
        timeline = "\n\n".join(state.timeline) if state.timeline else "(no records)"
        prompt = f"""Below is the timeline recorded live during the stream “{title}”. Write the final, complete summary in {self.lang}, covering:
1. **The bottom line in one sentence**
2. **Which topics were discussed** (in chronological order, with their timestamps)
3. **Key points and conclusions** (be concrete: names, numbers, decisions)
4. **Details worth noting**
Those four labels only describe what each part must contain — phrase the headings yourself, in {self.lang}, like the rest of the summary.

=== TIMELINE ===
{timeline}

=== LATEST ROLLING SUMMARY ===
{state.rolling_summary}"""
        return self._chat([{"role": "user", "content": prompt}])

    # ---------- 內部 ----------

    def _generate(self, contents: list, json_mode: bool = False,
                  retries: int | None = None) -> str:
        """與 Gemini 版對齊的純文字介面(供 /api/ask 等使用)。"""
        text = "\n".join(c for c in contents if isinstance(c, str))
        return self._chat([{"role": "user", "content": text}],
                          json_mode=json_mode, retries=retries)

    def _vod_prompt(self, title: str, channel: str,
                    duration: float | None = None) -> str:
        # 段落標題只用文字描述、不給現成範本:給定範本會被模型原樣抄走,
        # 英文總結就會冒出中文標題。
        span = ""
        if duration and duration > 60:
            from .summarizer import _hms
            span = (f"\nThe recording is {_hms(duration)} long. **Cover it from "
                    f"beginning to end**: the outline has to run all the way to "
                    f"the end (close to {_hms(duration)}), not stop after the "
                    f"opening. Write every timestamp as a zero-padded "
                    f"[HH:MM:SS] — [00:35:00] for minute 35, for instance; "
                    f"never shorten it to [35:00].")
        return f"""Summarize this video/podcast. Write the whole summary in {self.lang}.{self._gloss_note()}{span}
Title: {title}
Channel: {channel}

Answer in Markdown with exactly these four sections, in this order, each opened by its own level-2 heading (##). The section names below only describe what belongs in each section — they are not a template: phrase the headings yourself, in {self.lang}, like the rest of the summary.
1. One-sentence summary: the whole thing in a single sentence.
2. Outline: the content in chronological order, with timestamps wherever the transcript has them.
3. Key points: the concrete substance — names, figures, conclusions, recommendations.
4. Worth a closer look."""

    def _parse_json(self, raw: str, state: LiveState) -> dict:
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {"current_topic": state.current_topic, "topic_changed": False,
                    "new_points": [raw.strip()[:200]] if raw.strip() else [],
                    "rolling_summary": state.rolling_summary}

"""Gemini 總結器:文字逐字稿總結、音訊檔總結、直播滾動式總結。"""
import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from google import genai
from google.genai import types

from . import config, stats
from .i18n import t


def _audio_duration(path: Path) -> float:
    """用 ffprobe 取得音訊長度(秒);失敗回傳 0。"""
    import subprocess
    ffprobe = None
    if config.FFMPEG:
        # 跟著 ffmpeg 的檔名慣例:Windows 有 .exe,其他平台沒有。
        # 寫死 .exe 會讓非 Windows 永遠取不到時長,長音訊分段與涵蓋要求就失效。
        ff = Path(config.FFMPEG)
        ffprobe = ff.with_name("ffprobe" + ff.suffix)
    if not ffprobe or not ffprobe.exists():
        return 0.0
    try:
        r = subprocess.run(
            [str(ffprobe), "-v", "quiet", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, timeout=60)
        return float(r.stdout.strip())
    except (subprocess.SubprocessError, ValueError):
        return 0.0


def _hms(seconds: float) -> str:
    """秒 → 零填充 HH:MM:SS。語言中立且不會被誤讀成分:秒。"""
    s = int(seconds)
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def _transcript_span(transcript: str) -> float:
    """從逐字稿最後一個 [時:分:秒] 標記推算內容總長(秒);沒有標記回傳 0。"""
    import re
    marks = re.findall(r"\[(\d+):(\d{2})(?::(\d{2}))?\]", transcript)
    if not marks:
        return 0.0
    h, m, s = marks[-1]
    return (int(h) * 3600 + int(m) * 60 + int(s)) if s \
        else (int(h) * 60 + int(m))


def _parse_seconds_list(raw, limit: int) -> list[int]:
    """把模型回傳的 need_frames 轉成秒數列表(容忍 "1:30" 這類格式)。"""
    out: list[int] = []
    for item in (raw or [])[:3]:
        try:
            s = int(item)
        except (TypeError, ValueError):
            try:
                mm, ss = str(item).split(":")
                s = int(mm) * 60 + int(ss)
            except ValueError:
                continue
        if 0 <= s <= limit:
            out.append(s)
    return out


@dataclass
class LiveState:
    """直播的滾動狀態:目前話題 + 累積時間軸 + 滾動摘要。"""
    current_topic: str = ""
    rolling_summary: str = ""
    timeline: list[str] = field(default_factory=list)  # "[時間] 話題:重點"
    media: list[tuple[str, str]] = field(default_factory=list)  # (時間, 相對路徑)


class GeminiSummarizer:
    provider = "gemini"

    def __init__(self, api_key: str | None = None, model: str | None = None,
                 lang: str | None = None):
        self.retries = 3
        self.glossary: list[str] = []
        key = api_key or config.GEMINI_API_KEY
        if not key:
            raise RuntimeError(t("engine.no_gemini_key"))
        # 明確設 timeout(毫秒):預設無上限,網路異常時呼叫會永久懸掛
        self.client = genai.Client(
            api_key=key,
            http_options=types.HttpOptions(timeout=120_000))
        self.model = model or config.MODEL
        self.lang = lang or config.LANG

    # ---------- VOD:文字逐字稿 ----------

    def summarize_text(self, title: str, channel: str, transcript: str) -> str:
        if len(transcript) > config.MAX_TRANSCRIPT_CHARS:
            return self._summarize_long_text(title, channel, transcript)
        prompt = (self._vod_prompt(title, channel, _transcript_span(transcript))
                  + "\n\n=== TRANSCRIPT ===\n" + transcript)
        return self._generate([prompt])

    def _summarize_long_text(self, title: str, channel: str, transcript: str) -> str:
        """超長逐字稿:分段摘要後再彙整。"""
        size = config.MAX_TRANSCRIPT_CHARS // 2
        parts = [transcript[i:i + size] for i in range(0, len(transcript), size)]
        partials = []
        for i, part in enumerate(parts, 1):
            p = (f"This is part {i} of {len(parts)} of the transcript of "
                 f"“{title}”. Write the key points of THIS part in {self.lang}, "
                 f"keeping every timestamp exactly as it appears:\n\n{part}")
            partials.append(self._generate([p]))
        merged = "\n\n".join(f"=== KEY POINTS OF PART {i} ===\n{s}"
                             for i, s in enumerate(partials, 1))
        prompt = (self._vod_prompt(title, channel)
                  + "\n\nBelow are the key points of each part of the transcript."
                    " Merge them into one complete summary:\n\n" + merged)
        return self._generate([prompt])

    # ---------- VOD:音訊檔 ----------

    def summarize_audio(self, path: Path, title: str, channel: str) -> str:
        dur = _audio_duration(path)
        # 超過 20 分鐘就分段:單次整檔時模型容易「只詳述開頭」,分段能保證後段也被讀到
        if dur and dur > 1200:
            return self._summarize_long_audio(path, title, channel, dur)
        audio_part = self._audio_part(path)
        prompt = (self._vod_prompt(title, channel, dur)
                  + "\nListen to the whole recording, from beginning to end, "
                    "before you summarize it.")
        return self._generate([audio_part, prompt])

    def _summarize_long_audio(self, path: Path, title: str, channel: str,
                              dur: float) -> str:
        seg_len = 1800
        seg_dir = path.parent / "gem_segments"
        seg_dir.mkdir(exist_ok=True)
        import subprocess
        subprocess.run([
            config.FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
            "-i", str(path), "-vn", "-ac", "1", "-ar", "16000",
            "-c:a", "libmp3lame", "-b:a", "48k",
            "-f", "segment", "-segment_time", str(seg_len),
            str(seg_dir / "seg_%03d.mp3")], check=True)
        segs = sorted(seg_dir.glob("seg_*.mp3"))
        n = len(segs)
        partials: list[str] = []
        failed: list[str] = []
        for i, seg in enumerate(segs):
            t0, t1 = i * seg_len, min((i + 1) * seg_len, int(dur))
            # 零填充 HH:MM:SS:不會被誤讀成分:秒,也與網頁跳轉連結的格式一致
            label = f"{_hms(t0)} ~ {_hms(t1)}"
            prompt = (f"This is part {i + 1} of {n} of the audio of “{title}”. "
                      f"It covers {label} of the recording "
                      f"(total length {_hms(dur)}). "
                      f"Listen to it and write the concrete key points of THIS "
                      f"part in {self.lang} — names, numbers, conclusions — as "
                      f"4 to 8 bullets. Start every bullet with the time the "
                      f"point occurs in the **whole** recording, written as a "
                      f"zero-padded [HH:MM:SS], for example [{_hms(t0 + 300)}].")
            try:
                part = types.Part.from_bytes(data=seg.read_bytes(),
                                             mime_type="audio/mpeg")
                partials.append(f"=== {label} ===\n" +
                                self._generate([part, prompt]))
                print("  " + t("run.long_audio_part", i=i + 1, n=n))
            except Exception as e:
                # 失敗的段落不能留下佔位文字:彙整提示詞要求「涵蓋每個時段」,
                # 模型會替這些空洞編出帶正確時間戳的假內容
                failed.append(label)
                print("  " + t("run.long_audio_failed", i=i + 1, n=n, err=e))
        if not partials:
            raise RuntimeError(t("engine.long_audio_all_failed", n=n))
        merged = "\n\n".join(partials)
        gap_note = ""
        if failed:
            gap_note = (f"- No content could be obtained for these stretches: "
                        f"{', '.join(failed)}. They **must not appear in the "
                        f"outline** and you must not guess what was said in "
                        f"them; instead, at the very end of the summary, note "
                        f"that these stretches could not be processed and list "
                        f"them\n")
        prompt = (self._vod_prompt(title, channel, dur)
                  + f"\n\nBelow are the key points for each stretch of this "
                    f"episode (total length {_hms(dur)}). Merge them into one "
                    f"complete summary, following these rules:\n"
                    f"- The outline needs **at least 2 entries per stretch** and "
                    f"must cover, in chronological order, all {len(partials)} "
                    f"stretches given below; the last entry has to reach the end "
                    f"of the final stretch\n"
                    f"- Start every outline entry with a zero-padded [HH:MM:SS] "
                    f"timestamp, for example [00:35:00]\n"
                    f"- Draw the key points evenly from every stretch; do not "
                    f"write up only the opening one\n"
                    f"- **Write only from the material below; never invent "
                    f"anything it does not mention**\n"
                    + gap_note + "\n" + merged)
        return self._generate([prompt])

    # ---------- 直播:滾動式更新 ----------

    def set_glossary(self, terms: list[str]) -> None:
        self.glossary = terms or []

    def _gloss_note(self) -> str:
        if not self.glossary:
            return ""
        return ("\nKnown proper nouns — whenever you hear something that sounds "
                "close to one of these, always use this spelling: "
                + ", ".join(self.glossary))

    def live_update(self, audio_bytes: bytes, mime: str, state: LiveState,
                    title: str, elapsed_label: str,
                    images: list[bytes] | None = None,
                    dense_lookup=None, chunk_seconds: int = 180,
                    topics: list[str] | None = None) -> dict:
        """輸入一段直播音訊(可附畫面截圖)+ 先前狀態,回傳 JSON 更新。

        dense_lookup:選用的 callable([秒數]) -> list[bytes]。提供時,
        模型可回傳 need_frames 主動要求加看特定時間點的畫面,
        會觸發第二次精修呼叫。
        """
        context = ""
        if state.rolling_summary:
            context = (f"\n=== SUMMARY SO FAR ===\n{state.rolling_summary}\n"
                       f"=== TOPIC OF THE PREVIOUS SEGMENT ==="
                       f"\n{state.current_topic}\n")
        visual_note = ""
        if images:
            visual_note = (f"\nAlso attached are {len(images)} screenshots taken "
                           "during this segment, in chronological order. Read the "
                           "information on screen as well (share prices, indices, "
                           "charts, captions, tickers) and fold the concrete "
                           "numbers you see into the key points.")
        smart_field = ""
        smart_note = ""
        if dense_lookup:
            smart_field = (',\n  "need_frames": [seconds you want extra frames for '
                           '(whole numbers, relative to the start of THIS audio '
                           f'segment, range 0-{chunk_seconds}), at most 3; give an '
                           'empty array if you do not need any]')
            smart_note = ("\nIf the speaker is clearly explaining something on "
                          "screen (“look at this chart”, “this one here”, “this "
                          "pattern”) and the attached screenshots do not show it, "
                          "use need_frames to ask for frames at those moments.")
        topic_field = ""
        if topics:
            topic_field = (',\n  "topic_hits": [if this segment is semantically '
                           'related to any of these watched topics (they need not '
                           f'appear literally), list the ones that match: {topics}; '
                           'otherwise an empty array]')
        prompt = f"""You are listening to the live stream “{title}” as it happens. The audio below is the latest segment, from around {elapsed_label} into the stream.{context}{visual_note}{self._gloss_note()}
Listen to this audio and reply with JSON and nothing else. Write the values in {self.lang}. Format:
{{
  "current_topic": "one sentence describing what is being discussed right now",
  "topic_changed": true or false (whether the topic clearly changed from the previous segment),
  "new_points": ["the concrete points made in this audio, 1 to 4 of them, including any names, numbers and conclusions"],
  "rolling_summary": "the earlier summary and this new material merged into one overall summary, 300 characters or fewer"{smart_field}{topic_field}
}}{smart_note}
If this audio is only music, a standby screen or has no real content, give an empty new_points array and say so in current_topic."""
        audio_part = types.Part.from_bytes(data=audio_bytes, mime_type=mime)
        parts: list = [audio_part]
        for img in images or []:
            parts.append(types.Part.from_bytes(data=img, mime_type="image/jpeg"))
        data = self._parse_live_json(self._generate(parts + [prompt], json_mode=True),
                                     state)

        # 模型主動要求加看畫面 → 撈密集截圖做第二次精修
        wanted = _parse_seconds_list(data.get("need_frames"), chunk_seconds)
        if dense_lookup and wanted:
            extra = dense_lookup(wanted)
            if extra:
                data["_requested_frames"] = wanted
                refine_prompt = f"""This was your analysis after listening to that audio:
{json.dumps({k: v for k, v in data.items() if not k.startswith('_')}, ensure_ascii=False)}

Here are {len(extra)} screenshots taken around the moments you asked for ({", ".join(str(s) for s in wanted)} seconds).
Output JSON again in the same format, in {self.lang}, without the need_frames field:
- Read the concrete information on screen (numbers, ticker symbols, names, chart patterns, captions) and fold it into new_points and rolling_summary
- Where a name or number on screen differs from what you heard, trust the screen and correct it"""
                parts2: list = [audio_part]
                for img in extra:
                    parts2.append(types.Part.from_bytes(data=img,
                                                        mime_type="image/jpeg"))
                refined = self._parse_live_json(
                    self._generate(parts2 + [refine_prompt], json_mode=True), state)
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
        """直播結束(或手動停止)時,把時間軸彙整成完整總結。"""
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
        return self._generate([prompt])

    # ---------- 內部 ----------

    def _vod_prompt(self, title: str, channel: str,
                    duration: float | None = None) -> str:
        # 一定要告知總長並要求涵蓋全程:否則模型常只詳述開頭幾分鐘就收尾。
        # 時間一律用零填充的 [HH:MM:SS]:語言中立、不會被誤讀成秒數,
        # 且符合網頁把時間戳轉成跳轉連結的格式。
        # 段落標題只用文字描述、不給現成範本:給定範本會被模型原樣抄走,
        # 英文總結就會冒出中文標題。
        span = ""
        if duration and duration > 60:
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
2. Outline: the main segments in chronological order, spread evenly over the entire recording, every entry carrying its timestamp.
3. Key points: the concrete substance — names mentioned, figures, conclusions, recommendations — as a bullet list.
4. Worth a closer look: whatever is contested, left unanswered, or that the speaker stressed."""

    def _audio_part(self, path: Path):
        mime = "audio/mp4" if path.suffix.lower() in (".m4a", ".mp4") else \
               "audio/mpeg" if path.suffix.lower() == ".mp3" else \
               "audio/webm" if path.suffix.lower() == ".webm" else "audio/ogg"
        if path.stat().st_size <= config.INLINE_AUDIO_LIMIT:
            return types.Part.from_bytes(data=path.read_bytes(), mime_type=mime)
        f = self.client.files.upload(file=str(path), config={"mime_type": mime})
        while f.state and f.state.name == "PROCESSING":
            time.sleep(3)
            f = self.client.files.get(name=f.name)
        if f.state and f.state.name == "FAILED":
            raise RuntimeError(t("engine.gemini_file_failed", name=path.name))
        return f

    def _generate(self, contents: list, json_mode: bool = False,
                  retries: int | None = None) -> str:
        retries = retries or self.retries
        gen_config = types.GenerateContentConfig(
            response_mime_type="application/json" if json_mode else "text/plain",
        )
        last_err: Exception | None = None
        for attempt in range(retries):
            try:
                stats.record_call()
                resp = self.client.models.generate_content(
                    model=self.model, contents=contents, config=gen_config)
                um = getattr(resp, "usage_metadata", None)
                if um:
                    total = um.total_token_count or 0
                    prompt = um.prompt_token_count or 0
                    stats.record_usage("gemini", in_tokens=prompt,
                                       out_tokens=max(total - prompt, 0))
                return (resp.text or "").strip()
            except Exception as e:  # 429/暫時性錯誤重試
                last_err = e
                msg = str(e)
                if any(k in msg for k in ("API key", "API_KEY", "PERMISSION_DENIED",
                                          "UNAUTHENTICATED")):
                    stats.record_failure()
                    raise RuntimeError(
                        t("engine.gemini_key_invalid", err=msg)) from e
                # 模型不存在/已下架:重試同一個模型永遠不會成功,直接拋出可行動的訊息
                if "NOT_FOUND" in msg or "no longer available" in msg:
                    stats.record_failure()
                    raise RuntimeError(
                        t("engine.gemini_model_gone", model=self.model,
                          err=msg[:200])) from e
                # 付費專案的預付點數耗盡:等待重試無用,需要儲值或改用免費層金鑰
                if "prepayment credits" in msg or "billing" in msg.lower():
                    stats.record_failure()
                    raise RuntimeError(
                        t("engine.gemini_credits", err=msg[:200])) from e
                stats.record_retry(msg)
                wait = 15 * (attempt + 1)
                print("  " + t("engine.gemini_retry", wait=wait,
                               attempt=attempt + 1, retries=retries, err=e))
                time.sleep(wait)
        stats.record_failure()
        raise RuntimeError(t("engine.gemini_failed", err=last_err))

    def _parse_live_json(self, raw: str, state: LiveState) -> dict:
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {"current_topic": state.current_topic, "topic_changed": False,
                    "new_points": [raw.strip()[:200]] if raw.strip() else [],
                    "rolling_summary": state.rolling_summary}


# ---------- 引擎工廠與自動切換 ----------

_QUOTA_MARKERS = ("RESOURCE_EXHAUSTED", "429", "quota", "rate limit",
                  "503", "UNAVAILABLE",
                  # 模型下架(404)也該切備援,重試同一個模型永遠不會成功
                  "NOT_FOUND", "no longer available")
_RETRY_PRIMARY_AFTER = 600  # 切到備援後,幾秒後回頭再試免費引擎


class AutoSummarizer:
    """優先 Gemini(免費);遇到額度/限流/過載錯誤且有 OpenAI 金鑰時自動切換,
    冷卻期過後會自動回頭試 Gemini。"""
    provider = "auto"

    _METHODS = ("summarize_text", "summarize_audio", "live_update",
                "finalize_live", "_generate")

    def __init__(self, model: str | None = None, lang: str | None = None):
        self.lang = lang
        self.glossary: list[str] = []
        self.primary = GeminiSummarizer(model=model, lang=lang)
        self.primary.retries = 1  # 有備援時快速失敗、快速切換
        self.fallback = None
        self.active = self.primary
        self._switched_at = 0.0
        # 備援本身也沒額度時,再切過去只會更糟:標記為不可用並回到主引擎
        self._fallback_dead = False

    def set_glossary(self, terms: list[str]) -> None:
        self.glossary = terms or []
        self.primary.set_glossary(self.glossary)
        if self.fallback:
            self.fallback.set_glossary(self.glossary)

    def _switch(self) -> bool:
        """切到備援;備援已知無額度時回傳 False,讓呼叫端留在主引擎。"""
        if self._fallback_dead:
            return False
        if self.fallback is None:
            from .openai_summarizer import OpenAISummarizer
            self.fallback = OpenAISummarizer(lang=self.lang)
            self.fallback.set_glossary(self.glossary)
        if self.active is not self.fallback:
            print("⚠ " + t("engine.switch_to_openai"))
        self.active = self.fallback
        self._switched_at = time.time()
        return True

    @staticmethod
    def _is_dead_credit_error(msg: str) -> bool:
        """餘額耗盡(非暫時性限流):等再久都不會好。"""
        low = msg.lower()
        return ("insufficient_quota" in low or "credit_balance_exhausted" in low
                or "no credits remaining" in low or "billing" in low)

    def _call(self, name, *args, **kwargs):
        if (self.active is self.fallback and
                time.time() - self._switched_at > _RETRY_PRIMARY_AFTER):
            print("↩ " + t("engine.retry_primary"))
            self.active = self.primary
        if self.active is self.primary:
            try:
                return getattr(self.primary, name)(*args, **kwargs)
            except RuntimeError as e:
                if not (config.OPENAI_API_KEY and any(
                        m in str(e) for m in _QUOTA_MARKERS)):
                    raise
                if not self._switch():
                    # 備援已確定沒額度:與其空轉,不如讓主引擎正常重試
                    self.primary.retries = 3
                    return getattr(self.primary, name)(*args, **kwargs)
        try:
            return getattr(self.active, name)(*args, **kwargs)
        except RuntimeError as e:
            msg = str(e)
            if self.active is self.fallback and self._is_dead_credit_error(msg):
                # 備援餘額耗盡:標記後永久回到主引擎,不再被冷卻期釘在死引擎上
                self._fallback_dead = True
                self.active = self.primary
                self.primary.retries = 3
                print("⚠ " + t("engine.fallback_exhausted"))
                return getattr(self.primary, name)(*args, **kwargs)
            raise

    def __getattr__(self, name):
        if name in self._METHODS:
            return lambda *a, **k: self._call(name, *a, **k)
        raise AttributeError(name)


def make_summarizer(provider: str | None = None, model: str | None = None,
                    lang: str | None = None):
    provider = (provider or config.PROVIDER or "auto").lower()
    if provider == "gemini":
        return GeminiSummarizer(model=model, lang=lang)
    if provider == "openai":
        from .openai_summarizer import OpenAISummarizer
        return OpenAISummarizer(model=model, lang=lang)
    # auto
    has_g, has_o = bool(config.GEMINI_API_KEY), bool(config.OPENAI_API_KEY)
    if has_g and has_o:
        return AutoSummarizer(model=model, lang=lang)
    if has_g:
        return GeminiSummarizer(model=model, lang=lang)
    if has_o:
        from .openai_summarizer import OpenAISummarizer
        return OpenAISummarizer(model=model, lang=lang)
    raise RuntimeError(t("engine.no_api_key"))

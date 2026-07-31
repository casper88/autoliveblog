"""Gemini 總結器:文字逐字稿總結、音訊檔總結、直播滾動式總結。"""
import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from google import genai
from google.genai import types

from . import config, stats


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
            raise RuntimeError(
                "找不到 GEMINI_API_KEY。請到 https://aistudio.google.com/apikey "
                "免費申請,並寫入專案根目錄的 .env(參考 .env.example)。"
            )
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
                  + "\n\n=== 逐字稿 ===\n" + transcript)
        return self._generate([prompt])

    def _summarize_long_text(self, title: str, channel: str, transcript: str) -> str:
        """超長逐字稿:分段摘要後再彙整。"""
        size = config.MAX_TRANSCRIPT_CHARS // 2
        parts = [transcript[i:i + size] for i in range(0, len(transcript), size)]
        partials = []
        for i, part in enumerate(parts, 1):
            p = (f"以下是「{title}」逐字稿的第 {i}/{len(parts)} 段,"
                 f"請用{self.lang}整理這一段的重點(保留時間標記):\n\n{part}")
            partials.append(self._generate([p]))
        merged = "\n\n".join(f"【第 {i} 段重點】\n{s}" for i, s in enumerate(partials, 1))
        prompt = (self._vod_prompt(title, channel)
                  + "\n\n以下是各段落的重點整理,請彙整成一份完整總結:\n\n" + merged)
        return self._generate([prompt])

    # ---------- VOD:音訊檔 ----------

    def summarize_audio(self, path: Path, title: str, channel: str) -> str:
        dur = _audio_duration(path)
        # 超過 20 分鐘就分段:單次整檔時模型容易「只詳述開頭」,分段能保證後段也被讀到
        if dur and dur > 1200:
            return self._summarize_long_audio(path, title, channel, dur)
        audio_part = self._audio_part(path)
        prompt = (self._vod_prompt(title, channel, dur)
                  + "\n請完整聆聽這段音訊(從頭到尾)後進行總結。")
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
            prompt = (f"這是「{title}」的第 {i + 1}/{n} 段音訊,"
                      f"對應整場的 {label}(整場全長 {_hms(dur)})。"
                      f"請用{self.lang}聆聽後整理這一段的具體重點"
                      f"(人名、數字、結論),條列 4~8 條。"
                      f"每條開頭標上該內容在**整場**的時間,"
                      f"格式為零填充的 [HH:MM:SS],例如 [{_hms(t0 + 300)}]。")
            try:
                part = types.Part.from_bytes(data=seg.read_bytes(),
                                             mime_type="audio/mpeg")
                partials.append(f"【{label}】\n" + self._generate([part, prompt]))
                print(f"  長音訊分段 {i + 1}/{n} 完成")
            except Exception as e:
                # 失敗的段落不能留下佔位文字:彙整提示詞要求「涵蓋每個時段」,
                # 模型會替這些空洞編出帶正確時間戳的假內容
                failed.append(label)
                print(f"  長音訊分段 {i + 1}/{n} 失敗:{e}")
        if not partials:
            raise RuntimeError(f"長音訊的 {n} 段全部總結失敗,無法產出報告")
        merged = "\n\n".join(partials)
        gap_note = ""
        if failed:
            gap_note = (f"- 以下時段無法取得內容:{'、'.join(failed)}。"
                        f"這些時段**不要出現在大綱裡**,也不可臆測內容;"
                        f"請在總結末尾註明「以下時段未能解析」並列出\n")
        prompt = (self._vod_prompt(title, channel, dur)
                  + f"\n\n以下是這集(全長 {_hms(dur)})各時段的重點整理。"
                    f"請彙整成一份完整總結,規則:\n"
                    f"- 內容大綱**每個時段至少 2 條**,依時間順序涵蓋下方全部 "
                    f"{len(partials)} 個時段,最後一條要對應到最後一個時段的結尾\n"
                    f"- 每條大綱開頭標上 [HH:MM:SS] 零填充時間,例如 [00:35:00]\n"
                    f"- 關鍵重點要平均取材於各時段,不可只寫開頭那一段\n"
                    f"- **只根據下方提供的內容撰寫,沒有提到的絕不臆測**\n"
                    + gap_note + "\n" + merged)
        return self._generate([prompt])

    # ---------- 直播:滾動式更新 ----------

    def set_glossary(self, terms: list[str]) -> None:
        self.glossary = terms or []

    def _gloss_note(self) -> str:
        if not self.glossary:
            return ""
        return ("\n已知專有名詞(聽到相近發音時,一律以此拼寫為準):"
                + "、".join(self.glossary))

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
            context = (f"\n=== 目前為止的摘要 ===\n{state.rolling_summary}\n"
                       f"=== 上一段的話題 ===\n{state.current_topic}\n")
        visual_note = ""
        if images:
            visual_note = (f"\n另附上這段期間的 {len(images)} 張畫面截圖(依時間順序)。"
                           "請一併讀取畫面上的資訊(股價、指數、圖表、字卡、跑馬燈),"
                           "把畫面上的具體數字納入重點。")
        smart_field = ""
        smart_note = ""
        if dense_lookup:
            smart_field = (',\n  "need_frames": [需要加看畫面的秒數(整數,相對這段音訊開頭'
                           f',範圍 0~{chunk_seconds}),最多 3 個;不需要就給空陣列]')
            smart_note = ("\n若講者明顯正在講解畫面上的內容(例如「看這張圖」「畫面上這檔」"
                          "「這個型態」),而附上的截圖時間點看不到該內容,"
                          "請用 need_frames 要求加看那幾個時間點的畫面。")
        topic_field = ""
        if topics:
            topic_field = (',\n  "topic_hits": [這段內容若與這些關注主題「語意相關」'
                           f'(不必字面出現),列出相關者:{topics};否則空陣列]')
        prompt = f"""你正在即時收聽直播「{title}」。以下音訊是直播中 {elapsed_label} 左右的最新片段。{context}{visual_note}{self._gloss_note()}
請聆聽這段音訊,用{self.lang}回傳 JSON(不要有其他文字),格式:
{{
  "current_topic": "一句話描述目前正在討論的話題",
  "topic_changed": true或false(相較於上一段話題是否明顯轉換),
  "new_points": ["這段音訊中的具體重點,1~4 條,包含提到的人名、數字、結論"],
  "rolling_summary": "把先前摘要與這段新內容融合後的整體摘要,300字以內"{smart_field}{topic_field}
}}{smart_note}
若這段音訊只有音樂、待機畫面或無實質內容,new_points 給空陣列並在 current_topic 說明。"""
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
                refine_prompt = f"""你剛才聽完這段音訊後的分析是:
{json.dumps({k: v for k, v in data.items() if not k.startswith('_')}, ensure_ascii=False)}

現在補上你要求的時間點({"、".join(str(s) for s in wanted)} 秒)附近的 {len(extra)} 張畫面截圖。
請重新輸出同格式的 JSON(不要包含 need_frames):
- 讀取畫面上的具體資訊(數字、股票代碼、名稱、圖表型態、字卡),補進 new_points 與 rolling_summary
- 若畫面上的正確名稱/數字與你聽到的不同,以畫面為準修正"""
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
        timeline = "\n\n".join(state.timeline) if state.timeline else "(無記錄)"
        prompt = f"""以下是直播「{title}」的即時記錄時間軸。請用{self.lang}寫出最終完整總結,包含:
1. **一句話結論**
2. **討論了哪些主題**(依時間順序,附時間點)
3. **關鍵重點與結論**(具體:人名、數字、決定)
4. **值得注意的細節**

=== 時間軸記錄 ===
{timeline}

=== 最後的滾動摘要 ===
{state.rolling_summary}"""
        return self._generate([prompt])

    # ---------- 內部 ----------

    def _vod_prompt(self, title: str, channel: str,
                    duration: float | None = None) -> str:
        # 一定要告知總長並要求涵蓋全程:否則模型常只詳述開頭幾分鐘就收尾。
        # 時間一律用零填充的 [HH:MM:SS]:語言中立、不會被誤讀成秒數,
        # 且符合網頁把時間戳轉成跳轉連結的格式。
        span = ""
        if duration and duration > 60:
            span = (f"\n這部內容全長 {_hms(duration)}。**務必涵蓋從頭到尾的完整內容**,"
                    f"大綱要一路列到最後(接近 {_hms(duration)}),不可只總結開頭。"
                    f"每個時間標記一律寫成 [HH:MM:SS] 零填充格式,"
                    f"例如 [00:35:00] 代表第 35 分鐘;不可省略成 [35:00]。")
        return f"""請用{self.lang}總結這部影片/Podcast。{self._gloss_note()}{span}
標題:{title}
頻道:{channel}

輸出格式(Markdown):
## 一句話總結
## 內容大綱
(依時間順序列出主要段落,平均分布於整段內容,每段附上時間標記)
## 關鍵重點
(具體重點:提到的人名、數據、結論、建議,條列)
## 值得深入的地方
(有爭議、留下疑問、或講者特別強調的部分)"""

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
            raise RuntimeError(f"Gemini 檔案處理失敗:{path.name}")
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
                    raise RuntimeError(f"Gemini 金鑰無效或無權限:{msg}") from e
                # 模型不存在/已下架:重試同一個模型永遠不會成功,直接拋出可行動的訊息
                if "NOT_FOUND" in msg or "no longer available" in msg:
                    stats.record_failure()
                    raise RuntimeError(
                        f"Gemini 模型「{self.model}」無法使用(可能已對新專案下架)。"
                        f"請改用可用的模型,例如設環境變數 "
                        f"AUTOLIVEBLOG_MODEL=gemini-3.5-flash-lite。原始錯誤:{msg[:200]}"
                    ) from e
                # 付費專案的預付點數耗盡:等待重試無用,需要儲值或改用免費層金鑰
                if "prepayment credits" in msg or "billing" in msg.lower():
                    stats.record_failure()
                    raise RuntimeError(
                        "Gemini 專案的預付點數已用盡(這不是每日免費額度問題)。"
                        "請到 https://ai.studio/projects 儲值,或改用未綁定帳單的"
                        f"免費層金鑰。原始錯誤:{msg[:200]}"
                    ) from e
                stats.record_retry(msg)
                wait = 15 * (attempt + 1)
                print(f"  [Gemini 呼叫失敗,{wait}s 後重試 {attempt+1}/{retries}] {e}")
                time.sleep(wait)
        stats.record_failure()
        raise RuntimeError(f"Gemini 呼叫失敗:{last_err}")

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
            print("⚠ Gemini 受限/過載,自動切換 OpenAI 引擎續跑")
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
            print("↩ 冷卻期結束,回頭嘗試 Gemini")
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
                print("⚠ OpenAI 餘額已用盡,停用備援並改回 Gemini(正常重試)")
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
    raise RuntimeError(
        "找不到任何 API 金鑰。請在 .env 設定 GEMINI_API_KEY(免費,"
        "https://aistudio.google.com/apikey)或 OPENAI_API_KEY。")

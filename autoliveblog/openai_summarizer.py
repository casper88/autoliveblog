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
from .summarizer import LiveState, _parse_seconds_list

_QA_RULES = "記錄裡沒有的資訊請直接說沒有提到,不要腦補。"


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
            raise RuntimeError(
                "找不到 OPENAI_API_KEY。請把 OPENAI_API_KEY=sk-... 加入專案根目錄的 .env。")
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
        return ("\n已知專有名詞(拼寫以此為準):" + "、".join(self.glossary))

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
                    raise RuntimeError(f"OpenAI 金鑰無效:{msg}") from e
                stats.record_retry(msg)
                wait = 10 * (attempt + 1)
                print(f"  [OpenAI 呼叫失敗,{wait}s 後重試 {attempt+1}/{retries}] {e}")
                time.sleep(wait)
        stats.record_failure()
        raise RuntimeError(f"OpenAI 呼叫失敗:{last_err}")

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
        if self.glossary:  # 詞彙偏置:提高專有名詞辨識正確率
            kwargs["prompt"] = "可能出現的專有名詞:" + "、".join(self.glossary[:20])
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
            print("[stt] 載入本地 Whisper 模型(small, CPU int8)…")
            OpenAISummarizer._local_model = WhisperModel(
                "small", device="cpu", compute_type="int8")
        segments, _ = OpenAISummarizer._local_model.transcribe(
            io.BytesIO(audio_bytes),
            language=(config.STT_LANG or None), beam_size=1, vad_filter=True,
            initial_prompt=("、".join(self.glossary[:20])
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
                p = (f"以下是「{title}」逐字稿的第 {i}/{len(parts)} 段,"
                     f"請用{self.lang}整理重點(保留時間標記):\n\n{part}")
                partials.append(self._chat([{"role": "user", "content": p}]))
            transcript = "\n\n".join(
                f"【第 {i} 段重點】\n{s}" for i, s in enumerate(partials, 1))
        prompt = self._vod_prompt(title, channel) + "\n\n=== 逐字稿 ===\n" + transcript
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
                raise RuntimeError(
                    f"音訊長 {dur / 60:.0f} 分鐘,OpenAI 轉錄約需 ${est:.2f},"
                    f"超過自動花費上限 ${config.MAX_AUTO_SPEND_USD}。"
                    f"若確定要花,請設 AUTOLIVEBLOG_MAX_AUTO_SPEND_USD 提高上限,"
                    f"或設 AUTOLIVEBLOG_STT_PROVIDER=local 用本地免費轉錄。")
        transcript = self._transcribe_file(path)
        # 付費轉錄的逐字稿永久保存,避免重複花費;檔名帶節目與標題才不會互相覆寫
        tdir = config.OUTPUT_DIR / "transcripts"
        tdir.mkdir(parents=True, exist_ok=True)
        stem = _safe_stem(f"{channel}_{title}") or path.stem
        (tdir / f"{stem}.txt").write_text(transcript, encoding="utf-8")
        return self.summarize_text(title, channel, transcript)

    def _transcribe_file(self, path: Path) -> str:
        limit = 24 * 1024 * 1024
        if path.stat().st_size <= limit:
            return self.transcribe(path.read_bytes(), path.name)
        if not config.FFMPEG:
            raise RuntimeError("音訊超過 24MB 需要 ffmpeg 切段轉錄")
        seg_dir = path.parent / "stt_segments"
        seg_dir.mkdir(exist_ok=True)
        subprocess.run([
            config.FFMPEG, "-hide_banner", "-loglevel", "error", "-i", str(path),
            "-vn", "-ac", "1", "-ar", "16000", "-c:a", "libmp3lame", "-b:a", "48k",
            "-f", "segment", "-segment_time", "900",
            str(seg_dir / "seg_%03d.mp3")], check=True)
        texts = []
        for i, seg in enumerate(sorted(seg_dir.glob("seg_*.mp3"))):
            texts.append(f"[第 {i * 15} 分鐘起]\n" +
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
            context = (f"\n=== 目前為止的摘要 ===\n{state.rolling_summary}\n"
                       f"=== 上一段的話題 ===\n{state.current_topic}\n")
        visual_note = (f"\n另附上這段期間的 {len(images)} 張畫面截圖(依時間順序),"
                       "請讀取畫面上的資訊(股價、圖表、字卡)納入重點。"
                       if images else "")
        smart_field = smart_note = ""
        if dense_lookup:
            smart_field = (',\n  "need_frames": [需要加看畫面的秒數(0~'
                           f'{chunk_seconds}),最多 3 個;不需要給空陣列]')
            smart_note = ("\n若逐字稿顯示講者在講解畫面(「看這張圖」等)而附圖不足,"
                          "用 need_frames 要求加看。")
        topic_field = ""
        if topics:
            topic_field = (',\n  "topic_hits": [內容若與這些主題「語意相關」'
                           f'(不必字面出現),列出相關者:{topics};否則空陣列]')
        prompt = f"""你正在即時追蹤直播「{title}」。以下是直播中 {elapsed_label} 左右片段的逐字稿:
=== 逐字稿 ===
{transcript or "(這段沒有可辨識的語音)"}
{context}{visual_note}{self._gloss_note()}
請用{self.lang}回傳 JSON,格式:
{{
  "current_topic": "一句話描述目前話題",
  "topic_changed": true或false,
  "new_points": ["具體重點 1~4 條,含人名、數字、結論"],
  "rolling_summary": "融合先前摘要後的整體摘要,300字以內"{smart_field}{topic_field}
}}{smart_note}
若無實質內容,new_points 給空陣列並在 current_topic 說明。"""
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
                refine = (f"你剛才的分析:{json.dumps({k: v for k, v in data.items() if not k.startswith('_')}, ensure_ascii=False)}\n"
                          f"現在補上你要求的時間點附近的 {len(extra)} 張畫面截圖。"
                          "請重新輸出同格式 JSON(不含 need_frames),"
                          "把畫面上的具體資訊補進重點,名稱數字以畫面為準。\n"
                          f"(原逐字稿同上:{transcript[:2000]})")
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
        return self._chat([{"role": "user", "content": prompt}])

    # ---------- 內部 ----------

    def _generate(self, contents: list, json_mode: bool = False,
                  retries: int | None = None) -> str:
        """與 Gemini 版對齊的純文字介面(供 /api/ask 等使用)。"""
        text = "\n".join(c for c in contents if isinstance(c, str))
        return self._chat([{"role": "user", "content": text}],
                          json_mode=json_mode, retries=retries)

    def _vod_prompt(self, title: str, channel: str) -> str:
        return f"""請用{self.lang}總結這部影片/Podcast。{self._gloss_note()}
標題:{title}
頻道:{channel}

輸出格式(Markdown):
## 一句話總結
## 內容大綱
(依時間順序,若有時間標記請附上)
## 關鍵重點
(具體重點:人名、數據、結論、建議)
## 值得深入的地方"""

    def _parse_json(self, raw: str, state: LiveState) -> dict:
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {"current_topic": state.current_topic, "topic_changed": False,
                    "new_points": [raw.strip()[:200]] if raw.strip() else [],
                    "rolling_summary": state.rolling_summary}

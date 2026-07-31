"""autoliveblog Web 介面:FastAPI 後端(SSE 即時推播)。"""
import asyncio
import json
import re
import sys
import threading
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from .. import config, feeds, i18n, live, platforms, stats, vod, ytdl
from ..i18n import t

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    except Exception:
        pass

app = FastAPI(title="autoliveblog")
STATIC = Path(__file__).parent / "static"


# ---------- 監看任務 ----------

class WatchRequest(BaseModel):
    url: str
    mode: str = "auto"          # auto / live / vod
    chunk: int | None = None
    duration: int | None = None  # 分鐘
    smart: bool = True
    frames: int | None = None
    lang: str | None = None
    provider: str = "auto"      # auto / gemini / openai
    from_start: bool = False    # 補課模式:從直播開頭總結
    keywords: list[str] = []


# 事件訂閱者:callable(job, event)。Telegram bot 等外部通道由此接收即時事件
SUBSCRIBERS: list = []


class Job:
    def __init__(self, req: WatchRequest):
        self.id = uuid.uuid4().hex[:8]
        self.req = req
        self.status = "starting"
        self.error = ""
        self.title = ""
        self.video_id = ""
        self.thumbnail = ""
        self.is_live = req.mode == "live"
        self.md_path = ""
        self.current_topic = ""
        self.rolling_summary = ""
        self.final_summary = ""
        self.timeline: list[dict] = []
        self.smart_hits = 0
        self.keywords = [k.strip() for k in req.keywords if k.strip()]
        self.created = time.time()
        self.chunk_seconds = req.chunk or config.CHUNK_SECONDS
        self.events: list[dict] = []
        self.stop_event = threading.Event()
        self.lock = threading.Lock()

    def emit(self, e: dict):
        e = dict(e)
        e["ts"] = round(time.time(), 2)
        with self.lock:
            t = e.get("type")
            if t == "started":
                self.title = e.get("title", self.title)
                self.video_id = e.get("video_id", self.video_id)
                self.md_path = e.get("md_path", self.md_path)
            elif t == "chunk":
                self.current_topic = e.get("topic", self.current_topic)
                self.rolling_summary = e.get("rolling_summary", self.rolling_summary)
                if e.get("requested_frames"):
                    self.smart_hits += 1
                self.timeline.append({
                    "elapsed": e.get("elapsed"), "seconds": e.get("seconds"),
                    "topic": e.get("topic"), "points": e.get("points") or [],
                    "smart": bool(e.get("requested_frames")),
                    "images": e.get("images") or []})
                hits = self._keyword_hits(e)
                if hits:
                    e["keyword_hits"] = hits
            elif t == "final":
                self.final_summary = e.get("summary", "")
                self.md_path = e.get("md_path", self.md_path)
            self.events.append(e)
        for cb in SUBSCRIBERS:
            try:
                cb(self, e)
            except Exception:
                pass

    def _keyword_hits(self, e: dict) -> list[str]:
        """字面 + 語意命中。大小寫不敏感:設 nvidia 也要能命中 Nvidia/NVIDIA。"""
        if not self.keywords:
            return []
        text = ((e.get("topic") or "") + " "
                + " ".join(e.get("points") or [])).casefold()
        literal = [k for k in self.keywords if k.casefold() in text]
        # 模型判定的語意命中(不必字面出現);回傳使用者原本的拼寫
        folded = {k.casefold(): k for k in self.keywords}
        semantic = []
        for t in (e.get("topic_hits") or []):
            k = folded.get(str(t).casefold())
            if k and k not in literal:
                semantic.append(k)
        return literal + semantic

    def snapshot(self) -> dict:
        with self.lock:
            plat = platforms.detect(self.req.url)
            timeline = [
                dict(t, watch_url=platforms.watch_url(
                    plat, self.video_id, t.get("seconds")))
                for t in self.timeline
            ]
            return {
                "id": self.id, "url": self.req.url, "status": self.status,
                "error": self.error, "title": self.title,
                "video_id": self.video_id, "thumbnail": self.thumbnail,
                "is_live": self.is_live, "md_path": self.md_path,
                "platform": plat.key, "platform_label": plat.label,
                "embed_url": platforms.embed_url(plat, self.video_id),
                "current_topic": self.current_topic,
                "rolling_summary": self.rolling_summary,
                "final_summary": self.final_summary,
                "timeline": timeline, "smart_hits": self.smart_hits,
                "keywords": self.keywords, "created": self.created,
                "chunk_seconds": self.chunk_seconds,
                "event_count": len(self.events),
            }


JOBS: dict[str, Job] = {}
JOBS_STATE_FILE = config.PROJECT_ROOT / "jobs_state.json"


def _save_jobs_state():
    """把進行中任務的啟動參數落地,伺服器重啟後可自動恢復監看。"""
    try:
        state = [{"req": (j.req.model_dump() if hasattr(j.req, "model_dump")
                          else j.req.dict()), "created": j.created}
                 for j in JOBS.values() if j.status in ("starting", "running")]
        JOBS_STATE_FILE.write_text(
            json.dumps(state, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def _recover_jobs():
    if not JOBS_STATE_FILE.exists():
        return
    try:
        state = json.loads(JOBS_STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    JOBS_STATE_FILE.unlink(missing_ok=True)
    for entry in state:
        if time.time() - entry.get("created", 0) > 12 * 3600:
            continue  # 太舊的不恢復
        try:
            req = WatchRequest(**entry["req"])
            print(f"[recover] 恢復中斷的監看:{req.url}")
            launch_job(req)
        except Exception as e:
            print(f"[recover] 恢復失敗:{e}")


_feed_info = feeds.feed_info  # 共用實作(CLI 也用同一份)


def _run_job(job: Job):
    req = job.req
    try:
        info = feeds.get_info_any(req.url)
        job.title = info.get("title", "")
        job.video_id = info.get("id", "")
        job.thumbnail = info.get("thumbnail", "") or ""
        is_live = (info.get("is_live", False) if req.mode == "auto"
                   else req.mode == "live")
        job.is_live = is_live
        job.status = "running"
        job.emit({"type": "status", "status": "running", "title": job.title,
                  "is_live": is_live, "thumbnail": job.thumbnail})
        if is_live:
            max_chunks = None
            if req.duration:
                max_chunks = max(1, req.duration * 60 // job.chunk_seconds)
            frames = req.frames or (30 if req.smart else 0)
            live.run(req.url, info, lang=req.lang, chunk_seconds=req.chunk,
                     max_chunks=max_chunks, frames_interval=frames,
                     smart_frames=req.smart, no_toast=True,
                     provider=req.provider, from_start=req.from_start,
                     keywords=job.keywords or None,
                     on_event=job.emit, stop_event=job.stop_event)
        else:
            vod.run(req.url, info, lang=req.lang, provider=req.provider,
                    on_event=job.emit)
        job.status = "done"
        job.emit({"type": "status", "status": "done"})
    except Exception as e:
        job.status = "error"
        job.error = str(e)
        job.emit({"type": "error", "message": str(e)})
    finally:
        _save_jobs_state()


def launch_job(req: WatchRequest) -> Job:
    """建立並啟動監看任務(API / 訂閱輪詢 / Telegram bot 共用)。"""
    job = Job(req)
    JOBS[job.id] = job
    threading.Thread(target=_run_job, args=(job,), daemon=True).start()
    _save_jobs_state()
    return job


@app.post("/api/watch")
def start_watch(req: WatchRequest):
    return {"job_id": launch_job(req).id}


@app.get("/api/jobs")
def list_jobs():
    return [j.snapshot() for j in
            sorted(JOBS.values(), key=lambda j: j.created, reverse=True)]


@app.post("/api/jobs/{jid}/stop")
def stop_job(jid: str):
    job = JOBS.get(jid)
    if not job:
        raise HTTPException(404)
    job.stop_event.set()
    return {"ok": True}


@app.delete("/api/jobs/{jid}")
def remove_job(jid: str):
    job = JOBS.get(jid)
    if not job:
        raise HTTPException(404)
    if job.status == "running":
        raise HTTPException(409, t("web.stop_running_first"))
    del JOBS[jid]
    return {"ok": True}


@app.get("/api/jobs/{jid}/events")
async def job_events(jid: str):
    job = JOBS.get(jid)
    if not job:
        raise HTTPException(404)

    async def gen():
        i = 0
        while True:
            while i < len(job.events):
                yield ("data: " + json.dumps(job.events[i], ensure_ascii=False)
                       + "\n\n")
                i += 1
            if job.status in ("done", "error"):
                yield "event: end\ndata: {}\n\n"
                return
            await asyncio.sleep(1)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache"})


@app.post("/api/inspect")
async def inspect(req: WatchRequest):
    loop = asyncio.get_event_loop()
    try:
        info = await loop.run_in_executor(None, feeds.get_info_any, req.url)
    except Exception as e:
        raise HTTPException(400, t("web.cannot_read", err=e))
    return {
        "title": info.get("title"), "channel": info.get("uploader")
        or info.get("channel"), "is_live": bool(info.get("is_live")),
        "duration": info.get("duration"), "thumbnail": info.get("thumbnail"),
        "video_id": info.get("id"),
    }


# ---------- 歷史紀錄 ----------

def _live_title_prefixes() -> list[str]:
    """各語系的直播標題前綴(含舊版寫死的中文)。歷史檔案可能是任一語言
    寫成的,列舉全部才不會在切換介面語言後解析不出標題。"""
    heads = ["直播即時總結:"]
    for cat in i18n.CATALOGS.values():
        head = cat.get("md.live_title", "").split("{title}")[0].strip()
        if head:
            heads.append(head)
    return list(dict.fromkeys(heads))


_TITLE_RE = re.compile(
    r"^#\s*(?:🔴\s*(?:%s)\s*)?(.+)$"
    % "|".join(re.escape(h) for h in _live_title_prefixes()), re.M)
# 標籤文字會隨語系變動,只認「- 標籤:網址」的形狀,不認特定語言的標籤
_URL_RE = re.compile(r"^-\s*[^:：\n]{1,40}[:：]\s*(https?://\S+)", re.M)


def _history_path(name: str) -> Path:
    """把相對名稱解析成 summaries 內的 .md 路徑,防目錄跳脫。"""
    base = config.OUTPUT_DIR.resolve()
    p = (config.OUTPUT_DIR / name).resolve()
    if base not in p.parents or p.suffix != ".md" or not p.is_file():
        raise HTTPException(404)
    return p


@app.get("/api/history")
def history():
    out = []
    base = config.OUTPUT_DIR
    if base.exists():
        for p in sorted(base.rglob("*.md"),
                        key=lambda p: p.stat().st_mtime, reverse=True):
            rel = p.relative_to(base)
            head = p.read_text(encoding="utf-8", errors="replace")[:600]
            m = _TITLE_RE.search(head)
            mu = _URL_RE.search(head)
            src_url = mu.group(1) if mu else ""
            # 檔名尾端是影片 ID:<標題>_<id>.md;用它算出可跳轉的觀看網址
            vid = p.stem.rsplit("_", 1)[-1] if "_" in p.stem else ""
            out.append({
                "name": str(rel).replace("\\", "/"),
                "channel": rel.parts[0] if len(rel.parts) > 1 else "",
                "title": (m.group(1).strip() if m else p.stem)[:120],
                "is_live": p.name.startswith("live_"),
                "mtime": p.stat().st_mtime,
                "url": src_url,
                "watch_base": platforms.watch_url(src_url, vid) if src_url else "",
                "seek_tpl": platforms.detect(src_url).seek_param_template or "",
            })
    return out


@app.get("/api/history/{name:path}")
def history_content(name: str):
    p = _history_path(name)
    return {"name": name,
            "content": p.read_text(encoding="utf-8", errors="replace")}


@app.delete("/api/history/{name:path}")
def history_delete(name: str):
    _history_path(name).unlink()
    return {"ok": True}


# ---------- 頻道訂閱:開播自動監看 ----------

SUBS_FILE = config.PROJECT_ROOT / "subscriptions.json"
SUBS: dict[str, dict] = {}
_SUBS_LOCK = threading.Lock()


def _load_subs():
    if SUBS_FILE.exists():
        try:
            SUBS.update(json.loads(SUBS_FILE.read_text(encoding="utf-8")))
        except Exception:
            pass


_RUNTIME_SUB_FIELDS = ("last_check", "live_now", "live_title", "startable",
                       "last_error", "is_feed")


def _save_subs():
    """只在設定真正變更時呼叫。先寫暫存檔再原子替換:
    直接覆寫或先搬走原檔,中途失敗會讓 subscriptions.json 消失。"""
    # 執行期狀態不落地(高頻寫檔曾造成訂閱被覆寫遺失)
    persist = {k: {kk: vv for kk, vv in v.items()
                   if kk not in _RUNTIME_SUB_FIELDS}
               for k, v in SUBS.items()}
    data = json.dumps(persist, ensure_ascii=False, indent=1)
    tmp = SUBS_FILE.with_suffix(".json.tmp")
    try:
        tmp.write_text(data, encoding="utf-8")
        if SUBS_FILE.exists():
            SUBS_FILE.replace(SUBS_FILE.with_suffix(".json.bak"))
        tmp.replace(SUBS_FILE)
    except OSError as e:
        # 寫檔失敗不能吞掉:呼叫端還要繼續發通知
        print(f"[subs] 儲存訂閱失敗:{e}")
        tmp.unlink(missing_ok=True)


class SubRequest(BaseModel):
    channel_url: str
    chunk: int | None = None
    smart: bool = True
    keywords: list[str] = []


def _clean_channel_url(channel_url: str) -> str:
    """去掉 ?si= 等追蹤參數與尾斜線(髒參數會讓網址拼接壞掉)。"""
    return platforms.clean_url(channel_url)


def _live_url_of(channel_url: str) -> str:
    """頻道網址 → 開播檢查網址(依平台規則,見 platforms.py)。"""
    return platforms.live_url_of(channel_url)


def _poll_subs_once():
    with _SUBS_LOCK:
        subs = [dict(s, id=k) for k, s in SUBS.items() if s.get("enabled", True)]
    for s in subs:
        sid = s["id"]
        is_feed = feeds.looks_like_feed(s["channel_url"])
        info = {}
        try:
            if is_feed:
                # Podcast:有「新集數」就等同於「開播」
                info = _feed_info(s["channel_url"])
                vid, is_live = info.get("id", ""), True
            else:
                info = ytdl.get_info(_live_url_of(s["channel_url"]))
                vid = info.get("id", "")
                is_live = bool(info.get("is_live"))
        except Exception as e:
            vid, is_live = "", False
            msg = str(e)[:120]
            # 「還沒開播」是正常狀態,不是錯誤;真正的錯誤才記錄,
            # 否則靜默失敗會讓「訂閱壞掉」和「還沒開播」長得一模一樣
            benign = ("not currently live" in msg or "未開播" in msg
                      or "UserNotLive" in msg)
            with _SUBS_LOCK:
                if sid in SUBS:
                    if benign:
                        SUBS[sid].pop("last_error", None)
                    else:
                        if SUBS[sid].get("last_error") != msg:
                            print(f"[subs] {s['channel_url']} 檢查失敗:{msg}")
                        SUBS[sid]["last_error"] = msg
        with _SUBS_LOCK:
            if sid not in SUBS:
                continue
            if is_live or vid:
                SUBS[sid].pop("last_error", None)
            SUBS[sid]["last_check"] = time.time()
            SUBS[sid]["is_feed"] = is_feed
            # feed 沒有「正在直播」的概念,只有新集數;不要讓清單一直顯示直播中
            SUBS[sid]["live_now"] = False if is_feed else is_live
            SUBS[sid]["live_title"] = (info.get("title") or "") if is_live else ""
            # 有可開始的內容嗎?直播看 live_now,podcast 看有沒有抓到集數。
            # 兩個清單的「開始」按鈕都以此為準,否則 podcast 訂閱永遠按不了。
            SUBS[sid]["startable"] = bool(vid) if is_feed else bool(is_live)
            if is_live and vid and vid != SUBS[sid].get("last_started"):
                # 只記錄+通知,不自動開始;使用者按按鈕才啟動
                first_seen = not SUBS[sid].get("last_started")
                SUBS[sid]["last_started"] = vid
                try:
                    _save_subs()  # 只有 last_started 變更才寫檔
                except Exception as e:
                    print(f"[subs] 儲存失敗但仍會通知:{e}")
                ch = (info.get("uploader") if is_feed else None) or \
                    SUBS[sid]["channel_url"].rstrip("/").rsplit("/", 1)[-1]
                title = (info.get("title") or "")[:80]
                # 剛訂閱時的「最新一集」不算新內容,只記錄不打擾
                if is_feed and first_seen:
                    continue
                btns = [[("▶ " + t("bot.btn_start"), f"/go {sid}")]]
                if is_feed:
                    _notify_tg("🎧 " + t("bot.new_episode", channel=ch,
                                         title=title), buttons=btns)
                else:
                    _notify_tg("🔴 " + t("bot.went_live", channel=ch,
                                         title=title), buttons=btns)


def _subs_poller():
    while True:
        try:
            _poll_subs_once()
        except Exception:
            pass
        time.sleep(config.SUB_POLL_SECONDS)


@app.on_event("startup")
def _startup():
    _load_subs()
    threading.Thread(target=_subs_poller, daemon=True).start()
    if config.TELEGRAM_BOT_TOKEN:
        from . import bot
        bot.start()
    threading.Thread(target=_digest_scheduler, daemon=True).start()
    _recover_jobs()


@app.get("/api/subscriptions")
def list_subs():
    with _SUBS_LOCK:
        return [dict(s, id=k) for k, s in SUBS.items()]


@app.post("/api/subscriptions")
def add_sub(req: SubRequest):
    sid = uuid.uuid4().hex[:8]
    with _SUBS_LOCK:
        SUBS[sid] = {"channel_url": _clean_channel_url(req.channel_url),
                     "chunk": req.chunk,
                     "smart": req.smart,
                     "keywords": [k.strip() for k in req.keywords if k.strip()],
                     "enabled": True, "live_now": None, "last_check": None,
                     "last_started": ""}
        _save_subs()
    threading.Thread(target=_poll_subs_once, daemon=True).start()
    return {"id": sid}


@app.delete("/api/subscriptions/{sid}")
def del_sub(sid: str):
    with _SUBS_LOCK:
        if sid not in SUBS:
            raise HTTPException(404)
        del SUBS[sid]
        _save_subs()
    return {"ok": True}


def _notify_tg(text: str, buttons=None):
    """透過 Telegram bot 推播(bot 未啟用時靜默略過)。"""
    try:
        from . import bot
        if bot._bot:
            bot._bot.push_q.put((text, [], buttons))
    except Exception:
        pass


def start_sub_watch(sid: str) -> Job:
    """使用者按下按鈕:直播→從開播處補課並接上即時;Podcast→總結最新一集。"""
    with _SUBS_LOCK:
        s = SUBS.get(sid)
        if not s:
            raise KeyError(t("bot.sub_not_found", arg=sid))
        vid = s.get("last_started")
        if not vid:
            raise KeyError(t("api.sub_no_content"))
        ch_url = s["channel_url"]
        kw = s.get("keywords") or []
        chunk = s.get("chunk")
    if feeds.looks_like_feed(ch_url):
        # Podcast:直接把 feed 網址交給 VOD 管線(它會抓最新一集)
        return launch_job(WatchRequest(url=ch_url, mode="vod", keywords=kw))
    # 直播:YouTube 要指向該場次的影片網址;Twitch 等平台的直播就在頻道頁上
    plat = platforms.detect(ch_url)
    watch = (platforms.watch_url(plat, vid) if plat.key == "youtube"
             else _live_url_of(ch_url))
    return launch_job(WatchRequest(url=watch, mode="live", chunk=chunk,
                                   smart=False, from_start=True, keywords=kw))


@app.post("/api/subscriptions/{sid}/go")
def sub_go(sid: str):
    try:
        job = start_sub_watch(sid)
    except KeyError as e:
        raise HTTPException(404, str(e))
    return {"job_id": job.id}


def set_sub_enabled(sid: str, enabled: bool) -> bool:
    """暫停/恢復訂閱(Telegram bot 用)。"""
    with _SUBS_LOCK:
        if sid not in SUBS:
            return False
        SUBS[sid]["enabled"] = enabled
        _save_subs()
        return True


@app.post("/api/subscriptions/{sid}/toggle")
def toggle_sub(sid: str):
    with _SUBS_LOCK:
        if sid not in SUBS:
            raise HTTPException(404)
        SUBS[sid]["enabled"] = not SUBS[sid].get("enabled", True)
        _save_subs()
        return {"enabled": SUBS[sid]["enabled"]}


# ---------- 內容問答 ----------

class AskRequest(BaseModel):
    question: str
    job_id: str | None = None
    history_name: str | None = None


def answer_question(question: str, job_id: str | None = None,
                    history_name: str | None = None) -> str:
    """針對任務或歷史總結回答問題(API 與 Telegram bot 共用,同步阻塞)。"""
    from ..summarizer import make_summarizer
    if job_id:
        job = JOBS.get(job_id)
        if not job:
            raise KeyError(t("api.job_not_found"))
        snap = job.snapshot()
        timeline = "\n".join(
            f"[{seg['elapsed']}] {seg['topic']}\n" +
            "\n".join(f"  - {p}" for p in seg["points"])
            for seg in snap["timeline"])
        context = (f"Title: {snap['title']}\n"
                   f"Current topic: {snap['current_topic']}\n"
                   f"Rolling summary: {snap['rolling_summary']}\n"
                   f"Timeline:\n{timeline}\n"
                   f"Final summary: {snap['final_summary']}")
    elif history_name:
        try:
            p = _history_path(history_name)
        except HTTPException:
            raise KeyError(t("api.summary_not_found"))
        context = p.read_text(encoding="utf-8", errors="replace")[:150_000]
    else:
        raise KeyError(t("api.ask_needs_target"))

    prompt = ("Below is the running summary of a live stream or video:\n\n"
              f"{context}\n\n"
              f"User's question: {question}\n"
              f"Answer concisely in {config.LANG}, using only what the record "
              "above contains. If the record does not cover it, say so "
              "instead of guessing.")
    return make_summarizer()._generate([prompt])


@app.post("/api/ask")
async def ask(req: AskRequest):
    loop = asyncio.get_event_loop()
    try:
        answer = await loop.run_in_executor(
            None, lambda: answer_question(req.question, req.job_id,
                                          req.history_name))
    except KeyError as e:
        raise HTTPException(404, str(e))
    return {"answer": answer}


@app.get("/media/{rest:path}")
def media_file(rest: str):
    base = (config.OUTPUT_DIR / "media").resolve()
    p = (base / rest).resolve()
    if not str(p).startswith(str(base)) or not p.is_file():
        raise HTTPException(404)
    return FileResponse(p)


# ---------- 每日晨報 ----------

def generate_digest() -> str | None:
    """把今天產出的所有總結彙整成一份晨報,存檔並回傳內文。"""
    from ..summarizer import make_summarizer
    import datetime as _dt
    today = _dt.date.today()
    entries = []
    for h in history():
        if h["name"].startswith("_digest/"):
            continue
        if _dt.date.fromtimestamp(h["mtime"]) != today:
            continue
        p = config.OUTPUT_DIR / h["name"]
        body = p.read_text(encoding="utf-8", errors="replace")
        entries.append(f"### Source: {h['channel'] or 'uncategorized'} —"
                       f" {h['title']}\n{body[:2500]}")
        if len(entries) >= 12:
            break
    if not entries:
        return None
    prompt = (f"Below are the summaries of everything watched today ({today}),"
              " one block per source.\n"
              f"Write a single daily digest in {config.LANG}. Take the subject"
              " matter from the sources themselves — do not assume any"
              " particular field. Use these sections:\n"
              "## In one sentence\n"
              "## Common threads (what several sources cover, and where they"
              " disagree)\n"
              "## Highlights per source (2-3 bullets each)\n"
              "## Worth following up\n\n"
              + "\n\n---\n\n".join(entries))
    digest = make_summarizer()._generate([prompt])
    ddir = config.OUTPUT_DIR / "_digest"
    ddir.mkdir(parents=True, exist_ok=True)
    out = ddir / f"{today}.md"
    out.write_text(f"# {t('md.digest_title', date=today)}\n\n{digest}\n",
                   encoding="utf-8")
    return digest


def _digest_scheduler():
    import datetime as _dt
    while True:
        try:
            if config.DIGEST_TIME:
                now = _dt.datetime.now()
                if now.strftime("%H:%M") == config.DIGEST_TIME:
                    out = config.OUTPUT_DIR / "_digest" / f"{now.date()}.md"
                    if not out.exists():
                        digest = generate_digest()
                        if digest:
                            _notify_tg("📰 " + t("bot.digest_title")
                                       + f"\n\n{digest[:3500]}")
        except Exception as e:
            print(f"[digest] 失敗:{e}")
        time.sleep(55)


@app.post("/api/digest/run")
async def digest_run():
    loop = asyncio.get_event_loop()
    digest = await loop.run_in_executor(None, generate_digest)
    if not digest:
        raise HTTPException(404, t("bot.digest_empty"))
    return {"digest": digest}


# ---------- 跨影片知識庫問答 ----------

def answer_question_global(question: str) -> str:
    """兩段式:先從目錄挑相關檔案,再載入內容回答。"""
    from ..summarizer import make_summarizer
    import datetime as _dt
    s = make_summarizer()
    catalog = [h for h in history() if not h["name"].startswith("_digest/")][:80]
    listing = "\n".join(
        f"{i}. [{h['channel'] or 'uncategorized'}] "
        f"{_dt.date.fromtimestamp(h['mtime'])} {h['title'][:70]}"
        for i, h in enumerate(catalog))
    pick_prompt = (f"Here is a catalog of past summaries:\n{listing}\n\n"
                   f"Question: {question}\n"
                   "Reply with JSON: "
                   "{\"indexes\": [up to 5 most relevant numbers]}")
    try:
        picked = json.loads(s._generate([pick_prompt], json_mode=True))
        idxs = [int(i) for i in picked.get("indexes", [])][:5]
    except Exception:
        idxs = list(range(min(3, len(catalog))))
    parts = []
    for i in idxs:
        if 0 <= i < len(catalog):
            h = catalog[i]
            body = (config.OUTPUT_DIR / h["name"]).read_text(
                encoding="utf-8", errors="replace")[:30_000]
            parts.append(f"=== {_dt.date.fromtimestamp(h['mtime'])} "
                         f"{h['channel']} {h['title']} ===\n{body}")
    if not parts:
        return t("api.no_matching_history")
    prompt = ("Below are several past summaries:\n\n" + "\n\n".join(parts)
              + f"\n\nQuestion: {question}\n"
              + f"Answer in {config.LANG}. When you cite something, name the "
                "source date and programme; if the records do not cover it, "
                "say so.")
    return s._generate([prompt])


class AskAllRequest(BaseModel):
    question: str


@app.post("/api/askall")
async def askall(req: AskAllRequest):
    loop = asyncio.get_event_loop()
    answer = await loop.run_in_executor(
        None, lambda: answer_question_global(req.question))
    return {"answer": answer}


# ---------- 用量 ----------

@app.get("/api/usage")
def usage():
    snap = stats.snapshot()
    snap["active_jobs"] = sum(1 for j in JOBS.values() if j.status == "running")
    return snap


# ---------- 介面文字 ----------

@app.get("/api/strings")
def strings():
    return {"lang": i18n.get_lang(), "strings": i18n.catalog()}


# ---------- 靜態頁 ----------

@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/app.js")
def app_js():
    return FileResponse(STATIC / "app.js", media_type="application/javascript")

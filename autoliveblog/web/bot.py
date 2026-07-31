"""Telegram 機器人:即時推播(話題/關鍵字/重要截圖)+ 指令控制。

在 web 伺服器行程內以執行緒運行,長輪詢 getUpdates,不需要對外網址。
只回應 TELEGRAM_CHAT_ID 白名單內的使用者。
"""
import html
import json
import queue
import threading
import time

import requests

from .. import config
from ..i18n import t

_API = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}"
_ALLOWED = {s.strip() for s in config.TELEGRAM_CHAT_ID.split(",") if s.strip()}


def _esc(s: str) -> str:
    return html.escape(str(s or ""))


class TgBot:
    def __init__(self):
        self.push_q: queue.Queue = queue.Queue()
        self._last_final: dict[str, bool] = {}
        self._pushed_first: set[str] = set()
        self._stalled_notified: set[str] = set()
        self._chunk_error_notified: set[str] = set()
        self._started_notified: set[str] = set()

    # ---------- 送出 ----------

    @staticmethod
    def _markup(buttons):
        """buttons: [[(顯示文字, callback指令), ...], ...] → inline_keyboard。"""
        if not buttons:
            return None
        return json.dumps({"inline_keyboard": [
            [{"text": label, "callback_data": d[:64]} for label, d in row]
            for row in buttons]})

    def send_text(self, chat_id: str, text: str, buttons=None):
        markup = self._markup(buttons)
        for i in range(0, len(text), 3800):
            data = {"chat_id": chat_id, "text": text[i:i + 3800],
                    "parse_mode": "HTML", "disable_web_page_preview": True}
            if markup and i + 3800 >= len(text):  # 按鈕掛在最後一則
                data["reply_markup"] = markup
            try:
                r = requests.post(f"{_API}/sendMessage", timeout=30, data=data)
                if not r.json().get("ok"):
                    print(f"[tg] sendMessage 被拒:{r.text[:200]}")
            except requests.RequestException as e:
                print(f"[tg] sendMessage 失敗:{e}")

    def send_photo(self, chat_id: str, path, caption: str = "", buttons=None):
        data = {"chat_id": chat_id, "caption": caption[:1000],
                "parse_mode": "HTML"}
        markup = self._markup(buttons)
        if markup:
            data["reply_markup"] = markup
        try:
            with open(path, "rb") as f:
                r = requests.post(f"{_API}/sendPhoto", timeout=60,
                                  data=data, files={"photo": f})
            if not r.json().get("ok"):
                print(f"[tg] sendPhoto 被拒:{r.text[:200]}")
        except (requests.RequestException, OSError) as e:
            print(f"[tg] sendPhoto 失敗:{e}")

    def broadcast(self, text: str, images: list | None = None, buttons=None):
        from .. import config as cfg
        for chat in _ALLOWED:
            if images:
                self.send_photo(chat, cfg.OUTPUT_DIR / images[0],
                                caption=text, buttons=buttons)
                for extra in images[1:3]:
                    self.send_photo(chat, cfg.OUTPUT_DIR / extra)
            else:
                self.send_text(chat, text, buttons=buttons)

    # ---------- 任務事件 → 推播 ----------

    def on_job_event(self, job, e: dict):
        kind = e.get("type")
        if kind == "started" and job.id not in self._started_notified:
            # 訂閱自動開播、或網頁啟動的任務 → 通知(bot 指令啟動的已回覆過)
            self._started_notified.add(job.id)
            self.push_q.put((
                "▶ " + t("bot.watching",
                         title=_esc(e.get('title') or job.req.url),
                         job=job.id), [],
                [[("📋 " + t("bot.btn_status"), f"/now {job.id}"),
                  ("⏹ " + t("bot.btn_stop"), f"/stop {job.id}")]]))
            return
        if kind == "chunk":
            hits = e.get("keyword_hits") or []
            first = job.id not in self._pushed_first
            self._pushed_first.add(job.id)
            if not (first or e.get("topic_changed") or hits
                    or e.get("images")):
                return
            lines = ["🗣 " + t("bot.topic", topic=_esc(e.get('topic')),
                               elapsed=_esc(e.get('elapsed')))]
            if hits:
                lines.insert(0, "⚡ " + t("bot.keyword_hit",
                                         kw=_esc('、'.join(hits))))
            lines += [f"• {_esc(p)}" for p in (e.get("points") or [])[:4]]
            self.push_q.put((("\n".join(lines)), e.get("images") or []))
        elif kind == "final" and not self._last_final.get(job.id):
            self._last_final[job.id] = True
            summary = e.get("summary") or t("bot.final_empty")
            self.push_q.put((
                "✅ " + t("bot.final_title", title=_esc(job.title))
                + f"\n\n{_esc(summary)}", []))
        elif kind == "chunk_error":
            # 只在第一次失敗時通知:重點是讓使用者立刻知道,而不是被洗版。
            # 先前這個事件完全沒推播,結果額度耗盡時使用者盯著沉默等了半小時
            if job.id not in self._chunk_error_notified:
                self._chunk_error_notified.add(job.id)
                msg = _esc(e.get("message", ""))
                hint = ""
                if "credit" in msg.lower() or "quota" in msg.lower() \
                        or "429" in msg:
                    hint = t("bot.quota_hint")
                self.push_q.put((
                    "⚠ " + t("bot.chunk_error",
                             title=_esc(job.title or t("bot.untitled_stream")),
                             elapsed=_esc(e.get('elapsed')),
                             err=msg[:400]) + hint, []))
        elif kind == "status" and e.get("status") == "stalled":
            if job.id not in self._stalled_notified:
                self._stalled_notified.add(job.id)
                self.push_q.put((
                    "⚠ " + t("bot.stalled",
                             title=_esc(job.title
                                        or t("bot.untitled_stream"))), []))
        elif kind == "error":
            self.push_q.put((
                "⚠ " + t("bot.job_error", err=_esc(e.get('message'))), []))

    def _push_worker(self):
        while True:
            item = self.push_q.get()
            text, images, buttons = (tuple(item) + (None, None))[:3]
            self.broadcast(text, images, buttons)

    # ---------- 指令 ----------

    def handle(self, msg: dict):
        chat_id = str(msg.get("chat", {}).get("id", ""))
        text = (msg.get("text") or "").strip()
        if chat_id not in _ALLOWED or not text:
            return
        from . import server
        cmd, _, arg = text.partition(" ")
        arg = arg.strip()
        cmd = cmd.split("@")[0].lower()
        if cmd.startswith("/go_"):  # 通知訊息裡的可點指令 /go_訂閱ID
            cmd, arg = "/go", cmd[4:]

        if cmd in ("/start", "/help"):
            self.send_text(chat_id, t("bot.help"))

        elif cmd == "/watch":
            if not arg:
                return self.send_text(chat_id, t("bot.watch_usage"))
            # 三種寫法都收:英文說明寫 catchup,中文說明寫補課,from-start 是舊寫法
            from_start = any(k in arg for k in ("補課", "catchup", "from-start"))
            url = arg.split()[0]
            req = server.WatchRequest(url=url, smart=True,
                                      from_start=from_start)
            job = server.launch_job(req)
            self._started_notified.add(job.id)  # 已在此回覆,不再重複推播
            self.send_text(chat_id,
                           "▶ " + t("bot.watch_started", job=job.id,
                                    mode=t("bot.mode_catchup")
                                    if from_start else ""))

        elif cmd == "/now":
            job = self._find_job(server, arg) if arg else self._latest_job(server)
            if not job:
                return self.send_text(chat_id, t("bot.no_jobs"))
            s = job.snapshot()
            lines = [f"📺 <b>{_esc(s['title'] or s['url'])}</b>({_esc(s['status'])})",
                     f"🗣 {t('md.current_topic')}:"
                     f"<b>{_esc(s['current_topic'] or t('md.waiting'))}</b>", ""]
            if s["rolling_summary"]:
                lines.append(_esc(s["rolling_summary"]))
            for seg in s["timeline"][-2:]:
                lines.append(f"\n[{_esc(seg['elapsed'])}] {_esc(seg['topic'])}")
                lines += [f"• {_esc(p)}" for p in seg["points"][:3]]
            self.send_text(chat_id, "\n".join(lines))
            images = [img for seg in s["timeline"][-3:]
                      for img in seg["images"]][-2:]
            for img in images:
                self.send_photo(chat_id, config.OUTPUT_DIR / img)

        elif cmd == "/ask":
            if not arg:
                return self.send_text(chat_id, t("bot.ask_usage"))
            job = self._latest_job(server)
            try:
                if job:
                    ans = server.answer_question(arg, job_id=job.id)
                else:
                    hist = server.history()
                    if not hist:
                        return self.send_text(chat_id, t("bot.no_history"))
                    ans = server.answer_question(
                        arg, history_name=hist[0]["name"])
                self.send_text(chat_id, f"💬 {_esc(ans)}")
            except Exception as e:
                self.send_text(chat_id, t("web.ask_failed", err=_esc(e)))

        elif cmd == "/stop":
            if arg:
                job = self._find_job(server, arg, running_only=True)
                if not job:
                    return self.send_text(
                        chat_id, t("bot.job_not_found", arg=_esc(arg)))
            else:
                running = [j for j in server.JOBS.values()
                           if j.status == "running"]
                if not running:
                    return self.send_text(chat_id, t("bot.no_running"))
                if len(running) > 1:
                    lines = [t("bot.pick_job")]
                    lines += [f"<code>{j.id}</code> {_esc((j.title or j.req.url)[:40])}"
                              for j in running]
                    return self.send_text(chat_id, "\n".join(lines))
                job = running[0]
            job.stop_event.set()
            self.send_text(chat_id,
                           "⏹ " + t("bot.stopping", job=job.id,
                                    title=_esc((job.title or '')[:30])))

        elif cmd == "/jobs":
            jobs = sorted(server.JOBS.values(), key=lambda j: j.created,
                          reverse=True)[:5]
            if not jobs:
                return self.send_text(chat_id, t("bot.no_jobs"))
            lines = [f"{j.id} [{j.status}] {_esc((j.title or j.req.url)[:50])}"
                     f"({t('bot.n_segments', n=len(j.timeline))})"
                     for j in jobs]
            btns = [[("📋 " + t("bot.btn_status_named", name=j.id[:4]),
                      f"/now {j.id}"),
                     ("⏹ " + t("bot.btn_stop_named", name=j.id[:4]),
                      f"/stop {j.id}")]
                    for j in jobs if j.status == "running"]
            self.send_text(chat_id, "\n".join(lines), buttons=btns or None)

        elif cmd == "/sub":
            if not arg:
                return self.send_text(chat_id, t("bot.sub_usage"))
            parts = arg.split(None, 1)
            keywords = []
            if len(parts) > 1:
                keywords = [k.strip() for k in parts[1].replace("，", ",")
                            .split(",") if k.strip()]
            r = server.add_sub(server.SubRequest(channel_url=parts[0],
                                                 keywords=keywords))
            mins = max(1, config.SUB_POLL_SECONDS // 60)
            self.send_text(chat_id,
                           "🔔 " + t("bot.sub_added", sid=r['id'], mins=mins,
                                    kw=t("bot.sub_keywords",
                                         kw=_esc('、'.join(keywords)))
                                    if keywords else ""))

        elif cmd == "/go":
            if not arg:
                return self.send_text(chat_id, t("bot.go_usage"))
            try:
                job = server.start_sub_watch(arg.strip())
                self._started_notified.add(job.id)
                self.send_text(chat_id,
                               "▶ " + t("bot.go_started", job=job.id))
            except KeyError as e:
                self.send_text(chat_id, t("bot.cannot_start", err=_esc(e)))

        elif cmd == "/subs":
            subs = server.list_subs()
            if not subs:
                return self.send_text(chat_id, t("bot.no_subs"))
            lines = []
            btns = []
            for s in subs:
                if not s.get("enabled", True):
                    st = "⏸ " + t("bot.sub_status_paused")
                elif s.get("last_error"):
                    st = "⚠ " + t("bot.sub_status_failed")
                elif s.get("is_feed"):
                    st = "🎧 " + (t("bot.sub_status_podcast")
                                 if s.get("startable")
                                 else t("bot.sub_status_podcast_empty"))
                else:
                    st = ("🔴 " + t("bot.sub_status_live") if s.get("live_now")
                          else "⚪ " + t("bot.sub_status_offline"))
                kw = f" ⚡{_esc('、'.join(s['keywords']))}" if s.get("keywords") else ""
                lines.append(f"<code>{s['id']}</code> {st} "
                             f"{_esc(s['channel_url'])}{kw}")
                tag = s["channel_url"].rstrip("/").rsplit("/", 1)[-1][:12]
                row = []
                if s.get("startable"):
                    row.append(("▶ " + t("bot.btn_start_named", name=tag),
                                f"/go {s['id']}"))
                row.append((("▶ " + t("bot.btn_resume", name=tag)
                             if not s.get("enabled", True)
                             else "⏸ " + t("bot.btn_pause", name=tag)),
                            ("/resume " if not s.get("enabled", True)
                             else "/pause ") + s["id"]))
                btns.append(row)
            self.send_text(chat_id, "\n".join(lines), buttons=btns or None)

        elif cmd in ("/pause", "/resume"):
            if not arg:
                return self.send_text(chat_id, t("bot.pause_usage", cmd=cmd))
            ok = server.set_sub_enabled(arg.strip(), cmd == "/resume")
            self.send_text(chat_id,
                           ("▶ " + t("bot.resumed", sid=_esc(arg))
                            if cmd == "/resume"
                            else "⏸ " + t("bot.paused", sid=_esc(arg))) if ok
                           else t("bot.sub_not_found", arg=_esc(arg)))

        elif cmd == "/unsub":
            if not arg:
                return self.send_text(chat_id, t("bot.unsub_usage"))
            try:
                server.del_sub(arg.strip())
                self.send_text(chat_id, t("bot.unsubbed", sid=_esc(arg)))
            except Exception:
                self.send_text(chat_id,
                               t("bot.sub_not_found", arg=_esc(arg)))

        elif cmd == "/digest":
            self.send_text(chat_id, "📰 " + t("bot.digest_building"))
            def _run():
                try:
                    d = server.generate_digest()
                    self.send_text(chat_id,
                                   "📰 " + t("bot.digest_title")
                                   + f"\n\n{_esc(d)}"
                                   if d else t("bot.digest_empty"))
                except Exception as e:
                    self.send_text(chat_id,
                                   t("bot.digest_failed", err=_esc(e)))
            threading.Thread(target=_run, daemon=True).start()

        elif cmd == "/askall":
            if not arg:
                return self.send_text(chat_id, t("bot.askall_usage"))
            self.send_text(chat_id, "🔎 " + t("web.searching_history"))
            def _runq():
                try:
                    self.send_text(chat_id,
                                   f"💬 {_esc(server.answer_question_global(arg))}")
                except Exception as e:
                    self.send_text(chat_id, t("web.ask_failed", err=_esc(e)))
            threading.Thread(target=_runq, daemon=True).start()

        elif cmd == "/glossary":
            from .. import glossary
            parts = arg.split(None, 1)
            if len(parts) < 2:
                gl = glossary.load_all()
                if not gl:
                    return self.send_text(chat_id, t("bot.glossary_usage"))
                lines = [f"{ch}:{_esc('、'.join(ts))}" for ch, ts in gl.items()]
                return self.send_text(
                    chat_id, t("bot.glossary_current") + "\n" + "\n".join(lines))
            terms = [w.strip() for w in parts[1].replace("，", ",").split(",")
                     if w.strip()]
            glossary.add_terms(parts[0], terms)
            self.send_text(chat_id,
                           t("bot.glossary_added", channel=_esc(parts[0]),
                             terms=_esc('、'.join(terms))))

        elif cmd == "/history":
            hist = server.history()[:5]
            if not hist:
                return self.send_text(chat_id, t("bot.no_history"))
            lines = [f"{'🔴' if h['is_live'] else '🎬'} {_esc(h['title'][:60])}"
                     for h in hist]
            self.send_text(chat_id, "\n".join(lines))

        else:
            self.send_text(chat_id, t("bot.help"))

    @staticmethod
    def _find_job(server, prefix: str, running_only: bool = False):
        prefix = prefix.strip().lower()
        for j in server.JOBS.values():
            if j.id.lower().startswith(prefix):
                if not running_only or j.status == "running":
                    return j
        return None

    @staticmethod
    def _latest_job(server, running_only: bool = False):
        jobs = sorted(server.JOBS.values(), key=lambda j: j.created,
                      reverse=True)
        for j in jobs:
            if not running_only or j.status == "running":
                return j
        return None

    # ---------- 輪詢 ----------

    def _poll_loop(self):
        offset = 0
        print(f"[tg] Telegram 機器人啟動(白名單:{len(_ALLOWED)} 人)")
        while True:
            try:
                r = requests.get(f"{_API}/getUpdates", timeout=60, params={
                    "offset": offset, "timeout": 50,
                    "allowed_updates": '["message","callback_query"]'})
                for upd in r.json().get("result", []):
                    offset = upd["update_id"] + 1
                    try:
                        if "message" in upd:
                            self.handle(upd["message"])
                        elif "callback_query" in upd:
                            cq = upd["callback_query"]
                            try:
                                requests.post(f"{_API}/answerCallbackQuery",
                                              timeout=10,
                                              data={"callback_query_id": cq["id"]})
                            except requests.RequestException:
                                pass
                            # 按鈕的 callback_data 就是指令字串,直接餵回指令處理
                            self.handle({"chat": cq.get("message", {})
                                        .get("chat", {}),
                                        "text": cq.get("data", "")})
                    except Exception as e:
                        print(f"[tg] 處理失敗:{e}")
            except requests.RequestException:
                time.sleep(10)
            except Exception as e:
                print(f"[tg] 輪詢異常:{e}")
                time.sleep(10)


_bot: TgBot | None = None


def start():
    global _bot
    if _bot is not None or not config.TELEGRAM_BOT_TOKEN:
        return
    _bot = TgBot()
    from . import server
    server.SUBSCRIBERS.append(_bot.on_job_event)
    threading.Thread(target=_bot._poll_loop, daemon=True).start()
    threading.Thread(target=_bot._push_worker, daemon=True).start()

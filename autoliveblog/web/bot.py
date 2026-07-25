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

_API = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}"
_ALLOWED = {s.strip() for s in config.TELEGRAM_CHAT_ID.split(",") if s.strip()}

HELP = """<b>autoliveblog 機器人指令</b>
/watch 網址 — 開始監看直播/影片(自動偵測)
/watch 網址 補課 — 從直播開頭補課
/now [任務ID] — 目前話題 + 滾動摘要 + 最新重要截圖
/ask 問題 — 針對最新任務(或最近總結)提問
/stop [任務ID] — 停止任務並產出最終總結(多任務時必須指定 ID)
/jobs — 列出任務與 ID
/history — 最近 5 份總結
/sub 頻道網址 [關鍵字,逗號分隔] — 訂閱頻道(開播通知,不自動開始)
/go 訂閱ID — 對開播中的訂閱開始「補課+即時」總結
/subs — 訂閱清單
/pause 訂閱ID、/resume 訂閱ID — 暫停/恢復訂閱
/unsub 訂閱ID — 取消訂閱
/digest — 立刻產出今日晨報
/askall 問題 — 跨所有歷史總結提問
/glossary 頻道名 詞1,詞2 — 教專有名詞(修同音字)"""


def _esc(s: str) -> str:
    return html.escape(str(s or ""))


class TgBot:
    def __init__(self):
        self.push_q: queue.Queue = queue.Queue()
        self._last_final: dict[str, bool] = {}
        self._pushed_first: set[str] = set()
        self._stalled_notified: set[str] = set()
        self._started_notified: set[str] = set()

    # ---------- 送出 ----------

    @staticmethod
    def _markup(buttons):
        """buttons: [[(顯示文字, callback指令), ...], ...] → inline_keyboard。"""
        if not buttons:
            return None
        return json.dumps({"inline_keyboard": [
            [{"text": t, "callback_data": d[:64]} for t, d in row]
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
        t = e.get("type")
        if t == "started" and job.id not in self._started_notified:
            # 訂閱自動開播、或網頁啟動的任務 → 通知(bot 指令啟動的已回覆過)
            self._started_notified.add(job.id)
            self.push_q.put((
                f"▶ 開始監看:<b>{_esc(e.get('title') or job.req.url)}</b>"
                f"(任務 {job.id})", [],
                [[("📋 現況", f"/now {job.id}"),
                  ("⏹ 停止並總結", f"/stop {job.id}")]]))
            return
        if t == "chunk":
            hits = e.get("keyword_hits") or []
            first = job.id not in self._pushed_first
            self._pushed_first.add(job.id)
            if not (first or e.get("topic_changed") or hits
                    or e.get("images")):
                return
            lines = [f"🗣 <b>{_esc(e.get('topic'))}</b>(<i>{_esc(e.get('elapsed'))}</i>)"]
            if hits:
                lines.insert(0, f"⚡ 關鍵字命中:{_esc('、'.join(hits))}")
            lines += [f"• {_esc(p)}" for p in (e.get("points") or [])[:4]]
            self.push_q.put((("\n".join(lines)), e.get("images") or []))
        elif t == "final" and not self._last_final.get(job.id):
            self._last_final[job.id] = True
            summary = e.get("summary") or "(直播結束,無最終總結)"
            self.push_q.put((
                f"✅ <b>最終總結:{_esc(job.title)}</b>\n\n{_esc(summary)}", []))
        elif t == "status" and e.get("status") == "stalled":
            if job.id not in self._stalled_notified:
                self._stalled_notified.add(job.id)
                self.push_q.put((
                    f"⚠ <b>{_esc(job.title or '直播')}</b> 目前收不到串流資料"
                    "(可能還沒正式開播),我會自動重連並在有內容時開始推播。", []))
        elif t == "error":
            self.push_q.put((f"⚠ 任務錯誤:{_esc(e.get('message'))}", []))

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
            self.send_text(chat_id, HELP)

        elif cmd == "/watch":
            if not arg:
                return self.send_text(chat_id, "用法:/watch 網址 [補課]")
            from_start = "補課" in arg or "from-start" in arg
            url = arg.split()[0]
            req = server.WatchRequest(url=url, smart=True,
                                      from_start=from_start)
            job = server.launch_job(req)
            self._started_notified.add(job.id)  # 已在此回覆,不再重複推播
            self.send_text(chat_id,
                           f"▶ 已開始監看(任務 {job.id})"
                           f"{',補課模式' if from_start else ''}。"
                           "話題轉換時會推播;隨時 /now 看進度。")

        elif cmd == "/now":
            job = self._find_job(server, arg) if arg else self._latest_job(server)
            if not job:
                return self.send_text(chat_id, "找不到任務。用 /jobs 看清單、"
                                               "/watch 網址 開始。")
            s = job.snapshot()
            lines = [f"📺 <b>{_esc(s['title'] or s['url'])}</b>({_esc(s['status'])})",
                     f"🗣 目前話題:<b>{_esc(s['current_topic'] or '等待中…')}</b>", ""]
            if s["rolling_summary"]:
                lines.append(_esc(s["rolling_summary"]))
            for t in s["timeline"][-2:]:
                lines.append(f"\n[{_esc(t['elapsed'])}] {_esc(t['topic'])}")
                lines += [f"• {_esc(p)}" for p in t["points"][:3]]
            self.send_text(chat_id, "\n".join(lines))
            images = [img for t in s["timeline"][-3:] for img in t["images"]][-2:]
            for img in images:
                self.send_photo(chat_id, config.OUTPUT_DIR / img)

        elif cmd == "/ask":
            if not arg:
                return self.send_text(chat_id, "用法:/ask 你的問題")
            job = self._latest_job(server)
            try:
                if job:
                    ans = server.answer_question(arg, job_id=job.id)
                else:
                    hist = server.history()
                    if not hist:
                        return self.send_text(chat_id, "沒有可提問的內容。")
                    ans = server.answer_question(
                        arg, history_name=hist[0]["name"])
                self.send_text(chat_id, f"💬 {_esc(ans)}")
            except Exception as e:
                self.send_text(chat_id, f"問答失敗:{_esc(e)}")

        elif cmd == "/stop":
            if arg:
                job = self._find_job(server, arg, running_only=True)
                if not job:
                    return self.send_text(chat_id, f"找不到進行中的任務「{_esc(arg)}」,"
                                                   "用 /jobs 看 ID。")
            else:
                running = [j for j in server.JOBS.values()
                           if j.status == "running"]
                if not running:
                    return self.send_text(chat_id, "沒有進行中的任務。")
                if len(running) > 1:
                    lines = ["有多個任務進行中,請用 /stop ID 指定:"]
                    lines += [f"<code>{j.id}</code> {_esc((j.title or j.req.url)[:40])}"
                              for j in running]
                    return self.send_text(chat_id, "\n".join(lines))
                job = running[0]
            job.stop_event.set()
            self.send_text(chat_id, f"⏹ 已要求停止任務 {job.id}"
                                    f"({_esc((job.title or '')[:30])}),"
                                    "最終總結產出後會推播給你。")

        elif cmd == "/jobs":
            jobs = sorted(server.JOBS.values(), key=lambda j: j.created,
                          reverse=True)[:5]
            if not jobs:
                return self.send_text(chat_id, "目前沒有任務。")
            lines = [f"{j.id} [{j.status}] {_esc((j.title or j.req.url)[:50])}"
                     f"(段落 {len(j.timeline)})" for j in jobs]
            btns = [[(f"📋 {j.id[:4]} 現況", f"/now {j.id}"),
                     (f"⏹ {j.id[:4]} 停止", f"/stop {j.id}")]
                    for j in jobs if j.status == "running"]
            self.send_text(chat_id, "\n".join(lines), buttons=btns or None)

        elif cmd == "/sub":
            if not arg:
                return self.send_text(chat_id,
                                      "用法:/sub 頻道網址 [關鍵字,逗號分隔]\n"
                                      "例:/sub https://www.youtube.com/@ustvbiz 台積電,比特幣")
            parts = arg.split(None, 1)
            keywords = []
            if len(parts) > 1:
                keywords = [k.strip() for k in parts[1].replace("，", ",")
                            .split(",") if k.strip()]
            r = server.add_sub(server.SubRequest(channel_url=parts[0],
                                                 keywords=keywords))
            mins = max(1, config.SUB_POLL_SECONDS // 60)
            self.send_text(chat_id,
                           f"🔔 已訂閱(ID {r['id']})。每 {mins} 分鐘檢查一次,"
                           "開播會推播通知並附「開始總結」按鈕(按了才開始,不會自動燒額度)"
                           + (f";關鍵字:{_esc('、'.join(keywords))}" if keywords else "")
                           + "。/subs 看清單。")

        elif cmd == "/go":
            if not arg:
                return self.send_text(chat_id, "用法:/go 訂閱ID(/subs 可查)")
            try:
                job = server.start_sub_watch(arg.strip())
                self._started_notified.add(job.id)
                self.send_text(chat_id,
                               f"▶ 開始補課+即時總結(任務 {job.id})。"
                               "補課速度受 YouTube 限制,開播越久補越久;"
                               "第一批摘要出來會馬上推播。")
            except KeyError as e:
                self.send_text(chat_id, f"無法開始:{_esc(e)}")

        elif cmd == "/subs":
            subs = server.list_subs()
            if not subs:
                return self.send_text(chat_id, "還沒有訂閱。用 /sub 頻道網址 新增。")
            lines = []
            btns = []
            for s in subs:
                if not s.get("enabled", True):
                    st = "⏸ 已暫停"
                elif s.get("last_error"):
                    st = "⚠ 檢查失敗"
                elif s.get("is_feed"):
                    st = "🎧 Podcast" if s.get("startable") else "🎧 尚無集數"
                else:
                    st = "🔴 直播中" if s.get("live_now") else "⚪ 未開播"
                kw = f" ⚡{_esc('、'.join(s['keywords']))}" if s.get("keywords") else ""
                lines.append(f"<code>{s['id']}</code> {st} "
                             f"{_esc(s['channel_url'])}{kw}")
                tag = s["channel_url"].rstrip("/").rsplit("/", 1)[-1][:12]
                row = []
                if s.get("startable"):
                    row.append((f"▶ 開始 {tag}", f"/go {s['id']}"))
                row.append((("▶ 恢復" if not s.get("enabled", True)
                             else "⏸ 暫停") + f" {tag}",
                            ("/resume " if not s.get("enabled", True)
                             else "/pause ") + s["id"]))
                btns.append(row)
            self.send_text(chat_id, "\n".join(lines), buttons=btns or None)

        elif cmd in ("/pause", "/resume"):
            if not arg:
                return self.send_text(chat_id, f"用法:{cmd} 訂閱ID(/subs 可查)")
            ok = server.set_sub_enabled(arg.strip(), cmd == "/resume")
            self.send_text(chat_id,
                           ("▶ 已恢復" if cmd == "/resume" else "⏸ 已暫停")
                           + f"訂閱 {_esc(arg)}。" if ok
                           else f"找不到訂閱「{_esc(arg)}」,/subs 看清單。")

        elif cmd == "/unsub":
            if not arg:
                return self.send_text(chat_id, "用法:/unsub 訂閱ID(/subs 可查)")
            try:
                server.del_sub(arg.strip())
                self.send_text(chat_id, f"已取消訂閱 {_esc(arg)}。")
            except Exception:
                self.send_text(chat_id, f"找不到訂閱「{_esc(arg)}」,/subs 看清單。")

        elif cmd == "/digest":
            self.send_text(chat_id, "📰 產出中,約 1 分鐘…")
            def _run():
                try:
                    d = server.generate_digest()
                    self.send_text(chat_id, f"📰 <b>今日晨報</b>\n\n{_esc(d)}"
                                   if d else "今天還沒有任何總結。")
                except Exception as e:
                    self.send_text(chat_id, f"晨報失敗:{_esc(e)}")
            threading.Thread(target=_run, daemon=True).start()

        elif cmd == "/askall":
            if not arg:
                return self.send_text(chat_id, "用法:/askall 你的問題(跨所有歷史總結)")
            self.send_text(chat_id, "🔎 翻閱歷史總結中…")
            def _runq():
                try:
                    self.send_text(chat_id,
                                   f"💬 {_esc(server.answer_question_global(arg))}")
                except Exception as e:
                    self.send_text(chat_id, f"問答失敗:{_esc(e)}")
            threading.Thread(target=_runq, daemon=True).start()

        elif cmd == "/glossary":
            from .. import glossary
            parts = arg.split(None, 1)
            if len(parts) < 2:
                gl = glossary.load_all()
                if not gl:
                    return self.send_text(chat_id,
                                          "用法:/glossary 頻道名 詞1,詞2\n目前辭典是空的。")
                lines = [f"{ch}:{_esc('、'.join(ts))}" for ch, ts in gl.items()]
                return self.send_text(chat_id, "目前辭典:\n" + "\n".join(lines))
            terms = [t.strip() for t in parts[1].replace("，", ",").split(",")
                     if t.strip()]
            glossary.add_terms(parts[0], terms)
            self.send_text(chat_id,
                           f"已加入「{_esc(parts[0])}」辭典:{_esc('、'.join(terms))}")

        elif cmd == "/history":
            hist = server.history()[:5]
            if not hist:
                return self.send_text(chat_id, "還沒有總結紀錄。")
            lines = [f"{'🔴' if h['is_live'] else '🎬'} {_esc(h['title'][:60])}"
                     for h in hist]
            self.send_text(chat_id, "\n".join(lines))

        else:
            self.send_text(chat_id, HELP)

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

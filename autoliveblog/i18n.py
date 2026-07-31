"""使用者介面文字的翻譯層。

English is the source of truth; Traditional Chinese is a translation.
Pick the locale with AUTOLIVEBLOG_UI_LANG (default "en").

Usage:
    from .i18n import t
    t("bot.watching", title=job.title, job=job.id)

Rules for adding strings:
- Key format is "area.thing", lower case, dots for nesting.
- Put the English text in EN. If a key is missing from a locale the
  English text is used, so a partial translation degrades gracefully
  instead of crashing.
- Use named placeholders ({title}), never positional ones.
"""
import os

_DEFAULT = "en"

EN: dict[str, str] = {
    # ---- web page ----
    "web.title": "autoliveblog — live stream summaries",
    "web.tagline": "Live summaries · YouTube / Twitch / Podcast",
    "web.url_placeholder": "Paste a live stream, video or podcast RSS URL",
    "web.inspect": "Check",
    "web.start": "Start",
    "web.smart_frames": "Smart re-look",
    "web.from_start": "Catch up from start",
    "web.every_n_min": "Every {n} min",
    "web.engine": "Engine: {name}",
    "web.engine_auto": "auto",
    "web.until_end": "Until the stream ends",
    "web.watch_minutes": "Watch {n} min",
    "web.keywords_placeholder": "Keyword alerts (comma separated)",
    "web.subs_section": "Channel subscriptions (notify when live)",
    "web.sub_url_placeholder": "Channel URL, e.g. https://www.youtube.com/@handle",
    "web.sub_keywords_placeholder": "Keyword alerts (optional)",
    "web.sub_add": "Subscribe",
    "web.history": "History",
    "web.askall_placeholder": "Ask across every past summary",
    "web.askall": "Ask all",
    "web.search_placeholder": "Search by title or channel",
    "web.refresh": "Refresh",
    "web.notifications": "Notifications",
    "web.notifications_on": "Notifications: on",
    "web.play": "Play",
    "web.stop_and_summarize": "Stop and summarize",
    "web.remove": "Remove",
    "web.current_topic": "Current topic",
    "web.waiting_first": "(waiting for the first segment…)",
    "web.segments": "Segments",
    "web.smart_hits": "Smart re-looks",
    "web.keyword_hits": "Keyword hits",
    "web.rolling_summary": "Rolling summary",
    "web.timeline": "Timeline (newest first)",
    "web.final_summary": "Final summary",
    "web.ask_placeholder": "Ask about what was just said",
    "web.ask": "Ask",
    "web.thinking": "Thinking…",
    "web.searching_history": "Searching past summaries…",
    "web.close": "Close",
    "web.close_player": "Close player",
    "web.no_history": "No summaries yet",
    "web.no_subs": ("No subscriptions yet. Add one and it will be checked "
                    "every few minutes; you get a notification when it goes live."),
    "web.start_summary": "Start summary",
    "web.pause": "Pause",
    "web.resume": "Resume",
    "web.checking": "checking",
    "web.live_now": "live",
    "web.not_live": "offline",
    "web.paused": "paused",
    "web.check_failed": "check failed",
    "web.podcast": "Podcast",
    "web.last_checked": "checked {time}",
    "web.live_badge": "LIVE",
    "web.video_badge": "Video",
    "web.status_starting": "starting…",
    "web.status_live": "watching live",
    "web.status_summarizing": "summarizing",
    "web.status_done": "done",
    "web.status_error": "error",
    "web.usage": "Today {calls} calls",
    "web.usage_openai": " · OpenAI {calls}",
    "web.usage_retries": " · {n} retries",
    "web.smart_badge": "re-look",
    "web.enter_url": "Paste a URL first",
    "web.cannot_read": "Could not read that URL: {err}",
    "web.started_watching": "Started watching",
    "web.cannot_start": "Cannot start: {err}",
    "web.confirm_delete": "Delete this summary?",
    "web.ask_failed": "Question failed: {err}",
    "web.stop_running_first": "Stop the job before removing it",
    "web.usage_hint": "API calls today",
    "web.usage_tip_gemini": ("Gemini: {in_k}k in / {out_k}k out tokens "
                             "(free tier $0; paid equivalent ${usd})"),
    "web.usage_tip_openai": ("OpenAI: {in_k}k in / {out_k}k out tokens "
                             "+ {mins} min transcription = ${usd}"),
    "web.notifications_hint": "Turn on browser notifications",
    "web.from_start_hint": ("Summarize from the start of the stream, catching "
                            "up quickly before joining live"),
    "web.engine_hint": "AI engine",
    "web.notify_topic_changed": "Topic changed",
    "web.notify_keyword": "Keyword: {kw}",
    "web.keyword_alert": "Keyword “{kw}”: {topic}",
    "web.notify_done": "Summary finished",
    "web.notify_done_body": "The final summary is ready",
    "web.none_yet": "(nothing yet)",
    "web.error": "Error: {err}",
    "web.duration_min": "{n} min",
    "web.ask_history_placeholder": "Ask about this summary",

    # ---- telegram bot ----
    "bot.help": """<b>autoliveblog commands</b>
/watch URL — summarize a live stream or video
/watch URL catchup — start from the beginning of the stream
/now [job] — current topic, rolling summary and latest frames
/ask QUESTION — ask about the job that is running
/stop [job] — stop a job and write the final summary
/jobs — list jobs and their ids
/history — the five most recent summaries
/sub CHANNEL [keywords] — notify me when this channel goes live
/go ID — start summarizing a subscription that is live
/subs — list subscriptions
/pause ID, /resume ID — pause or resume a subscription
/unsub ID — remove a subscription
/digest — build today's digest now
/askall QUESTION — ask across every past summary
/glossary CHANNEL TERMS — teach names the model keeps mishearing""",
    "bot.online": ("autoliveblog is online. Send /help for the command list."),
    "bot.watch_usage": "Usage: /watch URL [catchup]",
    "bot.watch_started": ("Watching (job {job}){mode}. "
                          "You get a push when the topic changes; /now for status."),
    "bot.mode_catchup": ", catching up from the start",
    "bot.no_jobs": "No jobs. Start one with /watch URL.",
    "bot.job_not_found": "No job matching “{arg}”. /jobs lists them.",
    "bot.ask_usage": "Usage: /ask your question",
    "bot.askall_usage": "Usage: /askall your question (searches every summary)",
    "bot.no_running": "Nothing is running.",
    "bot.pick_job": "Several jobs are running. Use /stop ID:",
    "bot.stopping": "Stopping job {job} ({title}). The final summary follows.",
    "bot.sub_usage": ("Usage: /sub CHANNEL_URL [keyword,keyword]\n"
                      "e.g. /sub https://www.youtube.com/@handle nvidia,earnings"),
    "bot.sub_added": ("Subscribed (id {sid}). Checked every {mins} min. "
                      "When it goes live you get a notification with a start "
                      "button — nothing runs until you press it{kw}."),
    "bot.sub_keywords": "; keywords: {kw}",
    "bot.no_subs": "No subscriptions. Add one with /sub CHANNEL_URL.",
    "bot.sub_not_found": "No subscription “{arg}”. /subs lists them.",
    "bot.unsubbed": "Removed subscription {sid}.",
    "bot.paused": "Paused subscription {sid}.",
    "bot.resumed": "Resumed subscription {sid}.",
    "bot.go_usage": "Usage: /go SUBSCRIPTION_ID (see /subs)",
    "bot.go_started": ("Started (job {job}). It catches up on what you missed, "
                       "then follows the stream live."),
    "bot.cannot_start": "Cannot start: {err}",
    "bot.digest_building": "Building today's digest, about a minute…",
    "bot.digest_title": "<b>Today's digest</b>",
    "bot.digest_empty": "Nothing summarized today.",
    "bot.digest_failed": "Digest failed: {err}",
    "bot.no_history": "No summaries yet.",
    "bot.glossary_usage": ("Usage: /glossary CHANNEL term,term\n"
                           "Current glossary is empty."),
    "bot.glossary_current": "Current glossary:",
    "bot.glossary_added": "Added to “{channel}”: {terms}",
    "bot.watching": "Watching: <b>{title}</b> (job {job})",
    "bot.btn_status": "Status",
    "bot.btn_stop": "Stop and summarize",
    "bot.btn_start": "Start summary",
    "bot.btn_start_named": "Start {name}",
    "bot.btn_pause": "Pause {name}",
    "bot.btn_resume": "Resume {name}",
    "bot.btn_jobs": "Jobs",
    "bot.btn_subs": "Subscriptions",
    "bot.topic": "<b>{topic}</b> (<i>{elapsed}</i>)",
    "bot.keyword_hit": "Keyword hit: {kw}",
    "bot.final_title": "<b>Final summary: {title}</b>",
    "bot.final_empty": "(stream ended, no final summary)",
    "bot.went_live": ("<b>{channel}</b> is live!\n{title}\n\n"
                      "Press the button when you want it summarized — it starts "
                      "by catching up on what you missed. Nothing runs until you do."),
    "bot.new_episode": ("<b>{channel}</b> has a new episode!\n{title}\n\n"
                        "Press the button to download and summarize it. "
                        "Nothing runs until you do."),
    "bot.stalled": ("<b>{title}</b> is not receiving any stream data "
                    "(it may not have started yet). Reconnecting automatically."),
    "bot.job_error": "Job error: {err}",
    "bot.chunk_error": ("First segment of <b>{title}</b> failed "
                        "(<i>{elapsed}</i>):\n{err}"),
    "bot.quota_hint": ("\n\nThis looks like an exhausted API quota. The Gemini "
                       "free tier resets daily, OpenAI needs credit, or set "
                       "AUTOLIVEBLOG_STT_PROVIDER=local for free local transcription."),
    "bot.server_restarted": ("autoliveblog server was down and has been "
                             "restarted; running jobs resume automatically."),
    "bot.sub_status_live": "live",
    "bot.sub_status_offline": "offline",
    "bot.sub_status_paused": "paused",
    "bot.sub_status_failed": "check failed",
    "bot.sub_status_podcast": "Podcast",
    "bot.sub_status_podcast_empty": "Podcast (no episodes)",
    "bot.untitled_stream": "this stream",
    "bot.n_segments": "{n} segments",
    "bot.btn_status_named": "Status {name}",
    "bot.btn_stop_named": "Stop {name}",
    "bot.pause_usage": "Usage: {cmd} SUBSCRIPTION_ID (see /subs)",
    "bot.unsub_usage": "Usage: /unsub SUBSCRIPTION_ID (see /subs)",

    # ---- http api errors ----
    "api.job_not_found": "No such job.",
    "api.summary_not_found": "No such summary.",
    "api.ask_needs_target": "A job id or a summary name is required.",
    "api.sub_no_content": "No new content detected for this subscription yet.",
    "api.no_matching_history": "No related past summaries found.",

    # ---- summary markdown ----
    "md.live_title": "Live summary: {title}",
    "md.url": "Source",
    "md.started": "Started watching",
    "md.timeline_note_stream": "timeline is measured from the start of the stream",
    "md.timeline_note_watch": "timeline is measured from when watching began",
    "md.updated": "Updated",
    "md.final_summary": "Final summary",
    "md.current_topic": "Current topic",
    "md.waiting": "(waiting for the first segment…)",
    "md.rolling_summary": "Rolling summary",
    "md.none_yet": "(nothing yet)",
    "md.key_frames": "Key frames (requested by the model)",
    "md.timeline": "Timeline",
    "md.channel": "Channel",
    "md.summarized_at": "Summarized at",
    "md.digest_title": "Digest for {date}",

    # ---- cli ----
    "cli.description": ("Summarize live streams, videos and podcasts "
                        "(YouTube, Twitch, RSS and anything yt-dlp supports)"),
    "cli.url_help": "Live stream, video or podcast feed URL",
    "cli.live_help": "force live mode",
    "cli.vod_help": "force video mode",
    "cli.lang_help": "summary language (default: {default})",
    "cli.provider_help": ("AI engine (default auto: Gemini first, "
                          "falls back to OpenAI when the quota runs out)"),
    "cli.model_help": "Gemini model (default: {default})",
    "cli.chunk_help": "seconds between live summaries (default: {default})",
    "cli.duration_help": ("stop after this many minutes and write the final "
                          "summary (default: run until the stream ends)"),
    "cli.frames_help": ("capture a frame every N seconds for the model to read "
                        "(0 disables; 30 is a good default)"),
    "cli.smart_help": ("smart re-look: keep dense frames locally and let the "
                       "model request specific moments (implies --frames 30)"),
    "cli.from_start_help": ("catch up from the beginning of the stream, then "
                            "continue live (disables frame capture)"),
    "cli.no_toast_help": "disable desktop notifications",
    "cli.dry_run_help": "fetch info and subtitles only, do not call the model",
    "cli.cookies_help": "read cookies from this browser for members-only content",
    "cli.reading": "Reading source information…",
    "cli.cannot_read": "Could not read that URL: {err}",
    "cli.is_live_dry": "This is live: {title} (--dry-run does not start watching)",
    "cli.aborted": "Aborted.",
    "cli.error": "Error: {err}",

    # ---- runtime / console ----
    "run.live_header": "Live: {title}",
    "run.summary_interval": "Summarizing every {n}s; live file: {path}",
    "run.will_stop_after": "Stopping automatically after {mins} min ({n} segments).",
    "run.ctrl_c": "Press Ctrl+C to stop and write the final summary.",
    "run.stream_elapsed": ("Stream has been running {elapsed}; "
                           "timeline is measured from its start."),
    "run.catchup_mode": ("Catch-up mode: downloading from the start of the "
                         "stream, then joining the live edge…"),
    "run.frames_off_catchup": ("Frame capture is disabled in catch-up mode "
                               "(history downloads faster than real time)."),
    "run.saved": "Saved: {path}",
    "run.stream_ended": "The stream has ended.",
    "run.stopping": "Stop requested, finishing up…",
    "run.reached_limit": "Reached {n} segments, finishing up…",
    "run.stalled": ("No stream data for {secs}s (it may not have started yet); "
                    "reconnecting…"),
    "run.reconnecting": "Stream interrupted, reconnecting…",
    "run.catchup_interrupted": ("Catch-up download interrupted; switching to the "
                                "live edge (there may be a gap)."),
    "run.too_many_failures": "Too many ffmpeg failures, stopping. Last error: {err}",
    "run.summary_failed": "Segment summary failed: {err}",
    "run.aborting": "{n} segments failed in a row, stopping. Last error: {err}",
    "run.glossary_applied": "Applied the “{channel}” glossary ({n} terms)",
    "run.final_failed": "Final summary failed: {err}",
    "run.smart_request": "Model asked to look at {secs}s and refined the summary",
    "run.video_header": "Video: {title}",
    "run.video_meta": "Channel: {channel}  Length: {mins}m {secs}s",
    "run.trying_subs": "Looking for subtitles…",
    "run.subs_failed": "Subtitle download failed: {err}",
    "run.got_subs": "Got subtitles ({name}, {n} characters)",
    "run.no_subs_preview": ("No subtitles; a real run would download the audio "
                            "for the model to listen to."),
    "run.summarizing_subs": "Summarizing from subtitles…",
    "run.no_subs_audio": "No subtitles, downloading audio…",
    "run.downloading_podcast": "Downloading podcast audio…",
    "run.audio_ready": "Audio ready ({name}, {mb} MB), sending to the model…",
    "run.long_audio_part": "Long audio: segment {i}/{n} done",
    "run.long_audio_failed": "Long audio: segment {i}/{n} failed: {err}",
    "run.need_ffmpeg": ("Live mode needs ffmpeg. Install it with: "
                        "winget install Gyan.FFmpeg.Essentials"),
    "run.topic_changed": "Topic change: {topic}",
    "run.transcript_preview": "--- Transcript preview (first 1500 characters) ---",
    "run.bad_audio_url": "Unsupported audio URL: {url}",
    "run.audio_too_large": ("The audio is {mb} MB, over the {limit} MB limit "
                            "(raise it with AUTOLIVEBLOG_MAX_AUDIO_MB)"),
    "run.audio_limit_hit": ("The audio went past the {limit} MB limit, download "
                            "aborted (this enclosure may be an endless stream)"),
    "run.download_timeout": ("The download went past the {mins} minute limit "
                             "and was aborted"),

    # ---- ai engines ----
    # 注意:AutoSummarizer 是用「錯誤訊息的文字」判斷要不要切換備援引擎
    # (_QUOTA_MARKERS 與 _is_dead_credit_error)。這裡的文字絕對不能出現
    # quota / rate limit / 429 / 503 / UNAVAILABLE / NOT_FOUND / billing /
    # insufficient_quota 等字樣,否則會誤判引擎狀態。真正的判斷依據來自
    # {err} 裡原廠的錯誤字串。
    "engine.no_gemini_key": ("GEMINI_API_KEY not found. Get a free key at "
                             "https://aistudio.google.com/apikey and write it "
                             "into the .env in the project root "
                             "(see .env.example)."),
    "engine.no_openai_key": ("OPENAI_API_KEY not found. Add "
                             "OPENAI_API_KEY=sk-... to the .env in the "
                             "project root."),
    "engine.no_api_key": ("No API key found. Set GEMINI_API_KEY "
                          "(free, https://aistudio.google.com/apikey) or "
                          "OPENAI_API_KEY in .env."),
    "engine.long_audio_all_failed": ("All {n} segments of the long audio failed "
                                     "to summarize, so no report could be "
                                     "produced."),
    "engine.gemini_file_failed": "Gemini could not process the file: {name}",
    "engine.gemini_key_invalid": ("Gemini key is invalid or lacks permission: "
                                  "{err}"),
    "engine.gemini_model_gone": ("The Gemini model “{model}” cannot be used "
                                 "(it may have been retired for new projects). "
                                 "Switch to a model that still works, e.g. set "
                                 "AUTOLIVEBLOG_MODEL=gemini-3.5-flash-lite. "
                                 "Original error: {err}"),
    "engine.gemini_credits": ("The Gemini project has run out of prepaid "
                              "credits (this is not the daily free allowance). "
                              "Top up at https://ai.studio/projects, or use a "
                              "free-tier key with no payment account attached. "
                              "Original error: {err}"),
    "engine.gemini_retry": ("[Gemini call failed, retrying in {wait}s — "
                            "attempt {attempt}/{retries}] {err}"),
    "engine.gemini_failed": "Gemini call failed: {err}",
    "engine.openai_key_invalid": "OpenAI key is invalid: {err}",
    "engine.openai_retry": ("[OpenAI call failed, retrying in {wait}s — "
                            "attempt {attempt}/{retries}] {err}"),
    "engine.openai_failed": "OpenAI call failed: {err}",
    "engine.local_whisper_loading": ("Loading the local Whisper model "
                                     "(small, CPU int8)…"),
    "engine.spend_guard": ("The audio is {mins} minutes long; OpenAI "
                           "transcription would cost about ${cost}, above the "
                           "automatic spend cap of ${cap}. Raise "
                           "AUTOLIVEBLOG_MAX_AUTO_SPEND_USD if you really want "
                           "to spend it, or set AUTOLIVEBLOG_STT_PROVIDER=local "
                           "to transcribe locally for free."),
    "engine.audio_needs_ffmpeg": ("Audio over 24MB needs ffmpeg to split it "
                                  "before transcription"),
    "engine.switch_to_openai": ("Gemini is throttled or overloaded; switching "
                                "to the OpenAI engine to keep going"),
    "engine.retry_primary": "Cooldown over, trying Gemini again",
    "engine.fallback_exhausted": ("OpenAI credit is used up; disabling the "
                                  "fallback and going back to Gemini "
                                  "(with normal retries)"),
}

ZH_TW: dict[str, str] = {
    # ---- 網頁 ----
    "web.title": "autoliveblog — 直播即時總結",
    "web.tagline": "直播即時總結 · YouTube / Twitch / Podcast",
    "web.url_placeholder": "貼上直播、影片或 Podcast RSS 網址",
    "web.inspect": "檢查",
    "web.start": "開始",
    "web.smart_frames": "智慧補看",
    "web.from_start": "從頭補課",
    "web.every_n_min": "每 {n} 分鐘",
    "web.engine": "引擎:{name}",
    "web.engine_auto": "自動",
    "web.until_end": "看到直播結束",
    "web.watch_minutes": "看 {n} 分鐘",
    "web.keywords_placeholder": "關鍵字警報(逗號分隔)",
    "web.subs_section": "頻道訂閱(開播通知)",
    "web.sub_url_placeholder": "頻道網址,例:https://www.youtube.com/@handle",
    "web.sub_keywords_placeholder": "關鍵字警報(選填)",
    "web.sub_add": "新增訂閱",
    "web.history": "歷史紀錄",
    "web.askall_placeholder": "跨所有歷史總結提問",
    "web.askall": "問全部",
    "web.search_placeholder": "搜尋標題或頻道",
    "web.refresh": "重新整理",
    "web.notifications": "通知",
    "web.notifications_on": "通知:開",
    "web.play": "播放",
    "web.stop_and_summarize": "停止並總結",
    "web.remove": "移除",
    "web.current_topic": "目前話題",
    "web.waiting_first": "(等待第一段…)",
    "web.segments": "已總結段數",
    "web.smart_hits": "智慧補看",
    "web.keyword_hits": "關鍵字命中",
    "web.rolling_summary": "滾動摘要",
    "web.timeline": "時間軸(最新在上)",
    "web.final_summary": "最終總結",
    "web.ask_placeholder": "問剛剛的內容",
    "web.ask": "問",
    "web.thinking": "思考中…",
    "web.searching_history": "翻閱歷史總結中…",
    "web.close": "關閉",
    "web.close_player": "關閉播放器",
    "web.no_history": "還沒有紀錄",
    "web.no_subs": "尚無訂閱。新增後會定期檢查,開播時通知你。",
    "web.start_summary": "開始總結",
    "web.pause": "暫停",
    "web.resume": "啟用",
    "web.checking": "檢查中",
    "web.live_now": "直播中",
    "web.not_live": "未開播",
    "web.paused": "已暫停",
    "web.check_failed": "檢查失敗",
    "web.podcast": "Podcast",
    "web.last_checked": "上次檢查 {time}",
    "web.live_badge": "直播",
    "web.video_badge": "影片",
    "web.status_starting": "解析中…",
    "web.status_live": "直播監看中",
    "web.status_summarizing": "總結中",
    "web.status_done": "已完成",
    "web.status_error": "錯誤",
    "web.usage": "今日 API {calls} 次",
    "web.usage_openai": " · OpenAI {calls}",
    "web.usage_retries": " · 重試 {n}",
    "web.smart_badge": "補看",
    "web.enter_url": "請先貼上網址",
    "web.cannot_read": "無法讀取:{err}",
    "web.started_watching": "已開始監看",
    "web.cannot_start": "無法開始:{err}",
    "web.confirm_delete": "刪除這份總結?",
    "web.ask_failed": "問答失敗:{err}",
    "web.stop_running_first": "執行中的任務要先停止",
    "web.usage_hint": "今日 API 呼叫數",
    "web.usage_tip_gemini": ("Gemini:{in_k}k 入 / {out_k}k 出 tokens"
                             "(免費層 $0;付費層等值 ${usd})"),
    "web.usage_tip_openai": ("OpenAI:{in_k}k 入 / {out_k}k 出 tokens "
                             "+ 轉錄 {mins} 分鐘 = ${usd}"),
    "web.notifications_hint": "開啟瀏覽器通知",
    "web.from_start_hint": "從直播開頭開始總結,快速消化歷史後接上即時",
    "web.engine_hint": "AI 引擎",
    "web.notify_topic_changed": "話題轉換",
    "web.notify_keyword": "關鍵字:{kw}",
    "web.keyword_alert": "關鍵字「{kw}」:{topic}",
    "web.notify_done": "總結完成",
    "web.notify_done_body": "最終總結已產出",
    "web.none_yet": "(尚無)",
    "web.error": "錯誤:{err}",
    "web.duration_min": "{n} 分鐘",
    "web.ask_history_placeholder": "針對這份總結提問",

    # ---- Telegram ----
    "bot.help": """<b>autoliveblog 指令</b>
/watch 網址 — 總結直播或影片
/watch 網址 補課 — 從直播開頭開始
/now [任務ID] — 目前話題、滾動摘要與最新截圖
/ask 問題 — 針對進行中的任務提問
/stop [任務ID] — 停止任務並產出最終總結
/jobs — 列出任務與 ID
/history — 最近 5 份總結
/sub 頻道網址 [關鍵字] — 訂閱頻道,開播時通知
/go 訂閱ID — 對開播中的訂閱開始總結
/subs — 訂閱清單
/pause 訂閱ID、/resume 訂閱ID — 暫停或恢復訂閱
/unsub 訂閱ID — 取消訂閱
/digest — 立刻產出今日晨報
/askall 問題 — 跨所有歷史總結提問
/glossary 頻道 詞1,詞2 — 教模型常聽錯的專有名詞""",
    "bot.online": "autoliveblog 已上線,傳 /help 看指令。",
    "bot.watch_usage": "用法:/watch 網址 [補課]",
    "bot.watch_started": "已開始監看(任務 {job}){mode}。話題轉換時會推播;隨時 /now 看進度。",
    "bot.mode_catchup": ",補課模式",
    "bot.no_jobs": "目前沒有任務。用 /watch 網址 開始。",
    "bot.job_not_found": "找不到任務「{arg}」,用 /jobs 看清單。",
    "bot.ask_usage": "用法:/ask 你的問題",
    "bot.askall_usage": "用法:/askall 你的問題(跨所有歷史總結)",
    "bot.no_running": "沒有進行中的任務。",
    "bot.pick_job": "有多個任務進行中,請用 /stop ID 指定:",
    "bot.stopping": "已要求停止任務 {job}({title}),最終總結產出後會推播。",
    "bot.sub_usage": "用法:/sub 頻道網址 [關鍵字,逗號分隔]\n例:/sub https://www.youtube.com/@handle 台積電,升息",
    "bot.sub_added": "已訂閱(ID {sid})。每 {mins} 分鐘檢查一次,開播會推播通知並附開始按鈕(按了才開始,不會自動燒額度){kw}。",
    "bot.sub_keywords": ";關鍵字:{kw}",
    "bot.no_subs": "還沒有訂閱。用 /sub 頻道網址 新增。",
    "bot.sub_not_found": "找不到訂閱「{arg}」,/subs 看清單。",
    "bot.unsubbed": "已取消訂閱 {sid}。",
    "bot.paused": "已暫停訂閱 {sid}。",
    "bot.resumed": "已恢復訂閱 {sid}。",
    "bot.go_usage": "用法:/go 訂閱ID(/subs 可查)",
    "bot.go_started": "已開始(任務 {job}):先補課錯過的內容,再接上即時總結。",
    "bot.cannot_start": "無法開始:{err}",
    "bot.digest_building": "晨報產出中,約 1 分鐘…",
    "bot.digest_title": "<b>今日晨報</b>",
    "bot.digest_empty": "今天還沒有任何總結。",
    "bot.digest_failed": "晨報失敗:{err}",
    "bot.no_history": "還沒有總結紀錄。",
    "bot.glossary_usage": "用法:/glossary 頻道名 詞1,詞2\n目前辭典是空的。",
    "bot.glossary_current": "目前辭典:",
    "bot.glossary_added": "已加入「{channel}」辭典:{terms}",
    "bot.watching": "監看中:<b>{title}</b>(任務 {job})",
    "bot.btn_status": "現況",
    "bot.btn_stop": "停止並總結",
    "bot.btn_start": "開始總結",
    "bot.btn_start_named": "開始 {name}",
    "bot.btn_pause": "暫停 {name}",
    "bot.btn_resume": "恢復 {name}",
    "bot.btn_jobs": "任務清單",
    "bot.btn_subs": "訂閱清單",
    "bot.topic": "<b>{topic}</b>(<i>{elapsed}</i>)",
    "bot.keyword_hit": "關鍵字命中:{kw}",
    "bot.final_title": "<b>最終總結:{title}</b>",
    "bot.final_empty": "(直播結束,無最終總結)",
    "bot.went_live": "<b>{channel}</b> 開播了!\n{title}\n\n想聽再按下面的按鈕(會先補課開播至今的內容,再接上即時總結),不按就只是通知。",
    "bot.new_episode": "<b>{channel}</b> 有新一集!\n{title}\n\n想聽再按下面的按鈕(會下載音檔並總結),不按就只是通知。",
    "bot.stalled": "<b>{title}</b> 目前收不到串流資料(可能還沒正式開播),我會自動重連。",
    "bot.job_error": "任務錯誤:{err}",
    "bot.chunk_error": "<b>{title}</b> 的第一段總結失敗(<i>{elapsed}</i>):\n{err}",
    "bot.quota_hint": "\n\n看起來是 API 額度用完了。Gemini 免費層每天會重置,OpenAI 需要儲值;也可以設 AUTOLIVEBLOG_STT_PROVIDER=local 用本地免費轉錄。",
    "bot.server_restarted": "autoliveblog 伺服器剛才停止運作,已自動重啟(監看任務會自動恢復)。",
    "bot.sub_status_live": "直播中",
    "bot.sub_status_offline": "未開播",
    "bot.sub_status_paused": "已暫停",
    "bot.sub_status_failed": "檢查失敗",
    "bot.sub_status_podcast": "Podcast",
    "bot.sub_status_podcast_empty": "Podcast(尚無集數)",
    "bot.untitled_stream": "這個直播",
    "bot.n_segments": "{n} 段",
    "bot.btn_status_named": "{name} 現況",
    "bot.btn_stop_named": "{name} 停止",
    "bot.pause_usage": "用法:{cmd} 訂閱ID(/subs 可查)",
    "bot.unsub_usage": "用法:/unsub 訂閱ID(/subs 可查)",

    # ---- HTTP API 錯誤 ----
    "api.job_not_found": "任務不存在。",
    "api.summary_not_found": "找不到這份總結。",
    "api.ask_needs_target": "需要指定任務 ID 或總結檔名。",
    "api.sub_no_content": "這個訂閱目前沒有偵測到新內容。",
    "api.no_matching_history": "找不到相關的歷史總結。",

    # ---- 總結 Markdown ----
    "md.live_title": "直播即時總結:{title}",
    "md.url": "網址",
    "md.started": "開始監看",
    "md.timeline_note_stream": "時間軸為直播開始起算",
    "md.timeline_note_watch": "時間軸為監看起算的相對時間",
    "md.updated": "最後更新",
    "md.final_summary": "最終總結",
    "md.current_topic": "目前話題",
    "md.waiting": "(等待第一段音訊…)",
    "md.rolling_summary": "滾動摘要",
    "md.none_yet": "(尚無)",
    "md.key_frames": "重要畫面(模型自主補看)",
    "md.timeline": "時間軸",
    "md.channel": "頻道",
    "md.summarized_at": "總結時間",
    "md.digest_title": "{date} 晨報",

    # ---- CLI ----
    "cli.description": "直播即時總結 / 影片與 Podcast 總結(支援 YouTube、Twitch、RSS 等)",
    "cli.url_help": "直播、影片或 Podcast feed 網址",
    "cli.live_help": "強制使用直播模式",
    "cli.vod_help": "強制使用影片模式",
    "cli.lang_help": "總結語言(預設:{default})",
    "cli.provider_help": "AI 引擎(預設 auto:Gemini 優先,額度耗盡自動切 OpenAI)",
    "cli.model_help": "Gemini 模型(預設:{default})",
    "cli.chunk_help": "直播每幾秒總結一次(預設:{default})",
    "cli.duration_help": "看滿幾分鐘後自動停止並產出最終總結(預設:直到直播結束)",
    "cli.frames_help": "每幾秒擷取一張畫面給模型看(0=關閉,建議 30)",
    "cli.smart_help": "智慧補看:本地密集抽圖,模型可自主要求加看特定時間點(未指定 --frames 時自動設 30)",
    "cli.from_start_help": "補課模式:從直播開頭開始總結,追上後接續即時(畫面截圖自動關閉)",
    "cli.no_toast_help": "關閉桌面通知",
    "cli.dry_run_help": "只抓資訊與字幕,不呼叫模型",
    "cli.cookies_help": "會員限定內容時從指定瀏覽器讀 cookie",
    "cli.reading": "讀取來源資訊中…",
    "cli.cannot_read": "無法讀取網址:{err}",
    "cli.is_live_dry": "這是直播:{title}(--dry-run 不進入監看)",
    "cli.aborted": "已中止。",
    "cli.error": "錯誤:{err}",

    # ---- 執行期訊息 ----
    "run.live_header": "直播:{title}",
    "run.summary_interval": "每 {n} 秒總結一次;即時結果寫入:{path}",
    "run.will_stop_after": "預計監看 {mins} 分鐘({n} 段)後自動收尾。",
    "run.ctrl_c": "按 Ctrl+C 停止並產出最終總結。",
    "run.stream_elapsed": "直播已進行 {elapsed},時間軸以直播開始起算。",
    "run.catchup_mode": "補課模式:從直播開頭下載,快速消化歷史段落後接上即時進度…",
    "run.frames_off_catchup": "補課模式下畫面截圖自動關閉(歷史音訊下載速度快於即時)",
    "run.saved": "已存檔:{path}",
    "run.stream_ended": "直播已結束。",
    "run.stopping": "收到停止指令,收尾中…",
    "run.reached_limit": "已看滿 {n} 段,收尾中…",
    "run.stalled": "串流停滯超過 {secs} 秒(可能還沒開播或斷流),強制重連…",
    "run.reconnecting": "串流中斷,重新連線中…",
    "run.catchup_interrupted": "補課下載中斷,改從最新進度續看(中間可能有缺口)…",
    "run.too_many_failures": "ffmpeg 連續失敗過多,停止監看。最後錯誤:{err}",
    "run.summary_failed": "總結失敗:{err}",
    "run.aborting": "連續 {n} 段總結失敗,停止監看。最後的錯誤:{err}",
    "run.glossary_applied": "已套用「{channel}」專有名詞辭典({n} 詞)",
    "run.final_failed": "最終總結失敗:{err}",
    "run.smart_request": "模型自主要求加看 {secs} 秒處的畫面並精修",
    "run.video_header": "影片:{title}",
    "run.video_meta": "頻道:{channel}  長度:{mins} 分 {secs} 秒",
    "run.trying_subs": "嘗試下載字幕…",
    "run.subs_failed": "字幕下載失敗:{err}",
    "run.got_subs": "取得字幕({name},{n} 字)",
    "run.no_subs_preview": "沒有字幕;正式執行時會下載音訊交給模型聽。",
    "run.summarizing_subs": "以字幕逐字稿總結中…",
    "run.no_subs_audio": "沒有字幕,下載音訊中…",
    "run.downloading_podcast": "下載 Podcast 音檔中…",
    "run.audio_ready": "音訊下載完成({name},{mb} MB),交給模型總結中…",
    "run.long_audio_part": "長音訊分段 {i}/{n} 完成",
    "run.long_audio_failed": "長音訊分段 {i}/{n} 失敗:{err}",
    "run.need_ffmpeg": "直播模式需要 ffmpeg。請安裝:winget install Gyan.FFmpeg.Essentials",
    "run.topic_changed": "話題轉換:{topic}",
    "run.transcript_preview": "--- 逐字稿預覽(前 1500 字)---",
    "run.bad_audio_url": "不支援的音檔網址:{url}",
    "run.audio_too_large": ("音檔 {mb} MB 超過上限 {limit} MB"
                            "(可調 AUTOLIVEBLOG_MAX_AUDIO_MB)"),
    "run.audio_limit_hit": ("音檔超過 {limit} MB 上限,已中止下載"
                            "(這個 enclosure 可能是無限長的串流)"),
    "run.download_timeout": "下載超過 {mins} 分鐘上限,已中止",

    # ---- AI 引擎 ----
    # 這裡的英文專有名詞不要亂動,原因見 EN 目錄同段落的註解
    "engine.no_gemini_key": ("找不到 GEMINI_API_KEY。請到 "
                             "https://aistudio.google.com/apikey 免費申請,"
                             "並寫入專案根目錄的 .env(參考 .env.example)。"),
    "engine.no_openai_key": ("找不到 OPENAI_API_KEY。請把 "
                             "OPENAI_API_KEY=sk-... 加入專案根目錄的 .env。"),
    "engine.no_api_key": ("找不到任何 API 金鑰。請在 .env 設定 GEMINI_API_KEY"
                          "(免費,https://aistudio.google.com/apikey)"
                          "或 OPENAI_API_KEY。"),
    "engine.long_audio_all_failed": "長音訊的 {n} 段全部總結失敗,無法產出報告。",
    "engine.gemini_file_failed": "Gemini 檔案處理失敗:{name}",
    "engine.gemini_key_invalid": "Gemini 金鑰無效或無權限:{err}",
    "engine.gemini_model_gone": ("Gemini 模型「{model}」無法使用"
                                 "(可能已對新專案下架)。請改用可用的模型,"
                                 "例如設環境變數 "
                                 "AUTOLIVEBLOG_MODEL=gemini-3.5-flash-lite。"
                                 "原始錯誤:{err}"),
    "engine.gemini_credits": ("Gemini 專案的預付點數已用盡"
                              "(這不是每日免費額度問題)。請到 "
                              "https://ai.studio/projects 儲值,"
                              "或改用未綁定付款帳戶的免費層金鑰。"
                              "原始錯誤:{err}"),
    "engine.gemini_retry": "[Gemini 呼叫失敗,{wait}s 後重試 {attempt}/{retries}] {err}",
    "engine.gemini_failed": "Gemini 呼叫失敗:{err}",
    "engine.openai_key_invalid": "OpenAI 金鑰無效:{err}",
    "engine.openai_retry": "[OpenAI 呼叫失敗,{wait}s 後重試 {attempt}/{retries}] {err}",
    "engine.openai_failed": "OpenAI 呼叫失敗:{err}",
    "engine.local_whisper_loading": "載入本地 Whisper 模型(small, CPU int8)…",
    "engine.spend_guard": ("音訊長 {mins} 分鐘,OpenAI 轉錄約需 ${cost},"
                           "超過自動花費上限 ${cap}。若確定要花,請設 "
                           "AUTOLIVEBLOG_MAX_AUTO_SPEND_USD 提高上限,"
                           "或設 AUTOLIVEBLOG_STT_PROVIDER=local 用本地免費轉錄。"),
    "engine.audio_needs_ffmpeg": "音訊超過 24MB 需要 ffmpeg 切段轉錄",
    "engine.switch_to_openai": "Gemini 受限/過載,自動切換 OpenAI 引擎續跑",
    "engine.retry_primary": "冷卻期結束,回頭嘗試 Gemini",
    "engine.fallback_exhausted": "OpenAI 餘額已用盡,停用備援並改回 Gemini(正常重試)",
}

CATALOGS: dict[str, dict[str, str]] = {"en": EN, "zh-TW": ZH_TW}
# 常見別名,讓 zh_TW / zh-tw / zh 都能對到繁中
_ALIASES = {"zh": "zh-TW", "zh-tw": "zh-TW", "zh_tw": "zh-TW",
            "zh-hant": "zh-TW", "en-us": "en", "english": "en"}


def _resolve(name: str) -> str:
    name = (name or "").strip()
    if name in CATALOGS:
        return name
    return _ALIASES.get(name.lower(), _DEFAULT)


def _initial_lang() -> str:
    # 直接讀 config 才能吃到 .env(config 會呼叫 load_dotenv);
    # 匯入失敗時退回純環境變數,避免循環相依把整個套件弄壞
    try:
        from . import config
        return _resolve(config.UI_LANG)
    except Exception:
        return _resolve(os.getenv("AUTOLIVEBLOG_UI_LANG", _DEFAULT))


_lang = _initial_lang()


def set_lang(name: str) -> None:
    global _lang
    _lang = _resolve(name)


def get_lang() -> str:
    return _lang


def t(key: str, **fmt) -> str:
    """取得目前語系的文字;缺翻譯時退回英文,缺 key 時回傳 key 本身。"""
    text = CATALOGS.get(_lang, EN).get(key) or EN.get(key)
    if text is None:
        return key
    if not fmt:
        return text
    try:
        return text.format(**fmt)
    except (KeyError, IndexError):
        # 翻譯的佔位符寫錯不該讓程式崩潰
        return EN.get(key, key).format(**fmt) if EN.get(key) else key


def catalog(lang: str | None = None) -> dict[str, str]:
    """整份目錄(給網頁前端用);缺的 key 以英文補齊。"""
    return {**EN, **CATALOGS.get(_resolve(lang or _lang), {})}

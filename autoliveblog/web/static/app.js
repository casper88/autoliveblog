"use strict";
const $ = (id) => document.getElementById(id);
const esc = (s) => (s || "").replace(/[&<>"']/g,
  (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));

// ---------- 介面文字(啟動時向 /api/strings 取回目前語系) ----------
let S = {};
// 日期時間跟著介面語系走;undefined = 交給瀏覽器自己的地區設定
let LOCALE;
const t = (k, f) => { let s = (S[k] || k); if (f) for (const p in f) s = s.replace("{"+p+"}", f[p]); return s; };

function applyStrings() {
  document.querySelectorAll("[data-i18n]").forEach(el => el.textContent = t(el.dataset.i18n));
  document.querySelectorAll("[data-i18n-ph]").forEach(el => el.placeholder = t(el.dataset.i18nPh));
  document.querySelectorAll("[data-i18n-title]").forEach(el => el.title = t(el.dataset.i18nTitle));
  // 帶參數的下拉選項:文字由 value 推導,選項本身維持不變
  document.querySelectorAll("#opt-chunk option").forEach(o =>
    o.textContent = t("web.every_n_min", { n: o.value / 60 }));
  const engines = { auto: t("web.engine_auto"), gemini: "Gemini", openai: "OpenAI" };
  document.querySelectorAll("#opt-provider option").forEach(o =>
    o.textContent = t("web.engine", { name: engines[o.value] || o.value }));
  document.querySelectorAll("#opt-duration option").forEach(o =>
    o.textContent = o.value ? t("web.watch_minutes", { n: o.value }) : t("web.until_end"));
}

// ---------- 通知 ----------
$("notif-btn").onclick = () => Notification.requestPermission().then(refreshNotifBtn);
function refreshNotifBtn() {
  $("notif-btn").textContent = "🔔 " +
    (Notification.permission === "granted" ? t("web.notifications_on") : t("web.notifications"));
}
function notify(title, body) {
  if (Notification.permission === "granted")
    new Notification(title, { body: body || "" });
}

// ---------- 用量 ----------
async function refreshUsage() {
  try {
    const u = await (await fetch("/api/usage")).json();
    const g = u.gemini, o = u.openai;
    let text = t("web.usage", { calls: g.calls });
    if (o.calls) text += t("web.usage_openai", { calls: o.calls });
    const retries = u.retries_429 + u.retries_503;
    if (retries) text += t("web.usage_retries", { n: retries });
    $("usage").textContent = text;
    $("usage").title =
      t("web.usage_tip_gemini", { in_k: (g.in_tokens / 1000).toFixed(1),
        out_k: (g.out_tokens / 1000).toFixed(1), usd: g.usd_equivalent.toFixed(3) }) + "\n" +
      t("web.usage_tip_openai", { in_k: (o.in_tokens / 1000).toFixed(1),
        out_k: (o.out_tokens / 1000).toFixed(1), mins: (o.audio_seconds / 60).toFixed(1),
        usd: o.usd.toFixed(4) });
  } catch {}
}

// ---------- 檢查網址 ----------
let inspected = null;
$("inspect-btn").onclick = async () => {
  const url = $("url").value.trim();
  if (!url) return;
  $("inspect-btn").innerHTML = '<span class="spin"></span>';
  try {
    const r = await fetch("/api/inspect", { method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url }) });
    if (!r.ok) throw new Error((await r.json()).detail);
    inspected = await r.json();
    $("preview").style.display = "flex";
    $("pv-thumb").src = inspected.thumbnail || "";
    $("pv-title").textContent = inspected.title || "";
    $("pv-channel").textContent = inspected.channel || "";
    $("pv-badge").className = "badge " + (inspected.is_live ? "b-red" : "b-blue");
    $("pv-badge").textContent = inspected.is_live ? t("web.live_badge") : t("web.video_badge");
    $("pv-dur").textContent = inspected.duration
      ? t("web.duration_min", { n: Math.floor(inspected.duration / 60) }) : "";
  } catch (e) { alert(t("web.cannot_read", { err: e.message })); }
  $("inspect-btn").textContent = t("web.inspect");
};

// ---------- 開始監看 ----------
$("start-btn").onclick = async () => {
  const url = $("url").value.trim();
  if (!url) return alert(t("web.enter_url"));
  const body = {
    url, mode: "auto",
    chunk: parseInt($("opt-chunk").value),
    duration: $("opt-duration").value ? parseInt($("opt-duration").value) : null,
    smart: $("opt-smart").checked,
    from_start: $("opt-fromstart").checked,
    provider: $("opt-provider").value,
    keywords: $("opt-keywords").value.split(/[,，]/).map(s => s.trim()).filter(Boolean),
  };
  const r = await fetch("/api/watch", { method: "POST",
    headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const { job_id } = await r.json();
  $("url").value = ""; $("preview").style.display = "none";
  await loadJobs(); subscribe(job_id);
};

// ---------- 任務卡片 ----------
const subscribed = new Set();

function jobCard(j) {
  const el = document.createElement("div");
  el.className = "card"; el.id = "job-" + j.id;
  el.innerHTML = `
    <div class="row">
      <span class="badge ${j.is_live ? "b-red" : "b-blue"}" id="st-${j.id}"></span>
      <b class="grow ellip" title="${esc(j.title)}">${esc(j.title || j.url)}</b>
      <button class="small play-btn">▶ ${esc(t("web.play"))}</button>
      <button class="small" id="stop-${j.id}">${esc(t("web.stop_and_summarize"))}</button>
      <button class="small" id="rm-${j.id}">${esc(t("web.remove"))}</button>
    </div>
    <div class="muted small">${esc(t("web.current_topic"))}</div>
    <div class="topic" id="topic-${j.id}">${esc(t("web.waiting_first"))}</div>
    <div class="stats">
      <div class="stat"><span class="muted small">${esc(t("web.segments"))}</span><b id="n-${j.id}">0</b></div>
      <div class="stat"><span class="muted small">${esc(t("web.smart_hits"))}</span><b id="sm-${j.id}">0</b></div>
      <div class="stat"><span class="muted small">${esc(t("web.keyword_hits"))}</span><b id="kw-${j.id}">0</b></div>
    </div>
    <div id="alerts-${j.id}"></div>
    <details><summary>${esc(t("web.rolling_summary"))}</summary>
      <p class="small" id="roll-${j.id}" style="color:var(--ink2)"></p></details>
    <div class="muted small" style="margin-top:8px">${esc(t("web.timeline"))}</div>
    <div class="tl" id="tl-${j.id}"></div>
    <div id="final-${j.id}"></div>
    <div class="row" style="margin-top:10px">
      <input type="text" id="q-${j.id}" class="grow small" placeholder="${esc(t("web.ask_placeholder"))}">
      <button class="small" id="ask-${j.id}">${esc(t("web.ask"))}</button>
    </div>
    <div class="md small" id="ans-${j.id}"></div>`;
  el.querySelector("#ask-" + j.id).onclick = () => askQuestion(j.id, null);
  el.querySelector("#stop-" + j.id).onclick = () =>
    fetch(`/api/jobs/${j.id}/stop`, { method: "POST" });
  el.querySelector("#rm-" + j.id).onclick = async () => {
    const r = await fetch(`/api/jobs/${j.id}`, { method: "DELETE" });
    if (r.ok) el.remove(); else alert(t("web.stop_running_first"));
  };
  return el;
}

function renderJob(j) {
  let el = $("job-" + j.id);
  if (!el) { el = jobCard(j); $("jobs").prepend(el); }
  const st = $("st-" + j.id);
  st.textContent = { starting: t("web.status_starting"),
    running: j.is_live ? t("web.status_live") : t("web.status_summarizing"),
    done: t("web.status_done"), error: t("web.status_error") }[j.status] || j.status;
  st.className = "badge " + (j.status === "error" ? "b-amber"
    : j.status === "done" ? "b-green" : j.is_live ? "b-red" : "b-blue");
  el.querySelector("b.ellip").textContent = j.title || j.url;
  const pb = el.querySelector(".play-btn");
  pb.style.display = j.embed_url ? "" : "none";
  pb.onclick = () => playInline(j.embed_url);
  if (j.current_topic) $("topic-" + j.id).textContent = j.current_topic;
  $("n-" + j.id).textContent = j.timeline.length;
  $("sm-" + j.id).textContent = j.smart_hits;
  $("roll-" + j.id).textContent = j.rolling_summary || t("web.none_yet");
  const tl = $("tl-" + j.id);
  tl.innerHTML = j.timeline.slice().reverse().map(t2 => `
    <div><div>${t2.watch_url
      ? `<a href="${esc(t2.watch_url)}" target="_blank"><b class="small">${esc(t2.elapsed)}</b></a>`
      : `<b class="small">${esc(t2.elapsed)}</b>`} · ${esc(t2.topic)}
      ${t2.smart ? `<span class="badge b-blue small">🔍 ${esc(t("web.smart_badge"))}</span>` : ""}</div>
      ${(t2.points || []).map(p => `<p class="pt">- ${esc(p)}</p>`).join("")}
      ${(t2.images || []).map(img => `<a href="/${esc(img)}" target="_blank"><img src="/${esc(img)}" style="height:64px;border-radius:6px;margin:4px 6px 0 0"></a>`).join("")}</div>`
  ).join("");
  if (j.status === "error" && j.error)
    $("final-" + j.id).innerHTML =
      `<div class="alert">${esc(t("web.error", { err: j.error }))}</div>`;
  if (j.final_summary)
    $("final-" + j.id).innerHTML =
      `<h2>${esc(t("web.final_summary"))}</h2><div class="md">${marked.parse(j.final_summary)}</div>`;
}

async function loadJobs() {
  const jobs = await (await fetch("/api/jobs")).json();
  jobs.forEach(j => { renderJob(j); subscribe(j.id); });
}

let kwCount = {};
function subscribe(id) {
  if (subscribed.has(id)) return;
  subscribed.add(id);
  const es = new EventSource(`/api/jobs/${id}/events`);
  es.onmessage = async (m) => {
    const e = JSON.parse(m.data);
    if (e.type === "chunk") {
      if (e.topic_changed) notify(t("web.notify_topic_changed"), e.topic);
      if (e.keyword_hits && e.keyword_hits.length) {
        kwCount[id] = (kwCount[id] || 0) + e.keyword_hits.length;
        $("kw-" + id).textContent = kwCount[id];
        const box = $("alerts-" + id);
        box.insertAdjacentHTML("afterbegin",
          `<div class="alert">⚡ ${esc(t("web.keyword_alert",
            { kw: e.keyword_hits.join("、"), topic: e.topic }))}</div>`);
        notify(t("web.notify_keyword", { kw: e.keyword_hits.join("、") }), e.topic);
      }
    }
    if (e.type === "final") notify(t("web.notify_done"), t("web.notify_done_body"));
    const jobs = await (await fetch("/api/jobs")).json();
    const j = jobs.find(x => x.id === id);
    if (j) renderJob(j);
    if (e.type === "final") loadHistory();
  };
  es.addEventListener("end", () => es.close());
  es.onerror = () => {};
}

// ---------- 內嵌播放器 ----------
window.playInline = (embedUrl) => {
  if (!embedUrl) return;
  const box = $("player-box");
  box.style.display = "block";
  box.innerHTML = `<div class="row" style="justify-content:flex-end;margin-bottom:4px">
    <button class="small" onclick="$('player-box').style.display='none';$('player-box').querySelector('iframe')?.remove()">✕ ${esc(t("web.close_player"))}</button></div>
    <iframe src="${esc(embedUrl)}" allow="autoplay; encrypted-media" allowfullscreen></iframe>`;
};

// ---------- 歷史紀錄 ----------
let histCache = [];
async function loadHistory() {
  histCache = await (await fetch("/api/history")).json();
  renderHistory();
}
function renderHistory() {
  const q = $("hist-search").value.trim();
  $("history").innerHTML = histCache
    .filter(h => !q || h.title.includes(q) || (h.channel || "").includes(q))
    .map(h => `
    <div class="hist-item" onclick="openHist('${esc(h.name)}', '${esc(h.watch_base || '')}', '${esc(h.seek_tpl || '')}')">
      <span class="badge ${h.is_live ? "b-red" : "b-blue"} small">${esc(h.is_live ? t("web.live_badge") : t("web.video_badge"))}</span>
      ${h.channel ? `<span class="badge b-green small">${esc(h.channel)}</span>` : ""}
      <span class="grow ellip">${esc(h.title)}</span>
      <span class="muted small">${new Date(h.mtime * 1000).toLocaleString(LOCALE, { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" })}</span>
      <button class="small" onclick="event.stopPropagation();delHist('${esc(h.name)}')">🗑</button>
    </div>`).join("") || `<p class="muted">${esc(t("web.no_history"))}</p>`;
}
$("hist-search").oninput = renderHistory;
$("hist-refresh").onclick = loadHistory;

window.openHist = async (name, watchBase, seekTpl) => {
  const { content } = await (await fetch("/api/history/" + encodeURIComponent(name))).json();
  let html = marked.parse(content);
  if (watchBase && seekTpl) {
    html = html.replace(/\[(\d+):(\d{2})(?::(\d{2}))?\]/g, (m, a, b, c) => {
      const secs = c ? (+a) * 3600 + (+b) * 60 + (+c) : (+a) * 60 + (+b);
      return `<a href="${watchBase}${seekTpl.replace("{seconds}", secs)}" target="_blank">${m}</a>`;
    });
  }
  const v = $("viewer");
  v.style.display = "block";
  v.innerHTML = `<div class="row" style="justify-content:flex-end">
    <button class="small" onclick="$('viewer').style.display='none'">✕ ${esc(t("web.close"))}</button></div>` + html
    + `<div class="row" style="margin-top:10px">
      <input type="text" id="q-hist" class="grow small" placeholder="${esc(t("web.ask_history_placeholder"))}">
      <button class="small" onclick="askQuestion(null, '${esc(name)}')">${esc(t("web.ask"))}</button>
    </div><div class="md small" id="ans-hist"></div>`;
  v.scrollIntoView({ behavior: "smooth" });
};
window.delHist = async (name) => {
  if (!confirm(t("web.confirm_delete"))) return;
  await fetch("/api/history/" + encodeURIComponent(name), { method: "DELETE" });
  loadHistory();
};

// ---------- 內容問答 ----------
async function askQuestion(jobId, histName) {
  const qEl = $(jobId ? "q-" + jobId : "q-hist");
  const ansEl = $(jobId ? "ans-" + jobId : "ans-hist");
  const q = qEl.value.trim();
  if (!q) return;
  ansEl.innerHTML = `<span class="spin"></span> ${esc(t("web.thinking"))}`;
  try {
    const r = await fetch("/api/ask", { method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question: q, job_id: jobId, history_name: histName }) });
    if (!r.ok) throw new Error((await r.json()).detail || r.status);
    const { answer } = await r.json();
    ansEl.innerHTML = `<div class="alert" style="background:var(--accent-bg);color:var(--accent)">
      ${marked.parse(answer)}</div>`;
  } catch (e) {
    ansEl.innerHTML = `<div class="alert">${esc(t("web.ask_failed", { err: e.message }))}</div>`;
  }
}
window.askQuestion = askQuestion;

// ---------- 頻道訂閱 ----------
async function loadSubs() {
  const subs = await (await fetch("/api/subscriptions")).json();
  $("subs").innerHTML = subs.map(s => `
    <div class="hist-item" style="cursor:default">
      <span class="badge ${s.live_now ? "b-red" : s.is_feed ? "b-green" : "b-blue"} small">
        ${esc(s.last_error ? t("web.check_failed") : s.is_feed ? t("web.podcast")
          : s.live_now === null ? t("web.checking") : s.live_now ? t("web.live_now") : t("web.not_live"))}</span>
      <span class="grow ellip">${esc(s.channel_url)}
        ${(s.keywords || []).length ? `<span class="muted small">⚡ ${esc(s.keywords.join("、"))}</span>` : ""}</span>
      <span class="muted small">${s.last_check
        ? esc(t("web.last_checked", { time: new Date(s.last_check * 1000).toLocaleTimeString(LOCALE, { hour: "2-digit", minute: "2-digit" }) })) : ""}</span>
      ${s.startable ? `<button class="small primary" onclick="goSub('${s.id}')">▶ ${esc(t("web.start_summary"))}</button>` : ""}
      <button class="small" onclick="toggleSub('${s.id}')">${esc(s.enabled ? t("web.pause") : t("web.resume"))}</button>
      <button class="small" onclick="delSub('${s.id}')">🗑</button>
    </div>`).join("") || `<p class="muted small">${esc(t("web.no_subs"))}</p>`;
}
$("sub-add").onclick = async () => {
  const u = $("sub-url").value.trim();
  if (!u) return;
  await fetch("/api/subscriptions", { method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ channel_url: u,
      keywords: $("sub-keywords").value.split(/[,，]/).map(s => s.trim()).filter(Boolean) }) });
  $("sub-url").value = ""; $("sub-keywords").value = "";
  loadSubs();
};
window.goSub = async (id) => {
  const r = await fetch(`/api/subscriptions/${id}/go`, { method: "POST" });
  if (r.ok) { loadJobs(); alert(t("web.started_watching")); }
  else alert(t("web.cannot_start", { err: (await r.json()).detail }));
};
window.toggleSub = async (id) => {
  await fetch(`/api/subscriptions/${id}/toggle`, { method: "POST" }); loadSubs();
};
window.delSub = async (id) => {
  await fetch(`/api/subscriptions/${id}`, { method: "DELETE" }); loadSubs();
};

// ---------- 跨影片知識庫問答 ----------
$("askall-btn").onclick = async () => {
  const q = $("q-all").value.trim();
  if (!q) return;
  $("ans-all").innerHTML = `<span class="spin"></span> ${esc(t("web.searching_history"))}`;
  try {
    const r = await fetch("/api/askall", { method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question: q }) });
    const { answer } = await r.json();
    $("ans-all").innerHTML = `<div class="alert" style="background:var(--accent-bg);color:var(--accent)">${marked.parse(answer)}</div>`;
  } catch (e) {
    $("ans-all").innerHTML = `<div class="alert">${esc(t("web.ask_failed", { err: e.message }))}</div>`;
  }
};

// ---------- 啟動:先取回文字目錄,再做第一次繪製 ----------
async function init() {
  try {
    const d = await (await fetch("/api/strings")).json();
    S = d.strings || {};
    if (d.lang) { document.documentElement.lang = d.lang; LOCALE = d.lang; }
  } catch {}
  applyStrings();
  refreshNotifBtn();
  refreshUsage();
  setInterval(refreshUsage, 30000);
  loadJobs(); loadHistory(); loadSubs();
  setInterval(() => { loadSubs(); loadJobs(); }, 60000);
}
init();

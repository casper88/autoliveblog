"use strict";
const $ = (id) => document.getElementById(id);
const esc = (s) => (s || "").replace(/[&<>"']/g,
  (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));

// ---------- 通知 ----------
$("notif-btn").onclick = () => Notification.requestPermission().then(refreshNotifBtn);
function refreshNotifBtn() {
  $("notif-btn").textContent =
    Notification.permission === "granted" ? "🔔 通知:開" : "🔔 通知";
}
refreshNotifBtn();
function notify(title, body) {
  if (Notification.permission === "granted")
    new Notification(title, { body: body || "" });
}

// ---------- 用量 ----------
async function refreshUsage() {
  try {
    const u = await (await fetch("/api/usage")).json();
    const g = u.gemini, o = u.openai;
    const parts = [`Gemini ${g.calls} 次 $0`];
    if (o.calls) parts.push(`OpenAI ${o.calls} 次 $${o.usd.toFixed(3)}`);
    const retries = u.retries_429 + u.retries_503;
    if (retries) parts.push(`重試 ${retries}`);
    $("usage").textContent = parts.join(" · ");
    $("usage").title =
      `Gemini:${(g.in_tokens / 1000).toFixed(1)}k 入 / ${(g.out_tokens / 1000).toFixed(1)}k 出 tokens` +
      `(免費層 $0;付費層等值 $${g.usd_equivalent.toFixed(3)})\n` +
      `OpenAI:${(o.in_tokens / 1000).toFixed(1)}k 入 / ${(o.out_tokens / 1000).toFixed(1)}k 出 tokens` +
      ` + 轉錄 ${(o.audio_seconds / 60).toFixed(1)} 分鐘 = $${o.usd.toFixed(4)}`;
  } catch {}
}
refreshUsage(); setInterval(refreshUsage, 30000);

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
    $("pv-badge").textContent = inspected.is_live ? "直播中" : "影片";
    $("pv-dur").textContent = inspected.duration
      ? `${Math.floor(inspected.duration / 60)} 分鐘` : "";
  } catch (e) { alert("無法讀取:" + e.message); }
  $("inspect-btn").textContent = "檢查";
};

// ---------- 開始監看 ----------
$("start-btn").onclick = async () => {
  const url = $("url").value.trim();
  if (!url) return alert("請先貼上網址");
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
      <button class="small play-btn">▶ 播放</button>
      <button class="small" id="stop-${j.id}">停止並總結</button>
      <button class="small" id="rm-${j.id}">移除</button>
    </div>
    <div class="muted small">目前話題</div>
    <div class="topic" id="topic-${j.id}">(等待第一段…)</div>
    <div class="stats">
      <div class="stat"><span class="muted small">已總結段數</span><b id="n-${j.id}">0</b></div>
      <div class="stat"><span class="muted small">智慧補看</span><b id="sm-${j.id}">0</b></div>
      <div class="stat"><span class="muted small">關鍵字命中</span><b id="kw-${j.id}">0</b></div>
    </div>
    <div id="alerts-${j.id}"></div>
    <details><summary>滾動摘要</summary>
      <p class="small" id="roll-${j.id}" style="color:var(--ink2)"></p></details>
    <div class="muted small" style="margin-top:8px">時間軸(最新在上)</div>
    <div class="tl" id="tl-${j.id}"></div>
    <div id="final-${j.id}"></div>
    <div class="row" style="margin-top:10px">
      <input type="text" id="q-${j.id}" class="grow small" placeholder="問剛剛的內容,例:那檔股票代碼多少?">
      <button class="small" id="ask-${j.id}">問</button>
    </div>
    <div class="md small" id="ans-${j.id}"></div>`;
  el.querySelector("#ask-" + j.id).onclick = () => askQuestion(j.id, null);
  el.querySelector("#stop-" + j.id).onclick = () =>
    fetch(`/api/jobs/${j.id}/stop`, { method: "POST" });
  el.querySelector("#rm-" + j.id).onclick = async () => {
    const r = await fetch(`/api/jobs/${j.id}`, { method: "DELETE" });
    if (r.ok) el.remove(); else alert("執行中的任務要先停止");
  };
  return el;
}

function renderJob(j) {
  let el = $("job-" + j.id);
  if (!el) { el = jobCard(j); $("jobs").prepend(el); }
  const st = $("st-" + j.id);
  st.textContent = { starting: "解析中…", running: j.is_live ? "直播監看中" : "總結中",
    done: "已完成", error: "錯誤" }[j.status] || j.status;
  st.className = "badge " + (j.status === "error" ? "b-amber"
    : j.status === "done" ? "b-green" : j.is_live ? "b-red" : "b-blue");
  el.querySelector("b.ellip").textContent = j.title || j.url;
  const pb = el.querySelector(".play-btn");
  pb.style.display = j.video_id ? "" : "none";
  pb.onclick = () => playInline(j.video_id);
  if (j.current_topic) $("topic-" + j.id).textContent = j.current_topic;
  $("n-" + j.id).textContent = j.timeline.length;
  $("sm-" + j.id).textContent = j.smart_hits;
  $("roll-" + j.id).textContent = j.rolling_summary || "(尚無)";
  const tl = $("tl-" + j.id);
  tl.innerHTML = j.timeline.slice().reverse().map(t => `
    <div><div>${j.video_id && t.seconds != null
      ? `<a href="https://www.youtube.com/watch?v=${esc(j.video_id)}&t=${Math.floor(t.seconds)}s" target="_blank"><b class="small">${esc(t.elapsed)}</b></a>`
      : `<b class="small">${esc(t.elapsed)}</b>`} · ${esc(t.topic)}
      ${t.smart ? '<span class="badge b-blue small">🔍 補看</span>' : ""}</div>
      ${(t.points || []).map(p => `<p class="pt">- ${esc(p)}</p>`).join("")}
      ${(t.images || []).map(img => `<a href="/${esc(img)}" target="_blank"><img src="/${esc(img)}" style="height:64px;border-radius:6px;margin:4px 6px 0 0"></a>`).join("")}</div>`
  ).join("");
  if (j.status === "error" && j.error)
    $("final-" + j.id).innerHTML = `<div class="alert">錯誤:${esc(j.error)}</div>`;
  if (j.final_summary)
    $("final-" + j.id).innerHTML =
      `<h2>最終總結</h2><div class="md">${marked.parse(j.final_summary)}</div>`;
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
      if (e.topic_changed) notify("話題轉換", e.topic);
      if (e.keyword_hits && e.keyword_hits.length) {
        kwCount[id] = (kwCount[id] || 0) + e.keyword_hits.length;
        $("kw-" + id).textContent = kwCount[id];
        const box = $("alerts-" + id);
        box.insertAdjacentHTML("afterbegin",
          `<div class="alert">⚡ 關鍵字「${esc(e.keyword_hits.join("、"))}」:${esc(e.topic)}</div>`);
        notify("關鍵字:" + e.keyword_hits.join("、"), e.topic);
      }
    }
    if (e.type === "final") notify("總結完成", "最終總結已產出");
    const jobs = await (await fetch("/api/jobs")).json();
    const j = jobs.find(x => x.id === id);
    if (j) renderJob(j);
    if (e.type === "final") loadHistory();
  };
  es.addEventListener("end", () => es.close());
  es.onerror = () => {};
}

// ---------- 內嵌播放器 ----------
window.playInline = (vid) => {
  if (!vid) return;
  const box = $("player-box");
  box.style.display = "block";
  box.innerHTML = `<div class="row" style="justify-content:flex-end;margin-bottom:4px">
    <button class="small" onclick="$('player-box').style.display='none';$('player-box').querySelector('iframe')?.remove()">✕ 關閉播放器</button></div>
    <iframe src="https://www.youtube.com/embed/${vid}?autoplay=1" allow="autoplay; encrypted-media" allowfullscreen></iframe>`;
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
    <div class="hist-item" onclick="openHist('${esc(h.name)}', '${esc(h.url)}')">
      <span class="badge ${h.is_live ? "b-red" : "b-blue"} small">${h.is_live ? "直播" : "影片"}</span>
      ${h.channel ? `<span class="badge b-green small">${esc(h.channel)}</span>` : ""}
      <span class="grow ellip">${esc(h.title)}</span>
      <span class="muted small">${new Date(h.mtime * 1000).toLocaleString("zh-TW", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" })}</span>
      <button class="small" onclick="event.stopPropagation();delHist('${esc(h.name)}')">🗑</button>
    </div>`).join("") || '<p class="muted">還沒有紀錄</p>';
}
$("hist-search").oninput = renderHistory;
$("hist-refresh").onclick = loadHistory;

window.openHist = async (name, url) => {
  const { content } = await (await fetch("/api/history/" + encodeURIComponent(name))).json();
  let html = marked.parse(content);
  const vid = (url.match(/[?&]v=([\w-]{6,})/) || [])[1];
  if (vid) {
    html = html.replace(/\[(\d+):(\d{2})(?::(\d{2}))?\]/g, (m, a, b, c) => {
      const secs = c ? (+a) * 3600 + (+b) * 60 + (+c) : (+a) * 60 + (+b);
      return `<a href="https://www.youtube.com/watch?v=${vid}&t=${secs}s" target="_blank">${m}</a>`;
    });
  }
  const v = $("viewer");
  v.style.display = "block";
  v.innerHTML = `<div class="row" style="justify-content:flex-end">
    <button class="small" onclick="$('viewer').style.display='none'">✕ 關閉</button></div>` + html
    + `<div class="row" style="margin-top:10px">
      <input type="text" id="q-hist" class="grow small" placeholder="針對這份總結提問">
      <button class="small" onclick="askQuestion(null, '${esc(name)}')">問</button>
    </div><div class="md small" id="ans-hist"></div>`;
  v.scrollIntoView({ behavior: "smooth" });
};
window.delHist = async (name) => {
  if (!confirm("刪除這份總結?")) return;
  await fetch("/api/history/" + encodeURIComponent(name), { method: "DELETE" });
  loadHistory();
};

// ---------- 內容問答 ----------
async function askQuestion(jobId, histName) {
  const qEl = $(jobId ? "q-" + jobId : "q-hist");
  const ansEl = $(jobId ? "ans-" + jobId : "ans-hist");
  const q = qEl.value.trim();
  if (!q) return;
  ansEl.innerHTML = '<span class="spin"></span> 思考中…';
  try {
    const r = await fetch("/api/ask", { method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question: q, job_id: jobId, history_name: histName }) });
    if (!r.ok) throw new Error((await r.json()).detail || r.status);
    const { answer } = await r.json();
    ansEl.innerHTML = `<div class="alert" style="background:var(--accent-bg);color:var(--accent)">
      ${marked.parse(answer)}</div>`;
  } catch (e) { ansEl.innerHTML = `<div class="alert">問答失敗:${esc(e.message)}</div>`; }
}
window.askQuestion = askQuestion;

// ---------- 頻道訂閱 ----------
async function loadSubs() {
  const subs = await (await fetch("/api/subscriptions")).json();
  $("subs").innerHTML = subs.map(s => `
    <div class="hist-item" style="cursor:default">
      <span class="badge ${s.live_now ? "b-red" : "b-blue"} small">
        ${s.live_now === null ? "檢查中" : s.live_now ? "直播中" : "未開播"}</span>
      <span class="grow ellip">${esc(s.channel_url)}
        ${(s.keywords || []).length ? `<span class="muted small">⚡ ${esc(s.keywords.join("、"))}</span>` : ""}</span>
      <span class="muted small">${s.last_check
        ? "上次檢查 " + new Date(s.last_check * 1000).toLocaleTimeString("zh-TW", { hour: "2-digit", minute: "2-digit" }) : ""}</span>
      ${s.live_now ? `<button class="small primary" onclick="goSub('${s.id}')">▶ 開始總結</button>` : ""}
      <button class="small" onclick="toggleSub('${s.id}')">${s.enabled ? "暫停" : "啟用"}</button>
      <button class="small" onclick="delSub('${s.id}')">🗑</button>
    </div>`).join("") || '<p class="muted small">尚無訂閱。新增後每 3 分鐘檢查一次,開播就自動開始總結。</p>';
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
  if (r.ok) { loadJobs(); alert("已開始補課+即時總結"); }
  else alert("無法開始:" + (await r.json()).detail);
};
window.toggleSub = async (id) => {
  await fetch(`/api/subscriptions/${id}/toggle`, { method: "POST" }); loadSubs();
};
window.delSub = async (id) => {
  await fetch(`/api/subscriptions/${id}`, { method: "DELETE" }); loadSubs();
};
setInterval(() => { loadSubs(); loadJobs(); }, 60000);

// ---------- 跨影片知識庫問答 ----------
$("askall-btn").onclick = async () => {
  const q = $("q-all").value.trim();
  if (!q) return;
  $("ans-all").innerHTML = '<span class="spin"></span> 翻閱歷史總結中…';
  try {
    const r = await fetch("/api/askall", { method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question: q }) });
    const { answer } = await r.json();
    $("ans-all").innerHTML = `<div class="alert" style="background:var(--accent-bg);color:var(--accent)">${marked.parse(answer)}</div>`;
  } catch (e) { $("ans-all").innerHTML = `<div class="alert">失敗:${esc(e.message)}</div>`; }
};

loadJobs(); loadHistory(); loadSubs();

"use strict";

/* 面板渲染。
 *
 * 所有后端字符串一律走 textContent 写入（不用 innerHTML 拼接），避免 XSS。
 *
 * 签名元素「节奏刻度」：进度条上的一道细刻度，标出**时间窗口已经过去多少**。
 * 填充越过刻度 = 消耗快于额度回补速度，该省着点；落在刻度左边 = 还有余量。
 * 它把一个静态的量表变成了可以据此决策的东西——这正是这个小窗存在的意义。
 */

const WEEKDAYS = ["周日", "周一", "周二", "周三", "周四", "周五", "周六"];
const MINUTE = 60000;

/** 从窗口 id 推出该窗口的时长（毫秒）；推不出来就返回 null（则不画刻度）。 */
function windowDurationMs(id) {
  if (id === "week" || id === "week_opus") return 7 * 24 * 60 * MINUTE;
  if (id === "5h") return 5 * 60 * MINUTE;
  let m = /^(\d+)h$/.exec(id);
  if (m) return Number(m[1]) * 60 * MINUTE;
  m = /^(\d+)m$/.exec(id);
  if (m) return Number(m[1]) * MINUTE;
  return null;
}

/** 时间进度：窗口已经过去的百分比。缺重置时间或时长未知时返回 null。 */
function pacePct(w) {
  if (!w.resets_at) return null;
  const duration = windowDurationMs(w.id);
  if (!duration) return null;
  const remain = new Date(w.resets_at).getTime() - Date.now();
  if (!Number.isFinite(remain)) return null;
  const elapsed = duration - remain;
  if (elapsed <= 0 || elapsed >= duration) return null;
  return (elapsed / duration) * 100;
}

function fmtPct(p) {
  return (Math.round(p * 10) / 10).toFixed(1) + "%";
}

function barClass(p) {
  if (p >= 90) return "danger";
  if (p >= 70) return "warn";
  return "";
}

function fmtReset(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  const hh = String(d.getHours()).padStart(2, "0");
  const mm = String(d.getMinutes()).padStart(2, "0");
  const when = `${WEEKDAYS[d.getDay()]} ${hh}:${mm}`;
  const ms = d - new Date();
  if (ms <= 0) return `${when} 重置（已到点）`;
  const mins = Math.floor(ms / MINUTE);
  const days = Math.floor(mins / 1440);
  const hours = Math.floor((mins % 1440) / 60);
  const restMins = mins % 60;
  let remain;
  if (days > 0) remain = `剩 ${days} 天 ${hours} 小时`;
  else if (hours > 0) remain = `剩 ${hours} 小时 ${restMins} 分`;
  else remain = `剩 ${restMins} 分钟`;
  return `${when} 重置 · ${remain}`;
}

function fmtTime(iso) {
  const d = new Date(iso);
  const hh = String(d.getHours()).padStart(2, "0");
  const mm = String(d.getMinutes()).padStart(2, "0");
  return `${hh}:${mm}`;
}

function fmtStale(iso) {
  const mins = Math.floor((Date.now() - new Date(iso).getTime()) / MINUTE);
  if (mins < 1) return "数据来自 刚刚";
  if (mins < 60) return `数据来自 ${mins} 分钟前`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `数据来自 ${hours} 小时前`;
  return `数据来自 ${Math.floor(hours / 24)} 天前`;
}

function showError(msg) {
  const banner = document.getElementById("error-banner");
  banner.textContent = msg;
  banner.hidden = false;
}

function clearError() {
  document.getElementById("error-banner").hidden = true;
}

function isValidSummary(data) {
  return data && typeof data === "object" && Array.isArray(data.providers);
}

function renderWindow(w) {
  const row = document.createElement("div");
  row.className = "window";

  const labelRow = document.createElement("div");
  labelRow.className = "window-label-row";
  const label = document.createElement("span");
  label.className = "window-label";
  label.textContent = w.label;
  const pct = document.createElement("span");
  pct.className = "window-pct";
  pct.textContent = fmtPct(w.used_pct);
  labelRow.append(label, pct);
  row.appendChild(labelRow);

  const used = Math.min(100, Math.max(0, w.used_pct));
  const bar = document.createElement("div");
  bar.className = "bar";
  const fill = document.createElement("div");
  fill.className = ("bar-fill " + barClass(w.used_pct)).trim();
  fill.style.width = used + "%";
  bar.appendChild(fill);

  const pace = pacePct(w);
  if (pace !== null && pace > 1 && pace < 99) {
    const tick = document.createElement("div");
    tick.className = "pace";
    tick.style.left = pace + "%";
    tick.title = `时间已过去 ${Math.round(pace)}%——用量越过这道刻度，说明消耗快于额度回补`;
    bar.appendChild(tick);
  }
  row.appendChild(bar);

  // 底行：有重置时间就显示倒计时，否则显示绝对量（如按金额计费的 credit 池）
  const resetText = fmtReset(w.resets_at);
  const parts = [resetText, w.note].filter(Boolean);
  if (parts.length > 0) {
    const foot = document.createElement("div");
    foot.className = "window-reset";
    foot.textContent = parts.join(" · ");
    // 只在明显偏快时才点破，避免每行都挂个提示
    if (pace !== null && used - pace > 5 && used >= 25) {
      const ahead = document.createElement("span");
      ahead.className = "ahead";
      ahead.textContent = " · 快于节奏";
      foot.appendChild(ahead);
    }
    row.appendChild(foot);
  }
  return row;
}

function renderCard(p) {
  const card = document.createElement("section");
  card.className = "card";
  // 品牌色靠这个属性挂到 CSS 上（.card[data-provider="..."] 覆写 --fill）
  if (p.id) card.dataset.provider = p.id;

  const head = document.createElement("div");
  head.className = "card-head";
  const name = document.createElement("span");
  name.className = "card-name";
  name.textContent = p.name;
  head.appendChild(name);
  if (p.plan) {
    const plan = document.createElement("span");
    plan.className = "card-plan";
    plan.textContent = p.plan;
    head.appendChild(plan);
  }
  if (p.status === "stale") {
    const stale = document.createElement("span");
    stale.className = "card-stale";
    stale.textContent = fmtStale(p.fetched_at);
    head.appendChild(stale);
  }
  card.appendChild(head);

  if (p.status === "error" || p.status === "auth_expired") {
    const err = document.createElement("div");
    err.className = "card-error";
    err.textContent = p.error || "未知错误";
    card.appendChild(err);
    return card;
  }

  for (const w of p.windows) {
    card.appendChild(renderWindow(w));
  }
  return card;
}

function render(data) {
  const cards = document.getElementById("cards");
  cards.replaceChildren();
  if (data.providers.length === 0) {
    const empty = document.createElement("p");
    empty.className = "empty";
    empty.textContent = "还没有数据，稍等一轮或点刷新";
    cards.appendChild(empty);
  } else {
    for (const p of data.providers) {
      cards.appendChild(renderCard(p));
    }
  }
  const updated = document.getElementById("updated-at");
  updated.textContent = data.updated_at
    ? "更新于 " + fmtTime(data.updated_at)
    : "暂无数据";
}

/* 自适应窗口高度：把窗口调到刚好装下内容，不出滚动条、也不留多余空白。
 *
 * 只在 --app 模式的独立小窗里有意义，且浏览器未必允许脚本改顶层窗口尺寸；
 * 不允许就静默跳过，退回快捷方式里给的初始高度。
 *
 * 量高度必须量 body 而不是 documentElement：后者会被撑满视口，窗口比内容高时
 * 它的 scrollHeight 恒等于视口高度，于是永远看不出"内容其实更矮"——窗口只能
 * 变高不能变矮，底部就留下一截空白。body 默认高度自适应内容，能如实反映。 */
let fitAttempts = 0;

function fitWindowToContent() {
  if (fitAttempts >= 3) return; // 防止和浏览器的尺寸约束来回拉锯
  try {
    const chrome = window.outerHeight - window.innerHeight; // 标题栏 + 边框
    const needed = document.body.offsetHeight; // 含 padding，且不受滚动位置影响
    const target = Math.min(1200, Math.max(200, needed + chrome));
    if (Math.abs(target - window.outerHeight) <= 4) return; // 已经合适
    fitAttempts += 1;
    window.resizeTo(window.outerWidth, target);
  } catch (e) {
    fitAttempts = 3; // 不被允许，别再试了
  }
}

let lastData = null;

/** 只重画，不重新取数——用于让倒计时和节奏刻度随时间走。 */
function repaint() {
  if (lastData) render(lastData);
}

async function load(url, options, failMsg) {
  try {
    const resp = await fetch(url, options);
    if (!resp.ok) throw new Error("HTTP " + resp.status);
    const data = await resp.json();
    if (!isValidSummary(data)) throw new Error("bad payload");
    lastData = data;
    render(data);
    clearError();
    fitWindowToContent(); // 卡片数量/窗口条数变了，高度可能要跟着变
  } catch (e) {
    // 保留旧卡片，只在顶部提示
    showError(failMsg);
  }
}

const loadSummary = () => load("/api/summary", undefined, "获取数据失败，下面是上一次的结果");

async function refreshAll() {
  const btn = document.getElementById("refresh-btn");
  btn.disabled = true;
  try {
    await load("/api/refresh?provider=all", { method: "POST" },
      "刷新失败，下面是上一次的结果");
  } finally {
    btn.disabled = false;
  }
}

document.getElementById("refresh-btn").addEventListener("click", refreshAll);
loadSummary();
setInterval(loadSummary, 60000);
setInterval(repaint, 30000);

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

function fmtAgo(iso) {
  const mins = Math.floor((Date.now() - new Date(iso).getTime()) / MINUTE);
  if (mins < 1) return "刚刚";
  if (mins < 60) return `${mins} 分钟前`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours} 小时前`;
  return `${Math.floor(hours / 24)} 天前`;
}

function fmtStale(iso) {
  return `数据来自 ${fmtAgo(iso)}`;
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
  // 错误卡片没有数据可言，只说清最后一次尝试是什么时候，别写成"数据来自"
  if (p.status === "stale") {
    const stale = document.createElement("span");
    stale.className = "card-stale";
    stale.textContent = fmtStale(p.fetched_at);
    head.appendChild(stale);
  } else if (p.status === "error" || p.status === "auth_expired") {
    const attempt = document.createElement("span");
    attempt.className = "card-stale";
    attempt.textContent = `最后尝试 ${fmtAgo(p.fetched_at)}`;
    head.appendChild(attempt);
  }
  card.appendChild(head);

  if (p.status === "error" || p.status === "auth_expired") {
    const err = document.createElement("div");
    err.className = "card-error";
    err.textContent = p.error || "未知错误";
    card.appendChild(err);
    return card;
  }

  // stale 也可能带原因（如 Codex 走了本地快照）。数据照常渲染，只是多说一句为什么旧。
  if (p.status === "stale" && p.error) {
    const why = document.createElement("div");
    why.className = "card-error";
    why.textContent = p.error;
    card.appendChild(why);
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
  // 设置视图比卡片短得多。开着设置时后台那轮 60 秒刷新会把窗口缩掉，
  // 切回卡片就再也长不回来（次数用完了）。开着设置就不量。
  const panel = document.getElementById("settings");
  if (panel && !panel.hidden) return;
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

/* 会改状态的请求都要带这个头。服务端拿它当主防线：跨域请求带非 safelisted 自定义头
 * 必然触发预检，而服务端不返回任何 CORS 头，预检必失败——恶意网页发不出这种请求。 */
const MUTATE_HEADERS = { "X-Requested-By": "ai-usage-panel" };

async function refreshAll() {
  const btn = document.getElementById("refresh-btn");
  btn.disabled = true;
  try {
    await load("/api/refresh?provider=all",
      { method: "POST", headers: MUTATE_HEADERS },
      "刷新失败，下面是上一次的结果");
  } finally {
    btn.disabled = false;
  }
}

/* ---- 设置：跟随编辑器启动 ---- */

function renderHook(s) {
  document.getElementById("hook-switch").checked = s.enabled;
  const note = document.getElementById("hook-note");
  if (!s.hook_installed) {
    note.textContent = "未检测到编辑器钩子，开关暂时不起作用。装法见 README。";
    note.className = "setting-note warn";
  } else {
    note.textContent = s.enabled
      ? "打开 VSCode 时自动开出面板。"
      : "已关闭，打开 VSCode 不会自动开面板。";
    note.className = "setting-note";
  }
}

async function loadHook() {
  try {
    const resp = await fetch("/api/vscode-hook");
    if (!resp.ok) throw new Error("HTTP " + resp.status);
    renderHook(await resp.json());
  } catch (e) {
    const note = document.getElementById("hook-note");
    note.textContent = "读不到开关状态。";
    note.className = "setting-note warn";
  }
}

async function setHook(enabled) {
  const box = document.getElementById("hook-switch");
  box.disabled = true;
  try {
    const resp = await fetch("/api/vscode-hook", {
      method: "PUT",
      headers: { ...MUTATE_HEADERS, "Content-Type": "application/json" },
      body: JSON.stringify({ enabled }),
    });
    if (!resp.ok) throw new Error("HTTP " + resp.status);
    renderHook(await resp.json());
  } catch (e) {
    box.checked = !enabled; // 没改成就退回原状，别让界面撒谎
    const note = document.getElementById("hook-note");
    note.textContent = "改不动这个开关，看看 daemon 日志。";
    note.className = "setting-note warn";
  } finally {
    box.disabled = false;
  }
}

/* 主功能先接线、先取数，设置区放最后：万一页面是缓存里的旧版本、缺了设置区的元素，
 * 也只是没有设置区可用，不该把整个面板一起拖垮。
 * （之前就这么白过一次屏：设置区接线抛异常，loadSummary 根本没跑到。） */
document.getElementById("refresh-btn").addEventListener("click", refreshAll);
loadSummary();
setInterval(loadSummary, 60000);
setInterval(repaint, 30000);

const GEAR_SVG =
  '<svg viewBox="0 0 24 24" width="17" height="17" aria-hidden="true" fill="none" ' +
  'stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' +
  '<circle cx="12" cy="12" r="3"></circle>' +
  '<path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 ' +
  '1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09A1.65 1.65 0 0 0 9 ' +
  '19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 ' +
  '.33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 ' +
  '0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 ' +
  '0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 ' +
  '2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 ' +
  '0 4h-.09a1.65 1.65 0 0 0-1.51 1z"></path></svg>';

/* 齿轮和设置视图都由脚本生成，不依赖 index.html 里有没有它们。
 *
 * 页面本身会被浏览器缓存，而 /static/app.js 带 no-cache 永远是新的。把界面元素写死在
 * HTML 里，就会出现「新脚本 + 旧 DOM」——轻则功能静默消失（用户以为没做），
 * 重则接线时抛异常把整个面板带崩。两种都真踩过。
 * 让永远最新的那一方负责建元素，这个版本错配就不存在了。 */
function buildSettingsUI() {
  const header = document.querySelector("header");
  const cards = document.getElementById("cards");
  if (!header || !cards) return null;

  let btn = document.getElementById("settings-btn");
  if (!btn) {
    btn = document.createElement("button");
    btn.id = "settings-btn";
    btn.type = "button";
    btn.className = "gear";
    btn.title = "设置";
    btn.setAttribute("aria-label", "设置");
    btn.setAttribute("aria-expanded", "false");
    btn.innerHTML = GEAR_SVG; // 固定常量，不含任何外部数据
    header.prepend(btn);
  }

  let panel = document.getElementById("settings");
  if (!panel) {
    panel = document.createElement("section");
    panel.id = "settings";
    panel.className = "settings";
    panel.hidden = true;

    const row = document.createElement("label");
    row.className = "setting-row";
    const box = document.createElement("input");
    box.type = "checkbox";
    box.id = "hook-switch";
    const label = document.createElement("span");
    label.className = "setting-label";
    label.textContent = "跟随 VSCode 启动";
    row.append(box, label);

    const note = document.createElement("p");
    note.id = "hook-note";
    note.className = "setting-note";

    panel.append(row, note);
    cards.after(panel);
  }
  return { btn, panel, cards };
}

function wireSettings() {
  const built = buildSettingsUI();
  if (!built) return;
  const { btn, panel, cards } = built;
  const box = document.getElementById("hook-switch");
  const note = document.getElementById("hook-note");
  if (!box || !note) return;

  btn.addEventListener("click", () => {
    const open = panel.hidden;
    panel.hidden = !open;
    cards.hidden = open; // 互斥：设置开着就不显示卡片
    btn.setAttribute("aria-expanded", String(open));
    btn.classList.toggle("active", open);
    if (open) loadHook();
    // 故意不 resize：设置视图比卡片短，缩窗之后切回来未必长得回去
  });

  box.addEventListener("change", (e) => setHook(e.currentTarget.checked));
}

wireSettings();

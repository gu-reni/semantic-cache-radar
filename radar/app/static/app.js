/* 技术雷达页面的数据渲染。
 *
 * 这里的标题、摘要、链接全部来自第三方站点（Hacker News / GitHub / V2EX），
 * 属于不可信输入，所以两条纪律：
 *   1. 一律用 textContent 写入，绝不用 innerHTML 拼字符串 —— 否则一个带 <script>
 *      的标题就能在本站执行脚本。
 *   2. 链接只允许 http/https，其它协议（javascript:、data: 等）直接丢弃，
 *      不渲染成可点的东西。
 * 证据等级与「另有 N 条疑似同一事件」同样只做事实呈现，不做「已合并」的断言。
 */

const SOURCE_LABELS = {
  "hackernews": "Hacker News",
  "github-trending": "GitHub",
  "v2ex": "V2EX",
};

// 证据等级 -> CSS 类名。等级文本本身来自后端固定四档，但 CSS 类走这张白名单，
// 不直接拼接等级文本，免得哪天等级命名变了带出意外的类名。
const LEVEL_CLASS = {
  "证据充分": "level-sufficient",
  "信号在积累": "level-accumulating",
  "刚出现": "level-emerging",
  "有红旗": "level-redflag",
};

// 源站量化指标的中文标签与展示顺序。只展示数字型指标，author/node/language
// 这类身份信息不上页面（它们不是「证据强度」）。
const METRIC_LABELS = {
  points: "点",
  comments: "评论",
  replies: "回复",
  stars: "星",
  stars_today: "今日新增",
};
const METRIC_ORDER = ["points", "comments", "replies", "stars", "stars_today"];

const entriesEl = document.getElementById("entries");
const statusEl = document.getElementById("status");
const updatedEl = document.getElementById("updated");

let state = { source: "", level: "", items: [] };

function safeUrl(value) {
  try {
    const url = new URL(value, window.location.origin);
    return url.protocol === "http:" || url.protocol === "https:" ? url.href : null;
  } catch {
    return null;
  }
}

function formatTime(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  const pad = (n) => String(n).padStart(2, "0");
  const now = new Date();
  const sameDay =
    date.getFullYear() === now.getFullYear() &&
    date.getMonth() === now.getMonth() &&
    date.getDate() === now.getDate();
  const clock = `${pad(date.getHours())}:${pad(date.getMinutes())}`;
  return sameDay ? clock : `${date.getMonth() + 1}月${date.getDate()}日 ${clock}`;
}

function metricParts(metrics) {
  if (!metrics || typeof metrics !== "object") return [];
  const parts = [];
  for (const key of METRIC_ORDER) {
    const value = metrics[key];
    if (typeof value === "number" && Number.isFinite(value)) {
      parts.push(`${METRIC_LABELS[key]} ${value}`);
    }
  }
  return parts;
}

function renderItem(item) {
  const li = document.createElement("li");
  li.className = "entry";

  const meta = document.createElement("div");
  meta.className = "meta";
  const sourceEl = document.createElement("span");
  sourceEl.className = "source";
  sourceEl.textContent = SOURCE_LABELS[item.source] || item.source;
  meta.append(sourceEl);
  const time = formatTime(item.published_at || item.created_at);
  if (time) {
    const timeEl = document.createElement("time");
    timeEl.textContent = time;
    meta.append(timeEl);
  }
  if (item.evidence_level) {
    const badge = document.createElement("span");
    badge.className = "evidence-level " + (LEVEL_CLASS[item.evidence_level] || "");
    badge.textContent = item.evidence_level;
    meta.append(badge);
  }

  const body = document.createElement("div");
  const h2 = document.createElement("h2");
  const href = safeUrl(item.url);
  if (href) {
    const a = document.createElement("a");
    a.href = href;
    a.rel = "noopener noreferrer";
    a.target = "_blank";
    a.textContent = item.title || "(无标题)";
    h2.append(a);
  } else {
    // 拿不到可信链接时就只显示文字，不给一个点了会出问题的东西。
    h2.textContent = item.title || "(无标题)";
  }
  body.append(h2);

  const metrics = item.metrics || {};
  const metricText = metricParts(metrics).join(" · ");
  if (metricText) {
    const metricsEl = document.createElement("p");
    metricsEl.className = "metrics";
    metricsEl.textContent = metricText;
    body.append(metricsEl);
  }

  if (item.summary) {
    const p = document.createElement("p");
    p.className = "summary";
    p.textContent = item.summary;
    body.append(p);
  }

  if (Array.isArray(item.tags) && item.tags.length) {
    const ul = document.createElement("ul");
    ul.className = "tags";
    for (const tag of item.tags) {
      const tagEl = document.createElement("li");
      tagEl.textContent = String(tag);
      ul.append(tagEl);
    }
    body.append(ul);
  }

  const peerCount = metrics.semantic_peer_count;
  if (typeof peerCount === "number" && peerCount > 0) {
    const note = document.createElement("p");
    note.className = "same-event";
    // 只陈述事实：「另有 N 条疑似同一事件」，不下「已合并 / 就是同一件事」的断言。
    note.textContent = `另有 ${peerCount} 条疑似同一事件`;
    body.append(note);
  }

  li.append(meta, body);
  return li;
}

function render() {
  const items = state.items.filter(
    (item) => !state.level || item.evidence_level === state.level,
  );
  entriesEl.replaceChildren(...items.map(renderItem));

  if (!items.length) {
    statusEl.hidden = false;
    statusEl.textContent = state.source || state.level ? "这个筛选条件下暂时没有条目。" : "暂时没有条目。";
  } else {
    statusEl.hidden = true;
  }

  const newest = state.items
    .map((item) => item.published_at || item.created_at)
    .filter(Boolean)
    .sort()
    .pop();
  updatedEl.textContent = newest ? `最后更新 ${formatTime(newest)}` : "";
}

async function load(source) {
  statusEl.hidden = false;
  statusEl.textContent = "正在载入…";
  try {
    const query = new URLSearchParams({ limit: "100" });
    if (source) query.set("source", source);
    const response = await fetch(`/radar/items?${query}`, { headers: { Accept: "application/json" } });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    state.items = await response.json();
  } catch (error) {
    state.items = [];
    statusEl.hidden = false;
    statusEl.textContent = "载入失败，请稍后刷新重试。";
    updatedEl.textContent = "";
    entriesEl.replaceChildren();
    return;
  }
  render();
}

document.querySelectorAll(".filter[data-source]").forEach((button) => {
  button.addEventListener("click", () => {
    document.querySelectorAll(".filter[data-source]").forEach((other) => {
      other.classList.toggle("is-active", other === button);
    });
    state.source = button.dataset.source || "";
    load(state.source);
  });
});

document.querySelectorAll(".filter[data-level]").forEach((button) => {
  button.addEventListener("click", () => {
    document.querySelectorAll(".filter[data-level]").forEach((other) => {
      other.classList.toggle("is-active", other === button);
    });
    state.level = button.dataset.level || "";
    render();
  });
});

load("");

/* 技术雷达页面的数据渲染。
 *
 * 这里的标题、摘要、链接全部来自第三方站点（Hacker News / GitHub / V2EX），
 * 属于不可信输入，所以两条纪律：
 *   1. 一律用 textContent 写入，绝不用 innerHTML 拼字符串 —— 否则一个带 <script>
 *      的标题就能在本站执行脚本。
 *   2. 链接只允许 http/https，其它协议（javascript:、data: 等）直接丢弃，
 *      不渲染成可点的东西。
 */

const SOURCE_LABELS = {
  "hackernews": "Hacker News",
  "github-trending": "GitHub",
  "v2ex": "V2EX",
};

const entriesEl = document.getElementById("entries");
const statusEl = document.getElementById("status");
const updatedEl = document.getElementById("updated");

let state = { source: "", items: [] };

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

  li.append(meta, body);
  return li;
}

function render() {
  const items = state.items;
  entriesEl.replaceChildren(...items.map(renderItem));

  if (!items.length) {
    statusEl.hidden = false;
    statusEl.textContent = state.source ? "这个来源暂时没有条目。" : "暂时没有条目。";
  } else {
    statusEl.hidden = true;
  }

  const newest = items
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
    const query = new URLSearchParams({ limit: "60" });
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

document.querySelectorAll(".filter").forEach((button) => {
  button.addEventListener("click", () => {
    document.querySelectorAll(".filter").forEach((other) => {
      other.classList.toggle("is-active", other === button);
    });
    state.source = button.dataset.source || "";
    load(state.source);
  });
});

load("");

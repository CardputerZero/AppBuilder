/* CardputerZero developer portal front-end. Same-origin API (see worker/). */

import { parseDeb, compareDebVersions, extractEmail } from "/debparse.js";
import { XzReadableStream } from "https://cdn.jsdelivr.net/npm/xz-decompress@0.2.2/+esm";

const INDEX_URL = "https://cardputer.cc/packages/dists/stable/main/binary-arm64/Packages";

const $ = (id) => document.getElementById(id);
let me = null;
let parsed = null;
let file = null;
let publishedIndex = null;

/* ------------------------------ decompressors ---------------------------- */

async function streamToBytes(stream) {
  const chunks = [];
  const reader = stream.getReader();
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    chunks.push(value);
  }
  const total = chunks.reduce((n, c) => n + c.length, 0);
  const out = new Uint8Array(total);
  let off = 0;
  for (const c of chunks) { out.set(c, off); off += c.length; }
  return out;
}

const decompressors = {
  gzip: async (data) => streamToBytes(
    new Blob([data]).stream().pipeThrough(new DecompressionStream("gzip")),
  ),
  xz: async (data) => streamToBytes(
    new XzReadableStream(new Blob([data]).stream()),
  ),
  zstd: async (data) => {
    // Chrome 133+/Edge expose zstd in DecompressionStream; fall back with a hint.
    try {
      return await streamToBytes(
        new Blob([data]).stream().pipeThrough(new DecompressionStream("zstd")),
      );
    } catch {
      throw new Error("此浏览器不支持 zstd 解压，无法本地预览。建议用 dpkg-deb -Zxz 重新打包，或换 Chrome 133+ 浏览器");
    }
  },
};

/* --------------------------------- init ---------------------------------- */

async function init() {
  try {
    const r = await fetch("/api/me");
    if (r.ok) me = await r.json();
  } catch { /* not logged in */ }
  $(me ? "app-view" : "login-view").classList.remove("hidden");
  if (me) {
    $("who-box").classList.remove("hidden");
    $("who").textContent = me.login + (me.is_admin ? "（管理员）" : "");
  }
}

async function loadIndex() {
  if (publishedIndex) return publishedIndex;
  publishedIndex = new Map();
  try {
    const text = await (await fetch(INDEX_URL)).text();
    for (const para of text.split(/\n\n+/)) {
      const f = {};
      for (const line of para.split("\n")) {
        const i = line.indexOf(": ");
        if (i > 0 && !/^\s/.test(line)) f[line.slice(0, i)] = line.slice(i + 2).trim();
      }
      if (!f.Package) continue;
      if (!publishedIndex.has(f.Package)) publishedIndex.set(f.Package, []);
      publishedIndex.get(f.Package).push(f);
    }
  } catch { /* offline preview still works */ }
  return publishedIndex;
}

/* --------------------------------- tabs ---------------------------------- */

$("tab-upload").addEventListener("click", () => switchTab("upload"));
$("tab-mine").addEventListener("click", () => { switchTab("mine"); renderMine(); });

function switchTab(which) {
  $("tab-upload").classList.toggle("active", which === "upload");
  $("tab-mine").classList.toggle("active", which === "mine");
  $("upload-panel").classList.toggle("hidden", which !== "upload");
  $("mine-panel").classList.toggle("hidden", which !== "mine");
}

/* ------------------------------ file picking ----------------------------- */

const drop = $("drop");
drop.addEventListener("click", () => $("file").click());
drop.addEventListener("dragover", (e) => { e.preventDefault(); drop.classList.add("hover"); });
drop.addEventListener("dragleave", () => drop.classList.remove("hover"));
drop.addEventListener("drop", (e) => {
  e.preventDefault(); drop.classList.remove("hover");
  pick(e.dataTransfer.files[0]);
});
$("file").addEventListener("change", (e) => pick(e.target.files[0]));

async function pick(f) {
  if (!f) return;
  if (!f.name.endsWith(".deb")) return say("err", "请选择 .deb 文件");
  file = f;
  parsed = null;
  say("", "本地解析中…");
  $("preview-box").classList.add("hidden");
  try {
    const buf = await f.arrayBuffer();
    parsed = await parseDeb(buf, decompressors);
  } catch (err) {
    return say("err", `解析失败：${err.message}`);
  }
  say("", "");
  await renderPreview();
}

/* -------------------------------- preview -------------------------------- */

async function renderPreview() {
  const c = parsed.control;
  $("drop-text").innerHTML = `已选择 <b>${file.name}</b>（${(file.size / 1048576).toFixed(1)} MB）— 点击可更换`;
  $("p-name").textContent = (parsed.desktop && parsed.desktop.Name) || c.Package || "?";
  $("p-pkg").textContent = c.Package || "";
  $("p-version").textContent = c.Version || "?";
  $("p-arch").textContent = c.Architecture || "?";
  $("p-size").textContent = `${(parsed.totalInstalledSize / 1048576).toFixed(1)} MB`;
  $("p-maint").textContent = c.Maintainer || "（缺失）";

  if (parsed.icon && parsed.icon.isPng) {
    const url = URL.createObjectURL(new Blob([parsed.icon.bytes], { type: "image/png" }));
    $("p-icon").src = url;
    $("p-icon").classList.remove("hidden");
    $("p-noicon").classList.add("hidden");
  } else {
    $("p-icon").classList.add("hidden");
    $("p-noicon").classList.remove("hidden");
  }

  // 邮箱归属预检
  const email = (parsed.email || "").toLowerCase();
  const mine = me && me.emails.includes(email);
  $("p-emailmatch").innerHTML = mine
    ? `<span class="lv-pass">Maintainer 邮箱与你的 GitHub 已验证邮箱匹配</span>`
    : `<span class="lv-danger">Maintainer 邮箱 (${email || "缺失"}) 不在你的 GitHub 已验证邮箱里 — 提交会被拒绝${me && me.is_admin ? "（你是管理员，可豁免）" : ""}</span>`;

  // 版本 / 包名占用预检
  const idx = await loadIndex();
  const entries = idx.get(c.Package) || [];
  let verState = "", verOk = true;
  if (!entries.length) {
    verState = `<span class="lv-pass">新包名，首次提交后归属于你</span>`;
  } else {
    const owner = extractEmail(entries[0].Maintainer || "").toLowerCase();
    const owned = me && (me.emails.includes(owner) || me.is_admin);
    const latest = entries.map((e) => e.Version).sort(compareDebVersions).pop();
    if (!owned) {
      verOk = false;
      verState = `<span class="lv-danger">包名已被他人占用（Maintainer: ${owner.slice(0, 2)}***），无法提交</span>`;
    } else if (compareDebVersions(c.Version, latest) <= 0) {
      verOk = false;
      verState = `<span class="lv-danger">版本 ${c.Version} 不高于线上已发布的 ${latest}，请提升版本号</span>`;
    } else {
      verState = `<span class="lv-pass">已发布 ${latest} → 本次更新为 ${c.Version}</span>`;
    }
  }
  $("p-verstate").innerHTML = verState;

  // 检查报告
  const ul = $("report-list");
  ul.innerHTML = "";
  for (const [level, msg] of parsed.report) {
    const li = document.createElement("li");
    li.className = `lv-${level}`;
    li.textContent = msg;
    ul.appendChild(li);
  }

  // 文件清单 + 脚本
  $("p-filecount").textContent = parsed.files.length;
  $("p-files").textContent = parsed.files
    .filter((f) => f.type !== "dir")
    .map((f) => `${(f.mode & 0o7777).toString(8).padStart(4, "0")}  ${String(f.size).padStart(9)}  ${f.path}${f.linkname ? " -> " + f.linkname : ""}`)
    .join("\n");
  const scriptNames = Object.keys(parsed.scripts);
  if (scriptNames.length) {
    $("p-scripts-box").classList.remove("hidden");
    $("p-scripts").textContent = scriptNames.map((n) => `### ${n}\n${parsed.scripts[n]}`).join("\n\n");
  } else {
    $("p-scripts-box").classList.add("hidden");
  }

  const blocked = parsed.verdict === "danger" || (!mine && !(me && me.is_admin)) || !verOk;
  $("submit-btn").disabled = blocked;
  $("submit-btn").textContent = blocked ? "存在阻断性问题，无法提交" : "提交到 AppStore";
  $("preview-box").classList.remove("hidden");
}

/* --------------------------------- submit -------------------------------- */

$("submit-btn").addEventListener("click", async () => {
  if (!file || !parsed) return;
  $("submit-btn").disabled = true;
  say("", "上传中…");

  const body = new FormData();
  body.append("deb", file);
  body.append("package", parsed.control.Package);
  body.append("version", parsed.control.Version);
  body.append("arch", parsed.control.Architecture);
  const repo = $("source-repo").value.trim();
  if (repo) body.append("source_repo", repo);

  try {
    const r = await fetch("/api/submit", { method: "POST", body });
    const data = await r.json();
    if (!r.ok) throw new Error(data.detail || data.error || `HTTP ${r.status}`);
    say("ok",
      `✓ ${data.message}\n审核进度：${data.actions_url}\n发布 PR：${data.track_url}`);
  } catch (err) {
    say("err", `提交失败：${err.message}`);
    $("submit-btn").disabled = false;
  }
});

/* ------------------------------ my packages ------------------------------ */

async function renderMine() {
  const rows = $("mine-rows");
  const idx = await loadIndex();
  const mine = [];
  for (const [name, entries] of idx) {
    for (const e of entries) {
      const email = extractEmail(e.Maintainer || "").toLowerCase();
      if (me.emails.includes(email) || me.is_admin) mine.push({ name, ...e, email });
    }
  }
  if (!mine.length) {
    rows.innerHTML = `<tr><td colspan="4" class="muted">没有找到属于你的已发布软件包</td></tr>`;
    return;
  }
  rows.innerHTML = "";
  for (const p of mine.sort((a, b) => a.name.localeCompare(b.name))) {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td>${p.name}</td><td>${p.Version}</td><td>${p.email}</td><td></td>`;
    const btn = document.createElement("button");
    btn.textContent = "下架";
    btn.addEventListener("click", () => unpublish(p, btn));
    tr.lastElementChild.appendChild(btn);
    rows.appendChild(tr);
  }
}

async function unpublish(p, btn) {
  if (!confirm(`确认下架 ${p.name} ${p.Version}？将生成移除 PR。`)) return;
  btn.disabled = true;
  try {
    const r = await fetch("/api/unpublish", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ package: p.name, version: p.Version }),
    });
    const data = await r.json();
    if (!r.ok) throw new Error(data.detail || data.error);
    btn.textContent = "已提交";
  } catch (err) {
    alert(`下架失败：${err.message}`);
    btn.disabled = false;
  }
}

function say(kind, text) {
  const el = $("status");
  el.className = `status ${kind}`;
  el.textContent = text;
}

init();

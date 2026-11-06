/* 场景 12 实验手册 - 共享脚本：顶栏、目录、仓库链接、复制按钮、A/B 路线切换
 *
 * 仓库地址只在这里定义一次（占位符）。正式发布前在仓库根目录运行：
 *   ./tools/set-repo-url.sh https://github.com/<owner>/<name>
 * 该脚本会把全仓库中的占位符替换为真实地址。
 */
const REPO_URL = "https://github.com/weiping-bj/agentdlc-eval-requirement-triage";
const REPO_BRANCH = "main";

/* 章节清单：目录、上一页/下一页都由它生成。path: common / a / b */
const CHAPTERS = [
  { group: "导读" },
  { id: "index", file: "index.html", no: "0", title: "导读与路线选择", path: "common" },
  { id: "discussion", file: "discussion.html", no: "讨", title: "11:00 分组讨论材料", path: "common" },
  { group: "通用准备（A/B 都做）" },
  { id: "01-setup", file: "01-setup.html", no: "1", title: "准备实验环境（EC2 + Coding Agent）", path: "common" },
  { id: "02-data", file: "02-data.html", no: "2", title: "输入数据与黄金集", path: "common" },
  { group: "A 版：接入自有 Agent" },
  { id: "a3-instrument", file: "a3-instrument.html", no: "A3", title: "埋点：OTel + ADOT → CloudWatch", path: "a" },
  { id: "a4-logs", file: "a4-logs.html", no: "A4", title: "已有日志：转换并导入 CloudWatch", path: "a" },
  { id: "a5-check", file: "a5-check.html", no: "A5", title: "遥测检查：能否被 Evaluations 使用", path: "a" },
  { group: "B 版：mock agent 上 AgentCore Runtime" },
  { id: "b3-deploy", file: "b3-deploy.html", no: "B3", title: "部署 V0 到 AgentCore Runtime", path: "b" },
  { id: "b4-invoke", file: "b4-invoke.html", no: "B4", title: "在控制台查看 Runtime 与 trace", path: "b" },
  { group: "通用评估与优化（A/B 都做）" },
  { id: "06-evaluators", file: "06-evaluators.html", no: "6", title: "注册评估器 + 开启在线评估", path: "common" },
  { id: "07-evaluate", file: "07-evaluate.html", no: "7", title: "跑黄金集（V0）并评估", path: "common" },
  { id: "08-optimize", file: "08-optimize.html", no: "8", title: "基于评估优化（V0 → V1）", path: "common" },
  { id: "09-compare", file: "09-compare.html", no: "9", title: "再评估与前后对比", path: "common" },
  { id: "10-cleanup", file: "10-cleanup.html", no: "10", title: "清理资源", path: "common" },
  { group: "附录" },
  { id: "appendix", file: "appendix.html", no: "附", title: "配置、切换区域/模型、排错、文件索引", path: "common" },
];

function repoLink(path) {
  const isDir = path.endsWith("/");
  return `${REPO_URL}/${isDir ? "tree" : "blob"}/${REPO_BRANCH}/${path.replace(/\/$/, "")}`;
}

function buildTopbar() {
  const bar = document.getElementById("topbar");
  if (!bar) return;
  bar.innerHTML = `
    <div>
      <div class="title">需求价值打分 Agent · 动手实验手册</div>
      <div class="subtitle">场景 12 · Strands Agent + AgentCore Observability + AgentCore Evaluations · 评估驱动的 Agent 迭代</div>
    </div>
    <div class="spacer"></div>
    <div class="path-switch">我的路线：
      <button class="btn" data-set-path="a">A 版 自有 Agent</button>
      <button class="btn" data-set-path="b">B 版 mock agent</button>
      <button class="btn" data-set-path="">全部显示</button>
    </div>
    <a class="btn" href="${REPO_URL}" target="_blank" rel="noopener">GitHub 仓库 ↗</a>`;
  bar.querySelectorAll("[data-set-path]").forEach((b) => {
    b.addEventListener("click", () => setPath(b.dataset.setPath));
  });
}

function setPath(p) {
  try { localStorage.setItem("s12lab-path", p); } catch (e) { /* ignore */ }
  document.body.classList.remove("path-a", "path-b");
  if (p) document.body.classList.add("path-" + p);
  document.querySelectorAll("[data-set-path]").forEach((b) => b.classList.toggle("active", b.dataset.setPath === p));
}

function buildToc() {
  const toc = document.getElementById("toc");
  if (!toc) return;
  const cur = document.body.dataset.page;
  let html = '<div class="group" style="margin-top:0">目录</div>';
  for (const c of CHAPTERS) {
    if (c.group) { html += `<div class="group">${c.group}</div>`; continue; }
    const badge = c.path === "a" ? '<span class="badge-a">A</span>' : c.path === "b" ? '<span class="badge-b">B</span>' : "";
    html += `<a href="${c.file}" class="${c.id === cur ? "current" : ""}">${badge}${c.no} · ${c.title}</a>`;
    if (c.id === cur) {
      document.querySelectorAll("main .step[id]").forEach((s) => {
        const h = s.querySelector(".step-head h3");
        const n = s.querySelector(".step-head .num");
        if (h) html += `<a class="sub" href="#${s.id}">${n ? n.textContent + " " : ""}${h.textContent}</a>`;
      });
    }
  }
  toc.innerHTML = html;
}

function buildPager() {
  const main = document.querySelector("main");
  const cur = document.body.dataset.page;
  const list = CHAPTERS.filter((c) => !c.group);
  const i = list.findIndex((c) => c.id === cur);
  if (i < 0 || !main) return;
  const prev = list[i - 1], next = list[i + 1];
  const div = document.createElement("div");
  div.className = "pager";
  div.innerHTML = `<span>${prev ? `<a href="${prev.file}">← ${prev.no} · ${prev.title}</a>` : ""}</span>
                   <span>${next ? `<a href="${next.file}">${next.no} · ${next.title} →</a>` : ""}</span>`;
  main.appendChild(div);
}

function resolveRepoLinks() {
  // <a data-repo="agent/app.py">...</a>  -> GitHub 文件链接（目录以 / 结尾）
  document.querySelectorAll("a[data-repo]").forEach((a) => {
    a.href = repoLink(a.dataset.repo);
    a.target = "_blank";
    a.rel = "noopener";
    if (!a.textContent.trim()) a.textContent = a.dataset.repo;
  });
  // <span data-repo-url></span> -> 仓库地址文本；<span data-clone-url></span> -> clone 地址
  document.querySelectorAll("[data-repo-url]").forEach((e) => (e.textContent = REPO_URL));
  document.querySelectorAll("[data-clone-url]").forEach((e) => (e.textContent = REPO_URL + ".git"));
  document.querySelectorAll("[data-raw]").forEach((e) => {
    const raw = REPO_URL.replace("https://github.com/", "https://raw.githubusercontent.com/") + "/" + REPO_BRANCH + "/" + e.dataset.raw;
    e.textContent = raw;
  });
}

function addCopyButtons() {
  document.querySelectorAll("pre:not(.out)").forEach((pre) => {
    const btn = document.createElement("button");
    btn.className = "copy";
    btn.textContent = "复制";
    btn.addEventListener("click", () => {
      const code = pre.querySelector("code") || pre;
      const text = code.innerText.replace(/\n$/, "");
      navigator.clipboard.writeText(text).then(() => {
        btn.textContent = "已复制";
        setTimeout(() => (btn.textContent = "复制"), 1200);
      });
    });
    pre.appendChild(btn);
  });
}

function trackActiveStep() {
  const subs = Array.from(document.querySelectorAll("#toc a.sub"));
  if (!subs.length || !("IntersectionObserver" in window)) return;
  const map = new Map(subs.map((a) => [a.getAttribute("href").slice(1), a]));
  const obs = new IntersectionObserver((entries) => {
    entries.forEach((e) => {
      if (e.isIntersecting) {
        subs.forEach((a) => a.classList.remove("active"));
        const a = map.get(e.target.id);
        if (a) a.classList.add("active");
      }
    });
  }, { rootMargin: "-70px 0px -70% 0px" });
  document.querySelectorAll("main .step[id]").forEach((s) => obs.observe(s));
}

document.addEventListener("DOMContentLoaded", () => {
  resolveRepoLinks();
  buildTopbar();
  buildToc();
  buildPager();
  addCopyButtons();
  trackActiveStep();
  let p = "";
  try { p = localStorage.getItem("s12lab-path") || ""; } catch (e) { /* ignore */ }
  const forced = new URLSearchParams(location.search).get("path");
  if (forced !== null) p = forced;
  setPath(p);
});

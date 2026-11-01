const $ = (s) => document.querySelector(s);
const state = { runs: [], m: null, active: -1 };

const fmt = (t) => {
  t = Math.max(0, t || 0);
  const m = Math.floor(t / 60);
  const s = Math.floor(t % 60);
  return `${m}:${String(s).padStart(2, "0")}`;
};
const pct = (v) => `${Math.round((v || 0) * 100)}%`;

function trackBadge(id) {
  if (id === "parallax") return '<span class="badge a">A</span>';
  if (id === "procedural" || id === "pregen") return '<span class="badge b">B</span>';
  return '<span class="badge d">兜底</span>';
}

async function boot() {
  let runs = [];
  try {
    runs = await fetch("/api/runs").then((r) => r.json());
  } catch (e) {
    runs = [];
  }
  state.runs = runs;
  const sel = $("#run");
  if (!runs.length) {
    $("#empty").classList.remove("hidden");
    return;
  }
  runs.forEach((r, i) => {
    const o = document.createElement("option");
    o.value = i;
    o.textContent = `${r.book} · ${r.run} · ${(r.film?.duration || 0).toFixed(0)}s`;
    sel.appendChild(o);
  });
  sel.onchange = () => load(runs[+sel.value]);
  load(runs[0]);
}

async function load(r) {
  const m = await fetch(
    `/api/manifest?book=${encodeURIComponent(r.book)}&run=${encodeURIComponent(r.run)}`
  ).then((x) => x.json());
  state.m = m;
  state.active = -1;
  render(m, r);
}

function render(m, r) {
  const film = m.film || {};
  const v = $("#film");
  v.src = `/media/${film.path}`;
  v.load();
  $("#empty").classList.add("hidden");

  const st = m.stats || {};
  const chips = [
    ["总时长", `${(film.duration || 0).toFixed(1)}s`, ""],
    ["镜头", `${st.shots || 0}`, ""],
    ["覆盖率", pct(st.coverage), st.coverage >= 0.999 ? "good" : "warn"],
    ["平均质检", (st.avg_qc || 0).toFixed(2), st.avg_qc >= 0.75 ? "good" : "warn"],
    ["降级", `${st.fallbacks || 0}`, st.fallbacks ? "warn" : "good"],
    ["缓存命中", `${st.cache_hits || 0}`, ""],
    ["耗时", `${(st.wall_s || 0).toFixed(1)}s`, ""],
  ];
  $("#chips").innerHTML = chips
    .map(([k, val, cls]) => `<span class="chip ${cls}">${k} <b>${val}</b></span>`)
    .join("");

  const thumbs = film.thumbs || [];
  const ch = film.chapters || [];
  $("#chapters").innerHTML = ch
    .map((c, i) => {
      const img = thumbs[i] ? `/media/${thumbs[i]}` : "";
      return `<li data-i="${i}" data-t="${c.start}">
        ${img ? `<img src="${img}" alt="p${c.page}" loading="lazy">` : "<img>"}
        <div class="t">${fmt(c.start)} – ${fmt(c.end)} · 第${c.page}页 ${trackBadge(c.track)}</div>
        <div class="x">${(c.text || "（无字幕）").slice(0, 60)}</div>
      </li>`;
    })
    .join("");
  document.querySelectorAll("#chapters li").forEach((li) => {
    li.onclick = () => {
      $("#film").currentTime = parseFloat(li.dataset.t) + 0.01;
      $("#film").play();
    };
  });

  renderQC(m);
}

function renderQC(m) {
  const rows = (m.shots || []).map((s) => {
    const q = s.qc || {};
    const mt = q.metrics || {};
    const score = q.score || 0;
    const color = score >= 0.75 ? "#7fb069" : score >= 0.45 ? "#d9a441" : "#c2564a";
    const fb = s.fallback_from && s.fallback_from.length
      ? `<span class="dim"> ← ${s.fallback_from.join(",")}</span>` : "";
    const reasons = (q.reasons || []).join("; ");
    return `<tr>
      <td>${s.shot_id}</td>
      <td>${s.page}</td>
      <td>${trackBadge(s.backend?.id)} ${s.backend?.id || ""}${fb}</td>
      <td><div class="bar-mini"><i style="width:${pct(score)};background:${color}"></i></div></td>
      <td>${score.toFixed(2)}</td>
      <td>${(mt.motion ?? 0).toFixed(3)}</td>
      <td>${(mt.flicker ?? 0).toFixed(3)}</td>
      <td>${(mt.drift_ncc ?? 0).toFixed(2)}</td>
      <td class="st-${s.status}">${s.status}</td>
      <td>${(s.cost?.wall_s ?? 0).toFixed(1)}s</td>
      <td class="txt">${reasons || "—"}</td>
    </tr>`;
  });
  $("#qc").innerHTML = `<table><thead><tr>
      <th>镜头</th><th>页</th><th>后端</th><th>质检</th><th>分</th>
      <th>运动</th><th>闪烁</th><th>保真</th><th>状态</th><th>耗时</th><th>说明</th>
    </tr></thead><tbody>${rows.join("")}</tbody></table>`;
}

/* ------------------------------------------------------------ transport */
const useVideo = (fn) => () => {
  const v = $("#film");
  if (v && v.readyState >= 0) fn(v);
};

document.addEventListener("DOMContentLoaded", () => {
  const v = $("#film");
  $("#play").onclick = () => {
    if (v.paused) v.play();
    else v.pause();
  };
  v.addEventListener("play", () => ($("#play").textContent = "❚❚"));
  v.addEventListener("pause", () => ($("#play").textContent = "▶"));
  v.addEventListener("timeupdate", () => {
    const d = v.duration || 0;
    $("#scrub-fill").style.width = `${d ? (v.currentTime / d) * 100 : 0}%`;
    $("#clock").textContent = `${fmt(v.currentTime)} / ${fmt(d)}`;
    const ch = state.m?.film?.chapters || [];
    let idx = -1;
    ch.forEach((c, i) => {
      if (v.currentTime >= c.start - 0.05 && v.currentTime < c.end) idx = i;
    });
    if (idx !== state.active) {
      state.active = idx;
      document.querySelectorAll("#chapters li").forEach((li, i) => {
        li.classList.toggle("active", i === idx);
        if (i === idx) li.scrollIntoView({ block: "nearest", behavior: "smooth" });
      });
    }
  });
  $("#toggle-qc").onclick = () => {
    const el = $("#qc");
    el.classList.toggle("hidden");
    $("#toggle-qc").querySelector(".caret").textContent = el.classList.contains("hidden") ? "▾" : "▴";
  };
  boot();
});

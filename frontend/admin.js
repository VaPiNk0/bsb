async function loadAdmin() {
  const response = await fetch("/api/admin/overview");
  const data = await response.json();
  if (!response.ok) {
    location.href = "/";
    return;
  }
  document.getElementById("currentUser").textContent = data.current_user;

  const stats = [
    ["Metrika", data.metric],
    ["k", data.k],
    ["Lockout", `${data.lockout_threshold} zlyhaní / ${data.lockout_seconds}s`],
    ["Rate limit", data.rate_limit],
  ];
  document.getElementById("statsGrid").innerHTML = stats.map(([label, value]) => `<div class="stat"><span>${escapeHtml(String(label))}</span><strong>${escapeHtml(String(value))}</strong></div>`).join("");

  document.getElementById("usersTable").innerHTML = data.users.map((u) => {
    const r = u.refresh;
    const refreshBadge = !u.enrolled ? '<span class="badge neutral">—</span>'
      : r.due ? `<span class="badge bad">po termíne (${r.days_since} dní)</span>`
      : `<span class="badge ok">o ${r.due_in_days} dní</span>`;
    const age = data.test_mode_allowed && u.enrolled
      ? `<button class="secondary age-btn" data-user="${u.id}" data-days="${data.refresh_days}">Posunúť o mesiac</button>` : "";
    return `
    <tr>
      <td>${escapeHtml(u.username)}</td>
      <td class="num">${u.enrollment_count}</td>
      <td><span class="badge ${u.enrolled ? "ok" : "warn"}">${u.enrolled ? "áno" : "nie"}</span></td>
      <td class="num">${u.template_cycle || 0}</td>
      <td>${refreshBadge}</td>
      <td class="num">${u.failed_count}</td>
      <td>${u.lock_until ? escapeHtml(new Date(u.lock_until).toLocaleString("sk-SK")) : "—"}</td>
      <td>${age}</td>
    </tr>`;
  }).join("");
  document.querySelectorAll(".age-btn").forEach((button) => button.addEventListener("click", async () => {
    const boot = await fetch("/api/bootstrap").then((r) => r.json());
    await fetch("/api/admin/age-template", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": boot.csrf },
      body: JSON.stringify({ user_id: Number(button.dataset.user), days: Number(button.dataset.days) }),
    });
    loadAdmin();
  }));

  renderHistory(data.history || []);
  renderReport(data.report);
  renderChart(data.attempts);
  document.getElementById("attemptsTable").innerHTML = data.attempts.map((a) => `
    <tr>
      <td>${escapeHtml(new Date(a.created_at).toLocaleString("sk-SK"))}</td>
      <td>${escapeHtml(a.username)}</td>
      <td>${escapeHtml(a.actor_label || "—")}</td>
      <td><span class="badge ${a.attempt_type === "genuine" ? "ok" : a.attempt_type === "imitacia" ? "warn" : "bad"}">${escapeHtml(a.attempt_type)}</span></td>
      <td class="num">${a.score === null ? "—" : Number(a.score).toFixed(4)}</td>
      <td class="num">${a.threshold === null ? "—" : Number(a.threshold).toFixed(4)}</td>
      <td><span class="badge ${a.accepted ? "ok" : "bad"}">${a.accepted ? "ACCEPT" : "REJECT"}</span></td>
      <td>${escapeHtml(a.replay_detected ? "replay" : (a.reason || "—"))}</td>
    </tr>`).join("");
}

const TREND = {
  improved: ["ok", "zlepšenie"],
  stable: ["neutral", "stabilné"],
  degraded: ["warn", "zhoršenie"],
};
const SERIES = 6;   // počet farieb radov v style.css (.s0 – .s5)

function pctText(v) {
  return v === null || v === undefined ? "—" : `${v > 0 ? "+" : ""}${Number(v).toFixed(1)} %`;
}

function renderHistory(history) {
  const body = document.getElementById("historyTable");
  const chart = document.getElementById("trendChart");
  const legend = document.getElementById("trendLegend");
  if (!history.length) {
    body.innerHTML = '<tr><td colspan="8">Zatiaľ žiadna obnova. Používateľ ju dostane pri prihlásení, keď šablóna zostarne o 30 dní.</td></tr>';
    chart.innerHTML = "";
    legend.innerHTML = "";
    return;
  }
  body.innerHTML = [...history].reverse().map((h) => {
    const [kind, label] = TREND[h.verdict] || TREND.stable;
    const note = h.threshold_capped ? "prah na strope, zváž novú registráciu" : "—";
    return `<tr>
      <td>${escapeHtml(new Date(h.created_at).toLocaleString("sk-SK"))}</td>
      <td>${escapeHtml(h.username)}</td>
      <td class="num">${h.cycle}</td>
      <td><span class="badge ${kind}">${label}</span></td>
      <td class="num">${pctText(h.speed_change_pct)}</td>
      <td class="num">${pctText(h.consistency_change_pct)}</td>
      <td class="num">${Number(h.old_threshold).toFixed(3)} → ${Number(h.new_threshold).toFixed(3)}</td>
      <td>${escapeHtml(note)}</td>
    </tr>`;
  }).join("");

  // graf: medián času medzi stlačeniami (ms) v jednotlivých cykloch; nižšie = rýchlejšie písanie
  const byUser = {};
  history.forEach((h) => {
    const list = (byUser[h.username] = byUser[h.username] || []);
    if (!list.length && h.median_dd_old) list.push({ cycle: h.cycle - 1, v: h.median_dd_old });
    if (h.median_dd_new) list.push({ cycle: h.cycle, v: h.median_dd_new });
  });
  const all = Object.values(byUser).flat();
  const W = 1100, H = 240, L = 54, R = 20, T = 14, B = 30;
  const maxCycle = Math.max(1, ...all.map((p) => p.cycle));
  const lo = Math.min(...all.map((p) => p.v)), hi = Math.max(...all.map((p) => p.v));
  const pad = Math.max(10, (hi - lo) * 0.25);
  const yMin = Math.floor((lo - pad) / 10) * 10, yMax = Math.ceil((hi + pad) / 10) * 10;
  const x = (c) => L + (c / maxCycle) * (W - L - R);
  const y = (v) => T + (1 - (v - yMin) / (yMax - yMin)) * (H - T - B);
  let svg = `<svg class="chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="Vývoj tempa písania podľa mesiacov">`;
  for (let i = 0; i <= 4; i++) {
    const v = yMin + ((yMax - yMin) * i) / 4;
    svg += `<line x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}" class="grid"/><text x="${L - 8}" y="${y(v) + 4}" text-anchor="end">${Math.round(v)} ms</text>`;
  }
  for (let c = 0; c <= maxCycle; c++) svg += `<text x="${x(c)}" y="${H - 8}" text-anchor="${c === 0 ? "start" : c === maxCycle ? "end" : "middle"}">${c === 0 ? "začiatok" : `mesiac ${c}`}</text>`;
  Object.entries(byUser).forEach(([name, pts], i) => {
    const series = `series s${i % SERIES}`;
    svg += `<polyline class="${series}" points="${pts.map((p) => `${x(p.cycle)},${y(p.v)}`).join(" ")}"/>`;
    pts.forEach((p) => { svg += `<circle cx="${x(p.cycle)}" cy="${y(p.v)}" r="5" class="dot ${series}"><title>${escapeHtml(name)}: ${p.v.toFixed(0)} ms</title></circle>`; });
  });
  chart.innerHTML = svg + "</svg>";
  legend.replaceChildren();
  Object.keys(byUser).forEach((name, i) => {
    const item = document.createElement("span");
    const dot = document.createElement("i");
    dot.className = `series s${i % SERIES}`;
    item.append(dot, name);
    legend.appendChild(item);
  });
  const note = document.createElement("span");
  note.className = "muted";
  note.textContent = "nižšie = rýchlejšie písanie";
  legend.appendChild(note);
}

const SCENARIOS = {
  genuine: "genuine (vlastník účtu)",
  impostor: "impostor (vlastný štýl)",
  imitacia: "imitácia",
  attacks_total: "útoky spolu",
};
const EXCLUDED = {
  password_failed: "nesprávne heslo",
  account_locked: "zamknutý účet",
  replay: "replay",
  insufficient_features: "málo použiteľných kláves",
  unknown_user: "neznámy účet",
};

function numText(v, digits) {
  return v === null || v === undefined ? "—"
    : Number(v).toLocaleString("sk-SK", { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

function rateText(v) {
  return v === null || v === undefined ? "—" : `${numText(v * 100, 1)} %`;
}

function rateWithCounts(rate, errors, n) {
  return n ? `${rateText(rate)} (${errors}/${n})` : "—";
}

function renderReport(report) {
  const genuine = report.scenarios.find((s) => s.scenario === "genuine");
  const attacks = report.scenarios.find((s) => s.scenario === "attacks_total");
  const stats = [
    ["FRR", rateWithCounts(genuine.rate, genuine.errors, genuine.n)],
    ["FAR", rateWithCounts(attacks.rate, attacks.errors, attacks.n)],
    ["EER", rateText(report.eer)],
    ["Vyhodnotené pokusy", `${report.scored} · účty ${report.accounts} · testeri ${report.testers}`],
  ];
  document.getElementById("reportStats").innerHTML = stats.map(([label, value]) => `<div class="stat"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></div>`).join("");

  document.getElementById("scenarioTable").innerHTML = report.scenarios.map((s) => `
    <tr>
      <td>${escapeHtml(SCENARIOS[s.scenario] || s.scenario)}</td>
      <td class="num">${s.n}</td>
      <td class="num">${s.accepted}</td>
      <td class="num">${s.rejected}</td>
      <td>${s.measure}</td>
      <td class="num">${rateText(s.rate)}</td>
      <td class="num">${s.n ? `${numText(s.ci_low * 100, 1)} – ${numText(s.ci_high * 100, 1)} %` : "—"}</td>
      <td class="num">${numText(s.median_ratio, 2)}</td>
    </tr>`).join("");

  const notes = [];
  if (genuine.n) notes.push(`Z ${genuine.n} genuine pokusov je ${report.genuine_labeled} označených v testovacom režime; pri ostatných sa predpokladá, že písal vlastník účtu.`);
  if (!attacks.n) notes.push("FAR a EER sa nedajú určiť, kým nie sú pokusy typu impostor alebo imitácia (zbierajú sa v testovacom režime).");
  if (report.excluded_total) {
    const reasons = report.excluded.map((e) => `${EXCLUDED[e.reason] || e.reason} ${e.n}`).join(", ");
    notes.push(`Mimo vyhodnotenia je ${report.excluded_total} pokusov bez biometrického skóre: ${reasons}.`);
  }
  notes.push("Interval je 95 % Wilsonov; EER je bod, kde sa FAR a FRR vyrovnajú pri spoločnom násobku prahov.");
  document.getElementById("reportNote").textContent = notes.join(" ");

  document.getElementById("userResultsTable").innerHTML = report.users.map((u) => `
    <tr>
      <td>${escapeHtml(u.username)}</td>
      <td class="num">${u.enrollment_n}</td>
      <td class="num">${u.median_dwell_ms === null ? "—" : `${numText(u.median_dwell_ms, 0)} ms`}</td>
      <td class="num">${u.median_dd_ms === null ? "—" : `${numText(u.median_dd_ms, 0)} ms`}</td>
      <td class="num">${numText(u.threshold, 3)}</td>
      <td class="num">${rateWithCounts(u.frr, u.genuine_rejected, u.genuine_n)}</td>
      <td class="num">${rateWithCounts(u.far, u.attack_accepted, u.attack_n)}</td>
      <td class="num">${numText(u.genuine_median_ratio, 2)}</td>
      <td class="num">${numText(u.attack_median_ratio, 2)}</td>
    </tr>`).join("");
}

function renderChart(attempts) {
  const rows = attempts.filter((a) => a.score !== null && a.threshold).reverse();
  const box = document.getElementById("scoreChart");
  if (!rows.length) {
    box.innerHTML = '<p>Zatiaľ žiadne vyhodnotené pokusy. Prihlás sa v testovacom režime a objavia sa tu.</p>';
    return;
  }
  const W = 1100, H = 280, L = 44, R = 16, T = 14, B = 28;
  const ratios = rows.map((a) => a.score / a.threshold);
  const max = Math.max(2, Math.ceil(Math.max(...ratios) * 2) / 2);
  const x = (i) => L + (rows.length === 1 ? (W - L - R) / 2 : (i / (rows.length - 1)) * (W - L - R));
  const y = (v) => T + (1 - v / max) * (H - T - B);
  const known = ["genuine", "impostor", "imitacia"];
  let svg = `<svg class="chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="Skóre pokusov vydelené prahom">`;
  svg += `<rect x="${L}" y="${y(1)}" width="${W - L - R}" height="${H - B - y(1)}" class="zone-ok"/>`;
  for (let v = 0; v <= max; v += 0.5) {
    svg += `<line x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}" class="${v === 1 ? "grid-strong" : "grid"}"/>`;
    svg += `<text x="${L - 8}" y="${y(v) + 4}" text-anchor="end">${v.toFixed(1)}</text>`;
  }
  rows.forEach((a, i) => {
    svg += `<circle cx="${x(i)}" cy="${y(ratios[i])}" r="6" class="dot series t-${known.includes(a.attempt_type) ? a.attempt_type : "other"}"><title>${escapeHtml(a.actor_label || a.username)} → ${escapeHtml(a.username)}: ${ratios[i].toFixed(2)}× prah</title></circle>`;
  });
  svg += `<text x="${W - R}" y="${y(1) - 6}" text-anchor="end">prah = 1,0</text></svg>`;
  box.innerHTML = svg;
}

function escapeHtml(value) {
  return value.replace(/[&<>'\"]/g, (char) => ({"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;",'"':"&quot;"}[char]));
}

document.addEventListener("DOMContentLoaded", () => {
  loadAdmin().catch(() => { location.href = "/"; });
  document.getElementById("logoutBtn").addEventListener("click", async () => {
    const boot = await fetch("/api/bootstrap").then((r) => r.json());
    await fetch("/api/logout", { method: "POST", headers: { "Content-Type": "application/json", "X-CSRF-Token": boot.csrf }, body: "{}" });
    location.href = "/";
  });
});

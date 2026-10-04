let BOOT = null;

const ALPHABET = "abcdefghijklmnopqrstuvwxyz ";

class TypingCapture {
  constructor(element, phraseEl, rhythmEl = null, tempoEl = null) {
    this.element = element;
    this.phraseEl = phraseEl;
    this.rhythmEl = rhythmEl;
    this.tempoEl = tempoEl;
    this.phrase = "";
    this.keyMap = new Map();
    this.onComplete = null;
    this.onInvalid = null;
    this.onChange = null;
    this.reset();
    this.bind();
  }

  bind() {
    this.element.addEventListener("keydown", (event) => this.keydown(event));
    this.element.addEventListener("keyup", (event) => this.keyup(event));
    this.element.addEventListener("paste", (event) => {
      event.preventDefault();
      this.invalid("Paste je zakázaný.");
    });
    this.element.addEventListener("drop", (event) => event.preventDefault());
    this.element.addEventListener("contextmenu", (event) => event.preventDefault());
    window.addEventListener("blur", () => {
      if (this.active) this.invalid("Okno stratilo fokus, pokus zopakuj.");
    });
  }

  setPhrase(phrase) {
    this.phrase = phrase;
    if (this.phraseEl) this.phraseEl.textContent = phrase;
    if (this.rhythmEl) {
      this.rhythmEl.innerHTML = Array.from(phrase, () => '<div class="slot"><div class="bar dwell"></div><div class="bar dd"></div></div>').join("");
      this.slots = Array.from(this.rhythmEl.children);
    }
  }

  reset() {
    this.events = [];
    this.buffer = [];
    this.corrections = 0;
    this.active = false;
    this.complete = false;
    this.invalidState = false;
    this.t0 = 0;
    this.keyMap.clear();
    this.element.classList.remove("invalid", "complete");
    this.render();
  }

  start(phrase = null) {
    if (phrase !== null) this.setPhrase(phrase);
    this.reset();
    this.active = true;
    this.t0 = performance.now();
    this.element.focus();
  }

  // indexy stlačení, ktoré v texte zostali (po odrátaní opravených)
  survivors() {
    const stack = [];
    this.events.forEach((e, i) => { if (e.key === "Backspace") stack.pop(); else stack.push(i); });
    return stack;
  }

  render() {
    // napísaný text: správne znaky normálne, chybné červené
    const frag = document.createDocumentFragment();
    this.buffer.forEach((ch, i) => {
      const span = document.createElement("span");
      span.className = ch === this.phrase[i] ? "ch ok" : "ch bad";
      span.textContent = ch === " " ? "\u00a0" : ch;
      frag.appendChild(span);
    });
    this.element.replaceChildren(frag);

    // zadaná veta: už správne napísaná časť je stlmená
    if (this.phraseEl && this.phrase) {
      let okPrefix = 0;
      while (okPrefix < this.buffer.length && this.buffer[okPrefix] === this.phrase[okPrefix]) okPrefix++;
      const done = document.createElement("span");
      done.className = "phrase-done";
      done.textContent = this.phrase.slice(0, okPrefix);
      const rest = document.createElement("span");
      rest.textContent = this.phrase.slice(okPrefix);
      this.phraseEl.replaceChildren(done, rest);
    }
    this.paintRhythm();
    if (this.onChange) this.onChange(this);
  }

  paintRhythm() {
    if (!this.slots) return;
    const surv = this.survivors();
    this.slots.forEach((slot, pos) => {
      const idx = surv[pos];
      const e = idx === undefined ? null : this.events[idx];
      const prevIdx = surv[pos - 1];
      const dwell = slot.children[0];
      const dd = slot.children[1];
      const hasDwell = !!e && Number.isFinite(e.up);
      dwell.classList.toggle("on", hasDwell);
      dwell.style.height = hasDwell ? `${Math.min(100, 2 + (e.up - e.down) * 0.55)}px` : "2px";
      const hasDd = !!e && prevIdx !== undefined && prevIdx === idx - 1;
      dd.classList.toggle("on", hasDd);
      dd.style.height = hasDd ? `${Math.min(100, 2 + Math.max(0, e.down - this.events[prevIdx].down) * 0.3)}px` : "2px";
    });
    if (this.tempoEl) {
      const typed = this.buffer.length;
      const first = this.events[0];
      const last = this.events[this.events.length - 1];
      let text = "—";
      if (typed > 3 && first && last && last.down > first.down) {
        const minutes = (last.down - first.down) / 60000;
        text = `${Math.round(typed / 5 / minutes)} slov/min`;
      }
      if (this.corrections) text += ` · opravy: ${this.corrections}`;
      this.tempoEl.textContent = text;
    }
  }

  keydown(event) {
    if (!this.active || this.invalidState) return;
    if (event.repeat) {
      event.preventDefault();
      return;
    }
    if (event.ctrlKey || event.metaKey || event.altKey) {
      event.preventDefault();
      this.invalid("Klávesové skratky zneplatňujú pokus.");
      return;
    }
    if (event.key === "Shift" || event.key === "CapsLock") {
      event.preventDefault();
      this.invalid("Píš len malé písmená (bez Shiftu, vypni Caps Lock).");
      return;
    }

    if (event.key === "Backspace") {
      event.preventDefault();
      if (this.buffer.length === 0 || this.keyMap.has(event.code)) return;
      if (this.corrections >= BOOT.max_corrections) {
        this.invalid("Príliš veľa opráv, začni túto vetu odznova.");
        return;
      }
      this.corrections += 1;
      this.keyMap.set(event.code, this.events.length);
      this.events.push({ key: "Backspace", code: event.code, down: performance.now() - this.t0, up: null });
      this.buffer.pop();
      this.render();
      return;
    }

    // ostatné nepísmenové klávesy (šípky, Tab, Enter...) ticho ignorujeme
    if (event.key.length !== 1) {
      event.preventDefault();
      return;
    }
    event.preventDefault();
    if (!ALPHABET.includes(event.key)) {
      this.onHint?.("Používaj len malé písmená bez diakritiky a medzeru.");
      return;
    }
    if (this.buffer.length >= this.phrase.length + 6 || this.keyMap.has(event.code)) return;
    this.keyMap.set(event.code, this.events.length);
    this.events.push({ key: event.key, code: event.code, down: performance.now() - this.t0, up: null });
    this.buffer.push(event.key);
    this.render();
  }

  keyup(event) {
    if (!this.active || this.invalidState) return;
    const index = this.keyMap.get(event.code);
    if (index === undefined) return;
    this.events[index].up = performance.now() - this.t0;
    this.keyMap.delete(event.code);
    this.render();
    this.checkComplete();
  }

  checkComplete() {
    if (this.keyMap.size !== 0 || this.buffer.join("") !== this.phrase) return;
    if (!this.events.every((e) => Number.isFinite(e.up))) return;
    this.active = false;
    this.complete = true;
    this.element.classList.add("complete");
    if (this.onComplete) this.onComplete(this.events);
  }

  invalid(message) {
    if (!this.active && !this.invalidState) return;
    this.active = false;
    this.invalidState = true;
    this.element.classList.add("invalid");
    if (this.onInvalid) this.onInvalid(message);
  }
}

async function api(path, body = null, method = "POST") {
  const options = { method, headers: { "Content-Type": "application/json", "X-CSRF-Token": BOOT.csrf } };
  if (body !== null) options.body = JSON.stringify(body);
  const response = await fetch(path, options);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(data.error || `HTTP ${response.status}`);
    error.status = response.status;
    error.data = data;
    throw error;
  }
  return data;
}

function showMessage(id, text, kind = "") {
  const el = document.getElementById(id);
  el.textContent = text || "";
  el.className = `message ${kind}`;
}

function setTab(name) {
  document.querySelectorAll(".tab").forEach((button) => button.classList.toggle("active", button.dataset.tab === name));
  document.querySelectorAll(".tab-panel").forEach((panel) => panel.classList.toggle("active", panel.id === `${name}Panel`));
}

let enrollCapture;
let enrollState = null;
let verifyCapture;
let verifyState = null;
let refreshCapture;
let refreshState = null;

async function beginEnrollmentSample() {
  const data = await api("/api/enrollment/begin", {
    username: enrollState.username,
    password: enrollState.password,
    password_confirm: enrollState.passwordConfirm,
  });
  enrollState.userId = data.user_id;
  enrollState.challenge = data.challenge;
  enrollState.count = data.count;
  renderEnrollState();
  enrollCapture.start(data.phrase);
}

async function startEnrollmentCapture() {
  enrollState.target = Math.min(Math.max(Number(document.getElementById("enrollTarget").value), BOOT.min_enrollment), BOOT.max_enrollment);
  await beginEnrollmentSample();
  document.getElementById("enrollForm").hidden = true;
  document.getElementById("enrollCancel").hidden = false;
  document.getElementById("enrollCapture").hidden = false;
  document.getElementById("enrollActiveUser").textContent = enrollState.username;
  enrollCapture.start(enrollCapture.phrase);
}

function renderEnrollState() {
  const count = enrollState.count;
  document.getElementById("enrollCounter").textContent = `${count} / ${enrollState.target}`;
  document.getElementById("enrollSampleNo").textContent = `${Math.min(count + 1, enrollState.target)} / ${enrollState.target}`;
  document.getElementById("enrollProgress").innerHTML = Array.from({ length: enrollState.target }, (_, i) =>
    `<span class="${i < count ? "done" : i === count ? "now" : ""}"></span>`).join("");
  document.getElementById("enrollFinish").disabled = count < BOOT.min_enrollment;
}

async function sendEnrollment(events) {
  try {
    const data = await api("/api/enrollment/sample", {
      user_id: enrollState.userId,
      challenge: enrollState.challenge,
      events,
    });
    enrollState.count = data.count;
    showMessage("enrollMessage", `Vzorka ${data.count} prijatá.`, "success");
    if (enrollState.count >= enrollState.target || enrollState.count >= BOOT.max_enrollment) {
      await finishEnrollment();
      return;
    }
    await beginEnrollmentSample();
  } catch (error) {
    showMessage("enrollMessage", error.message, "error");
    try { await beginEnrollmentSample(); } catch (_) { /* necháme správu o chybe */ }
  }
}

async function finishEnrollment() {
  try {
    const data = await api("/api/enrollment/finish", { user_id: enrollState.userId });
    enrollCapture.reset();
    document.getElementById("enrollCapture").hidden = true;
    document.getElementById("enrollCancel").hidden = true;
    document.getElementById("enrollForm").hidden = false;
    showMessage("enrollMessage", `Registrácia hotová. Šablóna má ${data.count} vzoriek, metrika ${data.metric}, prah ${data.threshold.toFixed(4)}.`, "success");
    document.getElementById("verifyUsername").value = enrollState.username;
    document.getElementById("verifyPassword").value = enrollState.password;
    enrollState = null;
  } catch (error) {
    showMessage("enrollMessage", error.message, "error");
  }
}

async function beginVerification() {
  const username = document.getElementById("verifyUsername").value.trim();
  const password = document.getElementById("verifyPassword").value;
  const testMode = document.getElementById("testMode").checked;
  verifyState = {
    username,
    password,
    testMode,
    actor: document.getElementById("actorLabel").value.trim(),
    attemptType: document.getElementById("attemptType").value,
  };
  const data = await api("/api/verification/begin", { username });
  verifyState.challenge = data.challenge;
  document.getElementById("verifyCapture").hidden = false;
  document.getElementById("verifyActiveUser").textContent = username;
  document.getElementById("resultCard").hidden = true;
  document.getElementById("refreshCard").hidden = true;
  document.getElementById("verifySubmit").disabled = true;
  showMessage("verifyMessage", "Challenge je aktívny. Prepíš vetu; preklepy môžeš opraviť Backspace-om.", "info");
  verifyCapture.start(data.phrase);
}

async function submitVerification(events) {
  try {
    const data = await api("/api/verification", {
      username: verifyState.username,
      password: verifyState.password,
      challenge: verifyState.challenge,
      events,
      test_mode: verifyState.testMode,
      actor_label: verifyState.actor,
      attempt_type: verifyState.attemptType,
    });
    renderResult(data);
  } catch (error) {
    if (error.status === 401 && error.data && error.data.result) {
      renderResult(error.data);
    } else {
      showMessage("verifyMessage", error.message, "error");
    }
    verifyCapture.reset();
  }
}

function renderResult(data) {
  document.getElementById("resultCard").hidden = false;
  document.getElementById("resultText").textContent = data.result || "ZAMIETNUTÝ";
  document.getElementById("resultScore").textContent = Number.isFinite(data.score) ? data.score.toFixed(6) : "—";
  document.getElementById("resultThreshold").textContent = Number.isFinite(data.threshold) ? data.threshold.toFixed(6) : "—";
  document.getElementById("resultMetric").textContent = data.metric || BOOT.metric;
  document.getElementById("adminLink").hidden = !data.accepted;
  const card = document.getElementById("resultCard");
  card.classList.toggle("accepted", !!data.accepted);
  card.classList.toggle("rejected", !data.accepted);
  const ratio = Number.isFinite(data.score) && Number.isFinite(data.threshold) && data.threshold > 0 ? data.score / data.threshold : 2;
  requestAnimationFrame(() => { document.getElementById("gaugeFill").style.width = `${Math.min(100, ratio * 50)}%`; });
  showMessage("verifyMessage", data.accepted ? "Oba faktory prešli." : "Biometrický faktor neprešiel.", data.accepted ? "success" : "error");
  document.getElementById("verifySubmit").disabled = true;
  document.getElementById("verifyCapture").hidden = true;
  if (data.accepted && data.refresh && data.refresh.due) showRefreshOffer(data.refresh);
}

// ---------- mesačná obnova šablóny ----------

function pct(value) {
  return Number.isFinite(value) ? `${value > 0 ? "+" : ""}${value.toFixed(1)} %` : "—";
}

function showRefreshOffer(info) {
  document.getElementById("refreshCard").hidden = false;
  document.getElementById("refreshReport").hidden = true;
  document.getElementById("refreshCapture").hidden = true;
  document.getElementById("refreshActions").hidden = false;
  document.getElementById("refreshText").textContent =
    `Šablóna je stará ${info.days_since} dní. Raz za ${info.interval_days} dní ju obnovíme ${info.needed} novými vetami, ` +
    "aby zodpovedala tomu, ako píšeš dnes. Systém zistí, či sa tvoje písanie zlepšilo alebo zhoršilo, a podľa toho upraví prah.";
  showMessage("refreshMessage", "");
  refreshState = { count: info.pending || 0, needed: info.needed, challenge: null };
}

function renderRefreshProgress() {
  const { count, needed } = refreshState;
  document.getElementById("refreshSampleNo").textContent = `${Math.min(count + 1, needed)} / ${needed}`;
  document.getElementById("refreshProgress").innerHTML = Array.from({ length: needed }, (_, i) =>
    `<span class="${i < count ? "done" : i === count ? "now" : ""}"></span>`).join("");
}

async function beginRefreshSample() {
  const data = await api("/api/refresh/begin", {});
  refreshState.challenge = data.challenge;
  refreshState.count = data.count;
  refreshState.needed = data.needed;
  renderRefreshProgress();
  refreshCapture.start(data.phrase);
}

async function sendRefresh(events) {
  try {
    const data = await api("/api/refresh/sample", { challenge: refreshState.challenge, events });
    refreshState.count = data.count;
    showMessage("refreshMessage", `Vzorka ${data.count} prijatá.`, "success");
    if (data.ready) {
      await finishRefresh();
      return;
    }
  } catch (error) {
    showMessage("refreshMessage", error.message, "error");
  }
  try { await beginRefreshSample(); } catch (error) { showMessage("refreshMessage", error.message, "error"); }
}

const VERDICTS = {
  improved: ["ok", "Zlepšenie", "Píšeš rýchlejšie alebo vyrovnanejšie ako minulý mesiac. Prah sa sprísnil, takže šablóna je presnejšia a systém bezpečnejší."],
  stable: ["neutral", "Stabilné", "Tvoj štýl písania sa nezmenil. Šablóna sa len doplnila novými vzorkami."],
  degraded: ["warn", "Zhoršenie", "Píšeš pomalšie alebo menej vyrovnane. Prah sa mierne uvoľnil, najviac o štvrtinu za jeden cyklus, aby ťa systém nezamietal."],
};

async function finishRefresh() {
  const r = await api("/api/refresh/finish", {});
  refreshCapture.reset();
  document.getElementById("refreshCapture").hidden = true;
  document.getElementById("refreshActions").hidden = true;
  const [kind, title, text] = VERDICTS[r.verdict] || VERDICTS.stable;
  const badge = document.getElementById("reportBadge");
  badge.className = `badge ${kind}`;
  badge.textContent = title;
  document.getElementById("reportTitle").textContent = "Šablóna je obnovená";
  let note = text;
  if (r.needs_reenrollment) note += " Zmena je väčšia, než sa dá bezpečne vyrovnať úpravou prahu. Odporúča sa nová registrácia.";
  else if (r.threshold_capped) note += " Prah dosiahol povolený strop.";
  document.getElementById("reportText").textContent = note;
  document.getElementById("reportSpeed").textContent = pct(r.speed_change_pct);
  document.getElementById("reportConsistency").textContent = pct(r.consistency_change_pct);
  document.getElementById("reportThreshold").textContent = `${r.old_threshold.toFixed(3)} → ${r.new_threshold.toFixed(3)}`;
  document.getElementById("refreshReport").hidden = false;
  showMessage("refreshMessage", "");
}

function setup() {
  fetch("/api/bootstrap").then((r) => r.json()).then((boot) => {
    BOOT = boot;
    document.getElementById("configPill").textContent = `${boot.metric} · k = ${boot.k}`;
    document.getElementById("testModeRow").hidden = !boot.test_mode_allowed;
    enrollCapture = new TypingCapture(document.getElementById("enrollTyping"), document.getElementById("enrollPhrase"), document.getElementById("enrollRhythm"), document.getElementById("enrollTempo"));
    verifyCapture = new TypingCapture(document.getElementById("verifyTyping"), document.getElementById("verifyPhrase"), document.getElementById("verifyRhythm"), document.getElementById("verifyTempo"));

    refreshCapture = new TypingCapture(document.getElementById("refreshTyping"), document.getElementById("refreshPhrase"), document.getElementById("refreshRhythm"), document.getElementById("refreshTempo"));
    refreshCapture.onComplete = sendRefresh;
    refreshCapture.onInvalid = (message) => showMessage("refreshMessage", message, "error");
    refreshCapture.onHint = (message) => showMessage("refreshMessage", message, "info");

    enrollCapture.onComplete = sendEnrollment;
    enrollCapture.onInvalid = (message) => {
      showMessage("enrollMessage", message, "error");
      renderEnrollState();
    };
    enrollCapture.onHint = (message) => showMessage("enrollMessage", message, "info");
    verifyCapture.onComplete = () => {
      document.getElementById("verifySubmit").disabled = false;
      showMessage("verifyMessage", "Veta je kompletná. Klikni na Vyhodnotiť pokus.", "info");
    };
    verifyCapture.onInvalid = (message) => {
      showMessage("verifyMessage", message, "error");
      document.getElementById("verifySubmit").disabled = true;
    };
    verifyCapture.onHint = (message) => showMessage("verifyMessage", message, "info");

    document.getElementById("enrollCounter").textContent = `0 / ${boot.min_enrollment}`;
    const select = document.getElementById("enrollTarget");
    select.innerHTML = [boot.min_enrollment, Math.round((boot.min_enrollment + boot.max_enrollment) / 2), boot.max_enrollment]
      .map((n, i) => `<option value="${n}" ${i === 1 ? "selected" : ""}>${n}</option>`).join("");
  }).catch((error) => showMessage("enrollMessage", `Bootstrap zlyhal: ${error.message}`, "error"));

  document.querySelectorAll(".tab").forEach((button) => button.addEventListener("click", () => setTab(button.dataset.tab)));

  document.getElementById("enrollForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    enrollState = {
      username: document.getElementById("enrollUsername").value.trim(),
      password: document.getElementById("enrollPassword").value,
      passwordConfirm: document.getElementById("enrollPasswordConfirm").value,
      target: Number(document.getElementById("enrollTarget").value),
      count: 0,
    };
    try {
      await startEnrollmentCapture();
      showMessage("enrollMessage", "Registrácia začala. Každá vzorka je nová veta.", "info");
    } catch (error) {
      showMessage("enrollMessage", error.message, "error");
      enrollState = null;
    }
  });

  document.getElementById("enrollRepeat").addEventListener("click", () => { enrollCapture.start(); });
  document.getElementById("enrollFinish").addEventListener("click", finishEnrollment);
  document.getElementById("enrollCancel").addEventListener("click", () => location.reload());

  document.getElementById("testMode").addEventListener("change", (event) => {
    const visible = event.target.checked;
    document.getElementById("actorField").hidden = !visible;
    document.getElementById("typeField").hidden = !visible;
    document.getElementById("verifyUsernameLabel").textContent = visible ? "Cieľový účet / koho napodobňuješ" : "Používateľské meno";
    document.getElementById("verifyConsent").checked = visible || document.getElementById("verifyConsent").checked;
  });

  document.getElementById("verifyForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      await beginVerification();
    } catch (error) {
      showMessage("verifyMessage", error.message, "error");
    }
  });

  document.getElementById("refreshStart").addEventListener("click", async () => {
    try {
      document.getElementById("refreshActions").hidden = true;
      document.getElementById("refreshCapture").hidden = false;
      await beginRefreshSample();
    } catch (error) {
      showMessage("refreshMessage", error.message, "error");
    }
  });
  document.getElementById("refreshLater").addEventListener("click", () => {
    document.getElementById("refreshCard").hidden = true;
  });

  document.getElementById("verifyRepeat").addEventListener("click", () => {
    document.getElementById("verifySubmit").disabled = true;
    verifyCapture.start();
  });
  document.getElementById("verifySubmit").addEventListener("click", () => {
    if (verifyCapture.complete) {
      document.getElementById("verifySubmit").disabled = true;
      submitVerification(verifyCapture.events);
    }
  });
}

document.addEventListener("DOMContentLoaded", setup);

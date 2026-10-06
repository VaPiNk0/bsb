let BOOT = null;

const ALPHABET = "abcdefghijklmnopqrstuvwxyz ";
const RETRY_DELAY_MS = 1100;   // koľko nechať svietiť dôvod, kým príde nová veta

const TAPE_MIN_MS = 8000;      // najkratší úsek, ktorý páska ukáže; dlhšia veta sa do nej zmestí celá

const $ = (id) => document.getElementById(id);

// Páska: každé stlačenie je značka, ktorej dĺžka je držanie klávesy a poloha čas stlačenia.
// Značky sa striedajú v dvoch riadkoch, takže je vidno aj prekryv dvoch kláves.
// `replay` ich nechá objaviť sa v čase, v akom boli stlačené.
function drawTape(el, strokes, span, replay = false) {
  const step = span > 20000 ? 5000 : span > 10000 ? 2000 : 1000;
  const nodes = [];
  for (let t = step; t < span; t += step) {
    const tick = document.createElement("i");
    tick.className = "tick";
    tick.dataset.t = `${t / 1000} s`;
    tick.style.left = `${(t / span) * 100}%`;
    nodes.push(tick);
  }
  strokes.forEach((stroke, i) => {
    const mark = document.createElement("i");
    mark.className = `mark${i % 2 ? " low" : ""}${stroke.kept === false ? " void" : ""}`;
    mark.dataset.key = stroke.key === " " ? "␣" : stroke.key;
    mark.style.left = `${(stroke.down / span) * 100}%`;
    mark.style.width = `${((stroke.up - stroke.down) / span) * 100}%`;
    if (replay) mark.style.animationDelay = `${Math.round(stroke.down)}ms`;
    nodes.push(mark);
  });
  el.classList.toggle("replay", replay);
  el.replaceChildren(...nodes);
}

// Ukážka v úvode: vymyslený, ale opakovateľný rytmus dvoch ľudí pri tej istej vete.
function demoStrokes(phrase, seed, hold, gap) {
  let state = seed;
  const random = () => (state = (state * 1664525 + 1013904223) >>> 0) / 4294967296;
  let t = 250;
  return Array.from(phrase, (key) => {
    const stroke = { key, down: t, up: t + hold * (0.7 + random() * 0.6) };
    t += gap * (0.55 + random() * 0.9) * (key === " " ? 1.5 : 1);
    return stroke;
  });
}

function drawDemo() {
  const phrase = t("rytmus pisania je ako podpis", "typing rhythm works like a signature");
  const a = demoStrokes(phrase, 7, 70, 125);
  const b = demoStrokes(phrase, 31, 150, 230);
  const span = Math.ceil((b[b.length - 1].up + 400) / 1000) * 1000;
  drawTape($("demoTapeA"), a, span, true);
  drawTape($("demoTapeB"), b, span, true);
}

// Plocha na prepis vety: veta je predpísaná bledo, napísané znaky sa do nej "vfarbujú"
// a kurzor sa posúva plynulo. Zároveň zbiera časovanie kláves (down/up) pre backend.
class TypingCapture {
  constructor(root, { rhythm = null, tempo = null } = {}) {
    this.root = root;
    this.rhythmEl = rhythm;
    this.tempoEl = tempo;
    this.text = document.createElement("div");
    this.text.className = "typing-text";
    this.caret = document.createElement("span");
    this.caret.className = "caret";
    this.text.append(this.caret);
    this.root.replaceChildren(this.text);

    this.phrase = "";
    this.cells = [];
    this.held = new Map();      // event.code -> index v this.events (klávesa je ešte dole)
    this.onComplete = null;
    this.onInvalid = null;
    this.onHint = null;
    this.onStart = null;        // prvé stlačenie v novej vete
    this.clear();
    this.bind();
  }

  bind() {
    this.root.addEventListener("keydown", (event) => this.keydown(event));
    this.root.addEventListener("keyup", (event) => this.keyup(event));
    this.root.addEventListener("paste", (event) => {
      event.preventDefault();
      this.invalid(t("Vkladanie textu nie je povolené.", "Pasting text is not allowed."));
    });
    this.root.addEventListener("drop", (event) => event.preventDefault());
    this.root.addEventListener("contextmenu", (event) => event.preventDefault());
    // Rozpísaná veta s prerušením by mala skreslené časy; nezačatú vetu prepnutie okna nekazí.
    window.addEventListener("blur", () => {
      if (this.active && this.started) this.invalid(t("Okno stratilo fokus uprostred vety.", "The window lost focus in the middle of the sentence."));
    });
    window.addEventListener("resize", () => this.moveCaret());
  }

  get active() { return this.state === "active"; }
  get started() { return this.events.length > 0; }

  clear() {
    this.events = [];
    this.buffer = [];
    this.corrections = 0;
    this.state = "idle";
    this.t0 = 0;
    this.held.clear();
    this.root.classList.remove("invalid", "complete", "busy");
  }

  // Nová veta: postaví znaky aj stĺpce grafu a začne zber.
  load(phrase) {
    this.clear();
    this.phrase = phrase;
    this.cells = Array.from(phrase, (ch) => {
      const span = document.createElement("span");
      span.className = "ch";
      span.textContent = ch;
      return span;
    });
    this.extra = document.createElement("span");
    this.extra.className = "ch bad";
    // kurzor je vnútri textu, aby mal rovnaký súradnicový systém ako znaky (aj počas animácie)
    this.text.replaceChildren(...this.cells, this.extra, this.caret);
    this.state = "active";
    this.t0 = performance.now();
    // znovu spustí animáciu príchodu vety
    this.root.classList.remove("swap");
    void this.root.offsetWidth;
    this.root.classList.add("swap");
    this.render();
    this.root.focus({ preventScroll: true });
  }

  stop() {
    this.clear();
  }

  // Čas udalosti berieme z event.timeStamp (kedy klávesu zaznamenal prehliadač), nie z okamihu,
  // keď sa dostal na rad handler: prekresľovanie po každej klávese by inak skresľovalo dwell/DD.
  stamp(event) {
    const t = event.timeStamp > 0 ? event.timeStamp : performance.now();
    return Math.max(0, t - this.t0);
  }

  // indexy stlačení, ktoré v texte zostali (po odrátaní opravených)
  survivors() {
    const stack = [];
    this.events.forEach((e, i) => { if (e.key === "Backspace") stack.pop(); else stack.push(i); });
    return stack;
  }

  render() {
    const typed = this.buffer.length;
    this.cells.forEach((cell, i) => {
      const want = this.phrase[i];
      if (i >= typed) {
        cell.className = "ch";
        cell.textContent = want;
      } else if (this.buffer[i] === want) {
        cell.className = "ch ok";
        cell.textContent = want;
      } else {
        cell.className = "ch bad";
        // namiesto medzery je napísané písmeno: ukážeme ho, inak by chybu nebolo vidno
        cell.textContent = want === " " ? this.buffer[i] : want;
      }
    });
    this.extra.textContent = this.buffer.slice(this.phrase.length).join("");
    this.moveCaret();
    this.paintRhythm();
  }

  moveCaret() {
    if (!this.cells.length) return;
    const typed = this.buffer.length;
    let x, y;
    if (typed < this.cells.length) {
      const cell = this.cells[typed];
      x = cell.offsetLeft;
      y = cell.offsetTop;
    } else {
      const last = this.extra.textContent ? this.extra : this.cells[this.cells.length - 1];
      x = last.offsetLeft + last.offsetWidth;
      y = last.offsetTop;
    }
    this.caret.style.transform = `translate(${x}px, ${y}px)`;
  }

  paintRhythm() {
    if (this.rhythmEl) {
      const kept = new Set(this.survivors());
      // klávesa, ktorá je ešte dole, má zatiaľ nulovú dĺžku
      // páska začína tesne pred prvým stlačením, nie čakaním, kým človek začne písať
      const origin = this.events.length ? this.events[0].down - 200 : 0;
      const strokes = this.events.map((e, i) => ({
        key: e.key === "Backspace" ? "←" : e.key,
        down: e.down - origin,
        up: (Number.isFinite(e.up) ? e.up : e.down) - origin,
        kept: kept.has(i),
      }));
      const end = strokes.length ? strokes[strokes.length - 1].up : 0;
      drawTape(this.rhythmEl, strokes, Math.max(TAPE_MIN_MS, end * 1.05));
    }
    if (this.tempoEl) {
      const typed = this.buffer.length;
      const first = this.events[0];
      const last = this.events[this.events.length - 1];
      let text = "—";
      if (typed > 3 && first && last && last.down > first.down) {
        const minutes = (last.down - first.down) / 60000;
        text = `${Math.round(typed / 5 / minutes)} ${t("slov/min", "wpm")}`;
      }
      if (this.corrections) text += `, ${t("opravy", "corrections")}: ${this.corrections}`;
      this.tempoEl.textContent = text;
    }
  }

  push(event, key) {
    if (!this.started && this.onStart) this.onStart();
    this.held.set(event.code, this.events.length);
    this.events.push({ key, code: event.code, down: this.stamp(event), up: null });
  }

  keydown(event) {
    // klávesnicová navigácia a funkčné klávesy (F5, F12) musia fungovať ďalej
    if (event.key === "Tab" || /^F\d+$/.test(event.key)) return;
    event.preventDefault();
    if (!this.active || event.repeat) return;
    if (event.ctrlKey || event.metaKey || event.altKey) {
      this.invalid(t("Klávesové skratky pokus zneplatnia.", "Keyboard shortcuts invalidate the attempt."));
      return;
    }
    if (event.key === "Shift" || event.key === "CapsLock") {
      this.invalid(t("Píš len malé písmená (bez Shiftu, vypni Caps Lock).", "Type lowercase letters only (no Shift, turn off Caps Lock)."));
      return;
    }

    if (event.key === "Backspace") {
      if (this.buffer.length === 0 || this.held.has(event.code)) return;
      if (this.corrections >= BOOT.max_corrections) {
        this.invalid(t("Príliš veľa opráv.", "Too many corrections."));
        return;
      }
      this.corrections += 1;
      this.push(event, "Backspace");
      this.buffer.pop();
      this.render();
      return;
    }

    // ostatné nepísmenové klávesy (šípky, Enter...) ticho ignorujeme
    if (event.key.length !== 1) return;
    if (!ALPHABET.includes(event.key)) {
      this.onHint?.(t("Používaj len malé písmená bez diakritiky a medzeru.", "Use only lowercase letters without accents and the space bar."));
      return;
    }
    if (this.buffer.length >= this.phrase.length + 6 || this.held.has(event.code)) return;
    this.push(event, event.key);
    this.buffer.push(event.key);
    this.render();
  }

  keyup(event) {
    if (!this.active) return;
    const index = this.held.get(event.code);
    if (index === undefined) return;
    this.events[index].up = Math.max(this.stamp(event), this.events[index].down);
    this.held.delete(event.code);
    this.paintRhythm();
    this.checkComplete();
  }

  checkComplete() {
    if (this.held.size !== 0 || this.buffer.join("") !== this.phrase) return;
    if (!this.events.every((e) => Number.isFinite(e.up))) return;
    this.state = "done";
    this.root.classList.add("complete", "busy");
    this.onComplete?.(this.events);
  }

  invalid(message) {
    if (!this.active) return;
    this.state = "invalid";
    this.onInvalid?.(message);
  }
}

// ---------- ochrana proti botom (Cloudflare Turnstile) ----------
// Widget sa zobrazí len vtedy, keď ho server má zapnutý. Token sa posiela s prvou požiadavkou
// registrácie/prihlásenia; server si potom reláciu chvíľu pamätá ako overenú.

const turnstileWidgets = {};

function renderTurnstile() {
  for (const name of ["enroll", "verify"]) {
    const box = $(`${name}Turnstile`);
    box.hidden = false;
    if (turnstileWidgets[name] !== undefined) window.turnstile.remove(turnstileWidgets[name]);
    turnstileWidgets[name] = window.turnstile.render(box, {
      sitekey: BOOT.turnstile_site_key, language: LANG, theme: document.documentElement.dataset.theme || "auto",
    });
  }
}

function setupTurnstile() {
  if (!BOOT.turnstile_site_key) return;
  window.onTurnstileLoad = renderTurnstile;
  // widget si tému drží od vykreslenia, po prepnutí ho treba vykresliť znova
  document.addEventListener("themechange", () => { if (window.turnstile) renderTurnstile(); });
  const script = document.createElement("script");
  script.src = "https://challenges.cloudflare.com/turnstile/v0/api.js?onload=onTurnstileLoad&render=explicit";
  script.async = true;
  document.head.append(script);
}

function turnstileToken(name) {
  const id = turnstileWidgets[name];
  return id === undefined ? "" : window.turnstile.getResponse(id) || "";
}

// Token je jednorazový: po použití alebo po odmietnutí si widget vypýta nový.
function resetTurnstile(name) {
  const id = turnstileWidgets[name];
  if (id !== undefined) window.turnstile.reset(id);
}

// ---------- pomocné ----------

async function api(path, body = null, method = "POST", retry = true) {
  const options = { method, headers: { "Content-Type": "application/json", "X-CSRF-Token": BOOT.csrf, "X-Lang": LANG } };
  if (body !== null) options.body = JSON.stringify(body);
  const response = await fetch(path, options);
  const data = await response.json().catch(() => ({}));
  // Relácia medzičasom zanikla (reštart servera, zmazané cookies): vypýtame si nový token a skúsime raz znova.
  if (response.status === 400 && retry && /CSRF/.test(data.error || "")) {
    const boot = await fetch("/api/bootstrap", { cache: "no-store" }).then((r) => r.json());
    BOOT.csrf = boot.csrf;
    return api(path, body, method, false);
  }
  if (!response.ok) {
    const error = new Error(data.error || `HTTP ${response.status}`);
    error.status = response.status;
    error.data = data;
    throw error;
  }
  return data;
}

function showMessage(id, text, kind = "") {
  const el = $(id);
  el.textContent = text || "";
  el.className = `${el.dataset.base || "message"} ${kind}`;
}

// Kým beží odoslanie formulára, ďalšie sa ignoruje: uspatý server odpovedá aj desiatky sekúnd
// a druhé kliknutie by spustilo tú istú registráciu či prihlásenie dvakrát naraz.
function submitOnce(form, action) {
  let pending = false;
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (pending) return;
    pending = true;
    const button = form.querySelector("[type=submit]");
    button.disabled = true;
    try {
      await action();
    } finally {
      pending = false;
      button.disabled = false;
    }
  });
}

function setTab(name) {
  document.querySelectorAll(".tab").forEach((button) => button.classList.toggle("active", button.dataset.tab === name));
  document.querySelectorAll(".tab-panel").forEach((panel) => panel.classList.toggle("active", panel.id === `${name}Panel`));
}

function segments(id, count, total) {
  $(id).innerHTML = Array.from({ length: total }, (_, i) =>
    `<span class="${i < count ? "done" : i === count ? "now" : ""}"></span>`).join("");
}

// Jeden "tok" písania (registrácia / prihlásenie / obnova): stará sa o to, aby po neúspešnom
// pokuse, zneplatnení alebo expirácii challenge prišla automaticky nová veta.
const NEW_SENTENCE = t("Pripravujem novú vetu…", "Getting a new sentence…");

class Flow {
  constructor(capture, statusId, hint, fetchPhrase) {
    this.capture = capture;
    this.statusId = statusId;
    this.hint = hint;
    this.fetchPhrase = fetchPhrase;   // async () => veta
    this.onStop = null;               // (správa) keď sa nová veta nedá získať
    this.generation = 0;              // zneplatní rozbehnuté načítania a časovače
    this.expiry = null;
    capture.onStart = () => this.status(this.hint);
    capture.onHint = (message) => this.status(message, "info");
    capture.onInvalid = (message) => this.retry(`${message} ${NEW_SENTENCE}`);
  }

  status(text, kind = "") { showMessage(this.statusId, text, kind); }

  // Zobrazí už načítanú vetu. `keepStatus` nechá na očiach dôvod, prečo sa veta zmenila.
  show(phrase, keepStatus = false) {
    this.capture.load(phrase);
    if (!keepStatus) this.status(this.hint);
    // Challenge má obmedzenú platnosť; kým človek nezačal písať, vetu vymeníme skôr, než vyprší.
    clearTimeout(this.expiry);
    this.expiry = setTimeout(() => {
      if (this.capture.active && !this.capture.started) this.next();
    }, Math.max(15, BOOT.challenge_ttl - 12) * 1000);
  }

  async next(keepStatus = false) {
    const generation = ++this.generation;
    clearTimeout(this.expiry);
    try {
      const phrase = await this.fetchPhrase();
      if (generation === this.generation) this.show(phrase, keepStatus);
    } catch (error) {
      if (generation !== this.generation) return;
      this.stop();
      this.onStop?.(error.message);
    }
  }

  // Ukáže dôvod a po chvíli (alebo keď sa okno vráti do popredia) dá novú vetu.
  retry(reason) {
    this.status(reason, "error");
    this.capture.root.classList.remove("busy", "complete");
    this.capture.root.classList.add("invalid");
    const generation = ++this.generation;
    clearTimeout(this.expiry);
    const go = () => { if (generation === this.generation) this.next(true); };
    if (document.hasFocus()) setTimeout(go, RETRY_DELAY_MS);
    else window.addEventListener("focus", go, { once: true });
  }

  stop() {
    this.generation += 1;
    clearTimeout(this.expiry);
    this.capture.stop();
  }
}

// ---------- registrácia ----------

let enrollFlow;
let enrollState = null;

async function fetchEnrollPhrase() {
  const data = await api("/api/enrollment/begin", {
    username: enrollState.username,
    password: enrollState.password,
    password_confirm: enrollState.passwordConfirm,
    turnstile: turnstileToken("enroll"),
  });
  enrollState.userId = data.user_id;
  enrollState.challenge = data.challenge;
  enrollState.count = data.count;
  renderEnrollState();
  return data.phrase;
}

function renderEnrollState() {
  const { count, target } = enrollState;
  $("enrollCounter").textContent = `${count} / ${target}`;
  $("enrollSampleNo").textContent = `${Math.min(count + 1, target)} / ${target}`;
  segments("enrollProgress", count, target);
  $("enrollFinish").disabled = count < BOOT.min_enrollment;
}

function showEnrollCapture(on) {
  $("enrollForm").hidden = on;
  $("enrollCapture").hidden = !on;
}

async function startEnrollment() {
  const target = Number($("enrollTarget").value);
  enrollState = {
    username: $("enrollUsername").value.trim(),
    password: $("enrollPassword").value,
    passwordConfirm: $("enrollPasswordConfirm").value,
    target: Math.min(Math.max(target, BOOT.min_enrollment), BOOT.max_enrollment),
    count: 0,
  };
  try {
    const phrase = await fetchEnrollPhrase();   // overí meno/heslo skôr, než prepneme obrazovku
    $("enrollActiveUser").textContent = enrollState.username;
    showMessage("enrollMessage", "");
    showEnrollCapture(true);
    enrollFlow.show(phrase);
  } catch (error) {
    showMessage("enrollMessage", error.message, "error");
    enrollState = null;
  }
  resetTurnstile("enroll");
}

async function sendEnrollment(events) {
  try {
    const data = await api("/api/enrollment/sample", {
      user_id: enrollState.userId,
      challenge: enrollState.challenge,
      events,
    });
    enrollState.count = data.count;
    renderEnrollState();
    if (data.count >= enrollState.target || data.count >= BOOT.max_enrollment) {
      await finishEnrollment();
      return;
    }
    enrollFlow.status(t(`Vzorka ${data.count} uložená. Pokračuj ďalšou vetou.`, `Sample ${data.count} saved. Continue with the next sentence.`), "success");
    await enrollFlow.next(true);
  } catch (error) {
    enrollFlow.retry(`${error.message} ${t("Táto vzorka sa nepočíta, pripravujem novú vetu…", "This sample does not count, getting a new sentence…")}`);
  }
}

async function finishEnrollment() {
  try {
    const data = await api("/api/enrollment/finish", { user_id: enrollState.userId });
    enrollFlow.stop();
    showEnrollCapture(false);
    $("enrollForm").reset();
    $("enrollCounter").textContent = `0 / ${BOOT.min_enrollment}`;
    // rovno na prihlásenie s predvyplneným účtom
    $("verifyUsername").value = enrollState.username;
    $("verifyPassword").value = enrollState.password;
    enrollState = null;
    setTab("verification");
    showMessage("verifyMessage", t(`Registrácia je hotová (${data.count} vzoriek, prah ${data.threshold.toFixed(3)}). Môžeš sa prihlásiť.`,
      `Sign-up is complete (${data.count} samples, threshold ${data.threshold.toFixed(3)}). You can log in now.`), "success");
  } catch (error) {
    enrollFlow.status(error.message, "error");
  }
}

function cancelEnrollment() {
  enrollFlow.stop();
  enrollState = null;
  showEnrollCapture(false);
  $("enrollCounter").textContent = `0 / ${BOOT.min_enrollment}`;
  showMessage("enrollMessage", t("Registrácia je prerušená. Uložené vzorky zostali; s rovnakým menom a heslom v nej môžeš pokračovať.", "Sign-up is paused. Saved samples are kept; you can continue with the same username and password."), "info");
}

// ---------- prihlásenie ----------

const BOTH_PASSED = t("Oba faktory prešli.", "Both factors passed.");

let verifyFlow;
let verifyState = null;

async function fetchVerifyPhrase() {
  const data = await api("/api/verification/begin", { username: verifyState.username, turnstile: turnstileToken("verify") });
  verifyState.challenge = data.challenge;
  return data.phrase;
}

function showVerifyCapture(on) {
  $("verifyForm").hidden = on;
  $("verifyCapture").hidden = !on;
}

// Návrat k formuláru (úspech, zlé heslo, zamknutý účet, limit pokusov, alebo to chce používateľ).
function endVerification(message = "", kind = "error") {
  verifyFlow.stop();
  showVerifyCapture(false);
  showMessage("verifyMessage", message, kind);
}

async function startVerification() {
  verifyState = {
    username: $("verifyUsername").value.trim(),
    password: $("verifyPassword").value,
    testMode: $("testMode").checked,
    actor: $("actorLabel").value.trim(),
    attemptType: $("attemptType").value,
  };
  if (verifyState.testMode && !verifyState.actor) {
    showMessage("verifyMessage", t("V testovacom móde vyplň Tester ID.", "Fill in the tester ID in test mode."), "error");
    $("actorLabel").focus();
    return;
  }
  try {
    const phrase = await fetchVerifyPhrase();
    $("verifyActiveUser").textContent = verifyState.testMode
      ? `${verifyState.username}, ${verifyState.attemptType} (${verifyState.actor})`
      : verifyState.username;
    $("resultCard").hidden = true;
    $("refreshCard").hidden = true;
    showMessage("verifyMessage", "");
    showVerifyCapture(true);
    verifyFlow.show(phrase);
  } catch (error) {
    showMessage("verifyMessage", error.message, "error");
  }
  resetTurnstile("verify");
}

async function submitVerification(events) {
  verifyFlow.status(t("Vyhodnocujem…", "Evaluating…"), "info");
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
    endVerification(BOTH_PASSED, "success");
    const refreshDue = !!(data.refresh && data.refresh.due);
    if (refreshDue) showRefreshOffer(data.refresh);
    // bežný používateľ po overení odchádza na internet; ponuka obnovy šablóny má prednosť
    leaveTo = data.redirect || null;
    if (!refreshDue) leaveAfterLogin("verifyMessage", BOTH_PASSED);
  } catch (error) {
    const data = error.data || {};
    if (error.status === 401 && data.result) {
      // biometria neprešla: výsledok ukážeme a rovno pripravíme ďalší pokus s inou vetou
      renderResult(data);
      const why = Number.isFinite(data.score)
        ? t(`Rytmus nesedel (skóre ${data.score.toFixed(3)}, prah ${data.threshold.toFixed(3)}).`,
          `The rhythm did not match (score ${data.score.toFixed(3)}, threshold ${data.threshold.toFixed(3)}).`)
        : data.error || t("Pokus bol zamietnutý.", "The attempt was rejected.");
      verifyFlow.retry(`${why} ${t("Skús to znova s novou vetou.", "Try again with a new sentence.")}`);
    } else if (error.status === 400) {
      // expirovaný challenge, príliš málo použiteľných kláves a pod.: stačí nová veta
      verifyFlow.retry(`${error.message} ${NEW_SENTENCE}`);
    } else {
      // zlé heslo, zamknutý účet alebo limit pokusov: ďalšie písanie by nepomohlo
      $("resultCard").hidden = true;
      endVerification(error.status === 401
        ? t("Prihlásenie zlyhalo. Skontroluj meno a heslo. Ak registrácia nie je dokončená, dokonči ju najprv na karte Registrácia.",
          "Login failed. Check the username and password. If the sign-up is not finished, finish it on the Sign up tab first.")
        : error.message);
      if (error.status === 401) $("verifyPassword").select();
    }
  }
}

function renderResult(data) {
  const card = $("resultCard");
  const hasScore = Number.isFinite(data.score) && Number.isFinite(data.threshold) && data.threshold > 0;
  $("resultText").textContent = data.accepted ? t("AUTENTIFIKOVANÝ", "AUTHENTICATED") : t("ZAMIETNUTÝ", "REJECTED");
  $("resultScore").textContent = Number.isFinite(data.score) ? data.score.toFixed(3) : "—";
  $("resultThreshold").textContent = Number.isFinite(data.threshold) ? data.threshold.toFixed(3) : "—";
  $("resultMetric").textContent = data.metric || BOOT.metric;
  $("adminLink").hidden = !data.is_admin;
  card.classList.toggle("accepted", !!data.accepted);
  card.classList.toggle("rejected", !data.accepted);
  // skryť a znova ukázať: animácia príchodu sa prehrá pri každom výsledku
  card.hidden = true;
  void card.offsetWidth;
  card.hidden = false;
  const fill = $("gaugeFill");
  fill.style.width = "0";
  const ratio = hasScore ? data.score / data.threshold : 2;
  requestAnimationFrame(() => requestAnimationFrame(() => { fill.style.width = `${Math.min(100, ratio * 50)}%`; }));
}

const LEAVE_DELAY_MS = 2500;   // nech si človek stihne prečítať výsledok
let leaveTo = null;

function leaveAfterLogin(messageId, text, delay = LEAVE_DELAY_MS) {
  if (!leaveTo) return;
  const url = leaveTo;
  leaveTo = null;
  const host = new URL(url).hostname;
  showMessage(messageId, `${text} ${t(`O chvíľu ťa presmerujem na ${host}.`, `Redirecting you to ${host} in a moment.`)}`, "success");
  setTimeout(() => location.assign(url), delay);
}

// ---------- mesačná obnova šablóny ----------

let refreshFlow;
let refreshState = null;

function pct(value) {
  return Number.isFinite(value) ? `${value > 0 ? "+" : ""}${value.toFixed(1)} %` : "—";
}

function showRefreshOffer(info) {
  $("refreshCard").hidden = false;
  $("refreshReport").hidden = true;
  $("refreshCapture").hidden = true;
  $("refreshActions").hidden = false;
  $("refreshText").textContent =
    t(`Šablóna je stará ${info.days_since} dní. Raz za ${info.interval_days} dní ju obnovíme ${info.needed} novými vetami, ` +
      "aby zodpovedala tomu, ako píšeš dnes. Systém zistí, či sa tvoje písanie zlepšilo alebo zhoršilo, a podľa toho upraví prah.",
      `The template is ${info.days_since} days old. Every ${info.interval_days} days we refresh it with ${info.needed} new sentences ` +
      "so it matches how you type today. The system checks whether your typing got better or worse and adjusts the threshold.");
  showMessage("refreshMessage", "");
  refreshState = { count: info.pending || 0, needed: info.needed, challenge: null };
}

function renderRefreshProgress() {
  const { count, needed } = refreshState;
  $("refreshSampleNo").textContent = `${Math.min(count + 1, needed)} / ${needed}`;
  segments("refreshProgress", count, needed);
}

async function fetchRefreshPhrase() {
  const data = await api("/api/refresh/begin", {});
  refreshState.challenge = data.challenge;
  refreshState.count = data.count;
  refreshState.needed = data.needed;
  renderRefreshProgress();
  return data.phrase;
}

async function sendRefresh(events) {
  try {
    const data = await api("/api/refresh/sample", { challenge: refreshState.challenge, events });
    refreshState.count = data.count;
    renderRefreshProgress();
    if (data.ready) {
      await finishRefresh();
      return;
    }
    refreshFlow.status(t(`Vzorka ${data.count} uložená. Pokračuj ďalšou vetou.`, `Sample ${data.count} saved. Continue with the next sentence.`), "success");
    await refreshFlow.next(true);
  } catch (error) {
    refreshFlow.retry(`${error.message} ${NEW_SENTENCE}`);
  }
}

const VERDICTS = {
  improved: ["ok", t("Zlepšenie", "Improved"), t("Píšeš rýchlejšie alebo vyrovnanejšie ako minulý mesiac. Prah sa sprísnil, takže šablóna je presnejšia a systém bezpečnejší.",
    "You type faster or more evenly than last month. The threshold got stricter, so the template is more precise and the system safer.")],
  stable: ["neutral", t("Stabilné", "Stable"), t("Tvoj štýl písania sa nezmenil. Šablóna sa len doplnila novými vzorkami.",
    "Your typing style has not changed. The template was only topped up with new samples.")],
  degraded: ["warn", t("Zhoršenie", "Declined"), t("Píšeš pomalšie alebo menej vyrovnane. Prah sa mierne uvoľnil, najviac o štvrtinu za jeden cyklus, aby ťa systém nezamietal.",
    "You type slower or less evenly. The threshold was relaxed slightly, by a quarter per cycle at most, so the system does not reject you.")],
};

async function finishRefresh() {
  try {
    const r = await api("/api/refresh/finish", {});
    refreshFlow.stop();
    $("refreshCapture").hidden = true;
    $("refreshActions").hidden = true;
    const [kind, title, text] = VERDICTS[r.verdict] || VERDICTS.stable;
    const badge = $("reportBadge");
    badge.className = `badge ${kind}`;
    badge.textContent = title;
    $("reportTitle").textContent = t("Šablóna je obnovená", "The template is refreshed");
    let note = text;
    if (r.needs_reenrollment) note += t(" Zmena je väčšia, než sa dá bezpečne vyrovnať úpravou prahu. Odporúča sa nová registrácia.",
      " The change is larger than a threshold adjustment can safely absorb. A new sign-up is recommended.");
    else if (r.threshold_capped) note += t(" Prah dosiahol povolený strop.", " The threshold reached its allowed ceiling.");
    $("reportText").textContent = note;
    $("reportSpeed").textContent = pct(r.speed_change_pct);
    $("reportConsistency").textContent = pct(r.consistency_change_pct);
    $("reportThreshold").textContent = `${r.old_threshold.toFixed(3)} → ${r.new_threshold.toFixed(3)}`;
    $("refreshReport").hidden = false;
    showMessage("refreshMessage", "");
    leaveAfterLogin("refreshMessage", t("Šablóna je obnovená.", "The template is refreshed."), LEAVE_DELAY_MS * 3);
  } catch (error) {
    refreshFlow.status(error.message, "error");
  }
}

// ---------- štart ----------

function buildFlows() {
  const capture = (name) => new TypingCapture($(`${name}Typing`), { rhythm: $(`${name}Rhythm`), tempo: $(`${name}Tempo`) });

  enrollFlow = new Flow(capture("enroll"), "enrollHelp",
    t("Prepíš vetu malými písmenami bez diakritiky. Preklepy oprav Backspace-om.", "Type the sentence in lowercase. Fix typos with Backspace."), fetchEnrollPhrase);
  enrollFlow.capture.onComplete = sendEnrollment;
  enrollFlow.onStop = (message) => {
    showEnrollCapture(false);
    showMessage("enrollMessage", message, "error");
  };

  verifyFlow = new Flow(capture("verify"), "verifyHelp",
    t("Prepíš vetu svojím bežným tempom. Po poslednom znaku sa pokus vyhodnotí sám.", "Type the sentence at your usual pace. The attempt is evaluated after the last character."), fetchVerifyPhrase);
  verifyFlow.capture.onComplete = submitVerification;
  verifyFlow.onStop = (message) => endVerification(message);

  refreshFlow = new Flow(capture("refresh"), "refreshHelp",
    t("Prepíš vetu. Vzorka, ktorá sa príliš líši od šablóny, sa nepočíta.", "Type the sentence. A sample that differs too much from the template does not count."), fetchRefreshPhrase);
  refreshFlow.capture.onComplete = sendRefresh;
  refreshFlow.onStop = (message) => {
    $("refreshCapture").hidden = true;
    $("refreshActions").hidden = false;
    showMessage("refreshMessage", message, "error");
  };
}

// Telefón server odmieta (rovnaký test hlavičky User-Agent je v backend/app.py), preto mu
// namiesto aplikácie ukážeme len zamietaciu obrazovku.
function isPhone() {
  return navigator.userAgentData?.mobile ?? /Mobi|iPhone|iPod/.test(navigator.userAgent);
}

function setup() {
  if (isPhone()) {
    document.querySelectorAll("main > :not(.topbar), #configPill").forEach((el) => { el.hidden = el.id !== "phoneBlock"; });
    return;
  }
  drawDemo();
  // Systém meria držanie kláves, čo dotyková klávesnica neposkytuje (a plocha na písanie ju ani neotvorí).
  $("touchNotice").hidden = !matchMedia("(hover: none) and (pointer: coarse)").matches;
  fetch("/api/bootstrap").then((r) => r.json()).then((boot) => {
    BOOT = boot;
    $("configPill").textContent = `${t("metrika", "metric")} ${boot.metric}, k = ${boot.k}`;
    $("testModeRow").hidden = !boot.test_mode_allowed;
    buildFlows();
    setupTurnstile();
    $("enrollCounter").textContent = `0 / ${boot.min_enrollment}`;
    $("enrollTarget").innerHTML = [boot.min_enrollment, Math.round((boot.min_enrollment + boot.max_enrollment) / 2), boot.max_enrollment]
      .map((n, i) => `<option value="${n}" ${i === 1 ? "selected" : ""}>${n}</option>`).join("");
  }).catch((error) => showMessage("enrollMessage", `${t("Načítanie konfigurácie zlyhalo", "Loading the configuration failed")}: ${error.message}`, "error"));

  document.querySelectorAll(".tab").forEach((button) => button.addEventListener("click", () => setTab(button.dataset.tab)));

  submitOnce($("enrollForm"), startEnrollment);
  $("enrollSkip").addEventListener("click", () => enrollFlow.next());
  $("enrollFinish").addEventListener("click", finishEnrollment);
  $("enrollCancel").addEventListener("click", cancelEnrollment);

  $("testMode").addEventListener("change", (event) => {
    const on = event.target.checked;
    $("actorField").hidden = !on;
    $("typeField").hidden = !on;
    $("verifyUsernameLabel").textContent = on ? t("Cieľový účet / koho napodobňuješ", "Target account / who you imitate") : t("Používateľské meno", "Username");
    if (on) $("verifyConsent").checked = true;
  });
  submitOnce($("verifyForm"), startVerification);
  $("verifyNew").addEventListener("click", () => verifyFlow.next());
  $("verifyBack").addEventListener("click", () => endVerification());

  $("refreshStart").addEventListener("click", () => {
    $("refreshActions").hidden = true;
    $("refreshCapture").hidden = false;
    refreshFlow.next();
  });
  $("refreshLater").addEventListener("click", () => {
    $("refreshCard").hidden = true;
    leaveAfterLogin("verifyMessage", BOTH_PASSED, 800);
  });
}

document.addEventListener("DOMContentLoaded", setup);

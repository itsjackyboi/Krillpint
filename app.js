/* DeepPint: a daily Pintland Isles trivia game. No framework, no build step.
   Data comes from /data (built by scripts/build.py). Progress lives in localStorage. */
(() => {
  "use strict";

  const CFG = window.DEEP_POUR_CONFIG || {};
  const TEST_MODE = Boolean(CFG.testMode) || location.hash === "#test";
  const STATE_PREFIX = "deep-pour:v1:";
  const TEST_DATE_KEY = "deep-pour:test-date";
  const MAX_ATTEMPTS = 3; // first try + 2 retries
  const QUESTION_SECONDS = 25;
  const GEM_MULTIPLIER = 1.5;
  // One emoji per question in the share message, by the depth that answer reached.
  const LAYERS = [
    { min: 80, emoji: "💎" }, // rarest depths
    { min: 60, emoji: "🦑" }, // deep water
    { min: 40, emoji: "🐟" }, // shallows
    { min: 20, emoji: "🍺" }, // the pint
    { min: 0, emoji: "🫧" },  // foam: a valid answer everyone knows
  ];
  const layerEmoji = (r) => (r.answer ? LAYERS.find((l) => r.score >= l.min).emoji : "❌"); // ❌ missed, passed or out of time

  const TIERS = [
    { min: 0, name: "Sea Foam", line: "Barely past the head. Everyone knows these." },
    { min: 15, name: "The Whiskey Shallows", line: "Wading depth. A few locals nod along." },
    { min: 30, name: "Gulf of Aleforge", line: "Harbor water. The pirates have heard of you." },
    { min: 45, name: "Dead Man Hallows", line: "Down among the wrecks. Scholars take notes." },
    { min: 60, name: "Bottomless Bay", line: "Past the bridge lights. Hoegaarden wants your library card." },
    { min: 75, name: "The Labyrinth Below", line: "Under the catacombs. The walls are breathing." },
    { min: 90, name: "The Pour Eternal", line: "The deepest drop there is. Speak the right name." },
  ];

  const $ = (sel) => document.querySelector(sel);
  const main = $("#main");
  const reduceMotion = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  let prompts = null;
  let lexicon = {};
  const answerCache = {};
  let ctx = null; // { date, dayIndex, picks, cats, state, isToday }
  let ui = {}; // transient per-prompt UI state
  let countdownTimer = null;
  let questionTimer = null;

  /* ---------- storage (never trusted to exist) ---------- */
  const store = {
    get(key, fallback = null) {
      try { const v = localStorage.getItem(key); return v == null ? fallback : JSON.parse(v); } catch { return fallback; }
    },
    set(key, value) { try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* private mode */ } },
    remove(key) { try { localStorage.removeItem(key); } catch { /* ignore */ } },
  };
  const session = {
    get(key) { try { return sessionStorage.getItem(key); } catch { return null; } },
    set(key, v) { try { sessionStorage.setItem(key, v); } catch { /* ignore */ } },
  };

  /* ---------- text ---------- */
  const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  // Must match normalize() in scripts/lib.py.
  function normalize(s) {
    let t = String(s).normalize("NFKD").replace(/[̀-ͯ]/g, "").toLowerCase();
    t = t.replace(/[‘’`]/g, "'").replace(/'/g, "").replace(/&/g, " and ");
    t = t.replace(/[^a-z0-9]+/g, " ").trim();
    for (const art of ["the ", "a ", "an "]) {
      if (t.startsWith(art) && t.length > art.length) { t = t.slice(art.length); break; }
    }
    return t;
  }

  function levenshtein(a, b) {
    if (a === b) return 0;
    let prev = Array.from({ length: b.length + 1 }, (_, i) => i);
    for (let i = 1; i <= a.length; i++) {
      const cur = [i];
      for (let j = 1; j <= b.length; j++) {
        cur[j] = Math.min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (a[i - 1] !== b[j - 1] ? 1 : 0));
      }
      prev = cur;
    }
    return prev[b.length];
  }
  const similarity = (a, b) => 1 - levenshtein(a, b) / Math.max(a.length, b.length, 1);

  /* ---------- dates (the day rolls over at midnight US Eastern) ---------- */
  function easternParts(date = new Date()) {
    try {
      const parts = new Intl.DateTimeFormat("en-US", {
        timeZone: prompts?.timezone || "America/New_York",
        year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23",
      }).formatToParts(date);
      const get = (t) => parts.find((p) => p.type === t).value;
      return { iso: `${get("year")}-${get("month")}-${get("day")}`, h: +get("hour"), m: +get("minute"), s: +get("second") };
    } catch {
      const d = date.toISOString();
      return { iso: d.slice(0, 10), h: date.getUTCHours(), m: date.getUTCMinutes(), s: date.getUTCSeconds() };
    }
  }
  const toUTC = (iso) => { const [y, m, d] = iso.split("-").map(Number); return Date.UTC(y, m - 1, d); };
  const daysBetween = (a, b) => Math.round((toUTC(b) - toUTC(a)) / 864e5);
  const addDays = (iso, n) => new Date(toUTC(iso) + n * 864e5).toISOString().slice(0, 10);
  const prettyDate = (iso) => new Date(toUTC(iso)).toLocaleDateString("en-GB", { weekday: "short", day: "numeric", month: "short", timeZone: "UTC" });

  /* ---------- daily lineup (precomputed by scripts/build.py) ---------- */
  let schedule = null;
  function lineupFor(dayIndex) {
    const days = schedule.days;
    return days[dayIndex] || days[dayIndex % days.length]; // past the horizon: reuse, never crash
  }

  /* ---------- data ---------- */
  async function getJSON(path) {
    const res = await fetch(path, { cache: "no-cache" });
    if (!res.ok) throw new Error(`${path}: ${res.status}`);
    return res.json();
  }
  async function loadCategory(id) {
    if (!answerCache[id]) {
      const cat = await getJSON(`data/answers/${id}.json`);
      cat.index = new Map();
      for (const a of cat.answers) {
        for (const alias of [a.name, ...a.aliases]) {
          const k = normalize(alias);
          if (k && !cat.index.has(k)) cat.index.set(k, a);
        }
      }
      answerCache[id] = cat;
    }
    return answerCache[id];
  }

  /* ---------- matching ---------- */
  function matchAnswer(input, cat) {
    const n = normalize(input);
    if (!n) return { type: "empty" };
    const exact = cat.index.get(n);
    if (exact) return { type: "exact", answer: exact };

    let best = null, second = null;
    for (const [key, answer] of cat.index) {
      let s = similarity(n, key);
      if (n.length >= 4 && ` ${key} `.includes(` ${n} `)) s = Math.max(s, 0.8); // "thicket" -> "thorny thicket"
      if (!best || s > best.s) {
        if (best && best.answer !== answer) second = best;
        best = { s, answer };
      } else if (answer !== best.answer && (!second || s > second.s)) {
        second = { s, answer };
      }
    }
    if (best) {
      const clear = !second || best.s - second.s >= 0.08;
      if (best.s >= 0.92 && n.length >= 6 && clear) return { type: "exact", answer: best.answer };
      if (best.s >= 0.72) return { type: "confirm", answer: best.answer };
    }
    if (lexicon[n]) return { type: "wrong" };
    return { type: "miss" };
  }

  /* ---------- state ---------- */
  const stateKey = (date) => STATE_PREFIX + date;
  function freshState(date, picks) {
    return { date, picks, started: false, idx: 0, results: [], misses: [0, 0, 0, 0, 0], clock: [], guesses: [[], [], [], [], []], done: false };
  }
  function saveState() { store.set(stateKey(ctx.date), ctx.state); }

  const totalScore = (results) => results.reduce((sum, r) => sum + (r ? r.score : 0), 0);
  const fathomsFor = (total) => Math.round(total / 5);
  const tierFor = (f) => TIERS.reduce((acc, t) => (f >= t.min ? t : acc), TIERS[0]);
  const DIFF_LABEL = { easy: "Easy", medium: "Medium", hard: "Hard" };

  /* ---------- small UI helpers ---------- */
  function announce(text) { const el = $("#announce"); el.textContent = ""; setTimeout(() => { el.textContent = text; }, 30); }
  function setView(html, focusSel) {
    clearInterval(countdownTimer);
    clearInterval(questionTimer);
    main.innerHTML = html;
    const target = focusSel ? main.querySelector(focusSel) : null;
    if (target) target.focus({ preventScroll: false });
  }
  function countUp(el, to, ms = 1200) {
    if (reduceMotion || to === 0) { el.textContent = to; return; }
    const start = performance.now();
    const step = (now) => {
      const p = Math.min(1, (now - start) / ms);
      el.textContent = Math.round(to * (1 - Math.pow(1 - p, 3)));
      if (p < 1) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  }

  function renderTracker() {
    const el = $("#tracker");
    el.innerHTML = ctx.picks.map((_, i) => {
      const r = ctx.state.results[i];
      const cls = [r ? "done" : "", r && !r.answer ? "miss" : "", !ctx.state.done && i === ctx.state.idx && ctx.state.started ? "current" : ""].join(" ").trim();
      const label = r ? `Pour ${i + 1}: ${r.score} depth` : `Pour ${i + 1}: not poured yet`;
      return `<li class="${cls}" aria-label="${esc(label)}"><span class="fill" style="height:${r ? Math.min(100, Math.max(4, r.score)) : 0}%"></span><span class="num">${r ? r.score : i + 1}</span></li>`;
    }).join("");
  }

  function gaugeHTML(fromF, toF) {
    const ticks = [0, 20, 40, 60, 80, 100].map((v) => {
      const shift = v === 0 ? "0" : v === 100 ? "-100%" : "-50%";
      return `<span style="top:${v}%;transform:translateY(${shift})">${v}</span>`;
    }).join("");
    const bubbles = reduceMotion ? "" : [18, 46, 70].map((x, i) => `<span class="bubble" style="left:${x}%;animation-delay:${i * 1.1}s"></span>`).join("");
    fromF = Math.min(100, fromF);
    toF = Math.min(100, toF); // gem bonuses can push past the bottom of the glass
    return `<div class="gauge${fromF === 0 ? " from-top" : ""}" style="--from:${fromF}%;--to:${toF}%" aria-hidden="true">
      <div class="ticks">${ticks}</div>
      <div class="glass"><span class="ghost-mark"></span><span class="line"></span><span class="bob"></span>${bubbles}</div>
    </div>`;
  }
  function sinkGauge(animate) {
    const g = main.querySelector(".gauge");
    if (!g) return;
    if (!animate || reduceMotion) { g.querySelectorAll(".line,.bob").forEach((n) => { n.style.transition = "none"; }); g.classList.add("sunk"); return; }
    requestAnimationFrame(() => requestAnimationFrame(() => g.classList.add("sunk")));
  }

  /* ---------- views ---------- */
  function renderIntro() {
    const lineup = ctx.cats.map((c) => DIFF_LABEL[c.difficulty]).join(" · ");
    setView(`
      <section class="card" aria-labelledby="intro-title">
        <p class="eyebrow">Pour #${ctx.dayIndex + 1} · ${esc(prettyDate(ctx.date))}</p>
        <h2 class="title" id="intro-title">How deep do you know the Isles?</h2>
        <ol class="rules">
          <li>Five prompts, the same for everyone today. One answer each.</li>
          <li>You get ${QUESTION_SECONDS} seconds per prompt, starting the moment it appears. Run out and it scores zero.</li>
          <li>Obvious answers barely count. The rarer your answer is in the lore, the deeper it scores.</li>
          <li>Miss, and you get two more tries. Then the pour scores zero.</li>
          <li>Each prompt hides one gem. Land it and that answer scores ${GEM_MULTIPLIER}×.</li>
        </ol>
        <p class="blurb">Today's pours: ${esc(lineup)}.</p>
        <div class="row"><button type="button" class="btn" id="start">Start pouring</button></div>
      </section>`);
    $("#start").addEventListener("click", () => {
      ctx.state.started = true;
      saveState();
      renderTracker();
      renderPrompt();
    });
  }

  // The clock starts the first time a question is shown and is stored with the day's
  // progress, so reloading or leaving the page doesn't stop it.
  function secondsLeft(i) {
    const startedAt = ctx.state.clock[i];
    if (!startedAt) return QUESTION_SECONDS;
    return Math.max(0, QUESTION_SECONDS - (Date.now() - startedAt) / 1000);
  }

  function renderPrompt() {
    const i = ctx.state.idx;
    const cat = ctx.cats[i];
    if (!ctx.state.clock) ctx.state.clock = [];
    if (!ctx.state.clock[i]) { ctx.state.clock[i] = Date.now(); saveState(); }
    if (secondsLeft(i) <= 0) { finalize({ status: "timeout", answer: null, score: 0, input: "" }); return; }
    const left = MAX_ATTEMPTS - ctx.state.misses[i];
    const msg = ui.message ? `<p class="notice" role="alert">${ui.message}</p>` : "";
    const confirm = ui.confirm ? `
      <div class="confirm" role="group" aria-labelledby="confirm-q">
        <p id="confirm-q">Did you mean <strong>${esc(ui.confirm.name)}</strong>?</p>
        <div class="row">
          <button type="button" class="btn small" id="confirm-yes">Yes, pour it</button>
          <button type="button" class="btn ghost small" id="confirm-no">No, let me retype</button>
        </div>
      </div>` : "";
    setView(`
      <section class="card" aria-labelledby="prompt-title">
        <div class="prompt-head">
          <p class="eyebrow">Pour ${i + 1} of 5 · ${DIFF_LABEL[cat.difficulty]}</p>
          <p class="clock" id="clock" role="timer" aria-label="Seconds left"><span id="clock-num">${Math.ceil(secondsLeft(i))}</span>s</p>
        </div>
        <div class="clock-bar" aria-hidden="true"><span id="clock-bar" style="width:${(secondsLeft(i) / QUESTION_SECONDS) * 100}%"></span></div>
        <h2 class="prompt" id="prompt-title">${esc(cat.prompt)}</h2>
        <p class="blurb">${esc(cat.blurb)}</p>
        <form class="answer-form" id="answer-form" autocomplete="off" novalidate>
          <label for="answer">Your answer</label>
          <div class="input-row">
            <input type="text" id="answer" name="answer" maxlength="80" spellcheck="false" autocapitalize="words" placeholder="Type a name" value="${esc(ui.draft || "")}">
            <button type="submit" class="btn">Pour</button>
          </div>
          <p class="tries">${left === MAX_ATTEMPTS ? "3 tries" : `${left} ${left === 1 ? "try" : "tries"} left`} · ${cat.answerCount} valid answers in the lore</p>
        </form>
        ${confirm}
        ${msg}
        <button type="button" class="linklike" id="pass">${ui.passArmed ? "Tap again to pass and score 0" : "Pass on this one"}</button>
      </section>`, ui.confirm ? "#confirm-yes" : "#answer");

    $("#answer-form").addEventListener("submit", (e) => { e.preventDefault(); submitAnswer($("#answer").value); });
    $("#answer").addEventListener("input", (e) => { ui.draft = e.target.value; });
    $("#pass").addEventListener("click", () => {
      if (!ui.passArmed) { ui.passArmed = true; renderPrompt(); clearTimeout(ui.passTimer); ui.passTimer = setTimeout(() => { ui.passArmed = false; if (!ctx.state.results[i]) renderPrompt(); }, 4000); return; }
      finalize({ status: "passed", answer: null, score: 0, input: "" });
    });
    if (ui.confirm) {
      $("#confirm-yes").addEventListener("click", () => {
        const a = ui.confirm.answer;
        finalize({ status: "scored", answer: a.name, score: scoreFor(a, cat), input: ui.confirm.input });
      });
      $("#confirm-no").addEventListener("click", () => { ui.confirm = null; ui.message = ""; renderPrompt(); });
    }
    startQuestionClock(i);
  }

  function startQuestionClock(i) {
    const num = $("#clock-num");
    const bar = $("#clock-bar");
    const clock = $("#clock");
    let lastWhole = Math.ceil(secondsLeft(i));
    const tick = () => {
      if (ctx.state.results[i]) { clearInterval(questionTimer); return; }
      const left = secondsLeft(i);
      const whole = Math.ceil(left);
      num.textContent = whole;
      bar.style.width = `${(left / QUESTION_SECONDS) * 100}%`;
      clock.classList.toggle("urgent", left <= 5);
      bar.parentElement.classList.toggle("urgent", left <= 5);
      if (whole !== lastWhole && (whole === 10 || whole === 5)) announce(`${whole} seconds left`);
      lastWhole = whole;
      if (left <= 0) {
        clearInterval(questionTimer);
        finalize({ status: "timeout", answer: null, score: 0, input: $("#answer")?.value.trim() || "" });
      }
    };
    clearInterval(questionTimer);
    questionTimer = setInterval(tick, 200);
    tick();
  }

  const scoreFor = (answer, cat) => (answer.name === cat.gem ? Math.round(answer.score * GEM_MULTIPLIER) : answer.score);

  function submitAnswer(raw) {
    const i = ctx.state.idx;
    const cat = ctx.cats[i];
    const input = raw.trim();
    ui.confirm = null;
    ui.passArmed = false;
    const m = matchAnswer(input, cat);
    if (m.type === "empty") { ui.message = "Type an answer first."; renderPrompt(); return; }
    if (m.type === "exact") { finalize({ status: "scored", answer: m.answer.name, score: scoreFor(m.answer, cat), input }); return; }
    if (m.type === "confirm") { ui.confirm = { answer: m.answer, name: m.answer.name, input }; ui.message = ""; renderPrompt(); announce(`Did you mean ${m.answer.name}?`); return; }

    const key = normalize(input);
    if (ctx.state.guesses[i].includes(key)) { ui.message = "You already tried that one. It doesn't cost a try twice."; renderPrompt(); return; }
    ctx.state.guesses[i].push(key);
    ctx.state.misses[i] += 1;
    saveState();
    const left = MAX_ATTEMPTS - ctx.state.misses[i];
    if (left <= 0) { finalize({ status: "missed", answer: null, score: 0, input }); return; }
    const why = m.type === "wrong"
      ? `<strong>${esc(input)}</strong> is real lore, but it doesn't fit this prompt.`
      : `<strong>${esc(input)}</strong> isn't in the lore for this prompt.`;
    ui.message = `${why} ${left} ${left === 1 ? "try" : "tries"} left.`;
    ui.draft = "";
    renderPrompt();
  }

  function finalize(result) {
    const i = ctx.state.idx;
    if (ctx.state.results[i]) return; // the clock and a click can race
    clearInterval(questionTimer);
    result.attempts = ctx.state.misses[i] + (result.status === "scored" ? 1 : 0);
    ctx.state.results[i] = result;
    saveState();
    ui = {};
    renderTracker();
    renderReveal(i, true);
  }

  function renderReveal(i, animate) {
    const cat = ctx.cats[i];
    const r = ctx.state.results[i];
    const before = fathomsFor(totalScore(ctx.state.results.slice(0, i)));
    const after = fathomsFor(totalScore(ctx.state.results.slice(0, i + 1)));
    const answer = r.answer ? cat.answers.find((a) => a.name === r.answer) : null;
    const last = i === ctx.picks.length - 1;
    const rarest = answer && cat.answers.slice(-5).some((a) => a.name === answer.name);
    const hitGem = answer && answer.name === cat.gem;

    const WHY = { passed: "You passed.", timeout: "Time ran out.", missed: "Three misses." };
    const yours = answer
      ? `<p class="your"><span class="name">${esc(r.input || answer.name)}</span></p>`
      : `<p class="your">${WHY[r.status] || "No answer."}</p>`;
    const callouts = [
      hitGem ? `<p class="callout gem">★ You found the gem: ${GEM_MULTIPLIER}× depth</p>` : "",
      rarest ? `<p class="callout">One of the 5 rarest answers.</p>` : "",
    ].join("");

    setView(`
      <section class="card" aria-labelledby="reveal-title">
        <p class="eyebrow">Pour ${i + 1} of 5 · ${esc(cat.prompt)}</p>
        <div class="result">
          ${gaugeHTML(before, after)}
          <div class="result-body">
            <div class="scoreline">
              <span class="score ${r.score === 0 ? "zero" : ""}" id="score-num">${animate ? 0 : r.score}</span>
              <span class="score-unit">depth · now ${after} fathoms</span>
            </div>
            <h2 class="sr-only" id="reveal-title">${r.score} depth. You are now at ${after} fathoms, ${esc(tierFor(after).name)}.</h2>
            ${yours}
            ${callouts}
          </div>
        </div>
        <div class="row"><button type="button" class="btn" id="next">${last ? "See your depth" : "Next pour"}</button></div>
      </section>`, "#reveal-title");
    main.querySelector("#reveal-title").setAttribute("tabindex", "-1");
    main.querySelector("#reveal-title").focus();
    sinkGauge(animate);
    if (animate) countUp($("#score-num"), r.score);
    announce(`${r.score} depth. Now at ${after} fathoms.${hitGem ? ` You found the gem: ${GEM_MULTIPLIER} times depth.` : ""}${rarest ? " One of the 5 rarest answers." : ""}`);

    $("#next").addEventListener("click", () => {
      ctx.state.idx = i + 1;
      if (ctx.state.idx >= ctx.picks.length) ctx.state.done = true;
      saveState();
      renderTracker();
      if (ctx.state.done) renderSummary(true); else renderPrompt();
    });
  }

  function shareText() {
    const gems = ctx.state.results.filter((r, i) => r.answer && r.answer === ctx.cats[i].gem).length;
    const lines = [
      "DeepPint",
      ctx.state.results.map(layerEmoji).join(""),
      `Score: ${totalScore(ctx.state.results)}`,
      `Found ${gems} ${gems === 1 ? "gem" : "gems"} today`,
    ];
    if (CFG.siteUrl) lines.push(CFG.siteUrl);
    return lines.join("\n");
  }


  function renderSummary(animate) {
    const total = totalScore(ctx.state.results);
    const f = fathomsFor(total);
    const tier = tierFor(f);
    const rows = ctx.cats.map((c, i) => {
      const r = ctx.state.results[i];
      return `<tr><td>${r.answer ? esc(r.input || r.answer) : `<span class="p">${r.status === "timeout" ? "Out of time" : "No answer"}</span>`}<span class="p">${esc(c.prompt)}</span></td><td class="s">${r.score}</td></tr>`;
    }).join("");
    setView(`
      <section class="card" aria-labelledby="sum-title">
        <p class="eyebrow">Pour #${ctx.dayIndex + 1} · ${esc(prettyDate(ctx.date))} · locked</p>
        <div class="result">
          ${gaugeHTML(0, f)}
          <div class="result-body">
            <div class="summary-head">
              <h2 class="sr-only" id="sum-title">You reached ${f} fathoms: ${esc(tier.name)}.</h2>
              <p class="fathoms" aria-hidden="true"><span id="fathom-num">${animate ? 0 : f}</span><small>fathoms</small></p>
              <p class="tier">${esc(tier.name)}</p>
              <p class="tier-line">${esc(tier.line)}</p>
            </div>
            <table class="recap">
              <thead><tr><th scope="col">Your answers</th><th scope="col" class="s">Depth</th></tr></thead>
              <tbody>${rows}</tbody>
              <tfoot><tr><td>Total</td><td class="s">${total}</td></tr></tfoot>
            </table>
          </div>
        </div>
        <div class="row">
          <button type="button" class="btn" id="share">Copy result</button>
          <span class="hint" id="share-status" role="status"></span>
        </div>
        <pre class="share-preview" aria-label="Share preview">${esc(shareText())}</pre>
        <p class="countdown" id="countdown"></p>
      </section>`, "#sum-title");
    main.querySelector("#sum-title").setAttribute("tabindex", "-1");
    main.querySelector("#sum-title").focus();
    sinkGauge(animate);
    if (animate) countUp($("#fathom-num"), f, 1600);
    announce(`Today's pour is done. ${f} fathoms, ${tier.name}.`);

    $("#share").addEventListener("click", async () => {
      const text = shareText();
      const status = $("#share-status");
      try {
        await navigator.clipboard.writeText(text);
        status.textContent = "Copied. No spoilers in it.";
      } catch {
        status.textContent = "Copy blocked here. Select the text below and copy it.";
        const pre = main.querySelector(".share-preview");
        const ta = document.createElement("textarea");
        ta.className = "copy-fallback";
        ta.value = text;
        ta.setAttribute("aria-label", "Result text");
        pre.replaceWith(ta);
        ta.focus();
        ta.select();
      }
    });
    startCountdown();
  }

  function startCountdown() {
    const el = $("#countdown");
    if (!el) return;
    if (!ctx.isToday) { el.textContent = "This is a past pour, loaded with test tools."; return; }
    const tick = () => {
      const p = easternParts();
      if (p.iso !== ctx.date) { el.textContent = "A new pour is ready. Reload to play it."; clearInterval(countdownTimer); return; }
      let left = 86400 - (p.h * 3600 + p.m * 60 + p.s);
      const h = Math.floor(left / 3600); left %= 3600;
      const m = Math.floor(left / 60);
      el.textContent = `Next pour in ${h}h ${String(m).padStart(2, "0")}m (midnight US Eastern).`;
    };
    tick();
    countdownTimer = setInterval(tick, 30000);
  }

  /* ---------- test tools ---------- */
  function setupTestTools(today) {
    if (!TEST_MODE) return;
    const box = $("#test-tools");
    box.hidden = false;
    const input = $("#test-date");
    input.min = prompts.epoch;
    input.value = ctx.date;
    $("#test-lineup").textContent = `Lineup for ${ctx.date}: ${ctx.cats.map((c) => c.prompt).join(" / ")}`;
    $("#test-go").onclick = () => {
      if (!input.value) return;
      session.set(TEST_DATE_KEY, input.value < prompts.epoch ? prompts.epoch : input.value);
      start();
    };
    $("#test-reset").onclick = () => { store.remove(stateKey(ctx.date)); start(); };
    if (ctx.date !== today) box.open = true;
  }

  /* ---------- boot ---------- */
  async function start() {
    try {
      if (!prompts) {
        [prompts, schedule, lexicon] = await Promise.all([
          getJSON("data/prompts.json"),
          getJSON("data/schedule.json"),
          getJSON("data/lexicon.json").catch(() => ({})),
        ]);
      }
      const today = easternParts().iso;
      let date = today;
      const testDate = TEST_MODE ? session.get(TEST_DATE_KEY) : null;
      if (testDate && /^\d{4}-\d{2}-\d{2}$/.test(testDate)) date = testDate;
      const dayIndex = Math.max(0, daysBetween(prompts.epoch, date));
      if (daysBetween(prompts.epoch, date) < 0) date = prompts.epoch;

      let state = store.get(stateKey(date));
      const picks = state && Array.isArray(state.picks) && state.picks.length === 5 ? state.picks : lineupFor(dayIndex);
      if (!state || !Array.isArray(state.results)) state = freshState(date, picks);
      const cats = await Promise.all(picks.map(loadCategory));
      ctx = { date, dayIndex, picks, cats, state, isToday: date === today };
      ui = {};

      $("#pour-meta").textContent = `Pour #${dayIndex + 1} · ${prettyDate(date)}${ctx.isToday ? "" : " · test"}`;
      renderTracker();
      setupTestTools(today);

      if (state.done) renderSummary(false);
      else if (!state.started) renderIntro();
      else if (state.results[state.idx]) renderReveal(state.idx, false);
      else renderPrompt();
    } catch (err) {
      console.error(err);
      main.innerHTML = `<section class="card"><h2 class="title">The tap is dry</h2><p class="blurb">Today's pours couldn't load. Check your connection and reload the page.</p></section>`;
    }
  }

  // Exposed for tests in the console.
  window.DeepPour = { normalize, matchAnswer: (input, id) => matchAnswer(input, answerCache[id]), lineupFor: (d) => lineupFor(d) };

  start();
})();

// The "Arc" theme: two glowing eyes on black, with neon-blue lightning.
//
// The page calls:
//   JarvisArc.mood(m)     "offline" | "idle" | "listening" | "thinking" | "speaking"
//   JarvisArc.emote(e)    the mood of the sentence being spoken (see brain emotions.py)
//   JarvisArc.level(x)    loudness of Jarvis's voice right now, 0..1
//   JarvisArc.track(s)    the mic node's tracking snapshot (to look at whoever is talking)
"use strict";

(() => {
  const canvas = document.getElementById("arc");
  const ctx = canvas.getContext("2d");
  const eyeCanvas = document.createElement("canvas");
  const ectx = eyeCanvas.getContext("2d");

  const CORE = "#e9fbff";
  const EDGE = "#7fdcff";
  const GLOW = "rgba(64, 190, 255, 1)";

  // ---------- expressions ----------
  // lt/rt: upper lid (0 open .. 1 shut) for the left/right eye (as you look at the screen)
  // lb: lower lid pushed up into a "^" squint (happy, amused)
  // tilt: upper lid slope; + = inner corners down (mischief), - = inner corners up (sad)
  // glow, scale; lx/ly: where the eyes look (-1..1, ly - is up); bounce: extra bob
  const BASE = { lt: 0, rt: 0, lb: 0, tilt: 0, glow: 0.75, scale: 1, lx: 0, ly: 0, bounce: 0 };
  const EMOTES = {
    neutral:   {},
    statement: { glow: 1.0, scale: 1.04, ly: -0.04 },
    happy:     { lb: 0.55, glow: 0.95, scale: 1.02, ly: -0.1, bounce: 0.3 },
    amused:    { lt: 0.08, rt: 0.08, lb: 0.62, tilt: 0.12, glow: 1.0, lx: 0.1, ly: -0.05, bounce: 0.6 },
    question:  { lt: 0.16, rt: 0.42, tilt: 0.1, glow: 0.9, lx: 0.12, ly: -0.08 },
    thinking:  { lt: 0.2, rt: 0.2, lb: 0.08, glow: 0.65, lx: 0.5, ly: -0.6 },
    sad:       { lt: 0.3, rt: 0.3, tilt: -0.28, glow: 0.45, scale: 0.95, ly: 0.45 },
    concerned: { lt: 0.22, rt: 0.22, tilt: -0.16, glow: 0.6, ly: 0.15 },
    surprised: { lt: 0, rt: 0, lb: 0, glow: 1.0, scale: 1.18, ly: -0.1 },
    excited:   { lt: 0, rt: 0, lb: 0.35, glow: 1.0, scale: 1.1, bounce: 1.0 },
  };
  const MOODS = {
    offline:   { lt: 0.86, rt: 0.86, glow: 0.15, scale: 0.92, ly: 0.2 },
    idle:      { lt: 0.18, rt: 0.18, glow: 0.5, scale: 0.97 },
    listening: { lt: 0.0, rt: 0.0, glow: 0.88, scale: 1.06 },
    thinking:  EMOTES.thinking,
    speaking:  EMOTES.statement,
  };

  let mood = "offline", emote = null, emoteUntil = 0, level = 0, track = null;
  const cur = { ...BASE, ...MOODS.offline };
  let idleLook = { lx: 0, ly: 0 }, nextIdleLook = 0, blink = 0, nextBlink = 1500;
  let active = false;

  window.JarvisArc = {
    mood(m) {
      if (m === mood) return;
      if (m !== "speaking" && mood === "speaking") emoteUntil = performance.now() + 1200;
      mood = m;
      nextBlink = performance.now() + 150;
    },
    emote(e) {
      if (!EMOTES[e]) return;
      emote = e;
      emoteUntil = Infinity;            // held while speaking; released after
      if (e === "surprised" || e === "excited") spawnBolt(true);
    },
    level(x) { level = x; },
    track(s) { track = s; },
    active(on) {
      active = on;
      if (on) { resize(); requestAnimationFrame(frame); }
    },
  };

  // ---------- geometry ----------
  let W = 1, H = 1, dpr = 1, G;
  function resize() {
    dpr = window.devicePixelRatio || 1;
    W = canvas.clientWidth || window.innerWidth;
    H = canvas.clientHeight || window.innerHeight;
    canvas.width = Math.round(W * dpr); canvas.height = Math.round(H * dpr);
    const ew = Math.min(W * 0.13, H * 0.17);
    G = { cx: W / 2, cy: H * 0.38, ew, eh: ew * 1.3, gap: ew * 1.35, r: ew * 0.42 };
    eyeCanvas.width = Math.ceil(G.ew * 1.6 * dpr);
    eyeCanvas.height = Math.ceil(G.eh * 1.6 * dpr);
  }
  // Re-measure whenever the canvas changes size (window resize, rotation, kiosk resolution)
  new ResizeObserver(() => active && resize()).observe(canvas);

  // ---------- lightning ----------
  const bolts = [];
  function boltPath(x1, y1, x2, y2, rough, depth, out, branches) {
    if (depth === 0) { out.push([x2, y2]); return; }
    const mx = (x1 + x2) / 2 + (Math.random() - 0.5) * rough;
    const my = (y1 + y2) / 2 + (Math.random() - 0.5) * rough;
    boltPath(x1, y1, mx, my, rough / 2, depth - 1, out, branches);
    if (branches && depth > 2 && Math.random() < 0.25) {
      const b = [[mx, my]];
      const ang = Math.atan2(y2 - y1, x2 - x1) + (Math.random() - 0.5) * 1.6;
      const len = Math.hypot(x2 - x1, y2 - y1) * 0.5;
      boltPath(mx, my, mx + Math.cos(ang) * len, my + Math.sin(ang) * len, rough / 2, depth - 2, b, false);
      branches.push(b);
    }
    boltPath(mx, my, x2, y2, rough / 2, depth - 1, out, branches);
  }
  function spawnBolt(big) {
    const x1 = W * (0.1 + Math.random() * 0.8), y1 = -10;
    const x2 = x1 + (Math.random() - 0.5) * W * 0.4, y2 = H * (0.35 + Math.random() * 0.4);
    const pts = [[x1, y1]], branches = [];
    boltPath(x1, y1, x2, y2, W * 0.12, 7, pts, branches);
    bolts.push({ pts, branches, born: performance.now(), life: big ? 520 : 380,
                 width: big ? 2.2 : 1.3, alpha: big ? 0.75 : 0.32, flash: big ? 0.12 : 0.04 });
  }
  function spawnArc(side) {
    // A small spark jumping off an eye while Jarvis talks
    const ex = G.cx + side * G.gap / 2, ey = G.cy;
    const a = (side > 0 ? 0 : Math.PI) + (Math.random() - 0.5) * 1.8;
    const r0 = G.ew * 0.75, r1 = G.ew * (1.3 + Math.random() * 0.9);
    const x1 = ex + Math.cos(a) * r0, y1 = ey + Math.sin(a) * r0 * 1.2;
    const x2 = ex + Math.cos(a) * r1, y2 = ey + Math.sin(a) * r1 * 1.2;
    const pts = [[x1, y1]];
    boltPath(x1, y1, x2, y2, G.ew * 0.35, 5, pts, null);
    bolts.push({ pts, branches: [], born: performance.now(), life: 200, width: 1.1, alpha: 0.6, flash: 0 });
  }
  function drawBolts(t) {
    let flash = 0;
    for (let i = bolts.length - 1; i >= 0; i--) {
      const b = bolts[i], age = (t - b.born) / b.life;
      if (age >= 1) { bolts.splice(i, 1); continue; }
      const flicker = age < 0.5 ? (Math.random() < 0.3 ? 0.4 : 1) : 1 - (age - 0.5) * 2;
      const a = b.alpha * flicker;
      flash = Math.max(flash, b.flash * (1 - age));
      ctx.save();
      ctx.strokeStyle = `rgba(200, 240, 255, ${a})`;
      ctx.shadowColor = GLOW; ctx.shadowBlur = 18;
      ctx.lineJoin = "round"; ctx.lineCap = "round";
      for (const [pts, w] of [[b.pts, b.width], ...b.branches.map((p) => [p, b.width * 0.5])]) {
        ctx.lineWidth = w;
        ctx.beginPath(); ctx.moveTo(pts[0][0], pts[0][1]);
        for (const [x, y] of pts) ctx.lineTo(x, y);
        ctx.stroke();
      }
      ctx.restore();
    }
    return flash;
  }

  // ---------- eyes ----------
  function targets(t) {
    const speakingEmote = emote && (mood === "speaking" || t < emoteUntil) ? EMOTES[emote] : null;
    if (!speakingEmote && t >= emoteUntil) emote = null;
    const m = { ...BASE, ...(MOODS[mood] || {}), ...(speakingEmote || {}) };
    if (mood === "idle") {
      if (t > nextIdleLook) {
        idleLook = Math.random() < 0.4 ? { lx: 0, ly: 0 }
          : { lx: (Math.random() - 0.5) * 1.2, ly: (Math.random() - 0.5) * 0.7 };
        nextIdleLook = t + 2500 + Math.random() * 4000;
      }
      m.lx = idleLook.lx; m.ly = idleLook.ly;
      m.glow += 0.08 * Math.sin(t / 1400);          // slow breathing
    }
    if ((mood === "listening" || mood === "idle") && track && track.state === "tracking" && track.gaze) {
      // look toward whoever is talking (+az = the eye's own right = the viewer's left)
      m.lx = Math.max(-1, Math.min(1, -track.gaze.az / 50));
      m.ly = -0.05;
    }
    if (mood === "thinking") m.lx += 0.25 * Math.sin(t / 700);   // scanning while it works
    return m;
  }

  function approach(k, dt, rate) {
    return (a, b) => a + (b - a) * Math.min(1, dt * rate);
  }

  function step(t, dt) {
    const T = targets(t);
    const ease = approach(null, dt, 7), slow = approach(null, dt, 4);
    for (const key of ["lt", "rt", "lb", "tilt"]) cur[key] = ease(cur[key], T[key]);
    for (const key of ["glow", "scale", "bounce"]) cur[key] = slow(cur[key], T[key]);
    cur.lx = ease(cur.lx, T.lx); cur.ly = ease(cur.ly, T.ly);
    if (blink === 0 && t > nextBlink && mood !== "offline") blink = 0.0001;
    if (blink > 0) {
      blink += dt / 0.16;
      if (blink >= 1) { blink = 0; nextBlink = t + 2200 + Math.random() * 4500; }
    }
  }

  function roundRect(c, x, y, w, h, r) {
    c.beginPath();
    c.moveTo(x + r, y); c.arcTo(x + w, y, x + w, y + h, r); c.arcTo(x + w, y + h, x, y + h, r);
    c.arcTo(x, y + h, x, y, r); c.arcTo(x, y, x + w, y, r); c.closePath();
  }

  // Draws one eye into the offscreen canvas, centred, with its lids cut away.
  function drawEyeShape(side, lid, sx, sy) {
    const w = G.ew * sx, h = G.eh * sy;
    const cw = eyeCanvas.width / dpr, ch = eyeCanvas.height / dpr;
    ectx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ectx.clearRect(0, 0, cw, ch);
    ectx.translate(cw / 2, ch / 2);
    ectx.globalCompositeOperation = "source-over";
    const grd = ectx.createRadialGradient(0, -h * 0.1, w * 0.05, 0, 0, Math.max(w, h) * 0.65);
    grd.addColorStop(0, CORE); grd.addColorStop(0.65, "#bff0ff"); grd.addColorStop(1, EDGE);
    ectx.fillStyle = grd;
    roundRect(ectx, -w / 2, -h / 2, w, h, Math.min(G.r * sx, w / 2, h / 2));
    ectx.fill();
    ectx.globalCompositeOperation = "destination-out";
    ectx.fillStyle = "#000";
    // upper lid: a sloped cut from above (slope mirrored, so "inner corners" mean the same on both)
    const shut = Math.min(1, lid + (blink > 0 ? Math.sin(Math.PI * blink) : 0));
    if (shut > 0.005 || Math.abs(cur.tilt) > 0.005) {
      ectx.save();
      ectx.rotate(cur.tilt * (side < 0 ? 1 : -1));
      ectx.fillRect(-w * 1.5, -h * 2, w * 3, h * 1.5 + shut * h * 1.02);
      ectx.restore();
    }
    // lower lid: a round cut rising into the middle from below. Narrower than the eye, so the
    // sides stay and the eye becomes an arch "∩" - the smiling squint.
    if (cur.lb > 0.01) {
      const rx = w * 0.36, ry = h * 0.5;
      ectx.beginPath();
      ectx.ellipse(0, h / 2 + ry * 0.95 - cur.lb * h * 1.1, rx, ry, 0, 0, Math.PI * 2);
      ectx.fill();
    }
    ectx.globalCompositeOperation = "source-over";
  }

  function drawEyes(t) {
    const speaking = mood === "speaking";
    const bob = speaking ? (0.35 + level) * (1 + cur.bounce) : 0.15 + cur.bounce * 0.3;
    for (const side of [-1, 1]) {
      const phase = side < 0 ? 0 : 0.9;
      const by = Math.sin(t / 210 + phase) * G.eh * 0.035 * bob - (speaking ? level * G.eh * 0.05 : 0);
      const bx = Math.sin(t / 470 + phase * 2) * G.ew * 0.02 * bob;
      const sc = cur.scale * (1 + (speaking ? level * 0.07 : 0));
      const sx = sc * (1 - Math.abs(cur.lx) * 0.12), sy = sc * (1 - Math.abs(cur.ly) * 0.08);
      drawEyeShape(side, side < 0 ? cur.lt : cur.rt, sx, sy);
      const x = G.cx + side * G.gap / 2 + cur.lx * G.ew * 0.7 + bx;
      const y = G.cy + cur.ly * G.eh * 0.45 + by;
      const glow = Math.max(0.05, Math.min(1.3, cur.glow + (speaking ? level * 0.35 : 0)));
      const cw = eyeCanvas.width / dpr, ch = eyeCanvas.height / dpr;
      ctx.save();
      ctx.shadowColor = GLOW;
      ctx.globalAlpha = Math.min(1, 0.25 + glow * 0.75);
      ctx.shadowBlur = 40 * glow;
      ctx.drawImage(eyeCanvas, x - cw / 2, y - ch / 2, cw, ch);
      ctx.shadowBlur = 14 * glow;
      ctx.drawImage(eyeCanvas, x - cw / 2, y - ch / 2, cw, ch);
      ctx.restore();
    }
  }

  // ---------- frame ----------
  let last = performance.now(), nextBolt = performance.now() + 4000;
  function frame(t) {
    if (!active) return;
    const dt = Math.min(0.05, (t - last) / 1000); last = t;
    step(t, dt);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.fillStyle = "#000"; ctx.fillRect(0, 0, W, H);

    if (mood !== "offline" && t > nextBolt) {
      spawnBolt(Math.random() < 0.25);
      nextBolt = t + (mood === "idle" ? 7000 : 4000) + Math.random() * 8000;
    }
    if (mood === "speaking" && level > 0.55 && Math.random() < 0.06) spawnArc(Math.random() < 0.5 ? -1 : 1);

    // soft blue halo behind the eyes, brighter when it talks
    const halo = 0.05 + 0.08 * cur.glow + (mood === "speaking" ? level * 0.08 : 0);
    const grd = ctx.createRadialGradient(G.cx, G.cy, G.ew * 0.5, G.cx, G.cy, G.ew * 5);
    grd.addColorStop(0, `rgba(40, 150, 255, ${halo})`); grd.addColorStop(1, "rgba(0, 0, 0, 0)");
    ctx.fillStyle = grd; ctx.fillRect(0, 0, W, H);

    const flash = drawBolts(t);
    if (flash > 0) { ctx.fillStyle = `rgba(120, 200, 255, ${flash})`; ctx.fillRect(0, 0, W, H); }
    drawEyes(t);
    requestAnimationFrame(frame);
  }
})();

// Jarvis's eye: a canvas-drawn eyeball that looks at whoever is talking.
// Ported from EGH490 acoustic-proto (eyehost/static/eye.js @ dac085a). The drawing is unchanged;
// what drives it is new: the tracker's sound direction AND what Jarvis is doing.
//
// The page calls:
//   JarvisEye.track(snapshot)   tracker state from the terminal ({"type": "eye", ...})
//   JarvisEye.mood(m)           "offline" | "idle" | "listening" | "thinking" | "speaking"
"use strict";

const canvas = document.getElementById("eye");
const ctx = canvas.getContext("2d");
const elNodeStatus = document.getElementById("eye-status");   // optional

// ---------- inputs ----------
let server = { state: "no_nodes", gaze: null, level: null, nodes: [] };
let lastMsg = 0;
let mood = "offline";
let lastSpeakerAz = null;     // where the last person who spoke to Jarvis was (deg, + = eye's right)

window.JarvisEye = {
  track(snap) { server = snap; lastMsg = performance.now(); },
  mood(m) { if (m !== mood) { mood = m; eye.nextBlink = performance.now(); } },
};

// ---------- geometry ----------
let W, H, dpr, G;     // G = eye geometry in CSS px
let irisTex, scleraTex;

function rand(a, b) { return a + Math.random() * (b - a); }
function clamp(x, a, b) { return Math.max(a, Math.min(b, x)); }

function resize() {
  dpr = window.devicePixelRatio || 1;
  W = canvas.clientWidth || 1; H = canvas.clientHeight || 1;
  canvas.width = Math.round(W * dpr); canvas.height = Math.round(H * dpr);
  const EW = Math.min(W * 0.8, H * 1.45);    // eye opening width
  const EH = EW * 0.5;                      // eye opening height (fully open)
  // R: eyeball (bigger than the opening so no edge shows), ri: iris, travel: how far the iris moves
  G = { cx: W / 2, cy: H * 0.47, EW, EH, R: EW * 0.6, ri: EW * 0.17, travel: EW * 0.27 };
  irisTex = makeIris(G.ri);
  scleraTex = makeSclera(G.R);
}

// Procedural iris: warm centre, green-hazel body, blue-grey rim, radial fibres, dark limbal ring
function makeIris(r) {
  const s = Math.ceil(2 * r * dpr), c = document.createElement("canvas");
  c.width = c.height = s;
  const g = c.getContext("2d");
  g.scale(dpr, dpr); g.translate(r, r);
  let grd = g.createRadialGradient(0, 0, 0, 0, 0, r);
  grd.addColorStop(0.00, "#3a2410"); grd.addColorStop(0.22, "#8a5a22");
  grd.addColorStop(0.42, "#6f7a3c"); grd.addColorStop(0.70, "#4d7a6e");
  grd.addColorStop(0.90, "#3b5662"); grd.addColorStop(1.00, "#1a2328");
  g.fillStyle = grd; g.beginPath(); g.arc(0, 0, r, 0, Math.PI * 2); g.fill();
  // fibres
  for (let i = 0; i < 900; i++) {
    const a = rand(0, Math.PI * 2), r0 = r * rand(0.18, 0.4), r1 = r * rand(0.6, 0.97);
    const wob = rand(-0.06, 0.06);
    const light = Math.random() < 0.55;
    g.strokeStyle = light ? `rgba(230,210,150,${rand(0.05, 0.16)})` : `rgba(10,15,10,${rand(0.06, 0.2)})`;
    g.lineWidth = rand(0.4, 1.3);
    g.beginPath();
    g.moveTo(Math.cos(a) * r0, Math.sin(a) * r0);
    g.quadraticCurveTo(Math.cos(a + wob) * (r0 + r1) / 2, Math.sin(a + wob) * (r0 + r1) / 2,
                       Math.cos(a + wob * 0.5) * r1, Math.sin(a + wob * 0.5) * r1);
    g.stroke();
  }
  // crypts
  for (let i = 0; i < 40; i++) {
    const a = rand(0, Math.PI * 2), rr = r * rand(0.35, 0.8);
    g.fillStyle = `rgba(15,20,15,${rand(0.08, 0.22)})`;
    g.beginPath();
    g.ellipse(Math.cos(a) * rr, Math.sin(a) * rr, r * rand(0.02, 0.05), r * rand(0.01, 0.025), a, 0, Math.PI * 2);
    g.fill();
  }
  // collarette
  g.strokeStyle = "rgba(220,190,120,0.25)"; g.lineWidth = r * 0.03;
  g.beginPath();
  for (let i = 0; i <= 64; i++) {
    const a = (i / 64) * Math.PI * 2, rr = r * (0.42 + 0.03 * Math.sin(i * 1.7) + rand(-0.01, 0.01));
    i ? g.lineTo(Math.cos(a) * rr, Math.sin(a) * rr) : g.moveTo(Math.cos(a) * rr, Math.sin(a) * rr);
  }
  g.stroke();
  // limbal ring
  grd = g.createRadialGradient(0, 0, r * 0.82, 0, 0, r);
  grd.addColorStop(0, "rgba(0,0,0,0)"); grd.addColorStop(1, "rgba(5,8,10,0.85)");
  g.fillStyle = grd; g.beginPath(); g.arc(0, 0, r, 0, Math.PI * 2); g.fill();
  return c;
}

// Sclera: off-white with pinkish edges and a few veins creeping in from the sides
function makeSclera(R) {
  const s = Math.ceil(2 * R * dpr), c = document.createElement("canvas");
  c.width = c.height = s;
  const g = c.getContext("2d");
  g.scale(dpr, dpr); g.translate(R, R);
  const grd = g.createRadialGradient(-R * 0.1, -R * 0.15, R * 0.1, 0, 0, R);
  grd.addColorStop(0, "#f4efe9"); grd.addColorStop(0.55, "#e6ddd4");
  grd.addColorStop(0.85, "#c9b3ad"); grd.addColorStop(1, "#a88a86");
  g.fillStyle = grd; g.beginPath(); g.arc(0, 0, R, 0, Math.PI * 2); g.fill();
  for (let v = 0; v < 16; v++) {
    const side = v % 2 ? 1 : -1;
    let x = side * R * rand(0.85, 0.98), y = R * rand(-0.35, 0.35);
    let a = side > 0 ? Math.PI + rand(-0.4, 0.4) : rand(-0.4, 0.4);
    let w = rand(0.8, 1.6);
    g.strokeStyle = `rgba(170,40,40,${rand(0.18, 0.35)})`;
    for (let k = 0; k < 7; k++) {
      const len = R * rand(0.05, 0.11);
      const nx = x + Math.cos(a) * len, ny = y + Math.sin(a) * len;
      g.lineWidth = w; g.beginPath(); g.moveTo(x, y); g.lineTo(nx, ny); g.stroke();
      x = nx; y = ny; a += rand(-0.6, 0.6); w *= 0.8;
    }
  }
  return c;
}

new ResizeObserver(resize).observe(canvas);
resize();

// ---------- animation state ----------
const eye = {
  gx: 0, gy: 0, vx: 0, vy: 0,    // gaze in [-1,1], screen axes (x right, y down), and velocity
  open: 0, pupil: 0.42,           // lid openness 0..1, pupil radius as a fraction of the iris
  blink: 0, nextBlink: 2000,      // blink phase 0..1 (0 = not blinking)
  jx: 0, jy: 0, nextJitter: 0,    // micro-saccade offset
};
let prevState = "no_nodes";

// az > 0 = the eye's own right = the viewer's left; +-60 deg reaches the edge of the eye
function gazeX(azDeg) { return clamp(-Math.sin(azDeg * Math.PI / 180) / Math.sin(Math.PI / 3), -1, 1); }

function targets(t) {
  // Tracker: is a sound being followed right now?
  const tr = performance.now() - lastMsg > 2000 ? "no_nodes" : server.state;
  if (tr !== prevState) { if (tr === "tracking" || prevState === "tracking") eye.nextBlink = t; prevState = tr; }
  const tracking = tr === "tracking" && server.gaze;
  const lvl = server.level == null ? -80 : server.level;
  const loud = clamp((lvl + 75) / 45, 0, 1);
  if (tracking && (mood === "listening" || mood === "idle")) lastSpeakerAz = server.gaze.az;
  const atSpeaker = lastSpeakerAz == null ? 0 : gazeX(lastSpeakerAz);
  const st = mood + "/" + tr;

  switch (mood) {
    case "offline":      // brain not connected: asleep
      return { st, gx: 0, gy: 0.2, open: 0.0, pupil: 0.36 };
    case "listening":    // wide awake, on whoever is talking
      return { st, gx: tracking ? gazeX(server.gaze.az) : atSpeaker, gy: -0.15,
               open: 1.0, pupil: 0.44 + 0.14 * loud };
    case "thinking":     // glance up and away, like someone thinking
      return { st, gx: 0.45 + 0.08 * Math.sin(t / 900), gy: -0.55, open: 0.85, pupil: 0.38 };
    case "speaking":     // look back at the person while answering (the tracker is muted now)
      return { st, gx: atSpeaker + 0.04 * Math.sin(t / 700), gy: -0.12, open: 1.0, pupil: 0.42 };
    default:             // idle: drowsy, but glances at sounds in the room
      if (tracking) return { st, gx: gazeX(server.gaze.az), gy: -0.1, open: 0.8, pupil: 0.40 + 0.1 * loud };
      if (tr === "searching") return { st, gx: atSpeaker * 0.5 + 0.3 * Math.sin(t / 2200), gy: 0.05, open: 0.6, pupil: 0.40 };
      return { st, gx: 0.2 * Math.sin(t / 5000), gy: 0.25, open: 0.38, pupil: 0.36 };
  }
}

function step(t, dt) {
  const T = targets(t);
  // gaze: fast saccade for big jumps, smooth pursuit for small ones (critically damped spring)
  const dx = T.gx - eye.gx, dy = T.gy - eye.gy;
  const k = Math.hypot(dx, dy) > 0.25 ? 260 : 45;
  eye.vx += (k * dx - 2 * Math.sqrt(k) * eye.vx) * dt;
  eye.vy += (k * dy - 2 * Math.sqrt(k) * eye.vy) * dt;
  eye.gx += eye.vx * dt; eye.gy += eye.vy * dt;
  if (t > eye.nextJitter) {
    const a = T.st.endsWith("/tracking") ? 0.02 : 0.008;
    eye.jx = rand(-a, a); eye.jy = rand(-a, a); eye.nextJitter = t + rand(400, 1400);
  }
  eye.open += (T.open - eye.open) * Math.min(1, dt * 4);
  eye.pupil += (T.pupil - eye.pupil) * Math.min(1, dt * 3);
  if (eye.blink === 0 && t > eye.nextBlink && eye.open > 0.2) eye.blink = 0.0001;
  if (eye.blink > 0) {
    eye.blink += dt / 0.28;
    if (eye.blink >= 1) { eye.blink = 0; eye.nextBlink = t + rand(2500, 6500); }
  }
  return T.st;
}

// ---------- drawing ----------
function lidPath(open, gy) {
  const { cx, cy, EW, EH } = G;
  const L = cx - EW / 2, Rx = cx + EW / 2;
  // A cubic with both control points at height c peaks at 0.75c, hence the 4/3 factors
  const up = cy - (EH * 0.55 * open - gy * EH * 0.12 * open) * 4 / 3;   // upper lid follows vertical gaze
  const lo = cy + (EH * 0.42 * Math.max(open, 0.04) + gy * EH * 0.05) * 4 / 3;
  const p = new Path2D();
  p.moveTo(L, cy + EH * 0.04);
  p.bezierCurveTo(L + EW * 0.22, up, Rx - EW * 0.3, up, Rx, cy - EH * 0.02);
  p.bezierCurveTo(Rx - EW * 0.25, lo, L + EW * 0.3, lo, L, cy + EH * 0.04);
  return { p, up, lo, L, Rx };
}

function draw(t, st) {
  const { cx, cy, EW, EH, R, ri } = G;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.fillStyle = "#07070a"; ctx.fillRect(0, 0, W, H);

  // skin around the eye, fading into the dark
  let grd = ctx.createRadialGradient(cx, cy, EW * 0.2, cx, cy, EW * 0.75);
  grd.addColorStop(0, "#6b4a3c"); grd.addColorStop(0.5, "#3a271f"); grd.addColorStop(1, "#07070a");
  ctx.fillStyle = grd; ctx.beginPath(); ctx.ellipse(cx, cy, EW * 0.75, EH * 1.6, 0, 0, Math.PI * 2); ctx.fill();

  const blinkClose = eye.blink > 0 ? Math.sin(Math.PI * eye.blink) : 0;
  const open = eye.open * (1 - blinkClose);
  const gx = clamp(eye.gx + eye.jx, -1, 1), gy = clamp(eye.gy + eye.jy, -1, 1);
  const lid = lidPath(open, gy);

  // eye socket shadow just outside the opening
  ctx.save();
  ctx.shadowColor = "rgba(0,0,0,0.6)"; ctx.shadowBlur = EH * 0.25;
  ctx.fillStyle = "#2a1a14"; ctx.fill(lid.p);
  ctx.restore();

  ctx.save();
  ctx.clip(lid.p);

  // eyeball: sclera texture shifts slightly with gaze (the ball rotates)
  ctx.drawImage(scleraTex, cx - R + gx * R * 0.06, cy - R + gy * R * 0.04, 2 * R, 2 * R);

  // iris + pupil, foreshortened as the eye turns
  const ox = gx * G.travel, oy = gy * G.travel * 0.6;
  const off = Math.hypot(ox, oy) / (G.travel * 1.6), fs = Math.sqrt(Math.max(0.3, 1 - off * off));
  const ang = Math.atan2(oy, ox);
  ctx.save();
  ctx.translate(cx + ox, cy + oy);
  ctx.rotate(ang); ctx.scale(fs, 1); ctx.rotate(-ang);
  ctx.drawImage(irisTex, -ri, -ri, 2 * ri, 2 * ri);
  const rp = ri * eye.pupil;
  grd = ctx.createRadialGradient(0, 0, rp * 0.85, 0, 0, rp * 1.08);
  grd.addColorStop(0, "#020203"); grd.addColorStop(1, "rgba(2,2,3,0)");
  ctx.fillStyle = grd; ctx.beginPath(); ctx.arc(0, 0, rp * 1.08, 0, Math.PI * 2); ctx.fill();
  ctx.restore();

  // shading: upper lid shadow and darker corners make it read as a sphere
  grd = ctx.createLinearGradient(0, lid.up, 0, lid.up + EH * 0.55);
  grd.addColorStop(0, "rgba(30,15,10,0.55)"); grd.addColorStop(1, "rgba(30,15,10,0)");
  ctx.fillStyle = grd; ctx.fillRect(lid.L, lid.up, EW, EH * 0.6);
  grd = ctx.createRadialGradient(cx, cy, EW * 0.18, cx, cy, EW * 0.55);
  grd.addColorStop(0, "rgba(0,0,0,0)"); grd.addColorStop(1, "rgba(40,20,20,0.45)");
  ctx.fillStyle = grd; ctx.fillRect(lid.L, lid.up, EW, lid.lo - lid.up);

  // corneal reflections: mostly fixed to the light, drifting a little with the cornea
  const hx = cx + ox * 0.55 - ri * 0.38, hy = cy + oy * 0.55 - ri * 0.42;
  ctx.fillStyle = "rgba(255,255,255,0.55)";
  ctx.beginPath(); ctx.ellipse(hx, hy, ri * 0.2, ri * 0.13, -0.5, 0, Math.PI * 2); ctx.fill();
  ctx.fillStyle = "rgba(255,255,255,0.85)";
  ctx.beginPath(); ctx.arc(hx + ri * 0.45, hy + ri * 0.5, ri * 0.045, 0, Math.PI * 2); ctx.fill();
  ctx.restore();

  // lid rims
  ctx.lineWidth = Math.max(2, EH * 0.03); ctx.strokeStyle = "#1c100c"; ctx.stroke(lid.p);

  // upper lashes and crease
  if (open > 0.05) {
    ctx.strokeStyle = "#140c09"; ctx.lineCap = "round";
    for (let i = 0; i < 46; i++) {
      const u = 0.06 + 0.9 * (i / 45);
      const x = lid.L + EW * u;
      const y = bez(cy + EH * 0.04, lid.up, lid.up, cy - EH * 0.02, u);
      const len = EH * (0.06 + 0.1 * Math.sin(Math.PI * u)) * (0.85 + 0.3 * ((i * 7919) % 13) / 13);
      const sideward = (u - 0.5) * 1.4;
      ctx.lineWidth = Math.max(1, EH * 0.012);
      ctx.beginPath(); ctx.moveTo(x, y);
      ctx.quadraticCurveTo(x + sideward * len * 0.3, y - len * 0.7, x + sideward * len * 0.8, y - len);
      ctx.stroke();
    }
    ctx.strokeStyle = "rgba(20,10,6,0.55)"; ctx.lineWidth = Math.max(1.5, EH * 0.02);
    ctx.beginPath(); ctx.moveTo(cx - EW * 0.44, cy - EH * 0.3);
    ctx.quadraticCurveTo(cx, cy - EH * 1.25, cx + EW * 0.46, cy - EH * 0.34); ctx.stroke();
  }
}

function bez(p0, p1, p2, p3, u) {
  const v = 1 - u;
  return v * v * v * p0 + 3 * v * v * u * p1 + 3 * v * u * u * p2 + u * u * u * p3;
}

// ---------- node status (small text under the eye) ----------
function status() {
  if (!elNodeStatus) return;
  const nodes = (server.nodes || []).filter((n) => n.connected);
  elNodeStatus.textContent = !nodes.length ? "no mic node"
    : nodes.map((n) => n.detecting && n.theta != null ? `${n.name}: ${n.theta.toFixed(0)}°` : `${n.name}: quiet`).join("  ");
}

let last = performance.now(), lastStatus = 0;
function frame(t) {
  const dt = Math.min(0.05, (t - last) / 1000); last = t;
  const st = step(t, dt);
  draw(t, st);
  if (t - lastStatus > 300) { status(); lastStatus = t; }
  requestAnimationFrame(frame);
}
requestAnimationFrame(frame);

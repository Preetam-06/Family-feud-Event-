// Fill the oval logo from the event name ("Family Feud" → FAMILY / FEUD)
function setLogo(root, name) {
  const w = (name || 'Family Feud').trim().split(/\s+/);
  const l1 = w.length > 1 ? w[0] : w[0];
  const l2 = w.length > 1 ? w.slice(1).join(' ') : '';
  const spans = root.querySelectorAll('.plate span');
  spans[0].textContent = l1; spans[1].textContent = l2;
  const longest = Math.max(l1.length, l2.length);
  const fs = Math.max(4.2, Math.min(11, 50 / (longest * 0.62)));
  root.querySelector('.plate').style.setProperty('--fs', fs + 'em');
}

// Synthesized game-show cues: no external or copyrighted audio files needed.
window.feudSound = (() => {
  let ctx = null;
  let enabled = localStorage.getItem('feud-sound') !== 'off';
  const clips = {
    strike: new Audio('/static/audio/strike.mp3'),
    correct: new Audio('/static/audio/correct.mp3')
  };
  Object.values(clips).forEach(c => { c.preload = 'auto'; c.volume = .82; });
  const audio = () => {
    if (!enabled) return null;
    ctx ||= new (window.AudioContext || window.webkitAudioContext)();
    if (ctx.state === 'suspended') ctx.resume();
    return ctx;
  };
  const note = (c, hz, at, length, type, volume) => {
    const o = c.createOscillator(), g = c.createGain();
    o.type = type; o.frequency.setValueAtTime(hz, at);
    g.gain.setValueAtTime(0.0001, at);
    g.gain.exponentialRampToValueAtTime(volume, at + 0.012);
    g.gain.exponentialRampToValueAtTime(0.0001, at + length);
    o.connect(g).connect(c.destination); o.start(at); o.stop(at + length + 0.02);
  };
  return {
    unlock() { audio(); },
    toggle() { enabled = !enabled; localStorage.setItem('feud-sound', enabled ? 'on' : 'off'); if (enabled) audio(); return enabled; },
    isEnabled() { return enabled; },
    play(kind) {
      if (!enabled) return;
      const clip = clips[kind];
      if (clip) { clip.currentTime = 0; clip.play().catch(() => {}); return; }
      const c = audio(); if (!c) return;
      const t = c.currentTime + 0.01;
      if (kind === 'strike') {
        note(c, 180, t, .28, 'sawtooth', .11); note(c, 120, t + .14, .38, 'square', .10);
      } else if (kind === 'correct') {
        note(c, 523.25, t, .14, 'triangle', .08); note(c, 659.25, t + .09, .14, 'triangle', .08); note(c, 783.99, t + .18, .28, 'triangle', .10);
      }
    }
  };
})();

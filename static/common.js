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

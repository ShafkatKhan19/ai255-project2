// Snip front end. Plain JavaScript, no libraries.
// Every 5 seconds (while the tab is visible) it asks the web service for
// the link list and for the state of links and stats, then redraws.

const $ = (id) => document.getElementById(id);
const REFRESH_MS = 5000;
const HISTORY = 40;

const state = {
  latencies: [],     // response times of recent refreshes, in ms
  podCounts: {},     // web pod name -> number of refreshes it answered
  lastPod: null,
  knownCodes: null,  // codes already on screen, to highlight new ones
  status: { links: null, stats: null },
};

// ---------- small helpers ----------

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

let toastTimer;
function toast(text) {
  const box = $('toast');
  box.textContent = text;
  box.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { box.hidden = true; }, 1800);
}

async function copy(text) {
  try {
    await navigator.clipboard.writeText(text);
    toast('Copied ' + text);
  } catch (error) {
    // Clipboard access needs HTTPS. On plain HTTP show the link instead.
    toast(text);
  }
}

function timeAgo(seconds) {
  const age = Math.max(0, Date.now() / 1000 - seconds);
  if (age < 60) return 'just now';
  if (age < 3600) return Math.floor(age / 60) + ' min ago';
  if (age < 86400) return Math.floor(age / 3600) + ' h ago';
  return Math.floor(age / 86400) + ' d ago';
}

function splitAddress(address) {
  try {
    const parsed = new URL(address);
    return { host: parsed.host, rest: parsed.pathname + parsed.search };
  } catch (error) {
    return { host: address, rest: '' };
  }
}

function median(values) {
  if (values.length === 0) return null;
  const sorted = [...values].sort((a, b) => a - b);
  return sorted[Math.floor(sorted.length / 2)];
}

// ---------- the live request path ----------

function setNode(name, className) {
  const node = document.querySelector('[data-node="' + name + '"]');
  node.classList.remove('ok', 'down');
  if (className) node.classList.add(className);
  const dot = node.querySelector('.dot');
  if (dot) dot.className = 'dot ' + (className || '');
}

function setEdge(name, dead) {
  document.querySelector('[data-edge="' + name + '"]').classList.toggle('dead', dead);
}

// Send a dot along the path, one hop after another.
function animatePath(hops) {
  hops.forEach((name, index) => {
    setTimeout(() => {
      const edge = document.querySelector('[data-edge="' + name + '"]');
      edge.classList.remove('run');
      void edge.offsetWidth;  // restart the animation
      edge.classList.add('run');
      const target = name === 'back' ? ['links', 'stats'] : [name];
      for (const nodeName of target) {
        const node = document.querySelector('[data-node="' + nodeName + '"]');
        node.classList.add('hit');
        setTimeout(() => node.classList.remove('hit'), 450);
      }
    }, index * 260);
  });
}

function setPill(id, className, text) {
  $(id).className = 'pill ' + className;
  $(id + '-text').textContent = text;
}

function showOverall() {
  const { links, stats } = state.status;
  setNode('links', links === 'ok' ? 'ok' : links ? 'down' : '');
  setNode('stats', stats === 'ok' ? 'ok' : stats ? 'down' : '');
  $('links-sub').textContent = links === 'down' ? 'not answering' : 'create + look up';
  $('stats-sub').textContent = stats === 'down' ? 'not answering' : 'count clicks';
  setEdge('back', links === 'down' && stats === 'down');
  $('links-banner').hidden = links !== 'down';
  $('stats-banner').hidden = !(stats === 'down' && links !== 'down');
  if (links === 'ok' && stats === 'ok') setPill('overall', 'ok', 'All services ready');
  else if (links === 'down') setPill('overall', 'down', 'links is down');
  else if (stats === 'down') setPill('overall', 'warn', 'stats is down');
}

// ---------- drawing ----------

function drawSpark() {
  const values = state.latencies;
  if (values.length < 2) return;
  const width = 400, height = 90, pad = 6;
  const max = Math.max(...values), min = Math.min(...values);
  const span = Math.max(max - min, 1);
  const step = width / (HISTORY - 1);
  const offset = (HISTORY - values.length) * step;
  const points = values.map((value, index) => {
    const x = offset + index * step;
    const y = height - pad - ((value - min) / span) * (height - 2 * pad);
    return x.toFixed(1) + ',' + y.toFixed(1);
  });
  const line = 'M' + points.join(' L');
  $('spark-line').setAttribute('d', line);
  $('spark-area').setAttribute('d', line + ' L' + width + ',' + height + ' L' + offset.toFixed(1) + ',' + height + ' Z');
  $('spark-min').textContent = 'fastest ' + min + ' ms';
  $('spark-max').textContent = 'slowest ' + max + ' ms';
}

function drawPods() {
  const box = $('pods');
  const names = Object.keys(state.podCounts).sort();
  const most = Math.max(...Object.values(state.podCounts));
  box.textContent = '';
  for (const name of names) {
    const row = el('div', 'pod-row');
    row.appendChild(el('div', 'pod-name' + (name === state.lastPod ? ' last' : ''), name));
    row.appendChild(el('div', 'pod-count', state.podCounts[name]));
    const bar = el('div', 'pod-bar');
    const fill = el('i');
    fill.style.width = (100 * state.podCounts[name] / most) + '%';
    bar.appendChild(fill);
    row.appendChild(bar);
    box.appendChild(row);
  }
  $('t-pods').textContent = names.length;
  $('web-sub').textContent = names.length + (names.length === 1 ? ' pod seen' : ' pods seen');
}

function drawLinks(data) {
  const rows = $('rows');
  rows.textContent = '';
  if (data.links.length === 0) {
    const row = el('tr');
    const cell = el('td', 'empty', 'No links yet. Paste an address above to make the first one.');
    cell.colSpan = 4;
    row.appendChild(cell);
    rows.appendChild(row);
  }
  const most = Math.max(1, ...data.links.map((link) => link.clicks || 0));
  for (const link of data.links) {
    const row = el('tr');
    if (state.knownCodes && !state.knownCodes.has(link.code)) row.className = 'new';

    const shortCell = el('td');
    const short = el('div', 'short');
    const anchor = el('a', '', '/r/' + link.code);
    anchor.href = '/r/' + link.code;
    anchor.target = '_blank';
    anchor.rel = 'noopener';
    const copyButton = el('button', 'copy', 'copy');
    copyButton.type = 'button';
    copyButton.addEventListener('click', () => copy(link.short_url));
    short.appendChild(anchor);
    short.appendChild(copyButton);
    shortCell.appendChild(short);
    row.appendChild(shortCell);

    const parts = splitAddress(link.url);
    const destination = el('td');
    const text = el('div', 'dest');
    text.title = link.url;
    text.appendChild(el('b', '', parts.host));
    text.appendChild(document.createTextNode(parts.rest === '/' ? '' : parts.rest));
    destination.appendChild(text);
    row.appendChild(destination);

    row.appendChild(el('td', 'when', timeAgo(link.created)));

    const clicksCell = el('td', 'num');
    const clicks = el('div', 'clicks');
    if (link.clicks === null) {
      clicks.appendChild(el('span', '', '?'));
    } else {
      const bar = el('div', 'bar');
      const fill = el('i');
      fill.style.width = (100 * link.clicks / most) + '%';
      bar.appendChild(fill);
      clicks.appendChild(bar);
      clicks.appendChild(el('span', '', link.clicks));
    }
    clicksCell.appendChild(clicks);
    row.appendChild(clicksCell);
    rows.appendChild(row);
  }
  state.knownCodes = new Set(data.links.map((link) => link.code));

  $('t-links').textContent = data.links.length;
  $('t-clicks').textContent = data.stats_available
    ? data.links.reduce((sum, link) => sum + link.clicks, 0)
    : '?';
}

// ---------- talking to the web service ----------

async function refreshLinks() {
  const started = performance.now();
  let response;
  try {
    response = await fetch('/api/links', { cache: 'no-store' });
  } catch (error) {
    setNode('web', 'down');
    setEdge('web', true);
    setPill('overall', 'down', 'web is not answering');
    $('trace').textContent = 'no answer from web';
    return;
  }
  const elapsed = Math.round(performance.now() - started);
  const pod = response.headers.get('X-Served-By');
  const requestId = response.headers.get('X-Request-ID');

  setNode('web', 'ok');
  setEdge('web', false);
  state.latencies.push(elapsed);
  if (state.latencies.length > HISTORY) state.latencies.shift();
  if (pod) {
    state.podCounts[pod] = (state.podCounts[pod] || 0) + 1;
    state.lastPod = pod;
  }
  $('t-latency').textContent = median(state.latencies) + ' ms';
  $('trace').textContent = 'request ' + requestId + ' answered by ' + pod + ' in ' + elapsed + ' ms';
  drawSpark();
  drawPods();

  if (response.ok) {
    const data = await response.json();
    drawLinks(data);
    animatePath(['lb', 'web', 'back', 'store']);
  } else {
    // web answered, but links did not: the path stops at web.
    animatePath(['lb', 'web']);
  }
}

async function refreshStatus() {
  try {
    const response = await fetch('/api/status', { cache: 'no-store' });
    if (!response.ok) throw new Error('status ' + response.status);
    const data = await response.json();
    state.status = { links: data.links, stats: data.stats };
    showOverall();
  } catch (error) {
    state.status = { links: null, stats: null };
  }
}

function tick() {
  // A hidden tab does not refresh, so a forgotten tab does not keep
  // reading from the datastore all day.
  if (document.hidden) return;
  refreshLinks();
  refreshStatus();
}

$('form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const button = $('go');
  button.disabled = true;
  $('msg').textContent = '';
  try {
    const response = await fetch('/api/links', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url: $('url').value }),
    });
    const data = await response.json();
    if (response.ok) {
      $('result-link').textContent = data.short_url;
      $('result-link').href = '/r/' + data.code;
      $('result').hidden = false;
      $('result-copy').onclick = () => copy(data.short_url);
      $('url').value = '';
      refreshLinks();
    } else {
      $('result').hidden = true;
      $('msg').textContent = data.detail || 'Something went wrong.';
    }
  } catch (error) {
    $('msg').textContent = 'Could not reach the server.';
  }
  button.disabled = false;
});

// ---------- start ----------

if (location.protocol === 'https:') {
  setPill('secure', 'ok', 'Encrypted (HTTPS)');
} else {
  setPill('secure', 'warn', 'Not encrypted (HTTP)');
}
$('lb-sub').textContent = location.host;
setNode('browser', 'ok');
setNode('lb', 'ok');
setNode('store', '');

tick();
setInterval(tick, REFRESH_MS);
document.addEventListener('visibilitychange', tick);

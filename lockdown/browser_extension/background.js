/**
 * AI Focus Lockdown — Firefox background script.
 * Connects to the lockdown daemon WebSocket and enforces domain restrictions.
 */

const DAEMON_WS       = "ws://127.0.0.1:8767/ws";
const RECONNECT_MS    = 5000;
const PING_INTERVAL   = 30000;

let socket         = null;
let isActive       = false;
let allowedDomains = [];
let reconnectTimer = null;
let pingTimer      = null;

// ── Helpers ────────────────────────────────────────────────────────────────────
function getDomain(url) {
  try { return new URL(url).hostname.toLowerCase(); } catch { return ""; }
}

// ── WebSocket ──────────────────────────────────────────────────────────────────
function connect() {
  if (socket && (socket.readyState === WebSocket.OPEN ||
                 socket.readyState === WebSocket.CONNECTING)) return;

  try {
    socket = new WebSocket(DAEMON_WS);

    socket.onopen = () => {
      console.log("[lockdown] Connected to daemon");
      if (reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = null; }
      pingTimer = setInterval(() => {
        if (socket && socket.readyState === WebSocket.OPEN)
          socket.send(JSON.stringify({ type: "ping" }));
      }, PING_INTERVAL);
    };

    socket.onmessage = ({ data }) => {
      let msg;
      try { msg = JSON.parse(data); } catch { return; }

      if (msg.type === "state") {
        isActive       = msg.active;
        allowedDomains = (msg.allowed_domains || []).map(d => d.toLowerCase());
        console.log(`[lockdown] state: active=${isActive} domains=[${allowedDomains}]`);
      }

      if (msg.type === "block_tab") {
        const dest = msg.redirect_url || "about:blank";
        browser.tabs.update(msg.tab_id, { url: dest }).catch(() => {});
        console.log(`[lockdown] blocked tab ${msg.tab_id} → ${dest}`);
      }
    };

    socket.onclose = () => {
      console.log("[lockdown] Disconnected");
      isActive = false;
      socket   = null;
      if (pingTimer) { clearInterval(pingTimer); pingTimer = null; }
      scheduleReconnect();
    };

    socket.onerror = () => { /* onclose fires after onerror */ };

  } catch (e) {
    console.log("[lockdown] connect error:", e);
    scheduleReconnect();
  }
}

function scheduleReconnect() {
  if (!reconnectTimer)
    reconnectTimer = setTimeout(() => { reconnectTimer = null; connect(); }, RECONNECT_MS);
}

// ── Tab monitoring ─────────────────────────────────────────────────────────────
function reportTab(tabId, url) {
  if (!isActive) return;
  if (!socket || socket.readyState !== WebSocket.OPEN) return;
  const domain = getDomain(url);
  if (!domain || domain === "newtab" || url.startsWith("about:") ||
      url.startsWith("moz-extension:")) return;
  socket.send(JSON.stringify({ type: "tab_change", domain, url, tab_id: tabId }));
}

browser.tabs.onActivated.addListener(({ tabId }) => {
  browser.tabs.get(tabId).then(tab => {
    if (tab.url) reportTab(tabId, tab.url);
  }).catch(() => {});
});

browser.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
  // Only act on URL changes for the active tab
  if (changeInfo.url && tab.active) reportTab(tabId, changeInfo.url);
});

// ── Boot ───────────────────────────────────────────────────────────────────────
connect();

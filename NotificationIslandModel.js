// Pure helpers for the notification island. The island only reads the host's
// popup snapshot (~/.local/state/omarchy/notifications/live.json, oldest row
// first); it never writes it or talks to the notification server directly.
.pragma library

var MAX_FILE_CHARS = 4 * 1024 * 1024
var MAX_ROWS = 100
var MAX_APP = 64
var MAX_SUMMARY = 160
var MAX_BODY = 240
var KEY = /^[A-Za-z0-9_-]{1,120}$/
var ICON_NAME = /^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$/
// A row this much older than its arrival is a restore or replay, not news.
var MAX_AGE_SECONDS = 15
var CRITICAL = 2
// Notifications the island keeps while open: the one shown plus the cards
// stacked behind it.
var MAX_STACK = 3

// Plain, single-line text: control and bidi-override characters go, runs of
// whitespace collapse, and the result is bounded. Markup stays literal because
// every sink is Text.PlainText.
function clean(value, limit) {
  var text = typeof value === "string" ? value : ""
  if (text.length > limit * 8) text = text.slice(0, limit * 8)
  text = text.replace(/[\u0000-\u001f\u007f-\u009f​-‏‪-‮⁦-⁩﻿]+/g, " ")
  text = text.replace(/\s+/g, " ").trim()
  if (text.length > limit) text = text.slice(0, limit - 1).replace(/\s+$/, "") + "…"
  return text
}

// Notification bodies may carry simple markup (the summary may not). The
// island shows it as plain text: tags go, the basic entities decode, and the
// result is still only ever drawn with Text.PlainText.
function plainBody(value) {
  var text = typeof value === "string" ? value.slice(0, MAX_BODY * 8) : ""
  return text.replace(/<[^>]*>/g, "")
    .replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&quot;/g, "\"")
    .replace(/&#39;|&apos;/g, "'").replace(/&amp;/g, "&")
}

function number(value, fallback) {
  return typeof value === "number" && isFinite(value) ? value : fallback
}

// One stored row, or null when it has no usable identity or text.
function row(entry) {
  if (!entry || typeof entry !== "object" || Array.isArray(entry)) return null
  var key = typeof entry.key === "string" ? entry.key : ""
  if (!KEY.test(key)) return null
  var out = {
    key: key,
    app: clean(entry.app, MAX_APP),
    appIcon: typeof entry.appIcon === "string" && ICON_NAME.test(entry.appIcon) ? entry.appIcon : "",
    summary: clean(entry.summary, MAX_SUMMARY),
    body: clean(plainBody(entry.body), MAX_BODY),
    urgency: Math.max(0, Math.min(CRITICAL, Math.round(number(entry.urgency, 1)))),
    ts: Math.max(0, number(entry.ts, 0))
  }
  if (!out.summary && !out.body) return null
  return out
}

// The snapshot's rows, oldest first. Anything but a bounded array is nothing.
function parseLive(text) {
  var raw = typeof text === "string" ? text : ""
  if (!raw.trim() || raw.length > MAX_FILE_CHARS) return []
  var value
  try { value = JSON.parse(raw) } catch (e) { return [] }
  if (!Array.isArray(value)) return []
  var out = []
  var start = Math.max(0, value.length - MAX_ROWS)
  for (var i = start; i < value.length; i++) {
    var parsed = row(value[i])
    if (parsed) out.push(parsed)
  }
  return out
}

function keysOf(rows) {
  var keys = {}
  for (var i = 0; i < rows.length; i++) keys[rows[i].key] = true
  return keys
}

// What a fresh snapshot means for the island. `seen` is null before the first
// read: whatever is already on screen when the bar starts is not news.
// Returns { seen, fresh, show }: fresh is every unseen recent row, oldest
// first, and show is the newest of them or null.
function arrivals(seen, rows, nowSeconds) {
  var next = keysOf(rows)
  if (!seen) return { seen: next, fresh: [], show: null }
  var fresh = []
  for (var i = 0; i < rows.length; i++) {
    var candidate = rows[i]
    if (seen[candidate.key]) continue
    if (nowSeconds - candidate.ts > MAX_AGE_SECONDS) continue
    fresh.push(candidate)
  }
  return { seen: next, fresh: fresh, show: fresh.length ? fresh[fresh.length - 1] : null }
}

// The open island's stack, newest first: fresh rows (oldest first) go on top
// of what it already holds, each key once, at most MAX_STACK cards.
function stacked(stack, fresh) {
  var out = []
  var keys = {}
  var incoming = (fresh || []).slice().reverse().concat(stack || [])
  for (var i = 0; i < incoming.length && out.length < MAX_STACK; i++) {
    var item = incoming[i]
    if (!item || keys[item.key]) continue
    keys[item.key] = true
    out.push(item)
  }
  return out
}

// The row the host's "latest toast" IPC would target, as far as the snapshot
// knows: its newest row.
function newestKey(rows) {
  return rows.length ? rows[rows.length - 1].key : ""
}

// Whether "invoke the newest toast" would reach the island's row: it is the
// snapshot's newest, and the host shows exactly the rows the snapshot holds.
function mayActivate(rows, key, hostCount) {
  return !!key && newestKey(rows) === key && hostCount === rows.length
}

// How long the island stays open. Critical waits longer, but the host's own
// toast is what keeps a critical notification on screen.
function holdMs(urgency) {
  if (urgency === CRITICAL) return 8000
  if (urgency === 0) return 4000
  return 5500
}

// Icon-theme names worth looking up for a row, best first.
function iconNames(item) {
  if (!item) return []
  var names = []
  var app = String(item.app || "")
  var slug = app.toLowerCase().replace(/[^a-z0-9._-]+/g, "-").replace(/^[-.]+|[-.]+$/g, "")
  var candidates = [item.appIcon, app, slug]
  for (var i = 0; i < candidates.length; i++) {
    var name = String(candidates[i] || "")
    if (ICON_NAME.test(name) && names.indexOf(name) < 0) names.push(name)
  }
  return names
}

// The headline: summary, or the body when the sender sent no summary.
function headline(item) {
  if (!item) return ""
  return item.summary || item.body
}

// The second line, never a repeat of the headline.
function detail(item) {
  if (!item || !item.summary) return ""
  return item.body
}

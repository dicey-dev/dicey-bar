.pragma library

// Pure display-layout logic for the Displays panel and its hotplug service.
// Nothing here touches Hyprland or the filesystem, so tests run it through
// Qt's own JavaScript engine. display-ctl.py turns the plans built here into
// Hyprland rules.
//
// A plan is a list of outputs:
//   { name, fp, selector, internal, enabled, autoOff, offReason,
//     mode: { w, h, hz }, x, y, scale, vrr, hdr, transform, ddcWatch }
// `autoOff` marks a monitor that is connected but off for now: "lid" (the
// laptop is shut; Omarchy's clamshell handling owns the panel) or "input"
// (it was switched to another input, or off; this bar turns it off and on).
// Memory (displays.json) keeps two things apart:
//   monitors[fp]  settings that belong to one monitor wherever it is plugged in
//   layouts[key]  positions and on/off state for one set of monitors

var SCALE_PRESETS = [1, 1.25, 1.5, 1.6, 2, 3]
var MAX_LAYOUTS = 64
// A Hyprland refresh rate reads back with rounding (100.00 vs 99.998).
var HZ_TOLERANCE = 0.05

function isInternalName(name) {
  return /^(eDP|LVDS|DSI)-/.test(String(name || ""))
}

function clean(value) {
  return String(value === undefined || value === null ? "" : value).trim()
}

// Hardware identity: EDID make, model and serial, so a monitor keeps its
// settings on any port or dock. Outputs without EDID data fall back to the
// connector name.
function baseFingerprint(monitor) {
  var make = clean(monitor.make)
  var model = clean(monitor.model)
  var serial = clean(monitor.serial)
  if (!make && !model && !serial) return "name:" + clean(monitor.name)
  return make + "|" + model + "|" + serial
}

// Gives each connected monitor a fingerprint and a Hyprland selector. Two
// identical monitors that report the same (often empty) serial cannot be told
// apart by EDID, so only those get the connector name appended. Internal
// panels are always addressed by connector name, which is what Omarchy's
// clamshell handling reads back from monitors.lua.
function identify(monitors) {
  var counts = {}
  var list = Array.isArray(monitors) ? monitors : []
  for (var i = 0; i < list.length; i++) {
    var fp = baseFingerprint(list[i])
    counts[fp] = (counts[fp] || 0) + 1
  }
  var out = []
  for (var j = 0; j < list.length; j++) {
    var m = list[j]
    var base = baseFingerprint(m)
    var unique = counts[base] === 1
    var description = clean(m.description)
    var internal = isInternalName(m.name)
    out.push({
      name: clean(m.name),
      fp: unique ? base : base + "|" + clean(m.name),
      selector: (!internal && unique && description) ? "desc:" + description : clean(m.name),
      internal: internal
    })
  }
  return out
}

function layoutKey(fps) {
  return fps.slice().sort().join("\n")
}

function parseMode(text) {
  var match = /^(\d+)x(\d+)@([0-9.]+)/.exec(String(text || ""))
  if (!match) return null
  return { w: parseInt(match[1], 10), h: parseInt(match[2], 10), hz: parseFloat(match[3]) }
}

function sameHz(a, b) {
  return Math.abs(Number(a) - Number(b)) < HZ_TOLERANCE
}

function sameMode(a, b) {
  return !!a && !!b && a.w === b.w && a.h === b.h && sameHz(a.hz, b.hz)
}

function formatHz(hz) {
  var n = Number(hz)
  if (Math.abs(n - Math.round(n)) < 0.005) return String(Math.round(n))
  return n.toFixed(2)
}

// Resolutions, largest first, each with its own refresh rates (fastest first),
// so the panel can offer resolution and refresh rate as separate choices.
function modeGroups(availableModes) {
  var groups = []
  var byRes = {}
  var list = Array.isArray(availableModes) ? availableModes : []
  for (var i = 0; i < list.length; i++) {
    var mode = typeof list[i] === "string" ? parseMode(list[i]) : list[i]
    if (!mode || !(mode.w > 0) || !(mode.h > 0) || !(mode.hz > 0)) continue
    var key = mode.w + "x" + mode.h
    var group = byRes[key]
    if (!group) {
      group = { key: key, w: mode.w, h: mode.h, rates: [] }
      byRes[key] = group
      groups.push(group)
    }
    var known = false
    for (var r = 0; r < group.rates.length; r++) if (sameHz(group.rates[r], mode.hz)) known = true
    if (!known) group.rates.push(mode.hz)
  }
  for (var g = 0; g < groups.length; g++) groups[g].rates.sort(function(a, b) { return b - a })
  groups.sort(function(a, b) { return (b.w * b.h - a.w * a.h) || (b.w - a.w) })
  return groups
}

// The mode to run: the remembered one when this monitor still offers it, else
// the fastest rate at the remembered resolution, else the largest resolution
// at its fastest rate.
function pickMode(availableModes, wanted) {
  var groups = modeGroups(availableModes)
  if (!groups.length) return wanted || null
  if (wanted) {
    for (var i = 0; i < groups.length; i++) {
      var group = groups[i]
      if (group.w !== wanted.w || group.h !== wanted.h) continue
      for (var r = 0; r < group.rates.length; r++)
        if (sameHz(group.rates[r], wanted.hz)) return { w: group.w, h: group.h, hz: group.rates[r] }
      return { w: group.w, h: group.h, hz: group.rates[0] }
    }
  }
  return { w: groups[0].w, h: groups[0].h, hz: groups[0].rates[0] }
}

function gcd(a, b) {
  while (b) {
    var t = a % b
    a = b
    b = t
  }
  return a
}

// Hyprland accepts a scale only when the mode divides into whole logical
// pixels in 1/120 steps; round a requested scale up to the next one that does.
function cleanScale(scale, w, h) {
  var requested = Number(scale)
  if (!(requested > 0) || !(w > 0) || !(h > 0)) return 1
  var divisor = gcd(Math.round(w * 120), Math.round(h * 120))
  var units = Math.round(requested * 120)
  if (units > divisor) units = divisor
  while (divisor % units !== 0) units++
  return Math.round(units / 120 * 1000) / 1000
}

function scaleOptions(w, h) {
  var out = []
  for (var i = 0; i < SCALE_PRESETS.length; i++) {
    var value = cleanScale(SCALE_PRESETS[i], w, h)
    if (out.indexOf(value) === -1) out.push(value)
  }
  return out
}

function rotated(transform) {
  return Number(transform) % 2 === 1
}

function logicalSize(output) {
  var mode = output.mode || { w: 0, h: 0 }
  var scale = Number(output.scale) > 0 ? Number(output.scale) : 1
  var w = Math.round(mode.w / scale)
  var h = Math.round(mode.h / scale)
  return rotated(output.transform) ? { w: h, h: w } : { w: w, h: h }
}

function rectOf(output) {
  var size = logicalSize(output)
  return { x: Math.round(Number(output.x) || 0), y: Math.round(Number(output.y) || 0), w: size.w, h: size.h }
}

function overlaps(a, b) {
  return a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h
}

function sharedEdge(a, b) {
  var vertical = Math.min(a.y + a.h, b.y + b.h) - Math.max(a.y, b.y)
  var horizontal = Math.min(a.x + a.w, b.x + b.w) - Math.max(a.x, b.x)
  if ((a.x + a.w === b.x || b.x + b.w === a.x) && vertical > 0) return vertical
  if ((a.y + a.h === b.y || b.y + b.h === a.y) && horizontal > 0) return horizontal
  return 0
}

function clamp(value, low, high) {
  return Math.max(low, Math.min(high, value))
}

// Moves `rect` the shortest distance to a spot where it touches the placed
// rectangles along an edge without covering any of them. Hyprland lets the
// pointer fall into gaps between monitors, so every monitor must touch the
// rest. `alignWithin` also snaps edges and centres into line when they are
// that close, which is what makes dragging feel magnetic.
function attach(rect, placed, alignWithin) {
  if (!placed.length) return { x: rect.x, y: rect.y, w: rect.w, h: rect.h }
  var snap = Number(alignWithin) > 0 ? Number(alignWithin) : 0
  var free = true
  var touching = false
  for (var i = 0; i < placed.length; i++) {
    if (overlaps(rect, placed[i])) free = false
    if (sharedEdge(rect, placed[i]) > 0) touching = true
  }
  if (free && touching && !snap) return { x: rect.x, y: rect.y, w: rect.w, h: rect.h }

  var best = null
  for (var p = 0; p < placed.length; p++) {
    var o = placed[p]
    var sides = [
      { x: o.x + o.w, y: clamp(rect.y, o.y - rect.h + 1, o.y + o.h - 1), axis: "y" },
      { x: o.x - rect.w, y: clamp(rect.y, o.y - rect.h + 1, o.y + o.h - 1), axis: "y" },
      { x: clamp(rect.x, o.x - rect.w + 1, o.x + o.w - 1), y: o.y + o.h, axis: "x" },
      { x: clamp(rect.x, o.x - rect.w + 1, o.x + o.w - 1), y: o.y - rect.h, axis: "x" }
    ]
    for (var s = 0; s < sides.length; s++) {
      var c = { x: sides[s].x, y: sides[s].y, w: rect.w, h: rect.h }
      if (snap) {
        var lines = sides[s].axis === "y"
          ? [o.y, o.y + o.h - c.h, o.y + Math.round((o.h - c.h) / 2)]
          : [o.x, o.x + o.w - c.w, o.x + Math.round((o.w - c.w) / 2)]
        for (var l = 0; l < lines.length; l++) {
          var current = sides[s].axis === "y" ? c.y : c.x
          if (Math.abs(current - lines[l]) <= snap) {
            if (sides[s].axis === "y") c.y = lines[l]
            else c.x = lines[l]
            break
          }
        }
      }
      var clear = true
      for (var q = 0; q < placed.length; q++) if (overlaps(c, placed[q])) clear = false
      if (!clear) continue
      var dx = c.x - rect.x
      var dy = c.y - rect.y
      var distance = dx * dx + dy * dy
      if (!best || distance < best.distance) best = { x: c.x, y: c.y, distance: distance }
    }
  }
  if (!best) {
    var right = -Infinity
    var top = Infinity
    for (var b = 0; b < placed.length; b++) {
      right = Math.max(right, placed[b].x + placed[b].w)
      top = Math.min(top, placed[b].y)
    }
    best = { x: right, y: top }
  }
  return { x: best.x, y: best.y, w: rect.w, h: rect.h }
}

function byPosition(a, b) {
  return (a.x - b.x) || (a.y - b.y)
}

// Closes gaps and removes overlaps among the enabled outputs, moving each as
// little as possible, then puts the layout's top-left corner at 0,0. `fixed`
// (an index) is the output the user is dragging: it is placed last so the
// others hold still and it snaps against them.
function solve(outputs, fixed, alignWithin) {
  var result = outputs.map(function(o) {
    var copy = {}
    for (var k in o) copy[k] = o[k]
    return copy
  })
  var order = []
  for (var i = 0; i < result.length; i++) if (result[i].enabled) order.push(i)
  order.sort(function(a, b) {
    if (a === fixed) return 1
    if (b === fixed) return -1
    return byPosition(rectOf(result[a]), rectOf(result[b]))
  })
  var placed = []
  for (var j = 0; j < order.length; j++) {
    var output = result[order[j]]
    var r = attach(rectOf(output), placed, order[j] === fixed ? alignWithin : 0)
    output.x = r.x
    output.y = r.y
    placed.push(r)
  }
  if (placed.length) {
    var minX = Infinity
    var minY = Infinity
    for (var p = 0; p < placed.length; p++) {
      minX = Math.min(minX, placed[p].x)
      minY = Math.min(minY, placed[p].y)
    }
    for (var e = 0; e < order.length; e++) {
      result[order[e]].x -= minX
      result[order[e]].y -= minY
    }
  }
  return result
}

// Which side of `other` an output sits on, and its offset along that edge, so
// a monitor can return beside the same neighbour in a combination it has not
// been used in before.
function neighbourOf(output, outputs) {
  var mine = rectOf(output)
  var best = null
  for (var i = 0; i < outputs.length; i++) {
    var other = outputs[i]
    if (other === output || !other.enabled || other.fp === output.fp) continue
    var theirs = rectOf(other)
    var shared = sharedEdge(mine, theirs)
    if (shared <= 0 || (best && shared <= best.shared)) continue
    var edge = mine.x === theirs.x + theirs.w ? "right"
      : mine.x + mine.w === theirs.x ? "left"
      : mine.y === theirs.y + theirs.h ? "below" : "above"
    var offset = (edge === "right" || edge === "left") ? mine.y - theirs.y : mine.x - theirs.x
    best = { fp: other.fp, edge: edge, offset: offset, shared: shared }
  }
  return best ? { fp: best.fp, edge: best.edge, offset: best.offset } : null
}

function placeBeside(output, neighbour, edge, offset) {
  var size = logicalSize(output)
  var n = rectOf(neighbour)
  if (edge === "right") return { x: n.x + n.w, y: n.y + offset }
  if (edge === "left") return { x: n.x - size.w, y: n.y + offset }
  if (edge === "below") return { x: n.x + offset, y: n.y + n.h }
  return { x: n.x + offset, y: n.y - size.h }
}

function emptyMemory() {
  return { version: 1, monitors: {}, layouts: {} }
}

function normalizeMemory(memory) {
  var m = memory && typeof memory === "object" ? memory : {}
  return {
    version: 1,
    monitors: m.monitors && typeof m.monitors === "object" ? m.monitors : {},
    layouts: m.layouts && typeof m.layouts === "object" ? m.layouts : {}
  }
}

function hasMemory(memory) {
  var m = normalizeMemory(memory)
  return Object.keys(m.monitors).length > 0 || Object.keys(m.layouts).length > 0
}

// The saved layout sharing the most monitors with `fps`, most recent first.
function closestLayout(memory, fps) {
  var best = null
  for (var key in memory.layouts) {
    var layout = memory.layouts[key]
    if (!layout || !layout.outputs) continue
    var common = 0
    for (var i = 0; i < fps.length; i++) if (layout.outputs[fps[i]]) common++
    if (common === 0) continue
    var used = String(layout.lastUsed || "")
    if (!best || common > best.common || (common === best.common && used > best.used))
      best = { layout: layout, common: common, used: used }
  }
  return best ? best.layout : null
}

function cleanTextSize(value) {
  var n = Math.round(Number(value))
  return (isFinite(n) && n >= 9 && n <= 20) ? n : null
}

function cleanBrightness(value) {
  if (value === null || value === undefined || value === "") return null
  var n = Math.round(Number(value))
  return (isFinite(n) && n >= 1 && n <= 100) ? n : null
}

function cleanNightLight(value) {
  if (!value || typeof value !== "object") return null
  var kelvin = Math.round(Number(value.kelvin))
  if (!isFinite(kelvin) || kelvin < 1500 || kelvin > 6500) kelvin = 4000
  return { enabled: value.enabled === true, kelvin: kelvin }
}

function sameNightLight(a, b) {
  if (!a && !b) return true
  if (!a || !b) return false
  return a.enabled === b.enabled && a.kelvin === b.kelvin
}

// Remembered text size for the active combination of monitors (e.g. laptop-only
// vs docked desk). Omarchy's text size is OS-wide across all outputs, so
// storing it per monitor topology restores the right desk size on dock/undock
// without pretending two simultaneous monitors can have different system fonts.
function layoutTextSize(memory, plan) {
  var mem = normalizeMemory(memory)
  var active = (Array.isArray(plan) ? plan : []).filter(function(o) { return o && !o.autoOff })
  if (!active.length) return null
  var exact = mem.layouts[layoutKey(active.map(function(o) { return o.fp }))]
  if (exact && exact.textSize !== undefined && exact.textSize !== null)
    return cleanTextSize(exact.textSize)
  return null
}

function liveOutput(monitor, id) {
  var mode = { w: Number(monitor.width), h: Number(monitor.height), hz: Number(monitor.refreshRate) }
  return {
    name: id.name,
    fp: id.fp,
    selector: id.selector,
    internal: id.internal,
    enabled: monitor.disabled !== true,
    autoOff: false,
    offReason: "",
    mode: mode,
    x: Number(monitor.x) || 0,
    y: Number(monitor.y) || 0,
    scale: Number(monitor.scale) > 0 ? Number(monitor.scale) : 1,
    vrr: monitor.vrr === true ? 1 : Number(monitor.vrr) || 0,
    hdr: /^hdr/i.test(String(monitor.colorManagementPreset || "")),
    transform: Number(monitor.transform) || 0,
    ddcWatch: false,
    textSize: null,
    brightness: null,
    nightLight: null
  }
}

// The current state as a plan: what the panel starts editing from, and what a
// revert goes back to.
function planFromLive(monitors, memory, autoOff) {
  var mem = normalizeMemory(memory)
  var off = autoOff || {}
  var ids = identify(monitors)
  return monitors.map(function(m, i) {
    var out = liveOutput(m, ids[i])
    var profile = mem.monitors[out.fp]
    out.ddcWatch = !!(profile && profile.ddcWatch)
    // Hyprland reports whether VRR is active, not the Off/On/Fullscreen
    // setting, so the remembered setting is the one to edit and revert to.
    if (profile && [0, 1, 2].indexOf(Number(profile.vrr)) !== -1) out.vrr = Number(profile.vrr)
    if (profile) {
      out.textSize = cleanTextSize(profile.textSize)
      out.brightness = cleanBrightness(profile.brightness)
      out.nightLight = cleanNightLight(profile.nightLight)
    }
    if (off[out.fp]) {
      out.autoOff = true
      out.offReason = String(off[out.fp])
    }
    return out
  })
}

// The layout to run for the connected monitors. `autoOff` maps fingerprints
// to a reason ("lid", "input") for monitors that are connected but should be
// left off; the layout is chosen for the rest, as if they were unplugged.
function derive(monitors, memory, autoOff) {
  var mem = normalizeMemory(memory)
  var off = autoOff || {}
  var ids = identify(monitors)
  var outputs = []
  for (var i = 0; i < monitors.length; i++) {
    var m = monitors[i]
    var out = liveOutput(m, ids[i])
    var profile = mem.monitors[out.fp] || {}
    out.mode = pickMode(m.availableModes, profile.mode || null) || out.mode
    // A monitor with no memory keeps the scale Hyprland chose for it.
    var scale = Number(profile.scale) > 0 ? Number(profile.scale) : out.scale
    out.scale = cleanScale(scale, out.mode.w, out.mode.h)
    out.vrr = [0, 1, 2].indexOf(Number(profile.vrr)) !== -1 ? Number(profile.vrr) : 0
    out.hdr = profile.hdr === true && m.hdrCapable !== false
    out.ddcWatch = profile.ddcWatch === true
    out.textSize = cleanTextSize(profile.textSize)
    out.brightness = cleanBrightness(profile.brightness)
    out.nightLight = cleanNightLight(profile.nightLight)
    out.autoOff = !!off[out.fp]
    out.offReason = off[out.fp] ? String(off[out.fp]) : ""
    out.enabled = true
    outputs.push(out)
  }

  // Never leave nothing lit: if every monitor would be off, the internal
  // panel (or the first monitor) stays on.
  var active = outputs.filter(function(o) { return !o.autoOff })
  if (!active.length && outputs.length) {
    var keep = outputs.filter(function(o) { return o.internal })[0] || outputs[0]
    keep.autoOff = false
    keep.offReason = ""
    active = [keep]
  }
  var fps = active.map(function(o) { return o.fp })
  var exact = mem.layouts[layoutKey(fps)]
  var source = exact && exact.outputs ? exact : closestLayout(mem, fps)

  var known = {}
  // With nothing saved that shares a monitor, the arrangement Hyprland has
  // now is the starting point.
  if (!source) for (var l = 0; l < active.length; l++) known[active[l].fp] = true
  for (var a = 0; source && a < active.length; a++) {
    var saved = source && source.outputs ? source.outputs[active[a].fp] : null
    if (!saved) continue
    active[a].x = Number(saved.x) || 0
    active[a].y = Number(saved.y) || 0
    active[a].enabled = saved.enabled !== false
    known[active[a].fp] = true
  }
  // Monitors the chosen layout does not know return beside the neighbour they
  // were last used next to, when it is here; otherwise they go right of the
  // others, top-aligned.
  for (var n = 0; n < active.length; n++) {
    var output = active[n]
    if (known[output.fp]) continue
    var memo = (mem.monitors[output.fp] || {}).lastNeighbour
    var neighbour = null
    for (var k = 0; memo && k < active.length; k++)
      if (active[k].fp === memo.fp && known[active[k].fp] && active[k].enabled) neighbour = active[k]
    if (neighbour) {
      var spot = placeBeside(output, neighbour, memo.edge, Number(memo.offset) || 0)
      output.x = spot.x
      output.y = spot.y
    } else {
      var right = 0
      var top = Infinity
      for (var r = 0; r < active.length; r++) {
        if (!known[active[r].fp] || !active[r].enabled) continue
        var rect = rectOf(active[r])
        right = Math.max(right, rect.x + rect.w)
        top = Math.min(top, rect.y)
      }
      output.x = right
      output.y = top === Infinity ? 0 : top
    }
    known[output.fp] = true
  }
  if (!active.some(function(o) { return o.enabled })) active[0].enabled = true

  // Only the monitors left on are arranged; the solved positions are copied
  // back in place so the plan keeps the connected order.
  var solved = solve(active)
  for (var s = 0; s < solved.length; s++) {
    var index = outputs.indexOf(active[s])
    outputs[index] = solved[s]
  }
  return outputs
}

function outputMatchesLive(output, live) {
  if (!live) return false
  if (output.autoOff) return output.offReason !== "input" || live.enabled === false
  if (output.enabled !== live.enabled) return false
  if (!output.enabled) return true
  return sameMode(output.mode, live.mode) && output.x === live.x && output.y === live.y
    && Math.abs(output.scale - live.scale) < 0.001 && output.vrr === live.vrr
    && output.hdr === live.hdr
}

// True when applying `plan` would change nothing Hyprland shows.
function planMatchesLive(plan, livePlan) {
  for (var i = 0; i < plan.length; i++) {
    var live = null
    for (var j = 0; j < livePlan.length; j++) if (livePlan[j].name === plan[i].name) live = livePlan[j]
    if (!outputMatchesLive(plan[i], live)) return false
  }
  return true
}

// Applying a new mode can leave a monitor black, so the panel asks for a
// confirmation (and reverts on its own) only when a mode or an output's
// on/off state changes.
function needsConfirmation(plan, livePlan) {
  for (var i = 0; i < plan.length; i++) {
    var live = null
    for (var j = 0; j < livePlan.length; j++) if (livePlan[j].name === plan[i].name) live = livePlan[j]
    if (!live || plan[i].autoOff) continue
    if (plan[i].enabled !== live.enabled) return true
    if (plan[i].enabled && (!sameMode(plan[i].mode, live.mode) || plan[i].hdr !== live.hdr)) return true
  }
  return false
}

function copyMemory(memory) {
  return JSON.parse(JSON.stringify(normalizeMemory(memory)))
}

// The memory after the user presses Apply on `plan`: every monitor's own
// settings, and the arrangement (plus optional OS-wide text size) for this
// combination of monitors.
function remember(memory, plan, now, options) {
  var mem = copyMemory(memory)
  var opts = options && typeof options === "object" ? options : {}
  var members = plan.filter(function(o) { return !o.autoOff })
  var key = layoutKey(members.map(function(o) { return o.fp }))
  var prevLayout = mem.layouts[key] || {}
  var layout = { members: members.map(function(o) { return o.fp }).sort(), lastUsed: String(now || ""), outputs: {} }
  var savedTextSize = cleanTextSize(opts.textSize !== undefined ? opts.textSize : prevLayout.textSize)
  if (savedTextSize !== null) layout.textSize = savedTextSize
  for (var i = 0; i < plan.length; i++) {
    var o = plan[i]
    var previous = mem.monitors[o.fp] || {}
    if (!o.autoOff) layout.outputs[o.fp] = { x: o.x, y: o.y, enabled: o.enabled }
    // An output that is off reports the last mode it ran, which is not a new
    // choice; keep what was saved for it.
    if (!o.enabled || o.autoOff) {
      if (mem.monitors[o.fp]) {
        if (o.ddcWatch !== (previous.ddcWatch === true)) {
          mem.monitors[o.fp].ddcWatch = o.ddcWatch === true
          if (!o.ddcWatch) delete mem.monitors[o.fp].ddcInput
        }
        continue
      }
    }
    var profile = {
      mode: { w: o.mode.w, h: o.mode.h, hz: o.mode.hz },
      scale: o.scale,
      vrr: o.vrr,
      hdr: o.hdr === true,
      ddcWatch: o.ddcWatch === true
    }
    var brightness = cleanBrightness(o.brightness !== null && o.brightness !== undefined ? o.brightness : previous.brightness)
    if (brightness !== null) profile.brightness = brightness
    // The input a watched monitor is expected on is learned when the watch is
    // switched on, and kept while it stays on.
    if (profile.ddcWatch && previous.ddcWatch && previous.ddcInput) profile.ddcInput = previous.ddcInput
    var neighbour = o.enabled && !o.autoOff ? neighbourOf(o, members) : null
    if (neighbour) profile.lastNeighbour = neighbour
    else if (previous.lastNeighbour) profile.lastNeighbour = previous.lastNeighbour
    mem.monitors[o.fp] = profile
  }
  mem.layouts[key] = layout

  var keys = Object.keys(mem.layouts)
  if (keys.length > MAX_LAYOUTS) {
    keys.sort(function(a, b) {
      return String(mem.layouts[a].lastUsed || "") < String(mem.layouts[b].lastUsed || "") ? -1 : 1
    })
    for (var d = 0; d < keys.length - MAX_LAYOUTS; d++) delete mem.layouts[keys[d]]
  }
  return mem
}

// One DDC/CI reading of a watched monitor's input source. Two misses in a row
// (another input selected, or no answer at all because it was switched off)
// turn it off; reading its own input again turns it back on.
function ddcStep(misses, reading, expected) {
  var count = Number(misses) || 0
  if (reading && expected && String(reading).toLowerCase() === String(expected).toLowerCase())
    return { misses: 0, off: false }
  count++
  return { misses: count, off: count >= 2 }
}

// Hotplug handling applies the same plan at most twice in 20 seconds. The
// second try covers a config reload (from our own monitors.lua write)
// undoing the first; the limit stops a loop when Hyprland won't hold a plan.
function autoApplyStep(last, signature, now) {
  var same = !!last && last.signature === signature && now - last.at < 20000
  var count = same ? last.count + 1 : 1
  return { allowed: count <= 2, last: { signature: signature, at: same ? last.at : now, count: count } }
}

// Monitors that should be left off right now, keyed by fingerprint. The
// internal panel is off while the lid is shut and another monitor is
// connected; Omarchy's clamshell handling turns it off and on.
function autoOffFor(plan, lidClosed, ddcOff) {
  var out = {}
  var others = plan.filter(function(o) { return !o.internal }).length
  for (var i = 0; i < plan.length; i++) {
    var o = plan[i]
    if (o.internal && lidClosed && others > 0) out[o.fp] = "lid"
    else if (ddcOff && ddcOff[o.fp]) out[o.fp] = "input"
  }
  return out
}

if (typeof module !== "undefined") module.exports = {}

.pragma library

// Pure display-layout logic for the Displays panel and its hotplug service.
// Nothing here touches Hyprland or the filesystem, so tests run it through
// Qt's own JavaScript engine. display_ctl.py turns the plans built here into
// Hyprland rules.
//
// A plan is a list of outputs:
//   { name, fp, selector, internal, enabled, autoOff, offReason,
//     mode: { w, h, hz }, x, y, scale, vrr, hdr, sdrBrightness, sdrSaturation,
//     transform, mirrorOf, parkMirrorOf, brightness }
// `autoOff` marks a monitor that is connected but off for now: "lid" (the
// laptop is shut; Omarchy's clamshell handling owns the panel) or "input"
// (the monitor was switched to another input; this bar pauses it and wakes it
// when it comes back — see the auto-pause state machine at the end of this
// file). An input pause seen through HDMI SCDC is "mirror-parked": the monitor
// stays lit as a mirror of `parkMirrorOf` (live only, never in monitors.lua),
// so its link stays up and the moment it relocks it is unparked; one seen
// through DDC/CI is simply disabled.
// Memory (displays.json) keeps two things apart:
//   monitors[fp]  settings that belong to one monitor wherever it is plugged in
//   layouts[key]  positions, on/off, mirror, text size & night light for one set
// Learned DDC/CI inputs live separately in ddc.json ({ monitors: { fp: { input,
// connector, learnedAt } } }), written only by display_ctl.py's ddc-learn.

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

function cleanTransform(value) {
  var n = Math.round(Number(value))
  return (isFinite(n) && n >= 0 && n <= 7) ? n : 0
}

function cleanSdrBrightness(value) {
  var n = Number(value)
  if (!isFinite(n) || n <= 0) return 1
  return Math.round(Math.max(0.5, Math.min(2.0, n)) * 100) / 100
}

function cleanSdrSaturation(value) {
  var n = Number(value)
  if (!isFinite(n) || n <= 0) return 1
  return Math.round(Math.max(0.5, Math.min(1.5, n)) * 100) / 100
}

function cleanMirrorOf(value, monitors) {
  var s = clean(value)
  if (!s || s.toLowerCase() === "none") return ""
  // Hyprland 0.56+ may report mirrorOf as a numeric monitor ID string (e.g. "0").
  if (/^\d+$/.test(s) && Array.isArray(monitors)) {
    var idNum = parseInt(s, 10)
    for (var i = 0; i < monitors.length; i++) {
      if (monitors[i] && Number(monitors[i].id) === idNum && monitors[i].name)
        return clean(monitors[i].name)
    }
  }
  return s
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
// apart by EDID, so only those get the connector name appended. Hyprland also
// prefix-matches `desc:` selectors, so if one description is a prefix of
// another connected or previously remembered monitor's description, fall back
// to the connector name. Internal panels are always addressed by connector name.
function identify(monitors, memory) {
  var counts = {}
  var list = Array.isArray(monitors) ? monitors : []
  var descs = []
  for (var i = 0; i < list.length; i++) {
    var fp = baseFingerprint(list[i])
    counts[fp] = (counts[fp] || 0) + 1
    descs.push(clean(list[i].description))
  }
  var knownDescs = []
  var memMonitors = memory && memory.monitors && typeof memory.monitors === "object" ? memory.monitors : null
  if (memMonitors) {
    for (var mfp in memMonitors) {
      var d = clean(memMonitors[mfp] && memMonitors[mfp].description)
      if (d && knownDescs.indexOf(d) === -1) knownDescs.push(d)
    }
  }
  var out = []
  for (var j = 0; j < list.length; j++) {
    var m = list[j]
    var base = baseFingerprint(m)
    var unique = counts[base] === 1
    var description = descs[j]
    var internal = isInternalName(m.name)
    var descSafe = !internal && unique && !!description
    if (descSafe) {
      for (var k = 0; k < descs.length; k++) {
        if (k === j || !descs[k]) continue
        if (descs[k].indexOf(description) === 0 || description.indexOf(descs[k]) === 0) {
          descSafe = false
          break
        }
      }
    }
    if (descSafe && knownDescs.length) {
      for (var r = 0; r < knownDescs.length; r++) {
        var kd = knownDescs[r]
        if (kd === description) continue
        if (kd.indexOf(description) === 0 || description.indexOf(kd) === 0) {
          descSafe = false
          break
        }
      }
    }
    out.push({
      name: clean(m.name),
      fp: unique ? base : base + "|" + clean(m.name),
      selector: descSafe ? "desc:" + description : clean(m.name),
      description: description,
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
  if (!groups.length) return (wanted && wanted.w > 0 && wanted.h > 0 && wanted.hz > 0) ? wanted : null
  if (wanted && wanted.w > 0 && wanted.h > 0) {
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

// When re-enabling a disabled monitor whose live mode was reported as 0x0,
// choose a usable mode from availableModes (preferring any remembered mode).
function ensureEnabledMode(output, availableModes, savedProfile) {
  if (output && output.mode && output.mode.w > 0 && output.mode.h > 0 && output.mode.hz > 0)
    return { w: output.mode.w, h: output.mode.h, hz: output.mode.hz }
  var wanted = savedProfile && savedProfile.mode ? savedProfile.mode : null
  return pickMode(availableModes, wanted)
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

// Guarantees Hyprland mirror invariants: an output cannot mirror itself,
// cannot mirror a disabled/autoOff output, and cannot mirror another mirror.
function sanitizeMirrors(outputs) {
  var byName = {}
  for (var i = 0; i < outputs.length; i++) {
    outputs[i].mirrorOf = cleanMirrorOf(outputs[i].mirrorOf)
    byName[outputs[i].name] = outputs[i]
  }
  for (var j = 0; j < outputs.length; j++) {
    var o = outputs[j]
    if (!o.mirrorOf) continue
    if (!o.enabled || o.autoOff) {
      o.mirrorOf = ""
      continue
    }
    var target = byName[o.mirrorOf]
    if (!target || target === o || !target.enabled || target.autoOff) {
      o.mirrorOf = ""
    }
  }
  for (var k = 0; k < outputs.length; k++) {
    var m = outputs[k]
    if (m.mirrorOf && byName[m.mirrorOf] && byName[m.mirrorOf].mirrorOf) {
      m.mirrorOf = ""
    }
  }
  return outputs
}

// Moves `rect` the shortest distance to a spot where it touches the placed
// rectangles along an edge without covering any of them, and returns both
// the snapped rectangle and any active alignment guide line (edge or H/V center).
// Hyprland lets the pointer fall into gaps between monitors, so every monitor
// must touch the rest. `alignWithin` magnetically snaps edges and centers into
// line when within that logical-pixel threshold, always picking the closest
// matching guide line.
function overlapsAny(rect, placed) {
  for (var i = 0; i < placed.length; i++) if (overlaps(rect, placed[i])) return true
  return false
}

// Pointer-anchored canvas drag math: computes logical coordinates from screen
// deltas relative to the current drag anchor so minimum-sized tiles never jump
// on initial grab and viewport expansion never creates zoom feedback.
function createDragAnchor(pointer, startRect, viewK) {
  var k = Number(viewK) > 0 ? Number(viewK) : 1
  return {
    screenX: Number(pointer && pointer.x) || 0,
    screenY: Number(pointer && pointer.y) || 0,
    logicalX: startRect.x,
    logicalY: startRect.y,
    w: startRect.w,
    h: startRect.h,
    k: k
  }
}

function dragWantedRect(anchor, pointer) {
  var k = Number(anchor && anchor.k) > 0 ? Number(anchor.k) : 1
  return {
    x: Math.round(anchor.logicalX + ((Number(pointer && pointer.x) || 0) - anchor.screenX) / k),
    y: Math.round(anchor.logicalY + ((Number(pointer && pointer.y) || 0) - anchor.screenY) / k),
    w: anchor.w,
    h: anchor.h
  }
}

function rebaseDragAnchor(anchor, pointer, wanted, newViewK) {
  var k = Number(newViewK) > 0 ? Number(newViewK) : anchor.k
  return {
    screenX: Number(pointer && pointer.x) || 0,
    screenY: Number(pointer && pointer.y) || 0,
    logicalX: wanted.x,
    logicalY: wanted.y,
    w: anchor.w,
    h: anchor.h,
    k: k
  }
}

function attachWithGuides(rect, placed, alignWithin) {
  if (!placed.length) return { rect: { x: rect.x, y: rect.y, w: rect.w, h: rect.h }, snap: null }
  var snap = Number(alignWithin) > 0 ? Number(alignWithin) : 0
  var free = !overlapsAny(rect, placed)
  var touching = false
  for (var i = 0; i < placed.length; i++) {
    if (sharedEdge(rect, placed[i]) > 0) touching = true
  }
  if (free && touching && !snap) return { rect: { x: rect.x, y: rect.y, w: rect.w, h: rect.h }, snap: null }

  // Group bounding box so dragging also assists snapping to the overall
  // horizontal or vertical center of multi-monitor arrangements.
  var gMinX = Infinity, gMinY = Infinity, gMaxX = -Infinity, gMaxY = -Infinity
  for (var g = 0; g < placed.length; g++) {
    gMinX = Math.min(gMinX, placed[g].x)
    gMinY = Math.min(gMinY, placed[g].y)
    gMaxX = Math.max(gMaxX, placed[g].x + placed[g].w)
    gMaxY = Math.max(gMaxY, placed[g].y + placed[g].h)
  }

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
      var base = { x: sides[s].x, y: sides[s].y, w: rect.w, h: rect.h }
      var c = { x: base.x, y: base.y, w: base.w, h: base.h }
      var matchedGuide = null
      if (snap) {
        var candidates = []
        if (sides[s].axis === "y") {
          for (var t = 0; t < placed.length; t++) {
            var refY = placed[t]
            candidates.push({ pos: refY.y, guidePos: refY.y, kind: "top", label: "Top aligned" })
            candidates.push({ pos: refY.y + refY.h - base.h, guidePos: refY.y + refY.h, kind: "bottom", label: "Bottom aligned" })
            candidates.push({
              pos: refY.y + Math.round((refY.h - base.h) / 2),
              guidePos: refY.y + Math.round(refY.h / 2),
              kind: "v-center",
              label: "V-centered"
            })
          }
          if (placed.length > 1) {
            var gH = gMaxY - gMinY
            candidates.push({
              pos: gMinY + Math.round((gH - base.h) / 2),
              guidePos: gMinY + Math.round(gH / 2),
              kind: "v-center",
              label: "V-centered"
            })
          }
        } else {
          for (var u = 0; u < placed.length; u++) {
            var refX = placed[u]
            candidates.push({ pos: refX.x, guidePos: refX.x, kind: "left", label: "Left aligned" })
            candidates.push({ pos: refX.x + refX.w - base.w, guidePos: refX.x + refX.w, kind: "right", label: "Right aligned" })
            candidates.push({
              pos: refX.x + Math.round((refX.w - base.w) / 2),
              guidePos: refX.x + Math.round(refX.w / 2),
              kind: "h-center",
              label: "H-centered"
            })
          }
          if (placed.length > 1) {
            var gW = gMaxX - gMinX
            candidates.push({
              pos: gMinX + Math.round((gW - base.w) / 2),
              guidePos: gMinX + Math.round(gW / 2),
              kind: "h-center",
              label: "H-centered"
            })
          }
        }
        var currentVal = sides[s].axis === "y" ? base.y : base.x
        var within = []
        for (var l = 0; l < candidates.length; l++) {
          var cand = candidates[l]
          var delta = Math.abs(currentVal - cand.pos)
          if (delta <= snap) within.push({ pos: cand.pos, guidePos: cand.guidePos, kind: cand.kind, label: cand.label, delta: delta })
        }
        within.sort(function(a, b) { return a.delta - b.delta })
        // Choose the nearest valid (unobstructed, edge-sharing) alignment so an
        // obstructed guide never discards a valid unsnapped side placement.
        for (var wIdx = 0; wIdx < within.length; wIdx++) {
          var wCand = within[wIdx]
          var snappedC = {
            x: sides[s].axis === "x" ? wCand.pos : base.x,
            y: sides[s].axis === "y" ? wCand.pos : base.y,
            w: base.w,
            h: base.h
          }
          if (sharedEdge(snappedC, o) > 0 && !overlapsAny(snappedC, placed)) {
            c = snappedC
            matchedGuide = {
              axis: sides[s].axis,
              guidePos: wCand.guidePos,
              kind: wCand.kind,
              label: wCand.label
            }
            break
          }
        }
      }
      if (overlapsAny(c, placed)) continue
      var dx = c.x - rect.x
      var dy = c.y - rect.y
      var distance = dx * dx + dy * dy
      if (!best || distance < best.distance) best = { x: c.x, y: c.y, distance: distance, snap: matchedGuide }
    }
  }
  if (!best) {
    var right = -Infinity
    var top = Infinity
    for (var b = 0; b < placed.length; b++) {
      right = Math.max(right, placed[b].x + placed[b].w)
      top = Math.min(top, placed[b].y)
    }
    best = { x: right, y: top, snap: null }
  }
  return { rect: { x: best.x, y: best.y, w: rect.w, h: rect.h }, snap: best.snap }
}

function attach(rect, placed, alignWithin) {
  return attachWithGuides(rect, placed, alignWithin).rect
}

function byPosition(a, b) {
  return (a.x - b.x) || (a.y - b.y)
}

function layoutValid(result, order) {
  if (order.length <= 1) return true
  var rects = order.map(function(idx) { return rectOf(result[idx]) })
  for (var i = 0; i < rects.length; i++) {
    for (var j = i + 1; j < rects.length; j++) {
      if (overlaps(rects[i], rects[j])) return false
    }
  }
  var seen = [0]
  var visited = { 0: true }
  for (var q = 0; q < seen.length; q++) {
    var cur = seen[q]
    for (var k = 0; k < rects.length; k++) {
      if (visited[k]) continue
      if (sharedEdge(rects[cur], rects[k]) > 0) {
        visited[k] = true
        seen.push(k)
      }
    }
  }
  return seen.length === rects.length
}

// Closes gaps and removes overlaps among the enabled, non-mirrored outputs,
// moving each as little as possible, then puts the layout's top-left corner
// at 0,0. `fixed` (an index) is the output the user is dragging: it is placed
// last so the others hold still and it snaps against them.
function solve(outputs, fixed, alignWithin) {
  var result = outputs.map(function(o) {
    var copy = {}
    for (var k in o) copy[k] = o[k]
    if (o.mode) copy.mode = { w: o.mode.w, h: o.mode.h, hz: o.mode.hz }
    return copy
  })
  sanitizeMirrors(result)
  var order = []
  for (var i = 0; i < result.length; i++)
    if (result[i].enabled && !result[i].autoOff && !result[i].mirrorOf) order.push(i)
  var placed = []
  if (fixed === undefined && layoutValid(result, order)) {
    for (var v = 0; v < order.length; v++) placed.push(rectOf(result[order[v]]))
  } else {
    order.sort(function(a, b) {
      if (a === fixed) return 1
      if (b === fixed) return -1
      return byPosition(rectOf(result[a]), rectOf(result[b]))
    })
    for (var j = 0; j < order.length; j++) {
      var output = result[order[j]]
      var r = attach(rectOf(output), placed, order[j] === fixed ? alignWithin : 0)
      output.x = r.x
      output.y = r.y
      placed.push(r)
    }
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
  var byName = {}
  for (var b = 0; b < result.length; b++) byName[result[b].name] = result[b]
  for (var m = 0; m < result.length; m++) {
    if (result[m].mirrorOf && byName[result[m].mirrorOf]) {
      result[m].x = byName[result[m].mirrorOf].x
      result[m].y = byName[result[m].mirrorOf].y
    }
  }
  return result
}

// One-click multi-monitor layout presets: "extend", "external", "internal", "mirror".
function applyLayoutPreset(outputs, preset) {
  var list = (Array.isArray(outputs) ? outputs : []).map(function(o) {
    var c = {}
    for (var k in o) c[k] = o[k]
    if (o.mode) c.mode = { w: o.mode.w, h: o.mode.h, hz: o.mode.hz }
    return c
  })
  var avail = list.filter(function(o) { return !o.autoOff })
  if (avail.length < 2) return solve(list)

  var hasInternal = avail.some(function(o) { return o.internal })
  var hasExternal = avail.some(function(o) { return !o.internal })

  if (preset === "extend") {
    for (var i = 0; i < avail.length; i++) {
      avail[i].enabled = true
      avail[i].mirrorOf = ""
    }
  } else if (preset === "external" && hasExternal) {
    for (var j = 0; j < avail.length; j++) {
      avail[j].enabled = !avail[j].internal
      avail[j].mirrorOf = ""
    }
  } else if (preset === "internal" && hasInternal) {
    for (var k = 0; k < avail.length; k++) {
      avail[k].enabled = !!avail[k].internal
      avail[k].mirrorOf = ""
    }
  } else if (preset === "mirror") {
    var source = avail.filter(function(o) { return o.internal })[0] || avail[0]
    for (var m = 0; m < avail.length; m++) {
      avail[m].enabled = true
      avail[m].mirrorOf = avail[m] === source ? "" : source.name
    }
  }
  return solve(list)
}

function activeLayoutPreset(outputs) {
  var avail = (Array.isArray(outputs) ? outputs : []).filter(function(o) { return o && !o.autoOff })
  if (avail.length < 2) return ""
  var enabled = avail.filter(function(o) { return o.enabled })
  if (!enabled.length) return ""
  var mirrored = enabled.filter(function(o) { return !!cleanMirrorOf(o.mirrorOf) })
  if (mirrored.length > 0 && mirrored.length === enabled.length - 1 && enabled.length === avail.length)
    return "mirror"
  if (mirrored.length > 0) return ""
  var hasInternal = avail.some(function(o) { return o.internal })
  var hasExternal = avail.some(function(o) { return !o.internal })
  if (hasInternal && hasExternal) {
    var allExtOn = avail.every(function(o) { return o.internal ? !o.enabled : o.enabled })
    if (allExtOn) return "external"
    var allIntOn = avail.every(function(o) { return o.internal ? o.enabled : !o.enabled })
    if (allIntOn) return "internal"
  }
  if (enabled.length === avail.length) return "extend"
  return ""
}

// Which side of `other` an output sits on, and its offset along that edge, so
// a monitor can return beside the same neighbour in a combination it has not
// been used in before.
function neighbourOf(output, outputs) {
  if (output.mirrorOf) return null
  var mine = rectOf(output)
  var best = null
  for (var i = 0; i < outputs.length; i++) {
    var other = outputs[i]
    if (other === output || !other.enabled || other.mirrorOf || other.fp === output.fp) continue
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
  if (a.enabled !== b.enabled) return false
  return !a.enabled || Math.abs(Number(a.kelvin) - Number(b.kelvin)) < 50
}

// Remembered text size for the active combination of monitors (e.g. laptop-only
// vs docked desk). Omarchy's text size is OS-wide across all outputs, so
// storing it per monitor topology restores the right desk size on dock/undock.
function layoutTextSize(memory, plan) {
  var mem = normalizeMemory(memory)
  var active = (Array.isArray(plan) ? plan : []).filter(function(o) { return o && !o.autoOff })
  if (!active.length) return null
  var exact = mem.layouts[layoutKey(active.map(function(o) { return o.fp }))]
  if (exact && exact.textSize !== undefined && exact.textSize !== null)
    return cleanTextSize(exact.textSize)
  return null
}

// Remembered Night Light state for the active monitor combination.
function layoutNightLight(memory, plan) {
  var mem = normalizeMemory(memory)
  var active = (Array.isArray(plan) ? plan : []).filter(function(o) { return o && !o.autoOff })
  if (!active.length) return null
  var exact = mem.layouts[layoutKey(active.map(function(o) { return o.fp }))]
  if (exact && exact.nightLight) return cleanNightLight(exact.nightLight)
  return null
}

// Removes a monitor's saved profile and any saved layouts that included it.
function forgetMonitor(memory, fp) {
  var mem = copyMemory(memory)
  var target = clean(fp)
  if (!target) return mem
  delete mem.monitors[target]
  for (var key in mem.layouts) {
    var layout = mem.layouts[key]
    var members = layout && Array.isArray(layout.members) ? layout.members : []
    var inOutputs = layout && layout.outputs && Object.prototype.hasOwnProperty.call(layout.outputs, target)
    if (members.indexOf(target) !== -1 || inOutputs) delete mem.layouts[key]
  }
  return mem
}

function forgetLayout(memory, plan) {
  var mem = copyMemory(memory)
  var active = (Array.isArray(plan) ? plan : []).filter(function(o) { return o && !o.autoOff })
  if (!active.length) return mem
  delete mem.layouts[layoutKey(active.map(function(o) { return o.fp }))]
  return mem
}

function liveOutput(monitor, id, allMonitors) {
  var mode = { w: Number(monitor.width), h: Number(monitor.height), hz: Number(monitor.refreshRate) }
  return {
    name: id.name,
    fp: id.fp,
    selector: id.selector,
    description: id.description || clean(monitor.description),
    internal: id.internal,
    enabled: monitor.disabled !== true,
    autoOff: false,
    offReason: "",
    mode: mode,
    x: Number(monitor.x) || 0,
    y: Number(monitor.y) || 0,
    scale: Number(monitor.scale) > 0 ? Number(monitor.scale) : 1,
    vrr: monitor.vrr === true ? 1 : Number(monitor.vrr) || 0,
    vrrManaged: true,
    hdr: /^hdr/i.test(String(monitor.colorManagementPreset || "")),
    sdrBrightness: cleanSdrBrightness(monitor.sdrBrightness),
    sdrSaturation: cleanSdrSaturation(monitor.sdrSaturation),
    transform: cleanTransform(monitor.transform),
    mirrorOf: cleanMirrorOf(monitor.mirrorOf, allMonitors),
    parkMirrorOf: "",
    pauseMode: "",
    textSize: null,
    brightness: null,
    nightLight: null
  }
}

// The bar-owned targets `appliedParks` lists for `connectorName`, as an array.
function appliedParkTargets(appliedParks, connectorName) {
  var parks = appliedParks && typeof appliedParks === "object" ? appliedParks : {}
  var name = clean(connectorName)
  if (!name || !Object.prototype.hasOwnProperty.call(parks, name)) return []
  var raw = Array.isArray(parks[name]) ? parks[name] : [parks[name]]
  var out = []
  for (var i = 0; i < raw.length; i++) {
    var t = cleanMirrorOf(raw[i])
    if (t && out.indexOf(t) === -1) out.push(t)
  }
  return out
}

// `liveMirror` (the target connector `connectorName` mirrors live) when it is
// the bar's own park, else "". Ownership is only ever explicit: `appliedParks`
// ({ connector: [target, ...] }, the parks the bar applied and has not yet
// seen undone) must list that target. How a monitor is watched or paused
// never claims a mirror; any other mirror is the user's and is kept.
function liveParkOf(connectorName, liveMirror, appliedParks) {
  var target = cleanMirrorOf(liveMirror)
  if (!target) return ""
  return appliedParkTargets(appliedParks, connectorName).indexOf(target) !== -1 ? target : ""
}

// liveParkOf for a Hyprland monitor: "" when it is disabled or not a mirror.
function liveParkOfMonitor(monitor, allMonitors, appliedParks) {
  if (!monitor || monitor.disabled === true) return ""
  return liveParkOf(monitor.name, cleanMirrorOf(monitor.mirrorOf, allMonitors), appliedParks)
}

// The current state as a plan: what the panel starts editing from, and what a
// revert goes back to. `pauseModes` ({ fp: "mirror" | "disable" }, from
// ddcPauseModes) says how an input-paused monitor is kept off; `appliedParks`
// (see liveParkOf) which live mirrors are the bar's own parks. A park is
// reported as `parkMirrorOf` (paused, or still parked after a wake until the
// reconcile undoes it), never as a mirror the user set up. A mirror-paused
// monitor that is not parked live is reported as it is: on, not paused.
function planFromLive(monitors, memory, autoOff, pauseModes, appliedParks) {
  var mem = normalizeMemory(memory)
  var off = autoOff || {}
  var modes = pauseModes || {}
  var ids = identify(monitors, mem)
  var activeNames = {}
  for (var a = 0; a < monitors.length; a++) {
    var lm = monitors[a]
    if (lm && lm.name && lm.disabled !== true && !cleanMirrorOf(lm.mirrorOf, monitors)) activeNames[clean(lm.name)] = true
  }
  return monitors.map(function(m, i) {
    var out = liveOutput(m, ids[i], monitors)
    var profile = mem.monitors[out.fp]
    // Hyprland reports whether VRR is active, not the Off/On/Fullscreen
    // setting, so the remembered setting is the one to edit and revert to.
    if (profile && [0, 1, 2].indexOf(Number(profile.vrr)) !== -1) out.vrr = Number(profile.vrr)
    if (profile) {
      out.textSize = cleanTextSize(profile.textSize)
      out.brightness = cleanBrightness(profile.brightness)
      out.nightLight = cleanNightLight(profile.nightLight)
      if (!out.hdr) {
        if (profile.sdrBrightness !== undefined) out.sdrBrightness = cleanSdrBrightness(profile.sdrBrightness)
        if (profile.sdrSaturation !== undefined) out.sdrSaturation = cleanSdrSaturation(profile.sdrSaturation)
      }
    }
    var pausedAsMirror = off[out.fp] === "input" && modes[out.fp] === "mirror"
    var park = liveParkOfMonitor(m, monitors, appliedParks)
    if (park) {
      // Woken but still parked live, it keeps `parkMirrorOf` as a marker, so
      // the plan that unparks it is never mistaken for one already applied.
      out.mirrorOf = ""
      out.parkMirrorOf = park
    }
    if (off[out.fp]) {
      out.autoOff = true
      out.offReason = String(off[out.fp])
      if (out.offReason === "input") out.pauseMode = modes[out.fp] === "mirror" || modes[out.fp] === "disable" ? modes[out.fp] : ""
      if (out.parkMirrorOf && !(out.offReason === "input" && park !== out.name && activeNames[park])) out.parkMirrorOf = ""
      if (pausedAsMirror && !out.parkMirrorOf) {
        // Not parked (yet): it is lit and extended, never "off".
        out.autoOff = false
        out.offReason = ""
        out.pauseMode = ""
      }
    }
    return out
  })
}

// The active output an input-paused monitor is parked on as a mirror: enabled,
// not off for now, not itself a mirror or parked. The internal panel first
// while the lid is open, else the first such external; "" when there is none.
function pickParkMirrorTarget(outputs, pausedFp, lidClosed) {
  var list = Array.isArray(outputs) ? outputs : []
  var paused = null
  for (var p = 0; p < list.length; p++) if (list[p] && list[p].fp === pausedFp) paused = list[p]
  var candidates = list.filter(function(o) {
    return !!o && o.fp !== pausedFp && (!paused || o.name !== paused.name) && o.enabled !== false
      && !o.autoOff && !cleanMirrorOf(o.mirrorOf) && !cleanMirrorOf(o.parkMirrorOf) && !!clean(o.name)
  })
  var internal = candidates.filter(function(o) { return o.internal })
  var external = candidates.filter(function(o) { return !o.internal })
  var ordered = lidClosed ? external.concat(internal) : internal.concat(external)
  return ordered.length ? clean(ordered[0].name) : ""
}

function isParked(o) {
  return !!o && o.autoOff === true && o.offReason === "input" && !!cleanMirrorOf(o.parkMirrorOf)
}

function lidShutIn(plan) {
  return (Array.isArray(plan) ? plan : []).some(function(o) { return o && o.autoOff && o.offReason === "lid" })
}

// A plan about to be applied keeps every mirror-park valid: a parked monitor
// whose target is no longer an active output (or a mirror-pausing one with no
// target yet) is parked on another one, or, with none left, turned back on:
// never left as an unparkable mirror, and never `autoOff` with no park (which
// would disable it and drop the link it relocks on).
function settleParkedMirrors(plan) {
  var list = (Array.isArray(plan) ? plan : []).map(function(o) {
    var c = {}
    for (var k in o) c[k] = o[k]
    if (o.mode) c.mode = { w: o.mode.w, h: o.mode.h, hz: o.mode.hz }
    return c
  })
  var lid = lidShutIn(list)
  for (var i = 0; i < list.length; i++) {
    var o = list[i]
    var inputPaused = o.autoOff === true && o.offReason === "input"
    if (!inputPaused) {
      o.parkMirrorOf = ""
      o.pauseMode = ""
      continue
    }
    if (!cleanMirrorOf(o.parkMirrorOf) && o.pauseMode !== "mirror") {
      o.parkMirrorOf = ""
      continue
    }
    var target = null
    for (var j = 0; j < list.length; j++) if (list[j].name === cleanMirrorOf(o.parkMirrorOf)) target = list[j]
    var valid = target && target !== o && target.enabled !== false && !target.autoOff
      && !cleanMirrorOf(target.mirrorOf) && !cleanMirrorOf(target.parkMirrorOf)
    if (valid) {
      o.pauseMode = "mirror"
      continue
    }
    o.parkMirrorOf = ""
    var next = pickParkMirrorTarget(list, o.fp, lid)
    if (next) {
      o.parkMirrorOf = next
      o.pauseMode = "mirror"
    } else {
      o.autoOff = false
      o.offReason = ""
      o.pauseMode = ""
      o.enabled = true
    }
  }
  return list
}

// The layout to run for the connected monitors. `autoOff` maps fingerprints
// to a reason ("lid", "input") for monitors that are connected but should be
// left off; the layout is chosen for the rest, as if they were unplugged.
// Monitors without a saved profile keep their live mode/VRR/HDR/scale/rotation.
// An input-paused monitor whose `pauseModes` entry is "mirror" is parked on an
// active output (`parkMirrorOf`, `pauseMode: "mirror"`); with no output to
// park on it is not paused. `appliedParks` (see liveParkOf) keeps the bar's
// own live parks from being taken for mirrors the user set up.
function derive(monitors, memory, autoOff, pauseModes, appliedParks) {
  var modes = pauseModes || {}
  var outputs = deriveLayout(monitors, memory, autoOff, modes, appliedParks)
  var lid = lidShutIn(outputs)
  var unparkable = []
  for (var i = 0; i < outputs.length; i++) {
    var o = outputs[i]
    if (!(o.autoOff && o.offReason === "input" && modes[o.fp] === "mirror")) continue
    o.parkMirrorOf = pickParkMirrorTarget(outputs, o.fp, lid)
    if (!o.parkMirrorOf) unparkable.push(o.fp)
  }
  if (!unparkable.length) return outputs
  var off = {}
  for (var fp in autoOff || {}) if (unparkable.indexOf(fp) === -1) off[fp] = autoOff[fp]
  return derive(monitors, memory, off, modes, appliedParks)
}

function deriveLayout(monitors, memory, autoOff, pauseModes, appliedParks) {
  var modes = pauseModes || {}
  var mem = normalizeMemory(memory)
  var off = autoOff || {}
  var ids = identify(monitors, mem)
  var outputs = []
  for (var i = 0; i < monitors.length; i++) {
    var m = monitors[i]
    var out = liveOutput(m, ids[i], monitors)
    var hasProfile = Object.prototype.hasOwnProperty.call(mem.monitors, out.fp)
    var profile = hasProfile ? (mem.monitors[out.fp] || {}) : null
    if (hasProfile) {
      out.mode = pickMode(m.availableModes, profile.mode || out.mode) || out.mode
      var scale = Number(profile.scale) > 0 ? Number(profile.scale) : out.scale
      out.scale = cleanScale(scale, out.mode.w, out.mode.h)
      out.vrr = [0, 1, 2].indexOf(Number(profile.vrr)) !== -1 ? Number(profile.vrr) : out.vrr
      out.vrrManaged = true
      out.hdr = profile.hdr === true && m.hdrCapable !== false
      out.sdrBrightness = profile.sdrBrightness !== undefined ? cleanSdrBrightness(profile.sdrBrightness) : out.sdrBrightness
      out.sdrSaturation = profile.sdrSaturation !== undefined ? cleanSdrSaturation(profile.sdrSaturation) : out.sdrSaturation
      out.transform = profile.transform !== undefined ? cleanTransform(profile.transform) : out.transform
      out.textSize = cleanTextSize(profile.textSize)
      out.brightness = cleanBrightness(profile.brightness)
      out.nightLight = cleanNightLight(profile.nightLight)
    } else {
      // Unprofiled monitor: preserve whatever live settings Hyprland chose,
      // and leave its VRR policy unmanaged so fullscreen VRR isn't overwritten.
      if (!(out.mode.w > 0 && out.mode.h > 0 && out.mode.hz > 0)) {
        out.mode = pickMode(m.availableModes, null) || out.mode
      }
      out.scale = cleanScale(out.scale, out.mode.w, out.mode.h)
      out.vrrManaged = false
    }
    out.autoOff = !!off[out.fp]
    out.offReason = off[out.fp] ? String(off[out.fp]) : ""
    out.pauseMode = out.offReason === "input" && (modes[out.fp] === "mirror" || modes[out.fp] === "disable")
      ? modes[out.fp] : ""
    out.enabled = true
    out.mirrorOf = ""
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
  var byFp = {}
  for (var f = 0; f < active.length; f++) byFp[active[f].fp] = active[f]

  var exact = mem.layouts[layoutKey(fps)]
  var source = exact && exact.outputs ? exact : closestLayout(mem, fps)

  var known = {}
  // With nothing saved that shares a monitor, the arrangement Hyprland has
  // now is the starting point.
  if (!source) {
    for (var l = 0; l < active.length; l++) {
      known[active[l].fp] = true
      var origMonitor = monitors[outputs.indexOf(active[l])]
      // A live mirror the bar parked (and has not seen undone yet) is never a
      // mirror to keep; any other live mirror is the user's and stays.
      active[l].mirrorOf = liveParkOfMonitor(origMonitor, monitors, appliedParks)
        ? "" : cleanMirrorOf(origMonitor ? origMonitor.mirrorOf : "", monitors)
    }
  }
  for (var a = 0; source && a < active.length; a++) {
    var saved = source && source.outputs ? source.outputs[active[a].fp] : null
    if (!saved) continue
    active[a].x = Number(saved.x) || 0
    active[a].y = Number(saved.y) || 0
    active[a].enabled = saved.enabled !== false
    if (saved.mirrorTargetFp && byFp[saved.mirrorTargetFp]) {
      active[a].mirrorOf = byFp[saved.mirrorTargetFp].name
    } else if (saved.mirrorOf) {
      active[a].mirrorOf = cleanMirrorOf(saved.mirrorOf)
    }
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
      if (active[k].fp === memo.fp && known[active[k].fp] && active[k].enabled && !active[k].mirrorOf) neighbour = active[k]
    if (neighbour) {
      var spot = placeBeside(output, neighbour, memo.edge, Number(memo.offset) || 0)
      output.x = spot.x
      output.y = spot.y
    } else {
      var right = 0
      var top = Infinity
      for (var r = 0; r < active.length; r++) {
        if (!known[active[r].fp] || !active[r].enabled || active[r].mirrorOf) continue
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
  if (output.autoOff) {
    if (output.offReason !== "input") return true
    var park = cleanMirrorOf(output.parkMirrorOf)
    if (park) return live.enabled === true && cleanMirrorOf(live.mirrorOf || live.parkMirrorOf) === park
    return live.enabled === false
  }
  if (output.enabled !== live.enabled) return false
  if (!output.enabled) return true
  var mirrorA = cleanMirrorOf(output.mirrorOf)
  var mirrorB = cleanMirrorOf(live.mirrorOf || live.parkMirrorOf)
  if (mirrorA !== mirrorB) return false
  if (!mirrorA && (output.x !== live.x || output.y !== live.y)) return false
  if (!sameMode(output.mode, live.mode)) return false
  if (Math.abs(output.scale - live.scale) >= 0.001) return false
  if (output.vrr !== live.vrr) return false
  if (cleanTransform(output.transform) !== cleanTransform(live.transform)) return false
  if (output.hdr !== live.hdr) return false
  if (output.hdr) {
    if (Math.abs(cleanSdrBrightness(output.sdrBrightness) - cleanSdrBrightness(live.sdrBrightness)) >= 0.015) return false
    if (Math.abs(cleanSdrSaturation(output.sdrSaturation) - cleanSdrSaturation(live.sdrSaturation)) >= 0.015) return false
  }
  return true
}

// True when a mirror-park in `plan` or `livePlan` is out of step with the other
// (its target went away or changed, or a park can no longer be kept), so the
// service reconciles to re-park or wake it.
function parkedMirrorsStale(plan, livePlan) {
  var list = Array.isArray(plan) ? plan : []
  var liveList = Array.isArray(livePlan) ? livePlan : []
  for (var i = 0; i < list.length; i++) {
    var live = null
    for (var j = 0; j < liveList.length; j++) if (liveList[j] && liveList[j].name === list[i].name) live = liveList[j]
    if (!isParked(list[i]) && !isParked(live)) continue
    if (!outputMatchesLive(list[i], live)) return true
  }
  return false
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

// Applying a new mode, rotation, HDR, power, or mirror change can leave a
// monitor unreadable, so the panel asks for confirmation and reverts if missed.
function needsConfirmation(plan, livePlan) {
  for (var i = 0; i < plan.length; i++) {
    var live = null
    for (var j = 0; j < livePlan.length; j++) if (livePlan[j].name === plan[i].name) live = livePlan[j]
    if (!live || plan[i].autoOff) continue
    if (plan[i].enabled !== live.enabled) return true
    if (plan[i].enabled && (
      !sameMode(plan[i].mode, live.mode)
      || plan[i].hdr !== live.hdr
      || cleanTransform(plan[i].transform) !== cleanTransform(live.transform)
      || cleanMirrorOf(plan[i].mirrorOf) !== cleanMirrorOf(live.mirrorOf)
    )) return true
  }
  return false
}

function copyMemory(memory) {
  return JSON.parse(JSON.stringify(normalizeMemory(memory)))
}

// Older releases kept a per-monitor input watch toggle and its learned input
// inline in displays.json; display_ctl.py migrates the input to ddc.json, and
// every later save drops the old fields.
function stripLegacyDdc(profile) {
  if (!profile || typeof profile !== "object") return profile
  delete profile.ddcWatch
  delete profile.ddcInput
  delete profile.ddcConnector
  return profile
}

// The memory after the user presses Apply on `plan`: every monitor's own
// settings, and the arrangement (plus explicitly edited OS-wide text size /
// night light) for this combination of monitors.
function remember(memory, plan, now, options) {
  var mem = copyMemory(memory)
  var opts = options && typeof options === "object" ? options : {}
  var members = plan.filter(function(o) { return !o.autoOff })
  var byName = {}
  for (var m = 0; m < members.length; m++) byName[members[m].name] = members[m]
  var key = layoutKey(members.map(function(o) { return o.fp }))
  var prevLayout = mem.layouts[key] || {}
  var layout = { members: members.map(function(o) { return o.fp }).sort(), lastUsed: String(now || ""), outputs: {} }
  var savedTextSize = cleanTextSize(opts.textSize !== undefined ? opts.textSize : prevLayout.textSize)
  if (savedTextSize !== null) layout.textSize = savedTextSize
  var savedNightLight = cleanNightLight(opts.nightLight !== undefined ? opts.nightLight : prevLayout.nightLight)
  if (savedNightLight !== null) layout.nightLight = savedNightLight
  for (var i = 0; i < plan.length; i++) {
    var o = plan[i]
    var previous = mem.monitors[o.fp] || {}
    if (!o.autoOff) {
      var outEntry = { x: o.x, y: o.y, enabled: o.enabled }
      var mirrorName = cleanMirrorOf(o.mirrorOf)
      if (mirrorName && byName[mirrorName]) {
        outEntry.mirrorOf = mirrorName
        outEntry.mirrorTargetFp = byName[mirrorName].fp
      }
      layout.outputs[o.fp] = outEntry
    }
    // An output that is off reports the last mode it ran, which is not a new
    // choice; keep what was saved for it.
    if (!o.enabled || o.autoOff) {
      if (mem.monitors[o.fp]) {
        stripLegacyDdc(mem.monitors[o.fp])
        continue
      }
    }
    var profile = {
      mode: { w: o.mode.w, h: o.mode.h, hz: o.mode.hz },
      scale: o.scale,
      vrr: o.vrr,
      hdr: o.hdr === true,
      sdrBrightness: cleanSdrBrightness(o.sdrBrightness !== undefined ? o.sdrBrightness : previous.sdrBrightness),
      sdrSaturation: cleanSdrSaturation(o.sdrSaturation !== undefined ? o.sdrSaturation : previous.sdrSaturation),
      transform: cleanTransform(o.transform)
    }
    var desc = clean(o.description || previous.description)
    if (desc) profile.description = desc
    var brightness = cleanBrightness(o.brightness !== null && o.brightness !== undefined ? o.brightness : previous.brightness)
    if (brightness !== null) profile.brightness = brightness
    var neighbour = o.enabled && !o.autoOff && !o.mirrorOf ? neighbourOf(o, members) : null
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

// Merge confirmed live hardware brightness into enabled outputs of a plan on
// Apply so brightness changes made while the panel was closed (or via hotkeys/
// bar-icon scroll) are preserved instead of overwritten by stale saved values.
function mergeActualBrightness(plan, liveBrightness) {
  if (!Array.isArray(plan)) return []
  var map = liveBrightness && typeof liveBrightness === "object" ? liveBrightness : {}
  return plan.map(function(o) {
    var c = {}
    for (var k in o) c[k] = o[k]
    if (o.mode) c.mode = { w: o.mode.w, h: o.mode.h, hz: o.mode.hz }
    if (c.enabled && !c.autoOff && Object.prototype.hasOwnProperty.call(map, c.name)) {
      var clean = cleanBrightness(map[c.name])
      if (clean !== null) c.brightness = clean
    }
    return c
  })
}

function syncDraftBrightness(draft, monitorName, pct) {
  var clean = cleanBrightness(pct)
  if (!Array.isArray(draft) || !monitorName || clean === null) return { draft: draft || [], changed: false }
  var changed = false
  var next = draft.map(function(o) {
    var c = {}
    for (var k in o) c[k] = o[k]
    if (o.mode) c.mode = { w: o.mode.w, h: o.mode.h, hz: o.mode.hz }
    if (o.name === monitorName && c.brightness !== clean) {
      c.brightness = clean
      changed = true
    }
    return c
  })
  return { draft: next, changed: changed }
}

// Pure queue helpers for DisplayService's serialized brightness pipeline so
// generation checks, baseline capture, and write/step coalescing are unit-tested.
function brightnessQueueSet(queue, gens, name, pct) {
  var n = String(name || "").trim()
  var clamped = cleanBrightness(pct)
  if (!n || clamped === null) return { queue: queue || [], gens: gens || {}, gen: 0 }
  var nextGens = {}
  for (var k in gens || {}) nextGens[k] = gens[k]
  var gen = (Number(nextGens[n]) || 0) + 1
  nextGens[n] = gen
  var next = []
  for (var i = 0; i < (queue || []).length; i++) {
    var item = queue[i]
    // Preserve an unrun baseline read ahead of the write so Discard can revert.
    if (item.name === n && !(item.readOnly && item.baseline)) continue
    next.push(item)
  }
  next.push({ name: n, pct: clamped, gen: gen })
  return { queue: next, gens: nextGens, gen: gen }
}

function brightnessQueueStep(queue, gens, liveBrightness, name, deltaPct) {
  var n = String(name || "").trim()
  var d = Math.round(Number(deltaPct))
  if (!n || !isFinite(d) || d === 0) return { queue: queue || [], gens: gens || {}, gen: 0 }
  var nextGens = {}
  for (var k in gens || {}) nextGens[k] = gens[k]
  var prevGen = Number(nextGens[n]) || 0
  var gen = prevGen + 1
  nextGens[n] = gen
  var hasBaselineQueued = false
  var next = []
  var merged = false
  for (var i = 0; i < (queue || []).length; i++) {
    var item = queue[i]
    if (item.name === n && item.readOnly) {
      if (item.baseline) {
        hasBaselineQueued = true
        next.push(item)
      }
      continue
    }
    if (item.name === n && item.delta !== undefined) {
      var combined = Math.max(-100, Math.min(100, item.delta + d))
      if (combined !== 0) next.push({ name: n, delta: combined, gen: gen })
      merged = true
    } else {
      next.push(item)
    }
  }
  var hasKnown = liveBrightness && Object.prototype.hasOwnProperty.call(liveBrightness, n)
  if (!hasKnown && !hasBaselineQueued && !merged) {
    next.push({ name: n, readOnly: true, baseline: true, gen: prevGen })
  }
  if (!merged) next.push({ name: n, delta: Math.max(-100, Math.min(100, d)), gen: gen })
  next.push({ name: n, readOnly: true, afterStep: true, gen: gen })
  return { queue: next, gens: nextGens, gen: gen }
}

function brightnessQueueRead(queue, gens, name, baseline) {
  var n = String(name || "").trim()
  if (!n) return { queue: queue || [], gens: gens || {}, gen: 0 }
  var gen = Number(gens && gens[n]) || 0
  var next = (queue || []).slice()
  for (var i = 0; i < next.length; i++) {
    if (next[i].name === n && next[i].readOnly && !!next[i].baseline === !!baseline && next[i].gen === gen) {
      return { queue: next, gens: gens || {}, gen: gen }
    }
  }
  next.push({ name: n, readOnly: true, baseline: !!baseline, gen: gen })
  return { queue: next, gens: gens || {}, gen: gen }
}

// Supersede any queued scroll steps/readbacks on Discard, bump generations so
// in-flight step callbacks are ignored, and restore every monitor in restoreMap.
function brightnessQueueDiscard(queue, gens, activeJob, restoreMap) {
  var nextGens = {}
  for (var k in gens || {}) nextGens[k] = gens[k]
  var affected = {}
  for (var r in restoreMap || {}) {
    if (cleanBrightness(restoreMap[r]) !== null) affected[r] = true
  }
  for (var i = 0; i < (queue || []).length; i++) {
    var qItem = queue[i]
    if (qItem && (qItem.delta !== undefined || qItem.afterStep)) affected[qItem.name] = true
  }
  if (activeJob && (activeJob.delta !== undefined || activeJob.afterStep)) {
    affected[activeJob.name] = true
  }
  for (var name in affected) {
    nextGens[name] = (Number(nextGens[name]) || 0) + 1
  }
  var next = []
  for (var j = 0; j < (queue || []).length; j++) {
    var item = queue[j]
    if (!item) continue
    if (affected[item.name]) {
      // Preserve unrun baseline reads so future edits still know pre-edit hardware value.
      if (item.readOnly && item.baseline) next.push(item)
      continue
    }
    next.push(item)
  }
  for (var rn in restoreMap || {}) {
    var pct = cleanBrightness(restoreMap[rn])
    if (pct === null) continue
    next.push({ name: rn, pct: pct, gen: nextGens[rn] })
  }
  return { queue: next, gens: nextGens }
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
// connected; Omarchy's clamshell handling turns it off and on. Never mark
// the last external monitor auto-off while the lid is shut, or the internal
// panel would be forced back on behind a closed lid.
function autoOffFor(plan, lidClosed, ddcOff) {
  var out = {}
  var externals = plan.filter(function(o) { return !o.internal })
  // Only externals that would actually stay lit count: a user-disabled one
  // does not, so a lid-shut laptop never ends up with nothing on.
  var remainingExternal = externals.filter(function(o) {
    return o.enabled !== false && !(ddcOff && ddcOff[o.fp])
  }).length
  for (var i = 0; i < plan.length; i++) {
    var o = plan[i]
    if (o.internal && lidClosed && externals.length > 0) {
      out[o.fp] = "lid"
    } else if (ddcOff && ddcOff[o.fp]) {
      if (lidClosed && remainingExternal === 0) continue
      out[o.fp] = "input"
    }
  }
  return out
}


// ---------------------------------------------------------------- auto-pause
// A zero-toggle state machine, one entry per external monitor fingerprint,
// fed with read-only readings from display_ctl.py:
//   Tier 1, HDMI 2.0/2.1 SCDC signal lock (I2C 0x54, Status_Flags_0):
//     { status: "ok", signal: "scdc", scdc: "locked" | "unlocked" | "absent" }
//   Tier 2, DDC/CI VCP 0x60 (input source):
//     { status: "ok", code: "0x11" }
//   either: { status: "ambiguous" } | { status: "busy" } | { status: "no_tool" }
//           | { status: "dpms_off" } (DPMS-blanked: no evidence, nothing read)
// A monitor that answers "locked" twice while probing is watched through SCDC
// (`mode: "scdc"`, `pauseMode: "mirror"`): 3 reads without a lock (switched
// away, or a TV powered down) pause it by parking it as a mirror, which keeps
// its link up, and the first "locked" wakes it. Otherwise VCP 0x60 is learned
// (`mode: "ddc"`, `pauseMode: "disable"`). Either way it only ever pauses after
// strong, repeated evidence, and it wakes on the first sign of doubt, so a
// misbehaving monitor can never lock the user out:
//   UNPROBED     waits out a settle grace after startup, connect, or resume
//   PROBING      2 SCDC locks in a row -> WATCHING (scdc); else learns which
//                input this PC is on (2 identical reads in a row); 3
//                unreadable answers with no valid one -> UNSUPPORTED, and a
//                monitor that answered once (VCP, or SCDC without a lock)
//                gets 5 reads in all
//   WATCHING     5s polls (2s for SCDC); 3 identical reads of another input,
//                or 3 SCDC reads without a lock -> PAUSED
//   PAUSED       3s polls (2s for SCDC); this PC's input / an SCDC lock ->
//                wake; 2 unreadable -> wake. No lock keeps an SCDC pause (it
//                is parked lit, so it can always relock)
//   UNVERIFIED   started on another input than the learned one; never pauses
//                until this PC's input is seen
//   SNOOZED      woken by hand; never pauses until this PC's input is seen
//   UNSUPPORTED  no usable DDC/CI answer; no more polls until reconnect/Re-probe
//   SUSPENDED    paused too often (3 in 10 min); no polls until reconnect/Re-probe
//   NO_TOOL      ddcutil is not installed and no SCDC sink answers
// No I/O happens here; DisplayService runs the reads and carries out `learn`.

var DDC_GRACE_MS = 10000
var DDC_PROBE_READS = 3
var DDC_PROBE_MAX_READS = 5
var DDC_PAUSE_STREAK = 3
var DDC_WAKE_AMBIGUOUS = 2
var DDC_FLAP_WINDOW_MS = 600000
var DDC_FLAP_LIMIT = 3
var DDC_INTERVAL_PAUSED = 3000
var DDC_INTERVAL_ACTIVE = 5000
var DDC_INTERVAL_SLOW = 15000
// An SCDC read is one sub-millisecond ioctl, no ddcutil.
var DDC_INTERVAL_SCDC = 2000
var DDC_SCDC_PROBE_LOCKS = 2

var MCCS_INPUTS = {
  "0x01": "VGA-1", "0x02": "VGA-2", "0x03": "DVI-1", "0x04": "DVI-2",
  "0x05": "Composite-1", "0x06": "Composite-2", "0x07": "S-Video-1", "0x08": "S-Video-2",
  "0x0f": "DP-1", "0x10": "DP-2", "0x11": "HDMI-1", "0x12": "HDMI-2",
  "0x19": "USB-C-1", "0x1b": "USB-C"
}

// "0x01".."0xff" as two lowercase hex digits, else "". 0x00 is what many
// monitors report while asleep or without a signal, never a real input.
function ddcCode(code) {
  var text = clean(code).toLowerCase()
  if (!/^0x[0-9a-f]{1,2}$/.test(text)) return ""
  var n = parseInt(text.slice(2), 16)
  if (!(n >= 1 && n <= 255)) return ""
  return "0x" + (n < 16 ? "0" : "") + n.toString(16)
}

function ddcInputLabel(code) {
  var c = ddcCode(code)
  if (!c) return ""
  return MCCS_INPUTS[c] ? (MCCS_INPUTS[c] + " (" + c + ")") : ("Input " + c)
}

// The learned input for `fp` on `connector`, from ddc.json; "" when unknown or
// learned on another PC port (a moved cable is relearned, never trusted).
function ddcSavedInput(ddcMemory, fp, connector) {
  var mons = ddcMemory && ddcMemory.monitors && typeof ddcMemory.monitors === "object" ? ddcMemory.monitors : {}
  var entry = mons[fp]
  if (!entry || clean(entry.connector) !== clean(connector)) return ""
  return ddcCode(entry.input)
}

function ddcFresh(connector, now, hotplugged, recentPauses) {
  return {
    state: "UNPROBED",
    connector: clean(connector),
    readyAt: Number(now) || 0,
    hotplugged: hotplugged === true,
    expected: "",
    code: "",
    pausedCode: "",
    probeReads: 0,
    probeCode: "",
    probeSawValid: false,
    probeAmbiguous: 0,
    // An SCDC sink answered (locked or unlocked) during this probe.
    probeSawScdc: false,
    detectedBus: "",
    // How this monitor is watched once probed: "scdc" (signal lock, paused by
    // mirror-parking) or "ddc" (VCP 0x60, paused by disabling).
    mode: "",
    pauseMode: "",
    // Watched through SCDC before (resume / Re-probe): probe it through SCDC.
    scdcCapable: false,
    scdcLocks: 0,
    pausedReason: "",
    otherCode: "",
    otherStreak: 0,
    ambiguousStreak: 0,
    seenL: false,
    lastPollAt: 0,
    held: false,
    recentPauses: Array.isArray(recentPauses) ? recentPauses.slice() : []
  }
}

function ddcCopyState(st) {
  var c = {}
  for (var k in st) c[k] = st[k]
  c.recentPauses = Array.isArray(st.recentPauses) ? st.recentPauses.slice() : []
  return c
}

function ddcCopyStates(states) {
  var next = {}
  for (var fp in states || {}) if (states[fp]) next[fp] = states[fp]
  return next
}

function ddcToWatching(s, expected, now) {
  s.state = "WATCHING"
  s.expected = expected
  s.seenL = true
  s.hotplugged = false
  s.pausedCode = ""
  s.otherCode = ""
  s.otherStreak = 0
  s.ambiguousStreak = 0
  s.probeReads = 0
  s.probeCode = ""
  s.probeSawValid = false
  s.probeAmbiguous = 0
  s.probeSawScdc = false
  s.scdcLocks = 0
  s.pausedReason = ""
  if (now !== undefined) s.readyAt = now
  return s
}

function ddcResetProbe(s) {
  s.probeReads = 0
  s.probeCode = ""
  s.probeSawValid = false
  s.probeAmbiguous = 0
  s.probeSawScdc = false
  s.scdcLocks = 0
}

function ddcSetMode(s, mode) {
  s.mode = mode
  s.pauseMode = mode === "scdc" ? "mirror" : "disable"
  if (mode === "scdc") s.scdcCapable = true
  return s
}

// After 3 strong reads of "away": PAUSED, or SUSPENDED once it has paused 3
// times in 10 minutes. Only when pausing is safe and past the settle grace.
function ddcTryPause(s, now, canPause, pausedCode, reason) {
  if (!(s.otherStreak >= DDC_PAUSE_STREAK && canPause === true && s.seenL && now - s.readyAt >= DDC_GRACE_MS)) return s
  var recent = s.recentPauses.filter(function(t) { return now - Number(t) < DDC_FLAP_WINDOW_MS })
  s.recentPauses = recent
  s.otherStreak = 0
  if (recent.length >= DDC_FLAP_LIMIT) {
    s.state = "SUSPENDED"
    s.otherCode = ""
  } else {
    s.recentPauses = recent.concat([now])
    s.state = "PAUSED"
    s.pausedCode = pausedCode
    s.pausedReason = reason
    s.ambiguousStreak = 0
  }
  return s
}

// One reading for one monitor. ctx: { fp, now, savedL, canPause }.
// Returns { state, learn: null | { fp, connector, code } }.
function ddcTransition(st, reading, ctx) {
  var c = ctx || {}
  var now = Number(c.now) || 0
  var s = ddcCopyState(st || ddcFresh("", now, false))
  var status = reading && reading.status ? String(reading.status) : "ambiguous"
  var scdc = ""
  if (status === "ok" && reading.signal === "scdc") {
    scdc = String(reading.scdc || "")
    if (scdc !== "locked" && scdc !== "unlocked" && scdc !== "absent") {
      scdc = ""
      status = "ambiguous"
    }
  }
  var code = status === "ok" && !scdc ? ddcCode(reading.code) : ""
  if (status === "ok" && !code && !scdc) status = "ambiguous"
  var away = scdc === "unlocked" || scdc === "absent"
  var learn = null
  // A bus found by `ddcutil detect` is kept for this connection and passed
  // back as a hint, so detect runs once per connection, not once per read.
  var bus = reading && reading.bus !== undefined && reading.bus !== null ? String(reading.bus) : ""
  if (/^[0-9]{1,6}$/.test(bus)) s.detectedBus = bus
  if (status === "busy") return { state: s, learn: null }
  s.lastPollAt = now
  if (status === "dpms_off") {
    // The monitor (or desktop) is DPMS-blanked: no evidence either way. A
    // half-built pause streak is dropped, and the monitor is marked held at
    // once so waking before the next poll still starts a fresh settle grace.
    if (s.state !== "PAUSED") {
      s.held = true
      s.otherCode = ""
      s.otherStreak = 0
      if (s.state === "PROBING") {
        s.state = "UNPROBED"
        ddcResetProbe(s)
      }
    }
    return { state: s, learn: null }
  }
  // Without ddcutil, a monitor that answered SCDC (or is known to) is still
  // watched through SCDC: a stray "needs ddcutil" is only an unreadable answer.
  if (status === "no_tool" && (s.probeSawScdc || s.scdcCapable === true || s.mode === "scdc")) status = "ambiguous"
  if (status === "no_tool") {
    var nt = ddcFresh(s.connector, now, false, s.recentPauses)
    nt.state = "NO_TOOL"
    nt.expected = s.expected
    return { state: nt, learn: null }
  }
  if (code) s.code = code

  if (s.state === "UNPROBED") {
    if (now - s.readyAt < DDC_GRACE_MS) return { state: s, learn: null }
    s.state = "PROBING"
    ddcResetProbe(s)
  }

  if (s.state === "PROBING") {
    // Tier 1: an SCDC lock proves this PC is on screen; no input to learn.
    if (scdc === "locked") {
      s.scdcLocks = (Number(s.scdcLocks) || 0) + 1
      s.probeSawScdc = true
      if (s.scdcLocks >= DDC_SCDC_PROBE_LOCKS) ddcToWatching(ddcSetMode(s, "scdc"), s.expected)
      return { state: s, learn: null }
    }
    s.scdcLocks = 0
    if (away && s.scdcCapable === true) {
      // A monitor watched through SCDC before (resume, Re-probe) that shows
      // something else now: wait for its lock, never pause.
      ddcSetMode(s, "scdc")
      s.state = "UNVERIFIED"
      ddcResetProbe(s)
      return { state: s, learn: null }
    }
    if (away) {
      // An SCDC sink answering without a lock (switched away at startup, with
      // or without ddcutil): after 3 reads without a lock, keep polling SCDC
      // in UNVERIFIED until it locks (never pause, never give up as unsupported).
      s.probeSawScdc = true
      s.probeSawValid = true
      s.probeCode = ""
      s.probeReads += 1
      if (s.probeReads >= DDC_PROBE_READS) {
        ddcSetMode(s, "scdc")
        s.state = "UNVERIFIED"
        ddcResetProbe(s)
      }
      return { state: s, learn: null }
    }
    s.probeReads += 1
    if (code) s.probeSawValid = true
    else s.probeAmbiguous = (Number(s.probeAmbiguous) || 0) + 1
    if (code && s.probeCode === code) {
      var savedL = ddcCode(c.savedL)
      ddcSetMode(s, "ddc")
      if (!savedL || s.hotplugged) {
        learn = { fp: String(c.fp || ""), connector: s.connector, code: code }
        ddcToWatching(s, code)
      } else if (code === savedL) {
        ddcToWatching(s, savedL)
      } else {
        // Startup or resume with the monitor showing something else: keep the
        // learned input and never pause until it is seen again.
        s.state = "UNVERIFIED"
        s.expected = savedL
        ddcResetProbe(s)
      }
      return { state: s, learn: learn }
    }
    s.probeCode = code
    // A TV without DDC/CI never answers: 3 reads (15s) give up. A monitor that
    // answered at least once is only intermittent, so it gets a few more.
    var giveUp = s.probeSawValid ? s.probeReads >= DDC_PROBE_MAX_READS : s.probeAmbiguous >= DDC_PROBE_READS
    if (giveUp) {
      if (s.probeSawScdc) {
        ddcSetMode(s, "scdc")
        s.state = "UNVERIFIED"
      } else {
        s.state = "UNSUPPORTED"
      }
      ddcResetProbe(s)
    }
    return { state: s, learn: null }
  }

  // A monitor watched through DDC/CI that now shows an SCDC lock is on this PC:
  // from here on it is watched (and paused) through SCDC.
  if (scdc === "locked" && s.mode !== "scdc" && (s.state === "WATCHING" || s.state === "PAUSED"
      || s.state === "UNVERIFIED" || s.state === "SNOOZED")) {
    var keepGrace = s.state === "WATCHING"
    ddcToWatching(ddcSetMode(s, "scdc"), s.expected, keepGrace ? undefined : now)
    return { state: s, learn: null }
  }

  if (s.mode === "scdc") {
    if (s.state === "WATCHING") {
      if (scdc === "locked") {
        s.seenL = true
        s.otherCode = ""
        s.otherStreak = 0
      } else if (away) {
        s.otherStreak += 1
        ddcTryPause(s, now, c.canPause, "", scdc)
      } else {
        // A failed helper read is never evidence of a switch.
        s.otherStreak = 0
      }
      return { state: s, learn: null }
    }
    if (s.state === "PAUSED") {
      if (scdc === "locked") return { state: ddcToWatching(s, s.expected, now), learn: null }
      if (away) {
        // Parked lit, so its link stays up: it stays paused until it relocks.
        s.ambiguousStreak = 0
        s.pausedReason = scdc
        return { state: s, learn: null }
      }
      s.ambiguousStreak += 1
      // Fail open: a helper that stops answering wakes the monitor.
      if (s.ambiguousStreak >= DDC_WAKE_AMBIGUOUS) return { state: ddcToWatching(s, s.expected, now), learn: null }
      return { state: s, learn: null }
    }
    if (s.state === "UNVERIFIED" || s.state === "SNOOZED") {
      if (scdc === "locked") return { state: ddcToWatching(s, s.expected, now), learn: null }
      return { state: s, learn: null }
    }
    return { state: s, learn: null }
  }

  if (s.state === "WATCHING") {
    if (code && code === s.expected) {
      s.seenL = true
      s.otherCode = ""
      s.otherStreak = 0
    } else if (code) {
      if (code === s.otherCode) s.otherStreak += 1
      else {
        s.otherCode = code
        s.otherStreak = 1
      }
      ddcTryPause(s, now, c.canPause, code, "input")
    } else {
      // An unreadable answer is never evidence of a switch.
      s.otherCode = ""
      s.otherStreak = 0
    }
    return { state: s, learn: null }
  }

  if (s.state === "PAUSED") {
    if (code && code === s.expected) return { state: ddcToWatching(s, s.expected, now), learn: null }
    if (code) {
      s.ambiguousStreak = 0
      s.pausedCode = code
      return { state: s, learn: null }
    }
    s.ambiguousStreak += 1
    // Fail open: a monitor that stops answering (asleep, no signal, 0x00) is
    // woken, so no-signal sleep can never keep it dark.
    if (s.ambiguousStreak >= DDC_WAKE_AMBIGUOUS) return { state: ddcToWatching(s, s.expected, now), learn: null }
    return { state: s, learn: null }
  }

  if (s.state === "UNVERIFIED" || s.state === "SNOOZED") {
    if (code && code === s.expected) return { state: ddcToWatching(s, s.expected, now), learn: null }
    return { state: s, learn: null }
  }

  return { state: s, learn: null }
}

// Applies one reading to the map of states. Returns { states, learn, offChanged }.
function ddcObserve(states, fp, reading, ctx) {
  var next = ddcCopyStates(states)
  var cur = next[fp]
  if (!cur) return { states: next, learn: null, offChanged: false }
  var c = {}
  for (var k in ctx || {}) c[k] = ctx[k]
  c.fp = fp
  var res = ddcTransition(cur, reading, c)
  next[fp] = res.state
  var offChanged = (cur.state === "PAUSED") !== (res.state.state === "PAUSED")
  return { states: next, learn: res.learn, offChanged: offChanged }
}

// Fingerprints paused for an input switch, as autoOffFor's `ddcOff`. Derived
// from the states alone, so a pause can never outlive its state.
function ddcOffMap(states) {
  var out = {}
  for (var fp in states || {}) if (states[fp] && states[fp].state === "PAUSED") out[fp] = true
  return out
}

// How each paused monitor is kept off, for derive/planFromLive: "mirror"
// (parked as a mirror, SCDC) or "disable" (DDC/CI). Only paused monitors are
// listed: how a monitor is watched never decides whether a live mirror is the
// bar's park (that is `appliedParks`, see liveParkOf).
function ddcPauseModes(states) {
  var out = {}
  for (var fp in states || {}) {
    var st = states[fp]
    if (!st || st.state !== "PAUSED") continue
    out[fp] = st.mode === "scdc" ? "mirror" : "disable"
  }
  return out
}

// `appliedParks` ({ connector: [target, ...] }) for an apply of `plan`, recorded
// before the apply runs (and again once it has): every target it parks an
// output on is added, never replacing an earlier one, so whether the apply
// works, half works or fails, both the old and the new park stay the bar's
// until Hyprland's readback narrows them (pruneAppliedParks). A mirror the
// user asked for (`mirrorOf` with no park) is the user's from then on.
function recordAppliedParks(parks, plan) {
  var next = {}
  for (var k in parks || {}) {
    var have = appliedParkTargets(parks, k)
    if (have.length) next[k] = have
  }
  var list = Array.isArray(plan) ? plan : []
  for (var i = 0; i < list.length; i++) {
    var o = list[i]
    var name = o ? clean(o.name) : ""
    if (!name) continue
    var park = cleanMirrorOf(o.parkMirrorOf)
    var targets = next[name] ? next[name].slice() : []
    if (park) {
      if (targets.indexOf(park) === -1) targets.push(park)
    } else {
      var mirror = cleanMirrorOf(o.mirrorOf)
      if (mirror) targets = targets.filter(function(t) { return t !== mirror })
    }
    if (targets.length) next[name] = targets
    else delete next[name]
  }
  return next
}

// Narrows `parks` to what Hyprland (`monitors`) shows: an output gone, off,
// or not mirroring is dropped; one mirroring a target the bar applied keeps
// exactly that target; one mirroring anything else is the user's and is
// dropped. Returns { parks, changed }.
function pruneAppliedParks(parks, monitors) {
  var list = Array.isArray(monitors) ? monitors : []
  var byName = {}
  for (var i = 0; i < list.length; i++) if (list[i] && list[i].name) byName[clean(list[i].name)] = list[i]
  var next = {}
  var changed = false
  for (var name in parks || {}) {
    var have = appliedParkTargets(parks, name)
    var lm = byName[name]
    var cur = lm && lm.disabled !== true ? cleanMirrorOf(lm.mirrorOf, list) : ""
    if (cur && have.indexOf(cur) !== -1) {
      next[name] = [cur]
      if (!Array.isArray(parks[name]) || parks[name].length !== 1 || parks[name][0] !== cur) changed = true
    } else {
      changed = true
    }
  }
  return { parks: next, changed: changed }
}

// True while every enabled monitor is DPMS-off: the desktop idled (or was
// blanked on purpose), which is never a reason to pause or wake anything.
function ddcGlobalIdle(monitors) {
  var enabled = (Array.isArray(monitors) ? monitors : []).filter(function(m) { return m && m.disabled !== true })
  return enabled.length > 0 && enabled.every(function(m) { return m.dpmsStatus === false })
}

// Hyprland's monitors with `dpms` ({ name: on }, the newest power state a
// `ddc-read` saw) laid over their dpmsStatus.
function ddcLiveDpms(monitors, dpms) {
  var map = dpms && typeof dpms === "object" ? dpms : {}
  return (Array.isArray(monitors) ? monitors : []).map(function(m) {
    if (!m || !Object.prototype.hasOwnProperty.call(map, m.name)) return m
    var c = {}
    for (var k in m) c[k] = m[k]
    c.dpmsStatus = map[m.name] !== false
    return c
  })
}

// Whether `fp` may be paused now: never the internal panel, a monitor the user
// turned off, or a mirror source/target, and never the last lit display (the
// internal panel does not count while the lid is shut). `plan` is the layout
// as it would be with no monitor paused. With `liveMonitors` (Hyprland's
// monitors), a mirror the user set up live (any live mirror `appliedParks`
// does not list, see liveParkOf) on or onto it rules a pause out too, and
// another display only counts when it is actually lit right now: enabled, not
// DPMS-asleep and not a live mirror.
function ddcCanPause(plan, fp, lidClosed, ddcOff, liveMonitors, appliedParks) {
  var list = Array.isArray(plan) ? plan : []
  var live = null
  if (Array.isArray(liveMonitors)) {
    live = {}
    for (var m = 0; m < liveMonitors.length; m++)
      if (liveMonitors[m] && liveMonitors[m].name) live[liveMonitors[m].name] = liveMonitors[m]
  }
  // While the whole desktop idles every display is DPMS-off on purpose: that
  // never counts against keeping a monitor paused.
  if (live && ddcGlobalIdle(liveMonitors)) live = null
  var target = null
  for (var i = 0; i < list.length; i++) if (list[i] && list[i].fp === fp) target = list[i]
  if (!target || target.internal || target.enabled === false || target.autoOff) return false
  if (cleanMirrorOf(target.mirrorOf)) return false
  var liveList = Array.isArray(liveMonitors) ? liveMonitors : []
  for (var u = 0; u < liveList.length; u++) {
    var um = liveList[u]
    if (!um || !um.name || um.disabled === true) continue
    var umMirror = cleanMirrorOf(um.mirrorOf, liveList)
    if (!umMirror || liveParkOf(um.name, umMirror, appliedParks)) continue
    if (clean(um.name) === target.name || umMirror === target.name) return false
  }
  var lit = 0
  for (var j = 0; j < list.length; j++) {
    var o = list[j]
    if (!o || o === target) continue
    if (o.enabled !== false && cleanMirrorOf(o.mirrorOf) === target.name) return false
    if (o.enabled === false || o.autoOff || cleanMirrorOf(o.mirrorOf)) continue
    if (o.internal && lidClosed) continue
    if (ddcOff && ddcOff[o.fp]) continue
    if (live) {
      var lm = live[o.name]
      if (!lm || lm.disabled === true || lm.dpmsStatus === false) continue
      if (cleanMirrorOf(lm.mirrorOf, liveMonitors)) continue
    }
    lit++
  }
  return lit > 0
}

// Wakes a paused monitor. By hand (`manual`), it is snoozed: it stays on until
// this PC's input is seen again, and the learned input is kept. Otherwise
// (lid, no longer safe to pause) it goes back to watching after a grace.
function ddcWake(states, fp, now, manual) {
  var next = ddcCopyStates(states)
  var cur = next[fp]
  if (!cur || cur.state !== "PAUSED") return next
  var s = ddcToWatching(ddcCopyState(cur), cur.expected, Number(now) || 0)
  if (manual === true) s.state = "SNOOZED"
  next[fp] = s
  return next
}

// After suspend/resume every monitor starts over (paused ones wake) without
// trusting anything it reports in the first seconds; the learned input is
// kept, so a monitor that came back on another input is UNVERIFIED, not relearned.
function ddcResume(states, now) {
  var next = {}
  for (var fp in states || {}) {
    var cur = states[fp]
    if (!cur) continue
    if (cur.state === "UNSUPPORTED" || cur.state === "SUSPENDED" || cur.state === "NO_TOOL") {
      next[fp] = cur
      continue
    }
    var fresh = ddcFresh(cur.connector, now, false, cur.recentPauses)
    fresh.scdcCapable = cur.mode === "scdc" || cur.scdcCapable === true
    next[fp] = fresh
  }
  return next
}

// Re-probe (by hand): monitors that stopped (unsupported, suspended, no
// ddcutil) start over and may learn the input they show now. Snoozed and
// waiting monitors keep their learned input and are simply read again at the
// next tick, so a Re-probe while on another input never relearns that input.
function ddcReprobe(states, now) {
  var next = {}
  for (var fp in states || {}) {
    var cur = states[fp]
    if (!cur) continue
    if (cur.state === "SNOOZED" || cur.state === "UNVERIFIED") {
      var s = ddcCopyState(cur)
      s.lastPollAt = 0
      next[fp] = s
      continue
    }
    if (cur.state === "UNSUPPORTED" || cur.state === "SUSPENDED" || cur.state === "NO_TOOL") {
      var fresh = ddcFresh(cur.connector, now, true, cur.state === "SUSPENDED" ? [] : cur.recentPauses)
      fresh.scdcCapable = cur.mode === "scdc" || cur.scdcCapable === true
      next[fp] = fresh
      continue
    }
    next[fp] = cur
  }
  return next
}

// Keeps one state per connected external monitor. Unplugged monitors are
// dropped, a monitor on another PC connector starts over and relearns, and so
// does one freshly plugged in (`hotpluggedConnectors`: { name: true }), so a
// cable moved to another port on the monitor relearns too.
function ddcPrune(states, identifiedOutputs, hotpluggedConnectors, now) {
  var next = {}
  var hp = hotpluggedConnectors || {}
  var list = Array.isArray(identifiedOutputs) ? identifiedOutputs : []
  for (var i = 0; i < list.length; i++) {
    var o = list[i]
    if (!o || !o.fp || o.internal) continue
    var cur = states ? states[o.fp] : null
    var plugged = hp[o.name] === true
    if (!cur) next[o.fp] = ddcFresh(o.name, now, plugged)
    else if (cur.connector !== clean(o.name)) next[o.fp] = ddcFresh(o.name, now, true, cur.recentPauses)
    else if (plugged) next[o.fp] = ddcFresh(o.name, now, true, cur.recentPauses)
    else next[o.fp] = cur
  }
  return next
}

// Monitors that cannot be read right now (off, DPMS-asleep, user-disabled,
// mirrored) hold still: half-built evidence is dropped, and the settle grace
// starts over once they can be read again, so waking from DPMS can never count
// toward a pause. `pollable` is { fp: bool }; paused monitors are always
// pollable. Returns { states, changed }.
function ddcHold(states, pollable, now) {
  var next = ddcCopyStates(states)
  var changed = false
  for (var fp in next) {
    var cur = next[fp]
    if (!cur || cur.state === "PAUSED") continue
    if (cur.state === "UNSUPPORTED" || cur.state === "SUSPENDED" || cur.state === "NO_TOOL") continue
    var canRead = !!(pollable && pollable[fp])
    if (canRead === !cur.held) continue
    var s = ddcCopyState(cur)
    if (canRead) {
      s.held = false
      s.readyAt = now
    } else {
      s.held = true
      s.otherCode = ""
      s.otherStreak = 0
      if (s.state === "PROBING") {
        s.state = "UNPROBED"
        ddcResetProbe(s)
      }
    }
    next[fp] = s
    changed = true
  }
  return { states: next, changed: changed }
}

function ddcStateInterval(st) {
  if (!st) return 0
  if (st.mode === "scdc") {
    if (st.state === "PAUSED" || st.state === "WATCHING") return DDC_INTERVAL_SCDC
    if (st.state === "UNVERIFIED" || st.state === "SNOOZED") return DDC_INTERVAL_ACTIVE
  }
  if (st.state === "PROBING" && st.probeSawScdc === true) return DDC_INTERVAL_SCDC
  if (st.state === "PAUSED") return DDC_INTERVAL_PAUSED
  if (st.state === "PROBING" || st.state === "WATCHING" || st.state === "UNPROBED") return DDC_INTERVAL_ACTIVE
  if (st.state === "UNVERIFIED" || st.state === "SNOOZED") return DDC_INTERVAL_SLOW
  return 0
}

// True while a power-state-only `ddc-read` (no connector, no I2C) is needed to
// notice a DPMS wake: the whole desktop idles (`liveGlobalIdle`), or a watched
// monitor that is not paused is DPMS-off on its own (`liveDpms`: { name: on }),
// held until it wakes. Hyprland sends no event for DPMS, so it is polled.
function ddcNeedsDpmsProbe(states, liveDpms, liveGlobalIdle) {
  if (liveGlobalIdle === true) return true
  var dpms = liveDpms && typeof liveDpms === "object" ? liveDpms : {}
  var watched = ["WATCHING", "PROBING", "UNPROBED", "UNVERIFIED", "SNOOZED"]
  for (var fp in states || {}) {
    var st = states[fp]
    if (!st || watched.indexOf(st.state) === -1 || !st.connector) continue
    if (dpms[st.connector] === false) return true
  }
  return false
}

// How often the service should tick: the fastest any monitor needs, 0 for none.
// While a DPMS wake is being waited for (ddcNeedsDpmsProbe) it is never slower
// than DDC_INTERVAL_ACTIVE.
function ddcPollInterval(states, now, liveDpms, liveGlobalIdle) {
  var best = 0
  for (var fp in states || {}) {
    var iv = ddcStateInterval(states[fp])
    if (iv && (!best || iv < best)) best = iv
  }
  if (ddcNeedsDpmsProbe(states, liveDpms, liveGlobalIdle) && (!best || best > DDC_INTERVAL_ACTIVE))
    best = DDC_INTERVAL_ACTIVE
  return best
}

// Fingerprints to read now: pollable, due at their own pace, and past the
// settle grace when still unprobed.
function ddcDue(states, pollable, now) {
  var out = []
  for (var fp in states || {}) {
    var st = states[fp]
    var iv = ddcStateInterval(st)
    if (!iv) continue
    if (st.state !== "PAUSED" && !(pollable && pollable[fp])) continue
    if (st.state === "UNPROBED" && now - st.readyAt < DDC_GRACE_MS) continue
    if (st.lastPollAt && now - st.lastPollAt < iv - 250) continue
    out.push(fp)
  }
  return out
}

// `ddc-read` arguments for `targets` ([{ fp, name, mode? }]): each connector,
// with the bus an earlier detect found (`NAME:BUS`), `@scdc` for a monitor
// watched through SCDC (read through SCDC only, never ddcutil), and `--detect
// NAME` only for DDC connectors still probing with no bus known, so a watched
// or paused monitor never makes the helper run `ddcutil detect`.
function ddcReadArgs(targets, states) {
  var detect = []
  var specs = []
  var list = Array.isArray(targets) ? targets : []
  for (var i = 0; i < list.length; i++) {
    var t = list[i]
    var st = t && states ? states[t.fp] : null
    if (!st || !t.name) continue
    var bus = /^[0-9]{1,6}$/.test(String(st.detectedBus || "")) ? String(st.detectedBus) : ""
    var mode = clean(t.mode) || clean(st.mode)
    var scdc = mode === "scdc" || st.scdcCapable === true
    specs.push((bus ? t.name + ":" + bus : t.name) + (scdc ? "@scdc" : ""))
    if (!bus && !scdc && (st.state === "UNPROBED" || st.state === "PROBING")) detect.push("--detect", t.name)
  }
  return detect.concat(specs)
}

// Readings for a `ddc-read` that failed outright (crash, bad output): every
// target reads as ambiguous, so a paused monitor still fails open and a
// half-built pause streak is broken.
function ddcFailedReadings(targets) {
  var out = {}
  var list = Array.isArray(targets) ? targets : []
  for (var i = 0; i < list.length; i++) if (list[i] && list[i].name) out[list[i].name] = { status: "ambiguous" }
  return out
}

// True when an external monitor connected, disconnected, moved PC port, or
// was replugged (`plugged`: { connector: true }) between two state maps, so a
// DDC read started before the change can be thrown away.
function ddcConnectionsChanged(before, after, plugged) {
  var a = before || {}
  var b = after || {}
  var hp = plugged || {}
  for (var fp in b) {
    var p = a[fp]
    var n = b[fp]
    if (!n) continue
    if (!p || p.connector !== n.connector) return true
    if (hp[n.connector] === true && p !== n) return true
  }
  for (var k in a) if (a[k] && !b[k]) return true
  return false
}

// True when applying `plan` would turn on a monitor that is dark now, or bring
// back one that is lit but parked as a mirror (an SCDC wake).
function planWakes(plan, livePlan) {
  var list = Array.isArray(plan) ? plan : []
  var liveList = Array.isArray(livePlan) ? livePlan : []
  for (var i = 0; i < list.length; i++) {
    var o = list[i]
    if (!o || o.enabled === false || o.autoOff) continue
    var live = null
    for (var j = 0; j < liveList.length; j++) if (liveList[j] && liveList[j].name === o.name) live = liveList[j]
    if (!live || live.enabled === false || live.autoOff) return true
    var liveMirror = cleanMirrorOf(live.parkMirrorOf) || cleanMirrorOf(live.mirrorOf)
    if (liveMirror && !cleanMirrorOf(o.parkMirrorOf) && !cleanMirrorOf(o.mirrorOf)) return true
  }
  return false
}

// What monitors.lua gets for `plan`: each output paused for an input switch
// is put back as it is in `unpaused` (the layout with nothing paused), and
// every other output keeps exactly what `plan` says, edits included. A put-back
// output that would overlap the others goes to their right.
function persistablePlan(plan, unpaused) {
  var list = Array.isArray(plan) ? plan : []
  var byFp = {}
  var base = Array.isArray(unpaused) ? unpaused : []
  for (var u = 0; u < base.length; u++) if (base[u] && base[u].fp) byFp[base[u].fp] = base[u]
  // Paused for an input switch, or still parked live after a wake: either way
  // it goes back to its unpaused place.
  var paused = function(o) {
    return !!o && ((o.autoOff === true && o.offReason === "input") || (!o.autoOff && !!cleanMirrorOf(o.parkMirrorOf)))
  }
  // A mirror-park is live only: monitors.lua never gets one.
  var unparked = function(o) {
    if (!o || !o.parkMirrorOf) return o
    var c = {}
    for (var k in o) c[k] = o[k]
    if (o.mode) c.mode = { w: o.mode.w, h: o.mode.h, hz: o.mode.hz }
    c.parkMirrorOf = ""
    c.pauseMode = ""
    return c
  }
  if (!list.some(paused)) return list.some(function(o) { return !!o && !!o.parkMirrorOf }) ? list.map(unparked) : list
  var out = list.map(function(o) {
    var src = paused(o) ? byFp[o.fp] : null
    // No unpaused entry: monitors.lua writes an auto-off output as on anyway.
    if (!src || paused(src)) return unparked(o)
    var c = {}
    for (var k in src) c[k] = src[k]
    if (src.mode) c.mode = { w: src.mode.w, h: src.mode.h, hz: src.mode.hz }
    c.parkMirrorOf = ""
    c.pauseMode = ""
    return c
  })
  var lit = function(o) { return o && o.enabled !== false && !o.autoOff && !cleanMirrorOf(o.mirrorOf) && o.mode }
  // Every output that stays as `plan` says is fixed; each put-back output is
  // added once placed, so two put back never land on the same spot.
  var occupied = []
  for (var j = 0; j < out.length; j++) if (lit(out[j]) && !paused(list[j])) occupied.push(rectOf(out[j]))
  for (var i = 0; i < out.length; i++) {
    if (!paused(list[i]) || paused(out[i]) || !lit(out[i])) continue
    if (overlapsAny(rectOf(out[i]), occupied)) {
      var right = 0
      var top = Infinity
      for (var r = 0; r < occupied.length; r++) {
        right = Math.max(right, occupied[r].x + occupied[r].w)
        top = Math.min(top, occupied[r].y)
      }
      out[i].x = right
      out[i].y = top === Infinity ? 0 : top
    }
    occupied.push(rectOf(out[i]))
  }
  return out
}

// An edited draft keeps the user's edits, but runtime pauses and wakes come
// from `live`, so Wake now and the Paused banner never go stale and Apply never
// submits an outdated pause. A woken monitor takes its live on/off & position.
function syncDraftAutoOff(draft, live) {
  var list = Array.isArray(draft) ? draft : []
  var byFp = {}
  var liveList = Array.isArray(live) ? live : []
  for (var i = 0; i < liveList.length; i++) if (liveList[i] && liveList[i].fp) byFp[liveList[i].fp] = liveList[i]
  var changed = false
  var wokeIndices = []
  var wokeLivePos = {}
  var next = list.map(function(o, idx) {
    var l = o ? byFp[o.fp] : null
    if (!l) return o
    var wasOff = o.autoOff === true
    var nowOff = l.autoOff === true
    var livePark = nowOff ? String(l.parkMirrorOf || "") : ""
    var livePause = nowOff ? String(l.pauseMode || "") : ""
    if (wasOff === nowOff && String(o.offReason || "") === String(l.offReason || "")
        && String(o.parkMirrorOf || "") === livePark && String(o.pauseMode || "") === livePause) return o
    // A wake was requested, but Hyprland readback has not yet confirmed the
    // monitor is enabled and unparked: wait for the post-apply refresh so we
    // never copy stale disabled/parked state into an edited draft.
    if (wasOff && !nowOff && (l.enabled === false || !!cleanMirrorOf(l.parkMirrorOf))) return o
    changed = true
    var c = {}
    for (var k in o) c[k] = o[k]
    if (o.mode) c.mode = { w: o.mode.w, h: o.mode.h, hz: o.mode.hz }
    c.autoOff = nowOff
    c.offReason = nowOff ? String(l.offReason || "") : ""
    c.parkMirrorOf = livePark
    c.pauseMode = livePause
    if (wasOff && !nowOff) {
      c.enabled = l.enabled !== false
      var hasMode = o.mode && Number(o.mode.w) > 0 && Number(o.mode.h) > 0 && Number(o.mode.hz) > 0
      if (!hasMode && l.mode) c.mode = { w: l.mode.w, h: l.mode.h, hz: l.mode.hz }
      wokeIndices.push(idx)
      wokeLivePos[idx] = { x: l.x, y: l.y }
    }
    return c
  })
  if (wokeIndices.length) {
    var wokeSet = {}
    for (var w = 0; w < wokeIndices.length; w++) wokeSet[wokeIndices[w]] = true
    var activeIndices = []
    for (var p = 0; p < next.length; p++) {
      var o = next[p]
      if (!o || o.enabled === false || o.autoOff || cleanMirrorOf(o.mirrorOf)) continue
      activeIndices.push(p)
    }
    // Keep waking outputs at their draft positions if that already forms a
    // valid connected, non-overlapping layout (e.g. an edited A-C-B row);
    // otherwise attach them from their live positions against the fixed others.
    if (!layoutValid(next, activeIndices)) {
      var placed = []
      for (var q = 0; q < activeIndices.length; q++) {
        if (!wokeSet[activeIndices[q]]) placed.push(rectOf(next[activeIndices[q]]))
      }
      for (var u = 0; u < wokeIndices.length; u++) {
        var wi = wokeIndices[u]
        var wo = next[wi]
        if (!wo || wo.enabled === false || wo.autoOff || cleanMirrorOf(wo.mirrorOf)) continue
        wo.x = wokeLivePos[wi].x
        wo.y = wokeLivePos[wi].y
        if (placed.length) {
          var r = attach(rectOf(wo), placed, 0)
          wo.x = r.x
          wo.y = r.y
          placed.push(r)
        } else {
          placed.push(rectOf(wo))
        }
      }
    }
    if (activeIndices.length) {
      var minX = Infinity
      var minY = Infinity
      for (var m = 0; m < activeIndices.length; m++) {
        minX = Math.min(minX, next[activeIndices[m]].x)
        minY = Math.min(minY, next[activeIndices[m]].y)
      }
      if (minX !== 0 || minY !== 0) {
        for (var s = 0; s < activeIndices.length; s++) {
          var ai = activeIndices[s]
          if (!wokeSet[ai]) {
            var copy = {}
            for (var key in next[ai]) copy[key] = next[ai][key]
            if (next[ai].mode) copy.mode = { w: next[ai].mode.w, h: next[ai].mode.h, hz: next[ai].mode.hz }
            next[ai] = copy
          }
          next[ai].x -= minX
          next[ai].y -= minY
        }
      }
    }
  }
  return { draft: next, changed: changed }
}

// What the Displays panel shows for one monitor's auto-pause.
function ddcStatusText(status, ready) {
  if (!ready) return "Apply a layout once to enable"
  var st = status && status.state ? status.state : "UNPROBED"
  if (status && status.mode === "scdc") {
    if (st === "WATCHING") return "Active · HDMI signal lock"
    if (st === "PAUSED") return "Paused · switched away or powered off"
    if (st === "SNOOZED") return "Snoozed until HDMI lock returns"
    if (st === "UNVERIFIED") return "Waiting for HDMI signal lock"
  }
  if (st === "WATCHING") return "Watching · " + (ddcInputLabel(status.code) || "this PC's input")
  if (st === "PAUSED") return "Paused · switched to " + (ddcInputLabel(status.pausedCode) || "another input")
  if (st === "UNVERIFIED") return "Waiting for this PC's input"
  if (st === "SNOOZED") return "Snoozed until input returns"
  if (st === "UNSUPPORTED") return "Not supported by this display"
  if (st === "SUSPENDED") return "Suspended (unstable readings)"
  if (st === "NO_TOOL") return "Needs ddcutil"
  return "Checking input…"
}

// A brief HPD pulse (input switch, mode change, EDID refresh) drops and
// restores the same connector within a few seconds; only longer absences count
// as a physical same-port cable swap that may overwrite a saved DDC input.
var DDC_HPD_BOUNCE_MS = 8000

function ddcIsHotplug(removedAt, now) {
  var r = Number(removedAt) || 0
  if (r <= 0) return true
  return (Number(now) || 0) - r >= DDC_HPD_BOUNCE_MS
}

// The connector a Hyprland monitoradded/monitorremoved (or v2) event names
// ("HDMI-A-1" or "id,HDMI-A-1,description").
function monitorAddedName(eventName, data) {
  var text = String(data === undefined || data === null ? "" : data)
  if (eventName === "monitoraddedv2" || eventName === "monitorremovedv2") {
    var parts = text.split(",")
    return parts.length > 1 ? clean(parts[1]) : ""
  }
  return clean(text.split(",")[0])
}

function normalizeDdcMemory(ddcMemory) {
  var m = ddcMemory && typeof ddcMemory === "object" ? ddcMemory : {}
  var mons = m.monitors && typeof m.monitors === "object" ? m.monitors : {}
  var out = {}
  for (var fp in mons) {
    var e = mons[fp]
    var code = ddcCode(e && e.input)
    if (code && e.connector) out[fp] = { input: code, connector: clean(e.connector), learnedAt: clean(e.learnedAt) }
  }
  return { version: 1, monitors: out }
}

if (typeof module !== "undefined") module.exports = {}

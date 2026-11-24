.pragma library

// Pure display-layout logic for the Displays panel and its hotplug service.
// Nothing here touches Hyprland or the filesystem, so tests run it through
// Qt's own JavaScript engine. display_ctl.py turns the plans built here into
// Hyprland rules.
//
// A plan is a list of outputs:
//   { name, fp, selector, internal, enabled, autoOff, offReason,
//     mode: { w, h, hz }, x, y, scale, vrr, hdr, sdrBrightness, sdrSaturation,
//     transform, mirrorOf, ddcWatch, brightness }
// `autoOff` marks a monitor that is connected but off for now: "lid" (the
// laptop is shut; Omarchy's clamshell handling owns the panel) or "input"
// (it was switched to another input, or off; this bar turns it off and on).
// Memory (displays.json) keeps two things apart:
//   monitors[fp]  settings that belong to one monitor wherever it is plugged in
//   layouts[key]  positions, on/off, mirror, text size & night light for one set

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
  var ids = identify(monitors, mem)
  return monitors.map(function(m, i) {
    var out = liveOutput(m, ids[i], monitors)
    var profile = mem.monitors[out.fp]
    out.ddcWatch = !!(profile && profile.ddcWatch)
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
// Monitors without a saved profile keep their live mode/VRR/HDR/scale/rotation.
function derive(monitors, memory, autoOff) {
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
      out.ddcWatch = profile.ddcWatch === true
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
      active[l].mirrorOf = cleanMirrorOf(origMonitor ? origMonitor.mirrorOf : "", monitors)
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
  if (output.autoOff) return output.offReason !== "input" || live.enabled === false
  if (output.enabled !== live.enabled) return false
  if (!output.enabled) return true
  var mirrorA = cleanMirrorOf(output.mirrorOf)
  var mirrorB = cleanMirrorOf(live.mirrorOf)
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
        if (o.ddcWatch !== (previous.ddcWatch === true)) {
          mem.monitors[o.fp].ddcWatch = o.ddcWatch === true
          if (!o.ddcWatch) {
            delete mem.monitors[o.fp].ddcInput
            delete mem.monitors[o.fp].ddcConnector
          }
        }
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
      transform: cleanTransform(o.transform),
      ddcWatch: o.ddcWatch === true
    }
    var desc = clean(o.description || previous.description)
    if (desc) profile.description = desc
    var brightness = cleanBrightness(o.brightness !== null && o.brightness !== undefined ? o.brightness : previous.brightness)
    if (brightness !== null) profile.brightness = brightness
    // The input a watched monitor is expected on is learned when the watch is
    // switched on, and kept while it stays on. Also keep the PC connector name
    // so moving the monitor to a different PC port auto-relearns its input.
    if (profile.ddcWatch && previous.ddcWatch && previous.ddcInput) {
      profile.ddcInput = previous.ddcInput
      if (previous.ddcConnector) profile.ddcConnector = previous.ddcConnector
    }
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

// One DDC/CI reading of a watched monitor's input source.
// - Explicit different input reading (non-empty, != expected): 2 misses -> off.
// - No reply (null/empty, e.g. I2C bus contention from brightness slider or
//   powered off): 4 consecutive no-replies (20s) -> off.
// - Reading the expected input resets misses and restores the output.
function ddcStep(misses, reading, expected) {
  var count = Number(misses) || 0
  if (reading && expected && String(reading).toLowerCase() === String(expected).toLowerCase())
    return { misses: 0, off: false }
  if (reading) {
    count += 2
  } else {
    count += 1
  }
  return { misses: count, off: count >= 4 }
}

// True when a watched external monitor needs its VCP 0x60 input learned:
// missing input, moved to a different PC connector, or legacy profile saved
// before ddcConnector tracking existed.
function ddcNeedsRelearn(profile, connectorName) {
  if (!profile || profile.ddcWatch !== true) return false
  if (!profile.ddcInput) return true
  return !profile.ddcConnector || String(profile.ddcConnector) !== String(connectorName || "")
}

// Clear DDC state for disconnected monitors AND for watched monitors that
// reconnected on a different PC connector (or require legacy migration) so a
// quick port swap while auto-disabled never leaves the monitor stuck off.
function pruneDdcState(identifiedOutputs, memory, ddcOff, ddcMisses, relearnState) {
  var mem = normalizeMemory(memory)
  var present = {}
  var connectors = {}
  for (var i = 0; i < (identifiedOutputs || []).length; i++) {
    var o = identifiedOutputs[i]
    if (!o || !o.fp) continue
    present[o.fp] = true
    connectors[o.fp] = o.name
  }
  var off = {}
  var misses = {}
  var relearn = {}
  for (var fp in ddcOff || {}) {
    if (!present[fp]) continue
    var profOff = mem.monitors[fp]
    if (profOff && ddcNeedsRelearn(profOff, connectors[fp])) continue
    off[fp] = ddcOff[fp]
  }
  for (var mp in ddcMisses || {}) {
    if (!present[mp]) continue
    var profMiss = mem.monitors[mp]
    if (profMiss && ddcNeedsRelearn(profMiss, connectors[mp])) continue
    misses[mp] = ddcMisses[mp]
  }
  for (var rp in relearnState || {}) {
    if (!present[rp]) continue
    var entry = relearnState[rp]
    if (!entry || entry.connector !== connectors[rp]) continue
    relearn[rp] = entry
  }
  return { ddcOff: off, ddcMisses: misses, relearnState: relearn }
}

function ddcRelearnBegin(state, fp, connector, now) {
  var cur = state && state[fp] ? state[fp] : null
  var sameConn = !!(cur && cur.connector === connector)
  if (sameConn && cur.inFlight) return { allowed: false, state: state || {} }
  if (sameConn && cur.nextRetryAt && now < cur.nextRetryAt) return { allowed: false, state: state || {} }
  var next = {}
  for (var k in state || {}) next[k] = state[k]
  next[fp] = {
    connector: connector,
    inFlight: true,
    failures: sameConn ? (Number(cur.failures) || 0) : 0,
    nextRetryAt: 0
  }
  return { allowed: true, state: next }
}

function ddcRelearnFinish(state, fp, connector, ok, now) {
  var next = {}
  for (var k in state || {}) next[k] = state[k]
  var cur = next[fp]
  if (!cur || cur.connector !== connector) return next
  if (ok) {
    delete next[fp]
    return next
  }
  var failures = (Number(cur.failures) || 0) + 1
  var backoffMs = Math.min(60000, 5000 * Math.pow(2, failures - 1))
  next[fp] = {
    connector: connector,
    inFlight: false,
    failures: failures,
    nextRetryAt: now + backoffMs
  }
  return next
}

function ddcRelearnClearBackoff(state) {
  var next = {}
  for (var k in state || {}) {
    var cur = state[k]
    if (cur && cur.inFlight) next[k] = cur
  }
  return next
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
  var remainingExternal = externals.filter(function(o) { return !(ddcOff && ddcOff[o.fp]) }).length
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

if (typeof module !== "undefined") module.exports = {}

// The bar's sections as current Omarchy writes them to shell.json. The
// center box sits in the middle of the bar; centerBegin and centerEnd hang
// off its edges; tray is drawn inside the tray widget's drawer.
var sections = ["left", "centerBegin", "center", "centerEnd", "right", "tray"]
var centerSections = ["centerBegin", "center", "centerEnd"]

function isPlainObject(value) {
  return !!value && typeof value === "object" && !Array.isArray(value)
}

function normalizePosition(value) {
  var next = String(value || "").trim()
  return /^(top|bottom|left|right)$/.test(next) ? next : "top"
}

function entrySettings(entry) {
  if (!isPlainObject(entry)) return {}
  var copy = {}
  for (var key in entry) {
    if (key === "id") continue
    copy[key] = entry[key]
  }
  return copy
}

function entryId(entry) {
  if (typeof entry === "string") return entry
  if (isPlainObject(entry)) {
    var id = entry["id"]
    if (id !== undefined && id !== null && String(id) !== "") return String(id)
  }
  return ""
}

function pinTrayToInner(entries, section) {
  var trayEntry = null
  var result = []
  var values = Array.isArray(entries) ? entries : []
  for (var i = 0; i < values.length; i++) {
    if (entryId(values[i]) === "omarchy.tray") trayEntry = values[i]
    else result.push(values[i])
  }
  if (trayEntry) {
    if (section === "right") result.unshift(trayEntry)
    else result.push(trayEntry)
  }
  return result
}

// Dicey Bar's built-in Displays panel takes over `omarchy.monitor` so the bar
// shows one unified display icon. If a config also lists the legacy
// `dicey.displays` id alongside `omarchy.monitor`, drop the duplicate so both
// icons never appear side by side.
function mergeDisplayEntries(layout) {
  if (!isPlainObject(layout)) return layout
  var hasStock = false
  var hasDicey = false
  for (var i = 0; i < sections.length; i++) {
    var list = layout[sections[i]]
    if (!Array.isArray(list)) continue
    for (var j = 0; j < list.length; j++) {
      var id = entryId(list[j])
      if (id === "omarchy.monitor") hasStock = true
      else if (id === "dicey.displays") hasDicey = true
    }
  }
  if (!hasStock || !hasDicey) return layout
  var out = {}
  for (var s = 0; s < sections.length; s++) {
    var sec = sections[s]
    var entries = layout[sec]
    if (!Array.isArray(entries)) {
      out[sec] = entries
      continue
    }
    var kept = []
    for (var k = 0; k < entries.length; k++) {
      if (entryId(entries[k]) !== "dicey.displays") kept.push(entries[k])
    }
    out[sec] = kept
  }
  return out
}

function moduleString(entry, key, fallback) {
  var settings = entrySettings(entry)
  var value = settings[key]
  return value === undefined || value === null ? fallback : String(value)
}

function entryIndex(entries, name) {
  if (!Array.isArray(entries)) return -1
  for (var i = 0; i < entries.length; i++) {
    if (entryId(entries[i]) === name) return i
  }
  return -1
}

// Panel tabbing and positional hotkeys take the center as one section: the
// box and the two sections hanging off it, in the order they read.
function navigationRegions(region) {
  var name = String(region || "")
  return centerSections.indexOf(name) !== -1 ? centerSections.slice() : [name]
}

// The center island: the anchor group always shown, and the flanks the
// island reveals around it on hover. A legacy `centerAnchor` picks one entry
// of the center section as the anchor and leaves its neighbours in the
// flanks. Without one, the whole center section is the anchor and
// centerBegin/centerEnd are the flanks. Each flank keeps every entry's own
// section, so drags and settings write back where the entry lives.
function centerIsland(layout, anchorId) {
  function section(name) {
    var entries = isPlainObject(layout) ? layout[name] : null
    return Array.isArray(entries) ? entries : []
  }
  function rows(entries, region, into) {
    for (var i = 0; i < entries.length; i++) {
      into.entries.push(entries[i])
      into.regions.push(region)
    }
    return into
  }
  var center = section("center")
  var index = anchorId ? entryIndex(center, anchorId) : -1
  var before = rows(section("centerBegin"), "centerBegin", { entries: [], regions: [] })
  var after = { entries: [], regions: [] }
  if (index !== -1) {
    rows(center.slice(0, index), "center", before)
    rows(center.slice(index + 1), "center", after)
  }
  rows(section("centerEnd"), "centerEnd", after)
  return {
    before: before,
    anchor: index === -1 ? center.slice() : [center[index]],
    after: after
  }
}

// A shell.json write that only changes inline widget settings (the battery
// percentage toggle, a clock format change) must not rebuild the bar.
// Compare two normalized layouts: when the structure is unchanged — same
// entry ids in the same order per region — return the settings-only changes
// as {region, index, entry}. Return null when the change is structural, or
// touches an entry a live settings push cannot safely reach: custom modules
// read their entry directly rather than an injected settings property, and
// a duplicated id makes the push ambiguous.
function inlineSettingsDelta(current, next) {
  if (!isPlainObject(current) || !isPlainObject(next)) return null
  var regions = sections
  var counts = {}
  for (var r = 0; r < regions.length; r++) {
    var entries = Array.isArray(next[regions[r]]) ? next[regions[r]] : []
    for (var i = 0; i < entries.length; i++) {
      var id = entryId(entries[i])
      counts[id] = (counts[id] || 0) + 1
    }
  }
  var changes = []
  for (var s = 0; s < regions.length; s++) {
    var region = regions[s]
    var a = Array.isArray(current[region]) ? current[region] : []
    var b = Array.isArray(next[region]) ? next[region] : []
    if (a.length !== b.length) return null
    for (var j = 0; j < a.length; j++) {
      if (entryId(a[j]) !== entryId(b[j])) return null
      if (JSON.stringify(a[j]) === JSON.stringify(b[j])) continue
      if (customModuleType(a[j]) || customModuleType(b[j])) return null
      if (counts[entryId(b[j])] > 1) return null
      changes.push({ region: region, index: j, entry: b[j] })
    }
  }
  return changes
}

function expandPath(value, home) {
  var path = String(value || "")
  if (path === "") return ""
  if (path.indexOf("~/") === 0) return home + path.substring(1)
  if (path.indexOf("$HOME/") === 0) return home + path.substring(5)
  return path
}

function customModuleSafeName(name) {
  var value = String(name || "")
  return value !== "" && value.indexOf("..") === -1 && value[0] !== "/"
}

function customModuleType(entry) {
  var settings = entrySettings(entry)
  var type = String(settings.type || "")
  if (type) return type
  if (settings.exec) return "command"
  if (settings.source) return "qml"
  return ""
}

function customModulePath(entry, home, configDir) {
  var settings = entrySettings(entry)
  var name = entryId(entry)
  var source = settings.source ? expandPath(settings.source, home) : ""
  if (!source && customModuleSafeName(name))
    source = String(configDir || "") + "/bar/modules/" + String(name) + ".qml"
  return source
}

// A widget has a copy on every monitor's bar, and a center flank's copy is
// zero-size while the island is collapsed. Panel routing has to pick a drawn
// copy — it is the only one that can anchor a popup, carry the open-panel
// mark, or be found again by switchPanelFrom — and fall back to a hidden one
// only when nothing is on screen. The order copies are registered in is not
// stable across a live bar reconfiguration, so the first match is not enough.
function isDrawnSlot(slot) {
  return !!slot && slot.visible === true && slot.width > 0 && slot.height > 0
}

function pickDrawnSlot(slots) {
  var placeholder = null
  var list = slots || []
  for (var i = 0; i < list.length; i++) {
    if (!list[i]) continue
    if (isDrawnSlot(list[i])) return list[i]
    if (!placeholder) placeholder = list[i]
  }
  return placeholder
}

// A bar surface is built per monitor, so a panel hotkey has several live
// copies of the same widget to route to, and the panel opens on whichever
// monitor's copy answers. Candidates are `{ slot, screenName, opened }`.
//
// An open copy wins first: hide and toggle have to reach the panel the user
// can actually see, wherever it was opened from. Otherwise the focused
// monitor's copy wins, so a summon lands where the user is working instead of
// on whichever output registered its slot first. Neither narrowing applies on
// a single monitor, or when the focused output has no bar of its own.
function pickPanelSlot(candidates, focusedScreen) {
  var rows = Array.isArray(candidates) ? candidates : []
  var pool = rows.filter(function(row) { return row && row.opened === true })
  if (pool.length === 0) pool = rows.filter(function(row) { return !!row })

  var focused = String(focusedScreen || "")
  if (focused) {
    var onFocused = pool.filter(function(row) { return row.screenName === focused })
    if (onFocused.length > 0) pool = onFocused
  }

  return pickDrawnSlot(pool.map(function(row) { return row.slot }))
}

// Resolve a pointer anywhere along the bar to the closest insertion edge.
// Requiring the pointer to sit inside another widget makes the empty space
// around a centered group a dead zone, even though it visually reads as the
// most natural place to drop.
function nearestDropTarget(candidates, point, vertical) {
  var rows = Array.isArray(candidates) ? candidates : []
  var axis = vertical ? Number(point && point.y) : Number(point && point.x)
  if (!isFinite(axis)) return null

  var best = null
  var bestDistance = Infinity
  for (var i = 0; i < rows.length; i++) {
    var row = rows[i]
    if (!row || !row.slot) continue

    var start = Number(vertical ? row.y : row.x)
    var size = Number(vertical ? row.height : row.width)
    if (!isFinite(start) || !isFinite(size) || size <= 0) continue

    var beforeDistance = Math.abs(axis - start)
    var afterDistance = Math.abs(axis - (start + size))
    var after = afterDistance < beforeDistance
    var distance = after ? afterDistance : beforeDistance
    if (distance < bestDistance) {
      best = { slot: row.slot, after: after }
      bestDistance = distance
    }
  }
  return best
}

// Whether a real fullscreen window (Hyprland fullscreen mode bit 2) is on the
// given workspace.
function fullscreenOn(toplevels, workspaceId) {
  var list = toplevels || []
  for (var i = 0; i < list.length; i++) {
    var toplevel = list[i]
    if (!toplevel || !toplevel.workspace || toplevel.workspace.id !== workspaceId) continue
    var ipc = toplevel.lastIpcObject
    if (ipc && (Number(ipc.fullscreen) & 2) !== 0) return true
  }
  return false
}

if (typeof module !== "undefined") {
  module.exports = {
    isDrawnSlot: isDrawnSlot,
    pickDrawnSlot: pickDrawnSlot,
    pickPanelSlot: pickPanelSlot,
    nearestDropTarget: nearestDropTarget,
    normalizePosition: normalizePosition,
    entrySettings: entrySettings,
    entryId: entryId,
    pinTrayToInner: pinTrayToInner,
    mergeDisplayEntries: mergeDisplayEntries,
    moduleString: moduleString,
    entryIndex: entryIndex,
    sections: sections,
    navigationRegions: navigationRegions,
    centerIsland: centerIsland,
    fullscreenOn: fullscreenOn,
    inlineSettingsDelta: inlineSettingsDelta,
    expandPath: expandPath,
    customModuleSafeName: customModuleSafeName,
    customModuleType: customModuleType,
    customModulePath: customModulePath
  }
}

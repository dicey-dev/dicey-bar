import QtQuick
import QtQuick.Controls
import Quickshell
import Quickshell.Io
import qs.Ui
import qs.Commons
import qs.Commons as Commons
import "DisplayModel.js" as Model

// Unified Displays bar widget (replaces stock `omarchy.monitor` and answers
// `dicey.displays`). Per-monitor settings (position, resolution, refresh rate,
// scale, orientation, adaptive sync, HDR + SDR tuning, brightness, DDC/CI
// input watch) are scoped to the selected monitor; OS-wide controls
// (text size and night light) sit in a dedicated All Displays card with a
// separating bar so their scope is never ambiguous.
Panel {
  id: root
  moduleName: "omarchy.monitor"
  ipcTarget: "omarchy.monitor"
  manageIpc: false

  readonly property var service: bar && bar.displayService ? bar.displayService : null
  readonly property var live: service ? service.livePlan : []
  readonly property var liveMonitors: service ? service.monitors : []

  // The arrangement being edited; it becomes the plan on Apply.
  property var draft: []
  property int selected: 0
  property bool edited: false

  // Transient confirmation text in the footer (e.g. Forget, Sync all, Re-learn).
  property string noticeText: ""

  // OS-wide text size draft (remembered per monitor combination on Apply;
  // reverted on Discard if previewed live).
  property int openedTextSize: 0
  property int draftTextSize: 0
  property bool textSizeEdited: false

  // OS-wide Night Light draft (controlled via hyprsunset; remembered per
  // monitor combination on Apply when edited and reverted on Discard).
  property var openedNightLight: ({ enabled: false, kelvin: 4000 })
  property var draftNightLight: ({ enabled: false, kelvin: 4000 })
  property bool nightLightEdited: false

  // Snapshot of per-monitor brightness as first read on open, so Discard and
  // confirmation rollback undo hardware brightness changes cleanly.
  property var openedBrightness: ({})
  property bool brightnessEdited: false

  function monitorByName(name) {
    for (var i = 0; i < liveMonitors.length; i++)
      if (liveMonitors[i] && liveMonitors[i].name === name) return liveMonitors[i]
    return null
  }

  function draftTopologyKey(list) {
    return (Array.isArray(list) ? list : []).map(function(o) { return o ? o.name : "" }).sort().join("\n")
  }

  readonly property bool dirty: {
    if (!draft.length || draft.length !== live.length) return false
    if (textSizeEdited && draftTextSize !== openedTextSize) return true
    if (nightLightEdited && !Model.sameNightLight(draftNightLight, openedNightLight)) return true
    if (brightnessEdited) return true
    if (!Model.planMatchesLive(draft, live)) return true
    for (var i = 0; i < draft.length; i++) {
      if (draft[i].ddcWatch !== live[i].ddcWatch) return true
      if ((draft[i].brightness || null) !== (live[i].brightness || null)) return true
    }
    return false
  }
  readonly property var current: selected >= 0 && selected < draft.length ? draft[selected] : null
  readonly property var currentMonitor: current ? monitorByName(current.name) : null
  readonly property var currentGroups: currentMonitor ? Model.modeGroups(currentMonitor.availableModes) : []
  readonly property var currentRates: {
    if (!current || !current.mode) return []
    for (var i = 0; i < currentGroups.length; i++)
      if (currentGroups[i].w === current.mode.w && currentGroups[i].h === current.mode.h) return currentGroups[i].rates
    return [current.mode.hz]
  }
  readonly property var resolutionOptions: {
    var out = []
    for (var i = 0; i < currentGroups.length; i++) {
      var g = currentGroups[i]
      out.push({ value: g.key, label: g.w + " × " + g.h })
    }
    if (!out.length && current && current.mode) {
      var k = current.mode.w + "x" + current.mode.h
      out.push({ value: k, label: current.mode.w + " × " + current.mode.h })
    }
    return out
  }
  readonly property string currentResolutionKey: current && current.mode ? (current.mode.w + "x" + current.mode.h) : ""
  readonly property var rateOptions: {
    var out = []
    for (var i = 0; i < currentRates.length; i++) {
      var r = currentRates[i]
      out.push({ value: Model.formatHz(r), label: Model.formatHz(r) + " Hz" })
    }
    return out
  }
  readonly property string currentRateKey: {
    if (!current || !current.mode) return ""
    for (var i = 0; i < currentRates.length; i++)
      if (Model.sameHz(currentRates[i], current.mode.hz)) return Model.formatHz(currentRates[i])
    return Model.formatHz(current.mode.hz)
  }
  readonly property int enabledCount: {
    var n = 0
    for (var i = 0; i < draft.length; i++) if (draft[i].enabled && !draft[i].autoOff) n++
    return n
  }
  readonly property int availableMonitorCount: {
    var n = 0
    for (var i = 0; i < draft.length; i++) if (!draft[i].autoOff) n++
    return n
  }
  readonly property bool isMirroredMode: {
    for (var i = 0; i < draft.length; i++)
      if (draft[i] && draft[i].enabled && !draft[i].autoOff && !!draft[i].mirrorOf) return true
    return false
  }
  readonly property int shownTextSize: textSizeEdited && draftTextSize >= 9 ? draftTextSize : Style.font.baseSize
  readonly property bool shownNightLightOn: nightLightEdited
    ? !!draftNightLight.enabled
    : !!(service && service.liveNightLight && service.liveNightLight.enabled)
  readonly property int shownKelvin: {
    var src = nightLightEdited ? draftNightLight : (service ? service.liveNightLight : null)
    var k = src ? Math.round(Number(src.kelvin)) : 4000
    return (isFinite(k) && k >= 1500 && k <= 6400) ? k : 4000
  }
  readonly property bool currentHasSavedProfile: !!(service && current && service.hasSavedProfile(current.fp))
  readonly property bool currentSavedDdcWatch: !!(service && current && service.hasSavedDdcWatch(current.fp))
  readonly property string currentLearnedDdcInput: (service && current) ? service.savedDdcInput(current.fp) : ""

  // Theme-derived palette — strictly Commons.Color tokens so theme swaps apply live.
  readonly property color fg: bar ? bar.foreground : Commons.Color.foreground
  readonly property color bg: Commons.Color.popups.background
  readonly property color accent: Commons.Color.accent
  readonly property color alertColor: Commons.Color.bar.active
  readonly property color muted: Qt.rgba(fg.r, fg.g, fg.b, 0.58)
  readonly property color cardBg: Qt.rgba(fg.r, fg.g, fg.b, 0.035)
  readonly property color trackBg: Qt.rgba(fg.r, fg.g, fg.b, 0.055)
  readonly property color canvasBg: Qt.rgba(bg.r * 0.65, bg.g * 0.65, bg.b * 0.65, 0.92)
  readonly property color cardBorder: Qt.rgba(fg.r, fg.g, fg.b, 0.14)
  readonly property color hairline: Qt.rgba(fg.r, fg.g, fg.b, 0.10)
  readonly property color accentSoft: Qt.rgba(accent.r, accent.g, accent.b, 0.16)
  readonly property color accentBorder: Qt.rgba(accent.r, accent.g, accent.b, 0.72)
  readonly property color alertSoft: Qt.rgba(alertColor.r, alertColor.g, alertColor.b, 0.16)
  readonly property color alertBorder: Qt.rgba(alertColor.r, alertColor.g, alertColor.b, 0.68)
  readonly property real accentLuma: 0.2126 * accent.r + 0.7152 * accent.g + 0.0722 * accent.b
  readonly property color accentInk: accentLuma > 0.45 ? Commons.Color.background : Commons.Color.foreground
  readonly property string fontName: bar ? bar.fontFamily : Style.font.family

  Timer {
    id: noticeTimer
    interval: 4000
    onTriggered: root.noticeText = ""
  }

  function showNotice(msg) {
    noticeText = String(msg || "")
    if (noticeText) noticeTimer.restart()
    else noticeTimer.stop()
  }

  function ddcInputLabel(code) {
    var c = String(code || "").toLowerCase()
    var map = {
      "0x01": "VGA-1", "0x02": "VGA-2", "0x03": "DVI-1", "0x04": "DVI-2",
      "0x0f": "DP-1", "0x10": "DP-2", "0x11": "HDMI-1", "0x12": "HDMI-2",
      "0x19": "USB-C-1", "0x1b": "USB-C"
    }
    return map[c] ? (map[c] + " (" + c + ")") : c
  }

  function transformDegrees(t) {
    var n = Number(t) || 0
    if (n === 1) return "90°"
    if (n === 2) return "180°"
    if (n === 3) return "270°"
    return "0°"
  }

  function mirrorsOf(name) {
    var out = []
    for (var i = 0; i < draft.length; i++) {
      var o = draft[i]
      if (o && o.enabled && !o.autoOff && o.mirrorOf === name) out.push(o.name)
    }
    return out
  }

  function shortModelName(index) {
    var o = index >= 0 && index < draft.length ? draft[index] : null
    var m = o ? monitorByName(o.name) : (index >= 0 && index < liveMonitors.length ? liveMonitors[index] : null)
    if (!m) return o ? o.name : ""
    var model = String(m.model || "").trim()
    if (model && !/^0x[0-9a-f]+$/i.test(model)) return model
    var desc = String(m.description || "").trim()
    return desc || String(m.name || "")
  }

  function resetDraft() {
    draft = live.map(copyOutput)
    edited = false
    if (selected >= draft.length) selected = 0
  }

  function syncNightLightFromService() {
    if (nightLightEdited) return
    var nl = service && service.liveNightLight ? service.liveNightLight : { enabled: false, kelvin: 4000 }
    var k = Math.round(Number(nl.kelvin))
    if (!isFinite(k) || k < 1500 || k > 6400) k = 4000
    openedNightLight = { enabled: !!nl.enabled, kelvin: k }
    draftNightLight = { enabled: !!nl.enabled, kelvin: k }
  }

  function discardDraft() {
    if (textSizeEdited && openedTextSize >= 9 && openedTextSize <= 20 && service) {
      service.applyTextSize(openedTextSize)
    }
    if (nightLightEdited && service) {
      service.setNightLight(openedNightLight.enabled, openedNightLight.kelvin, null)
    }
    if (brightnessEdited && service) {
      for (var name in openedBrightness) {
        service.enqueueBrightness(name, openedBrightness[name])
      }
      if (current && Object.prototype.hasOwnProperty.call(openedBrightness, current.name))
        brightness = openedBrightness[current.name]
    }
    textSizeEdited = false
    draftTextSize = openedTextSize
    nightLightEdited = false
    draftNightLight = { enabled: openedNightLight.enabled, kelvin: openedNightLight.kelvin }
    brightnessEdited = false
    resetDraft()
    showNotice("Discarded unsaved changes")
  }

  function copyOutput(o) {
    var c = {}
    for (var k in o) c[k] = o[k]
    if (o.mode) c.mode = { w: o.mode.w, h: o.mode.h, hz: o.mode.hz }
    return c
  }

  function update(index, changes, rearrange) {
    if (index < 0 || index >= draft.length) return
    var next = draft.map(copyOutput)
    for (var k in changes) next[index][k] = changes[k]
    draft = rearrange ? Model.solve(next) : next
    edited = true
  }

  function applyPreset(preset) {
    if (!draft.length) return
    draft = Model.applyLayoutPreset(draft, preset)
    edited = true
  }

  function setResolutionByKey(index, key) {
    for (var i = 0; i < currentGroups.length; i++) {
      var group = currentGroups[i]
      if (group.key !== key) continue
      var o = draft[index]
      var hz = group.rates[0]
      for (var r = 0; r < group.rates.length; r++)
        if (Model.sameHz(group.rates[r], o.mode.hz)) hz = group.rates[r]
      update(index, {
        mode: { w: group.w, h: group.h, hz: hz },
        scale: Model.cleanScale(o.scale, group.w, group.h)
      }, true)
      return
    }
  }

  function setRateByKey(index, key) {
    var o = draft[index]
    if (!o || !o.mode) return
    for (var i = 0; i < currentRates.length; i++) {
      if (Model.formatHz(currentRates[i]) === key) {
        update(index, { mode: { w: o.mode.w, h: o.mode.h, hz: currentRates[i] } }, false)
        return
      }
    }
  }

  function pickTextSize(px) {
    var clean = Model.cleanTextSize(px)
    if (clean === null) return
    draftTextSize = clean
    textSizeEdited = true
    if (service) service.applyTextSize(clean)
  }

  function setNightLightDraft(enabled, kelvin, immediate) {
    var k = Math.max(1500, Math.min(6400, Math.round(Number(kelvin) || shownKelvin || 4000)))
    draftNightLight = { enabled: !!enabled, kelvin: k }
    nightLightEdited = true
    if (immediate) {
      kelvinDebounce.stop()
      if (service) service.setNightLight(draftNightLight.enabled, draftNightLight.kelvin, null)
    } else {
      kelvinDebounce.restart()
    }
  }

  function syncBrightnessToAll() {
    if (!brightnessAvailable || !service || enabledCount < 2) return
    var pct = Math.max(1, Math.min(100, Math.round(brightness)))
    var next = draft.map(copyOutput)
    for (var i = 0; i < next.length; i++) {
      if (next[i].enabled && !next[i].autoOff) next[i].brightness = pct
    }
    draft = next
    edited = true
    brightnessEdited = true
    service.setAllBrightness(pct, draft)
    showNotice("Synced brightness to " + pct + "% across all displays")
  }

  function forgetSelectedMonitor() {
    if (!service || !current) return
    var name = current.name
    var fp = current.fp
    service.forgetMonitor(fp, function(ok) {
      if (ok) showNotice("Forgot saved profile for " + name)
    })
  }

  function relearnSelectedDdc() {
    if (!service || !current) return
    var name = current.name
    var fp = current.fp
    showNotice("Reading DDC/CI input for " + name + "…")
    service.relearnDdcInput(name, fp, function(ok, data) {
      if (ok && data && data.ddcInput) {
        showNotice("Learned input " + root.ddcInputLabel(data.ddcInput) + " for " + name)
      } else {
        showNotice((data && data.error) ? data.error : ("Could not read input for " + name))
      }
    })
  }

  function canTurnOff(index) {
    var o = draft[index]
    if (!o) return false
    if (!o.enabled) return true
    var activeNonMirror = 0
    for (var i = 0; i < draft.length; i++) {
      if (i !== index && draft[i].enabled && !draft[i].autoOff && !draft[i].mirrorOf) activeNonMirror++
    }
    return activeNonMirror > 0
  }

  function apply() {
    if (!service || !dirty || service.confirming) return
    var plan = Model.solve(draft)
    // Only persist OS-wide text size / night light when explicitly edited in
    // this session so an unrelated display change never overwrites them.
    var options = {}
    if (textSizeEdited) options.textSize = draftTextSize
    if (nightLightEdited) options.nightLight = { enabled: shownNightLightOn, kelvin: shownKelvin }
    var snap = {
      textSizeEdited: textSizeEdited,
      openedTextSize: openedTextSize,
      nightLightEdited: nightLightEdited,
      openedNightLight: { enabled: openedNightLight.enabled, kelvin: openedNightLight.kelvin },
      openedBrightness: brightnessEdited ? Object.assign({}, openedBrightness) : null
    }
    edited = false
    if (textSizeEdited) openedTextSize = draftTextSize
    textSizeEdited = false
    if (nightLightEdited) openedNightLight = { enabled: shownNightLightOn, kelvin: shownKelvin }
    nightLightEdited = false
    brightnessEdited = false
    openedBrightness = ({})
    noticeText = ""
    service.applyFromPanel(plan, options, snap)
  }

  function offText(o) {
    if (!o) return ""
    if (o.offReason === "lid") return "Off while lid is closed"
    if (o.offReason === "input") return "Switched to another input"
    return ""
  }

  function statusBadgeText(o) {
    if (!o) return ""
    if (o.autoOff) return o.offReason === "lid" ? "Lid closed" : "Switched away"
    if (!o.enabled) return "Disabled"
    if (o.mirrorOf) return "Mirroring"
    return "Active"
  }

  function focusedOutputName() {
    for (var i = 0; i < liveMonitors.length; i++)
      if (liveMonitors[i] && liveMonitors[i].focused) return String(liveMonitors[i].name || "")
    return current ? current.name : ""
  }

  // Compatible with stock `omarchy.monitor` IPC so brightness hotkeys and
  // `omarchy launch monitor` route directly to this unified panel.
  ShellIpc {
    target: "omarchy.monitor"

    function brightness(percent: string): string {
      var val = Number(percent)
      var target = root.focusedOutputName()
      if (!target || !isFinite(val)) return "error invalid brightness"
      var clamped = Math.max(1, Math.min(100, Math.round(val)))
      if (root.service) root.service.enqueueBrightness(target, clamped)
      if (root.opened && root.current && root.current.name === target)
        root.brightness = clamped
      return "got " + clamped
    }

    function state(): string {
      return JSON.stringify({
        brightness: root.brightness,
        brightnessAvailable: root.brightnessAvailable,
        focusedMonitor: root.focusedOutputName(),
        scale: root.current ? String(root.current.scale) : "",
        displays: root.liveMonitors
      })
    }

    function open() { root.open() }
    function close() { root.close() }
    function toggle() { root.toggle() }
    function show() { root.open() }
    function hide() { root.close() }
  }

  readonly property string hostScreenName: (button.QsWindow && button.QsWindow.window && button.QsWindow.window.screen)
    ? String(button.QsWindow.window.screen.name || "") : ""
  property bool pendingHostSelect: false
  property real wheelAccumulator: 0

  function selectHostDisplay() {
    var target = hostScreenName || focusedOutputName()
    if (!target || !draft.length) return false
    for (var i = 0; i < draft.length; i++) {
      if (draft[i] && draft[i].name === target) {
        selected = i
        return true
      }
    }
    return false
  }

  Connections {
    target: root.service
    function onRefreshed() {
      if (!root.edited) {
        root.resetDraft()
        if (root.pendingHostSelect && root.selectHostDisplay()) {
          root.pendingHostSelect = false
          root.readBrightness()
        }
      } else if (root.draftTopologyKey(root.draft) !== root.draftTopologyKey(root.live)) {
        root.resetDraft()
        root.selectHostDisplay()
        root.showNotice("Display topology changed — refreshed")
      }
      root.syncNightLightFromService()
    }
  }

  onOpenedChanged: {
    if (!opened) {
      pendingHostSelect = false
      return
    }
    openedTextSize = Style.font.baseSize
    draftTextSize = Style.font.baseSize
    textSizeEdited = false
    nightLightEdited = false
    openedBrightness = ({})
    brightnessEdited = false
    noticeText = ""
    syncNightLightFromService()
    resetDraft()
    pendingHostSelect = !selectHostDisplay()
    if (service) service.refresh()
    readBrightness()
  }
  onSelectedChanged: {
    readBrightness()
  }

  // ---------------------------------------------------------------- brightness (per monitor)
  property int brightness: 0
  property bool brightnessAvailable: false
  property string brightnessFor: ""
  property bool brightnessReadQueued: false

  function readBrightness() {
    var o = selected >= 0 && selected < draft.length ? draft[selected] : null
    brightnessAvailable = false
    if (!o || !o.enabled || o.autoOff || !opened) return
    brightnessFor = o.name
    if (brightnessRead.running) {
      brightnessReadQueued = true
      return
    }
    brightnessRead.command = ["omarchy-brightness-display", "--monitor", o.name]
    brightnessRead.running = true
  }

  function setBrightness(value, recordInDraft) {
    var v = Number(value)
    if (!isFinite(v) || !brightnessFor) return
    var pct = Math.max(1, Math.min(100, Math.round(v)))
    brightness = pct
    if (recordInDraft !== false && current && current.name === brightnessFor) {
      brightnessEdited = true
      update(selected, { brightness: pct }, false)
    }
    if (service) service.enqueueBrightness(brightnessFor, pct)
  }

  Process {
    id: brightnessRead
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        var value = parseInt(String(text || "").trim(), 10)
        var asked = brightnessRead.command.length ? brightnessRead.command[brightnessRead.command.length - 1] : ""
        if (isFinite(value) && asked === root.brightnessFor) {
          var clamped = Math.max(1, Math.min(100, value))
          root.brightness = clamped
          root.brightnessAvailable = true
          if (!Object.prototype.hasOwnProperty.call(root.openedBrightness, asked)) {
            var nextMap = Object.assign({}, root.openedBrightness)
            nextMap[asked] = clamped
            root.openedBrightness = nextMap
          }
        }
      }
    }
    onRunningChanged: {
      if (running || !root.brightnessReadQueued) return
      root.brightnessReadQueued = false
      root.readBrightness()
    }
  }

  Timer {
    id: brightnessDebounce
    interval: 140
    onTriggered: root.setBrightness(root.brightness, true)
  }

  // ---------------------------------------------------------------- text size (OS-wide, remembered per setup)
  readonly property var textSizeStops: [9, 10, 11, 12, 14, 16, 20]

  // ---------------------------------------------------------------- night light (OS-wide via hyprsunset)
  readonly property var nightLightPresets: [
    { kelvin: 3000, label: "3000K" },
    { kelvin: 4000, label: "4000K" },
    { kelvin: 5000, label: "5000K" }
  ]

  Timer {
    id: kelvinDebounce
    interval: 120
    onTriggered: {
      if (root.service && root.draftNightLight.enabled)
        root.service.setNightLight(true, root.draftNightLight.kelvin, null)
    }
  }

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  BarIconButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: Quickshell.screens.length > 1 ? "󰍺" : "󰍹"
    tooltipText: "Displays · scroll for brightness"
    onPressed: function(b) { root.toggle() }
    onWheelMoved: function(delta) {
      var wheel = Util.wheelSteps(root.wheelAccumulator, delta)
      root.wheelAccumulator = wheel.remainder
      if (wheel.steps === 0) return
      var target = root.hostScreenName || root.focusedOutputName()
      if (!target || !root.service) return
      var stepPct = wheel.steps * 5
      root.service.enqueueBrightnessStep(target, stepPct)
      if (root.opened && root.current && root.current.name === target && root.brightnessAvailable) {
        root.brightness = Math.max(1, Math.min(100, root.brightness + stepPct))
      }
    }
  }

  KeyboardPanel {
    id: panel
    anchorItem: button
    owner: root
    bar: root.bar
    open: root.opened
    focusTarget: panel.loadedContent
    contentWidth: panel.fittedContentWidth(Style.space(620))
    contentHeight: panel.fittedContentHeight(panel.loadedContentHeight)

    content: PanelKeyCatcher {
      id: keyCatcher
      implicitHeight: column.implicitHeight + footer.implicitHeight + Style.space(14)
      anchors.fill: parent

      function scrollBy(delta) {
        var flick = scrollArea.contentItem
        if (!flick) return
        var maxY = Math.max(0, flick.contentHeight - flick.height)
        flick.contentY = Math.max(0, Math.min(maxY, flick.contentY + delta))
      }

      onCloseRequested: {
        if (resDropdown.popupOpen) resDropdown.close()
        else if (hzDropdown.popupOpen) hzDropdown.close()
        else root.close()
      }
      onTabRequested: function(direction) { root.switchPanel(direction) }
      onMoveRequested: function(dx, dy) {
        if (dx !== 0 && root.draft.length > 1) {
          root.selected = (root.selected + dx + root.draft.length) % root.draft.length
        } else if (dy !== 0) {
          scrollBy(dy * Style.space(56))
        }
      }
      onReturnRequested: {
        if (root.service && root.service.confirming) root.service.keep()
        else if (root.dirty) root.apply()
      }
      onTextKey: function(t, modifiers) {
        if (t >= "1" && t <= "9") {
          var idx = parseInt(t, 10) - 1
          if (idx >= 0 && idx < root.draft.length) root.selected = idx
          return
        }
        var lower = t.toLowerCase()
        if (lower === "r") {
          if (root.service) root.service.refresh()
          root.readBrightness()
        } else if (lower === "i") {
          if (root.service) root.service.identify()
        } else if ((t === "[" || t === "-") && root.brightnessAvailable) {
          root.setBrightness(root.brightness - 5, true)
        } else if ((t === "]" || t === "+" || t === "=") && root.brightnessAvailable) {
          root.setBrightness(root.brightness + 5, true)
        }
      }

      // ---------- Sticky Footer ----------
      Item {
        id: footer
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        implicitHeight: footerRow.implicitHeight + Style.space(10)
        height: implicitHeight

        Rectangle {
          anchors.left: parent.left
          anchors.right: parent.right
          anchors.top: parent.top
          height: 1
          color: root.hairline
        }

        Item {
          id: footerRow
          anchors.left: parent.left
          anchors.right: parent.right
          anchors.bottom: parent.bottom
          implicitHeight: Math.max(footerActions.implicitHeight, statusText.implicitHeight)
          height: implicitHeight

          Text {
            id: statusText
            anchors.left: parent.left
            anchors.right: footerActions.left
            anchors.rightMargin: Style.space(12)
            anchors.verticalCenter: parent.verticalCenter
            textFormat: Text.PlainText
            wrapMode: Text.Wrap
            text: {
              var s = root.service
              if (!s) return ""
              if (s.lastError) return s.lastError
              if (s.fallbacks.length) {
                var f = s.fallbacks[0]
                return f.name + " fell back to " + f.actual + " instead of " + f.wanted
              }
              if (s.confirming) return "Confirming changes (" + s.confirmSeconds + "s) · Enter to keep"
              if (root.noticeText) return root.noticeText
              if (root.dirty) return "Unsaved changes · Apply or press Enter"
              return "Synced with monitors.lua · auto-applies on reconnect"
            }
            color: {
              var s = root.service
              if (s && (s.lastError || s.fallbacks.length)) return root.alertColor
              if (root.dirty || root.noticeText || (s && s.confirming)) return root.accent
              return root.muted
            }
            font.family: root.fontName
            font.pixelSize: Style.font.caption
          }

          Row {
            id: footerActions
            anchors.right: parent.right
            anchors.verticalCenter: parent.verticalCenter
            spacing: Style.space(8)

            Rectangle {
              id: discardBtn
              readonly property bool activeAction: root.dirty && !!root.service && !root.service.confirming
              implicitWidth: discardLabel.implicitWidth + Style.space(18)
              implicitHeight: Style.space(28)
              radius: Style.cornerRadius
              color: discardMouse.containsMouse && activeAction ? root.trackBg : "transparent"
              border.color: discardMouse.containsMouse && activeAction ? root.accentBorder : root.cardBorder
              border.width: 1
              opacity: activeAction ? 1 : 0.4

              Text {
                id: discardLabel
                anchors.centerIn: parent
                textFormat: Text.PlainText
                text: "Discard"
                color: root.fg
                font.family: root.fontName
                font.pixelSize: Style.font.caption
                font.bold: true
              }

              MouseArea {
                id: discardMouse
                anchors.fill: parent
                enabled: discardBtn.activeAction
                hoverEnabled: true
                cursorShape: Qt.PointingHandCursor
                onClicked: root.discardDraft()
              }
            }

            Rectangle {
              id: applyBtn
              readonly property bool canApply: root.dirty && !!root.service && !root.service.confirming
              implicitWidth: applyLabel.implicitWidth + Style.space(22)
              implicitHeight: Style.space(28)
              radius: Style.cornerRadius
              color: canApply
                ? (applyMouse.containsMouse ? Qt.lighter(root.accent, 1.08) : root.accent)
                : root.trackBg
              border.color: canApply ? root.accent : root.cardBorder
              border.width: 1
              opacity: canApply ? 1 : 0.55

              Behavior on color { ColorAnimation { duration: Style.duration(120) } }

              Text {
                id: applyLabel
                anchors.centerIn: parent
                textFormat: Text.PlainText
                text: "Apply"
                color: applyBtn.canApply ? root.accentInk : root.muted
                font.family: root.fontName
                font.pixelSize: Style.font.caption
                font.bold: true
              }

              MouseArea {
                id: applyMouse
                anchors.fill: parent
                enabled: applyBtn.canApply
                hoverEnabled: true
                cursorShape: Qt.PointingHandCursor
                onClicked: root.apply()
              }
            }
          }
        }
      }

      ScrollView {
        id: scrollArea
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        anchors.bottom: footer.top
        anchors.bottomMargin: Style.space(10)
        clip: true
        ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
        ScrollBar.vertical.policy: column.implicitHeight > height ? ScrollBar.AsNeeded : ScrollBar.AlwaysOff
        rightPadding: ScrollBar.vertical.visible ? ScrollBar.vertical.width + Style.space(4) : 0
        Binding {
          target: scrollArea.contentItem
          property: "interactive"
          value: column.implicitHeight > scrollArea.height
        }

        Column {
          id: column
          width: scrollArea.availableWidth
          spacing: Style.space(10)

          readonly property bool twoColumn: width >= Style.space(500)

          // ---------- Clean Header ----------
          Item {
            width: parent.width
            implicitHeight: Math.max(headerLeft.implicitHeight, headerActions.implicitHeight)

            Row {
              id: headerLeft
              anchors.left: parent.left
              anchors.right: headerActions.left
              anchors.rightMargin: Style.space(8)
              anchors.verticalCenter: parent.verticalCenter
              spacing: Style.space(10)

              Rectangle {
                width: Style.space(32)
                height: Style.space(32)
                radius: Math.min(Style.cornerRadius, Style.space(7))
                color: root.accentSoft
                border.color: root.accentBorder
                border.width: 1
                anchors.verticalCenter: parent.verticalCenter

                Text {
                  anchors.centerIn: parent
                  textFormat: Text.PlainText
                  text: root.draft.length > 1 ? "󰍺" : "󰍹"
                  color: root.accent
                  font.family: root.fontName
                  font.pixelSize: Style.font.heading
                }
              }

              Column {
                anchors.verticalCenter: parent.verticalCenter
                spacing: Style.space(2)
                width: Math.max(0, headerLeft.width - Style.space(42))

                Text {
                  width: parent.width
                  textFormat: Text.PlainText
                  text: "Displays"
                  color: root.fg
                  font.family: root.fontName
                  font.pixelSize: Style.font.title
                  font.bold: true
                  elide: Text.ElideRight
                }

                Text {
                  width: parent.width
                  textFormat: Text.PlainText
                  text: (root.draft.length === 1 ? "1 display connected" : root.draft.length + " displays connected")
                    + " · " + root.enabledCount + " active"
                  color: root.muted
                  font.family: root.fontName
                  font.pixelSize: Style.font.caption
                  elide: Text.ElideRight
                }
              }
            }

            Row {
              id: headerActions
              anchors.right: parent.right
              anchors.verticalCenter: parent.verticalCenter
              spacing: Style.space(6)

              // Option A: Single Flip Button (Extend <-> Mirror, uniform with Re-probe & Identify)
              Rectangle {
                id: modeToggle
                visible: root.availableMonitorCount >= 2
                implicitWidth: modeToggleRow.implicitWidth + Style.space(16)
                implicitHeight: Style.space(26)
                radius: Math.min(Style.cornerRadius, Style.space(6))
                color: root.isMirroredMode
                  ? root.accentSoft
                  : (modeToggleMouse.containsMouse ? root.trackBg : root.cardBg)
                border.color: root.isMirroredMode || modeToggleMouse.containsMouse ? root.accentBorder : root.cardBorder
                border.width: 1
                anchors.verticalCenter: parent.verticalCenter

                Behavior on color { ColorAnimation { duration: Style.duration(120) } }

                Row {
                  id: modeToggleRow
                  anchors.centerIn: parent
                  spacing: Style.space(6)

                  // Crisp geometric Extend (side-by-side) / Mirror (overlapping) icon
                  Item {
                    width: Style.space(13)
                    height: Style.space(11)
                    anchors.verticalCenter: parent.verticalCenter
                    readonly property color iconColor: root.isMirroredMode || modeToggleMouse.containsMouse ? root.accent : root.fg

                    // Extend: two side-by-side screens
                    Rectangle {
                      visible: !root.isMirroredMode
                      x: 0; y: 1
                      width: Style.space(6); height: Style.space(9)
                      radius: 1.5; color: "transparent"
                      border.color: parent.iconColor; border.width: 1.4
                    }
                    Rectangle {
                      visible: !root.isMirroredMode
                      x: Style.space(7); y: 1
                      width: Style.space(6); height: Style.space(9)
                      radius: 1.5; color: "transparent"
                      border.color: parent.iconColor; border.width: 1.4
                    }

                    // Mirror: two overlapping screens
                    Rectangle {
                      visible: root.isMirroredMode
                      x: 0; y: 0
                      width: Style.space(9); height: Style.space(8)
                      radius: 1.5; color: "transparent"
                      border.color: parent.iconColor; border.width: 1.4
                    }
                    Rectangle {
                      visible: root.isMirroredMode
                      x: Style.space(4); y: Style.space(3)
                      width: Style.space(9); height: Style.space(8)
                      radius: 1.5; color: Commons.Color.popups.background
                      border.color: parent.iconColor; border.width: 1.4
                    }
                  }

                  Text {
                    textFormat: Text.PlainText
                    text: root.isMirroredMode ? "Mirror" : "Extend"
                    color: root.isMirroredMode || modeToggleMouse.containsMouse ? root.accent : root.fg
                    font.family: root.fontName
                    font.pixelSize: Style.font.caption
                    font.bold: root.isMirroredMode
                    anchors.verticalCenter: parent.verticalCenter
                  }
                }

                MouseArea {
                  id: modeToggleMouse
                  anchors.fill: parent
                  hoverEnabled: true
                  cursorShape: Qt.PointingHandCursor
                  onClicked: root.applyPreset(root.isMirroredMode ? "extend" : "mirror")
                }
              }

              MiniActionButton {
                text: "Re-probe"
                onClicked: {
                  if (root.service) root.service.refresh()
                  root.readBrightness()
                }
              }

              MiniActionButton {
                text: "Identify"
                accented: root.service && root.service.identifying
                onClicked: if (root.service) root.service.identify()
              }
            }
          }

          // ---------- Interactive Layout Canvas ----------
          LayoutCanvas {
            width: parent.width
            height: Style.space(184)
          }

          // ---------- Display Selector Strip ----------
          Flow {
            width: parent.width
            spacing: Style.space(6)

            Repeater {
              model: root.draft

              Rectangle {
                id: tabPill
                required property var modelData
                required property int index
                readonly property bool isSelected: root.selected === index
                readonly property bool isActive: modelData.enabled && !modelData.autoOff
                readonly property bool isMirrored: isActive && !!modelData.mirrorOf

                implicitWidth: pillRow.implicitWidth + Style.space(16)
                implicitHeight: Style.space(26)
                radius: Math.min(Style.cornerRadius, Style.space(6))
                color: isSelected
                  ? root.accentSoft
                  : (pillMouse.containsMouse ? root.trackBg : root.cardBg)
                border.color: isSelected
                  ? root.accentBorder
                  : (pillMouse.containsMouse ? Qt.rgba(root.fg.r, root.fg.g, root.fg.b, 0.28) : root.cardBorder)
                border.width: 1
                opacity: isActive || isSelected ? 1 : 0.65

                Behavior on color { ColorAnimation { duration: Style.duration(120) } }

                Row {
                  id: pillRow
                  anchors.centerIn: parent
                  spacing: Style.space(6)

                  Rectangle {
                    width: Style.space(6)
                    height: Style.space(6)
                    radius: width / 2
                    anchors.verticalCenter: parent.verticalCenter
                    color: tabPill.modelData.autoOff
                      ? root.alertColor
                      : (tabPill.isActive ? root.accent : root.muted)
                  }

                  Text {
                    textFormat: Text.PlainText
                    text: (tabPill.index + 1) + " · " + tabPill.modelData.name
                    color: tabPill.isSelected ? root.accent : root.fg
                    font.family: root.fontName
                    font.pixelSize: Style.font.caption
                    font.bold: tabPill.isSelected
                    anchors.verticalCenter: parent.verticalCenter
                  }

                  Text {
                    visible: !tabPill.isActive || tabPill.isMirrored
                    textFormat: Text.PlainText
                    text: tabPill.modelData.autoOff ? "AUTO OFF" : (!tabPill.isActive ? "OFF" : "MIRROR")
                    color: tabPill.modelData.autoOff
                      ? root.alertColor
                      : (tabPill.isMirrored ? root.accent : root.muted)
                    font.family: root.fontName
                    font.pixelSize: Math.max(8, Style.font.caption - 1)
                    font.bold: true
                    anchors.verticalCenter: parent.verticalCenter
                  }
                }

                MouseArea {
                  id: pillMouse
                  anchors.fill: parent
                  hoverEnabled: true
                  cursorShape: Qt.PointingHandCursor
                  onClicked: root.selected = tabPill.index
                }
              }
            }
          }

          // ---------- Selected Monitor Scope Banner ----------
          Rectangle {
            id: monitorBanner
            width: parent.width
            visible: root.current !== null
            implicitHeight: bannerRow.implicitHeight + Style.space(16)
            radius: Style.cornerRadius
            color: root.cardBg
            border.color: root.accentBorder
            border.width: 1

            Rectangle {
              width: Style.space(3)
              anchors.left: parent.left
              anchors.top: parent.top
              anchors.bottom: parent.bottom
              anchors.margins: Style.space(6)
              radius: width / 2
              color: root.current && root.current.autoOff ? root.alertColor : root.accent
            }

            Item {
              id: bannerRow
              anchors.left: parent.left
              anchors.right: parent.right
              anchors.verticalCenter: parent.verticalCenter
              anchors.leftMargin: Style.space(16)
              anchors.rightMargin: Style.space(12)
              implicitHeight: Math.max(bannerInfo.implicitHeight, bannerControls.implicitHeight)

              Column {
                id: bannerInfo
                anchors.left: parent.left
                anchors.right: bannerControls.left
                anchors.rightMargin: Style.space(10)
                anchors.verticalCenter: parent.verticalCenter
                spacing: Style.space(2)

                Text {
                  width: parent.width
                  textFormat: Text.PlainText
                  text: root.current ? root.shortModelName(root.selected) : ""
                  color: root.fg
                  font.family: root.fontName
                  font.pixelSize: Style.font.body
                  font.bold: true
                  elide: Text.ElideRight
                }

                Text {
                  width: parent.width
                  textFormat: Text.PlainText
                  text: {
                    if (!root.current) return ""
                    var off = root.offText(root.current)
                    if (off) return root.current.name + " · " + off
                    if (root.current.mirrorOf) return root.current.name + " · Mirroring " + root.current.mirrorOf
                    var mode = root.current.mode
                      ? (root.current.mode.w + "×" + root.current.mode.h + " @ " + Model.formatHz(root.current.mode.hz) + " Hz")
                      : ""
                    var sc = Commons.I18n.formatNumber(root.current.scale) + "×"
                    var rot = root.current.transform ? (" · " + root.transformDegrees(root.current.transform)) : ""
                    return root.current.name + (mode ? " · " + mode : "") + " · " + sc + rot
                  }
                  color: root.current && root.current.autoOff ? root.alertColor : root.muted
                  font.family: root.fontName
                  font.pixelSize: Style.font.caption
                  elide: Text.ElideRight
                }
              }

              Row {
                id: bannerControls
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
                spacing: Style.space(6)

                MiniActionButton {
                  visible: root.currentHasSavedProfile
                  text: "Forget"
                  anchors.verticalCenter: parent.verticalCenter
                  onClicked: root.forgetSelectedMonitor()
                }

                Rectangle {
                  id: statusPill
                  readonly property bool isOn: !!root.current && root.current.enabled && !root.current.autoOff
                  readonly property bool isWarn: !!root.current && root.current.autoOff
                  implicitWidth: statusPillLabel.implicitWidth + Style.space(12)
                  implicitHeight: statusPillLabel.implicitHeight + Style.space(4)
                  radius: height / 2
                  color: isWarn ? root.alertSoft : (isOn ? root.accentSoft : root.trackBg)
                  border.color: isWarn ? root.alertBorder : (isOn ? root.accentBorder : root.cardBorder)
                  border.width: 1
                  anchors.verticalCenter: parent.verticalCenter

                  Text {
                    id: statusPillLabel
                    anchors.centerIn: parent
                    textFormat: Text.PlainText
                    text: root.statusBadgeText(root.current)
                    color: statusPill.isWarn ? root.alertColor : (statusPill.isOn ? root.accent : root.muted)
                    font.family: root.fontName
                    font.pixelSize: Math.max(9, Style.font.caption - 1)
                    font.bold: true
                  }
                }

                AccentSwitch {
                  id: powerSwitch
                  anchors.verticalCenter: parent.verticalCenter
                  checked: !!root.current && root.current.enabled && !root.current.autoOff
                  interactive: !!root.current && !root.current.autoOff && root.canTurnOff(root.selected)
                  onToggled: {
                    var turningOn = !root.current.enabled
                    var patch = { enabled: turningOn }
                    if (turningOn) {
                      var saved = root.service && root.service.memory && root.service.memory.monitors
                        ? root.service.memory.monitors[root.current.fp] : null
                      var mode = Model.ensureEnabledMode(root.current, root.currentMonitor ? root.currentMonitor.availableModes : [], saved)
                      if (mode) patch.mode = mode
                    }
                    root.update(root.selected, patch, true)
                  }
                }
              }
            }
          }

          // ---------- Disabled / Auto-Off Notice ----------
          CardBox {
            width: parent.width
            visible: !!root.current && (!root.current.enabled || root.current.autoOff)

            Text {
              width: parent.width
              textFormat: Text.PlainText
              wrapMode: Text.Wrap
              text: root.current && root.current.autoOff
                ? "This display is paused (" + root.offText(root.current).toLowerCase() + ") and will restore automatically when available."
                : "This display is turned off. Flip the switch above and press Apply to re-enable it."
              color: root.muted
              font.family: root.fontName
              font.pixelSize: Style.font.caption
            }
          }

          // ---------- Mirrored Notice ----------
          CardBox {
            width: parent.width
            visible: !!root.current && root.current.enabled && !root.current.autoOff && !!root.current.mirrorOf

            Item {
              width: parent.width
              implicitHeight: Math.max(mirrorNoticeText.implicitHeight, unmirrorBtn.implicitHeight)

              Text {
                id: mirrorNoticeText
                anchors.left: parent.left
                anchors.right: unmirrorBtn.left
                anchors.rightMargin: Style.space(8)
                anchors.verticalCenter: parent.verticalCenter
                textFormat: Text.PlainText
                wrapMode: Text.Wrap
                text: root.current ? ("Mirroring " + root.current.mirrorOf + " · drag layout is paused while mirrored.") : ""
                color: root.muted
                font.family: root.fontName
                font.pixelSize: Style.font.caption
              }

              MiniActionButton {
                id: unmirrorBtn
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
                text: "Unmirror"
                accented: true
                onClicked: root.update(root.selected, { mirrorOf: "" }, true)
              }
            }
          }

          // ---------- Two-Column Per-Monitor Cards (Strictly Scoped to Selected Monitor) ----------
          Item {
            id: cardsContainer
            width: parent.width
            visible: !!root.current && root.current.enabled && !root.current.autoOff
            readonly property int gap: Style.space(10)
            readonly property real cardWidth: column.twoColumn ? Math.floor((width - gap) / 2) : width
            readonly property real matchedHeight: Math.max(displayCard.implicitHeight, pictureCard.implicitHeight)
            implicitHeight: visible
              ? (column.twoColumn ? matchedHeight : (displayCard.implicitHeight + gap + pictureCard.implicitHeight))
              : 0
            height: implicitHeight

            // ========== LEFT CARD: MODE & SCALE (PER MONITOR) ==========
            CardBox {
              id: displayCard
              x: 0
              y: 0
              width: cardsContainer.cardWidth
              height: column.twoColumn ? cardsContainer.matchedHeight : implicitHeight

              Column {
                id: displayCardCol
                width: parent.width
                spacing: Style.space(10)

                Text {
                  textFormat: Text.PlainText
                  text: "MODE & SCALE"
                  color: root.muted
                  font.family: root.fontName
                  font.pixelSize: Math.max(9, Style.font.caption - 1)
                  font.bold: true
                  font.letterSpacing: 1.0
                }

                // Side-by-side Resolution & Refresh Rate Dropdowns
                Row {
                  id: modeDropdownRow
                  width: parent.width
                  spacing: Style.space(8)
                  readonly property real halfWidth: Math.floor((width - spacing) / 2)

                  Dropdown {
                    id: resDropdown
                    width: modeDropdownRow.halfWidth
                    label: "Resolution"
                    options: root.resolutionOptions
                    value: root.currentResolutionKey
                    foreground: root.fg
                    background: Commons.Color.popups.background
                    accent: root.accent
                    fontFamily: root.fontName
                    onPopupOpenChanged: if (popupOpen && hzDropdown.popupOpen) hzDropdown.close()
                    onChanged: function(val) {
                      root.setResolutionByKey(root.selected, val)
                      value = Qt.binding(function() { return root.currentResolutionKey })
                    }
                  }

                  Dropdown {
                    id: hzDropdown
                    width: modeDropdownRow.halfWidth
                    label: "Refresh rate"
                    options: root.rateOptions
                    value: root.currentRateKey
                    foreground: root.fg
                    background: Commons.Color.popups.background
                    accent: root.accent
                    fontFamily: root.fontName
                    onPopupOpenChanged: if (popupOpen && resDropdown.popupOpen) resDropdown.close()
                    onChanged: function(val) {
                      root.setRateByKey(root.selected, val)
                      value = Qt.binding(function() { return root.currentRateKey })
                    }
                  }
                }

                // Per-monitor Scale
                Column {
                  width: parent.width
                  spacing: Style.space(5)

                  Item {
                    width: parent.width
                    implicitHeight: scaleTitle.implicitHeight

                    Text {
                      id: scaleTitle
                      anchors.left: parent.left
                      textFormat: Text.PlainText
                      text: "Scale"
                      color: root.fg
                      font.family: root.fontName
                      font.pixelSize: Style.font.caption
                      font.bold: true
                    }

                    Text {
                      anchors.right: parent.right
                      textFormat: Text.PlainText
                      text: root.current ? Commons.I18n.formatNumber(root.current.scale) + "×" : ""
                      color: root.accent
                      font.family: root.fontName
                      font.pixelSize: Style.font.caption
                      font.bold: true
                    }
                  }

                  SegmentedBar {
                    width: parent.width
                    options: {
                      var list = root.current && root.current.mode
                        ? Model.scaleOptions(root.current.mode.w, root.current.mode.h) : []
                      var out = []
                      for (var i = 0; i < list.length; i++)
                        out.push({ value: list[i], label: Commons.I18n.formatNumber(list[i]) + "×" })
                      return out
                    }
                    currentValue: root.current ? root.current.scale : 1
                    compareNumeric: true
                    onPicked: function(val) { root.update(root.selected, { scale: val }, true) }
                  }
                }

                // Per-monitor Orientation / Rotation
                Column {
                  width: parent.width
                  spacing: Style.space(5)

                  Item {
                    width: parent.width
                    implicitHeight: rotTitle.implicitHeight

                    Text {
                      id: rotTitle
                      anchors.left: parent.left
                      textFormat: Text.PlainText
                      text: "Orientation"
                      color: root.fg
                      font.family: root.fontName
                      font.pixelSize: Style.font.caption
                      font.bold: true
                    }

                    Text {
                      anchors.right: parent.right
                      textFormat: Text.PlainText
                      text: root.current ? root.transformDegrees(root.current.transform) : "0°"
                      color: root.accent
                      font.family: root.fontName
                      font.pixelSize: Style.font.caption
                      font.bold: true
                    }
                  }

                  SegmentedBar {
                    width: parent.width
                    options: [
                      { value: 0, label: "0°" },
                      { value: 1, label: "90°" },
                      { value: 2, label: "180°" },
                      { value: 3, label: "270°" }
                    ]
                    currentValue: root.current ? (Number(root.current.transform) % 4) : 0
                    onPicked: function(val) { root.update(root.selected, { transform: val }, true) }
                  }
                }

                // Per-monitor Adaptive Sync (VRR)
                Column {
                  width: parent.width
                  spacing: Style.space(5)

                  Text {
                    textFormat: Text.PlainText
                    text: "Adaptive sync (VRR)"
                    color: root.fg
                    font.family: root.fontName
                    font.pixelSize: Style.font.caption
                    font.bold: true
                  }

                  SegmentedBar {
                    width: parent.width
                    options: [
                      { value: 0, label: "Off" },
                      { value: 1, label: "Always" },
                      { value: 2, label: "Fullscreen" }
                    ]
                    currentValue: root.current ? root.current.vrr : 0
                    onPicked: function(val) { root.update(root.selected, { vrr: val }, false) }
                  }
                }
              }
            }

            // ========== RIGHT CARD: BRIGHTNESS, HDR & AUTOMATION (PER MONITOR) ==========
            CardBox {
              id: pictureCard
              x: column.twoColumn ? (cardsContainer.cardWidth + cardsContainer.gap) : 0
              y: column.twoColumn ? 0 : (displayCard.implicitHeight + cardsContainer.gap)
              width: cardsContainer.cardWidth
              height: column.twoColumn ? cardsContainer.matchedHeight : implicitHeight

              Column {
                id: pictureCardCol
                width: parent.width
                spacing: Style.space(10)

                Text {
                  textFormat: Text.PlainText
                  text: "PICTURE & AUTOMATION"
                  color: root.muted
                  font.family: root.fontName
                  font.pixelSize: Math.max(9, Style.font.caption - 1)
                  font.bold: true
                  font.letterSpacing: 1.0
                }

                // Per-monitor Brightness + Sync All
                Column {
                  width: parent.width
                  spacing: Style.space(5)

                  Item {
                    width: parent.width
                    implicitHeight: Math.max(brightHead.implicitHeight, brightRight.implicitHeight)

                    Row {
                      id: brightHead
                      anchors.left: parent.left
                      anchors.verticalCenter: parent.verticalCenter
                      spacing: Style.space(6)

                      Text {
                        textFormat: Text.PlainText
                        text: "Brightness"
                        color: root.fg
                        font.family: root.fontName
                        font.pixelSize: Style.font.caption
                        font.bold: true
                        anchors.verticalCenter: parent.verticalCenter
                      }

                      Text {
                        textFormat: Text.PlainText
                        text: root.current ? (root.current.internal ? "Backlight" : "DDC/CI") : ""
                        color: root.muted
                        font.family: root.fontName
                        font.pixelSize: Math.max(9, Style.font.caption - 1)
                        anchors.verticalCenter: parent.verticalCenter
                      }
                    }

                    Row {
                      id: brightRight
                      anchors.right: parent.right
                      anchors.verticalCenter: parent.verticalCenter
                      spacing: Style.space(6)

                      TinyChipButton {
                        visible: root.brightnessAvailable && root.enabledCount > 1
                        text: "Sync all"
                        anchors.verticalCenter: parent.verticalCenter
                        onClicked: root.syncBrightnessToAll()
                      }

                      Text {
                        id: brightPct
                        anchors.verticalCenter: parent.verticalCenter
                        textFormat: Text.PlainText
                        text: root.brightnessAvailable
                          ? Math.round(brightnessSlider.dragging ? brightnessSlider.liveValue : root.brightness) + "%"
                          : "N/A"
                        color: root.brightnessAvailable ? root.accent : root.muted
                        font.family: root.fontName
                        font.pixelSize: Style.font.caption
                        font.bold: true
                      }
                    }
                  }

                  PanelSlider {
                    id: brightnessSlider
                    visible: root.brightnessAvailable
                    bar: root.bar
                    width: parent.width
                    minimum: 1
                    maximum: 100
                    step: 1
                    integer: true
                    value: root.brightness
                    trackColor: Qt.rgba(root.fg.r, root.fg.g, root.fg.b, 0.14)
                    fillColor: root.accent
                    knobColor: root.accent
                    onMoved: function(v) {
                      root.brightness = Math.round(v)
                      brightnessDebounce.restart()
                    }
                    onReleased: function(v) {
                      brightnessDebounce.stop()
                      root.setBrightness(v, true)
                    }
                  }

                  Text {
                    visible: !root.brightnessAvailable
                    width: parent.width
                    textFormat: Text.PlainText
                    text: "No backlight or DDC/CI reported for this display"
                    color: root.muted
                    font.family: root.fontName
                    font.pixelSize: Math.max(9, Style.font.caption - 1)
                    elide: Text.ElideRight
                  }
                }

                Rectangle { width: parent.width; height: 1; color: root.hairline }

                // Per-monitor HDR + Conditional SDR Brightness & Saturation tuning
                Column {
                  width: parent.width
                  spacing: Style.space(6)

                  Item {
                    width: parent.width
                    implicitHeight: Math.max(hdrCol.implicitHeight, hdrSwitch.implicitHeight)

                    Column {
                      id: hdrCol
                      anchors.left: parent.left
                      anchors.right: hdrSwitch.left
                      anchors.rightMargin: Style.space(8)
                      anchors.verticalCenter: parent.verticalCenter
                      spacing: Style.space(1)

                      Text {
                        textFormat: Text.PlainText
                        text: "HDR"
                        color: root.fg
                        font.family: root.fontName
                        font.pixelSize: Style.font.caption
                        font.bold: true
                      }

                      Text {
                        width: parent.width
                        textFormat: Text.PlainText
                        text: root.currentMonitor && root.currentMonitor.hdrCapable
                          ? (root.current && root.current.hdr ? "10-bit PQ enabled" : "HDR capable · 10-bit PQ")
                          : "Not reported by display"
                        color: root.current && root.current.hdr ? root.accent : root.muted
                        font.family: root.fontName
                        font.pixelSize: Math.max(9, Style.font.caption - 1)
                        elide: Text.ElideRight
                      }
                    }

                    AccentSwitch {
                      id: hdrSwitch
                      anchors.right: parent.right
                      anchors.verticalCenter: parent.verticalCenter
                      checked: !!root.current && root.current.hdr
                      interactive: !!root.currentMonitor && root.currentMonitor.hdrCapable === true
                      onToggled: root.update(root.selected, { hdr: !root.current.hdr }, false)
                    }
                  }

                  // Conditional SDR-in-HDR controls (zero clutter when HDR is off)
                  Column {
                    width: parent.width
                    visible: !!root.current && root.current.hdr
                    spacing: Style.space(5)

                    Item {
                      width: parent.width
                      implicitHeight: sdrBTitle.implicitHeight

                      Text {
                        id: sdrBTitle
                        anchors.left: parent.left
                        textFormat: Text.PlainText
                        text: "SDR brightness"
                        color: root.muted
                        font.family: root.fontName
                        font.pixelSize: Math.max(9, Style.font.caption - 1)
                        font.bold: true
                      }

                      Text {
                        anchors.right: parent.right
                        textFormat: Text.PlainText
                        text: (sdrBSlider.dragging ? (Math.round(sdrBSlider.liveValue * 20) / 20) : (root.current ? root.current.sdrBrightness : 1)).toFixed(2) + "×"
                        color: root.accent
                        font.family: root.fontName
                        font.pixelSize: Math.max(9, Style.font.caption - 1)
                        font.bold: true
                      }
                    }

                    PanelSlider {
                      id: sdrBSlider
                      bar: root.bar
                      width: parent.width
                      minimum: 0.5
                      maximum: 2.0
                      step: 0.05
                      integer: false
                      value: root.current ? root.current.sdrBrightness : 1.0
                      trackColor: Qt.rgba(root.fg.r, root.fg.g, root.fg.b, 0.14)
                      fillColor: root.accent
                      knobColor: root.accent
                      onReleased: function(v) {
                        root.update(root.selected, { sdrBrightness: Model.cleanSdrBrightness(Math.round(v * 20) / 20) }, false)
                      }
                    }

                    Item {
                      width: parent.width
                      implicitHeight: sdrSTitle.implicitHeight

                      Text {
                        id: sdrSTitle
                        anchors.left: parent.left
                        textFormat: Text.PlainText
                        text: "SDR saturation"
                        color: root.muted
                        font.family: root.fontName
                        font.pixelSize: Math.max(9, Style.font.caption - 1)
                        font.bold: true
                      }

                      Text {
                        anchors.right: parent.right
                        textFormat: Text.PlainText
                        text: (sdrSSlider.dragging ? (Math.round(sdrSSlider.liveValue * 20) / 20) : (root.current ? root.current.sdrSaturation : 1)).toFixed(2) + "×"
                        color: root.accent
                        font.family: root.fontName
                        font.pixelSize: Math.max(9, Style.font.caption - 1)
                        font.bold: true
                      }
                    }

                    PanelSlider {
                      id: sdrSSlider
                      bar: root.bar
                      width: parent.width
                      minimum: 0.5
                      maximum: 1.5
                      step: 0.05
                      integer: false
                      value: root.current ? root.current.sdrSaturation : 1.0
                      trackColor: Qt.rgba(root.fg.r, root.fg.g, root.fg.b, 0.14)
                      fillColor: root.accent
                      knobColor: root.accent
                      onReleased: function(v) {
                        root.update(root.selected, { sdrSaturation: Model.cleanSdrSaturation(Math.round(v * 20) / 20) }, false)
                      }
                    }
                  }
                }

                Rectangle { width: parent.width; height: 1; color: root.hairline }

                // Per-monitor Auto-Disable
                Column {
                  width: parent.width
                  spacing: Style.space(6)
                  visible: root.current !== null

                  Text {
                    textFormat: Text.PlainText
                    text: "AUTO-DISABLE"
                    color: root.muted
                    font.family: root.fontName
                    font.pixelSize: Math.max(9, Style.font.caption - 1)
                    font.bold: true
                    font.letterSpacing: 1.0
                  }

                  // External display: DDC/CI input watch + Re-learn fallback
                  Column {
                    width: parent.width
                    visible: !!root.current && !root.current.internal
                    spacing: Style.space(6)

                    Item {
                      width: parent.width
                      implicitHeight: Math.max(ddcCol.implicitHeight, ddcSwitch.implicitHeight)

                      Column {
                        id: ddcCol
                        anchors.left: parent.left
                        anchors.right: ddcSwitch.left
                        anchors.rightMargin: Style.space(8)
                        anchors.verticalCenter: parent.verticalCenter
                        spacing: Style.space(2)

                        Text {
                          width: parent.width
                          textFormat: Text.PlainText
                          text: "Switch away / power off"
                          color: root.fg
                          font.family: root.fontName
                          font.pixelSize: Style.font.caption
                          font.bold: true
                          elide: Text.ElideRight
                        }

                        Text {
                          width: parent.width
                          textFormat: Text.PlainText
                          wrapMode: Text.Wrap
                          text: {
                            if (!root.current || !root.current.ddcWatch)
                              return "Auto-disable when monitor switches HDMI/DP input"
                            return root.currentLearnedDdcInput
                              ? ("Watching " + root.ddcInputLabel(root.currentLearnedDdcInput))
                              : "Will learn active DDC/CI input on Apply"
                          }
                          color: root.current && root.current.ddcWatch ? root.accent : root.muted
                          font.family: root.fontName
                          font.pixelSize: Math.max(9, Style.font.caption - 1)
                        }
                      }

                      AccentSwitch {
                        id: ddcSwitch
                        anchors.right: parent.right
                        anchors.verticalCenter: parent.verticalCenter
                        checked: !!root.current && root.current.ddcWatch
                        onToggled: root.update(root.selected, { ddcWatch: !root.current.ddcWatch }, false)
                      }
                    }

                    Column {
                      width: parent.width
                      visible: root.currentSavedDdcWatch
                      spacing: Style.space(4)

                      TinyChipButton {
                        text: "Re-learn input"
                        onClicked: root.relearnSelectedDdc()
                      }

                      Text {
                        width: parent.width
                        textFormat: Text.PlainText
                        wrapMode: Text.Wrap
                        text: "Auto-updates if you switch PC ports. Click only if you moved this cable to another port on the monitor."
                        color: root.muted
                        font.family: root.fontName
                        font.pixelSize: Math.max(9, Style.font.caption - 1)
                      }
                    }
                  }

                  // Internal display: Omarchy clamshell status
                  Rectangle {
                    width: parent.width
                    visible: !!root.current && root.current.internal
                    implicitHeight: lidCol.implicitHeight + Style.space(12)
                    radius: Math.min(Style.cornerRadius, Style.space(6))
                    color: root.accentSoft
                    border.color: root.accentBorder
                    border.width: 1

                    Column {
                      id: lidCol
                      anchors.left: parent.left
                      anchors.right: parent.right
                      anchors.verticalCenter: parent.verticalCenter
                      anchors.margins: Style.space(8)
                      spacing: Style.space(2)

                      Text {
                        width: parent.width
                        textFormat: Text.PlainText
                        text: "Clamshell lid guard active"
                        color: root.accent
                        font.family: root.fontName
                        font.pixelSize: Style.font.caption
                        font.bold: true
                      }

                      Text {
                        width: parent.width
                        textFormat: Text.PlainText
                        wrapMode: Text.Wrap
                        text: "Auto-disables on lid close while an external display is connected."
                        color: root.muted
                        font.family: root.fontName
                        font.pixelSize: Math.max(9, Style.font.caption - 1)
                      }
                    }
                  }
                }
              }
            }
          }

          // ---------- All Displays Card (OS-wide settings in Omarchy/Hyprland) ----------
          CardBox {
            width: parent.width

            Column {
              width: parent.width
              spacing: Style.space(8)

              Item {
                width: parent.width
                implicitHeight: allDispHead.implicitHeight

                Text {
                  id: allDispHead
                  anchors.left: parent.left
                  textFormat: Text.PlainText
                  text: "ALL DISPLAYS"
                  color: root.muted
                  font.family: root.fontName
                  font.pixelSize: Math.max(9, Style.font.caption - 1)
                  font.bold: true
                  font.letterSpacing: 1.0
                }

                Text {
                  anchors.right: parent.right
                  textFormat: Text.PlainText
                  text: "OS-wide · remembered per setup on Apply"
                  color: root.muted
                  font.family: root.fontName
                  font.pixelSize: Math.max(9, Style.font.caption - 1)
                }
              }

              Item {
                id: globalRow
                width: parent.width
                readonly property int sideGap: Style.space(10)
                readonly property int gap: sideGap * 2 + 1
                readonly property bool split: column.twoColumn
                readonly property real halfW: split ? Math.floor((width - gap) / 2) : width
                implicitHeight: split
                  ? Math.max(textSizeCol.implicitHeight, nightLightCol.implicitHeight)
                  : (textSizeCol.implicitHeight + gap + nightLightCol.implicitHeight)
                height: implicitHeight

                // Text size (remembered per monitor combination on Apply)
                Column {
                  id: textSizeCol
                  x: 0
                  y: 0
                  width: globalRow.halfW
                  spacing: Style.space(5)

                  Item {
                    width: parent.width
                    implicitHeight: textSzTitle.implicitHeight

                    Text {
                      id: textSzTitle
                      anchors.left: parent.left
                      textFormat: Text.PlainText
                      text: "Text size"
                      color: root.fg
                      font.family: root.fontName
                      font.pixelSize: Style.font.caption
                      font.bold: true
                    }

                    Text {
                      anchors.right: parent.right
                      textFormat: Text.PlainText
                      text: root.shownTextSize + "px · " + Math.round(root.shownTextSize * 9 / 12) + "pt term"
                      color: root.accent
                      font.family: root.fontName
                      font.pixelSize: Style.font.caption
                      font.bold: true
                    }
                  }

                  SegmentedBar {
                    width: parent.width
                    options: {
                      var out = []
                      for (var i = 0; i < root.textSizeStops.length; i++)
                        out.push({ value: root.textSizeStops[i], label: String(root.textSizeStops[i]) })
                      return out
                    }
                    currentValue: root.shownTextSize
                    onPicked: function(val) { root.pickTextSize(val) }
                  }

                  Text {
                    width: parent.width
                    textFormat: Text.PlainText
                    text: "Use Scale above for per-monitor size"
                    color: root.muted
                    font.family: root.fontName
                    font.pixelSize: Math.max(9, Style.font.caption - 1)
                    elide: Text.ElideRight
                  }
                }

                // Separating bar between All Displays options
                Rectangle {
                  x: globalRow.split ? (globalRow.halfW + globalRow.sideGap) : 0
                  y: globalRow.split ? Style.space(2) : (textSizeCol.implicitHeight + globalRow.sideGap)
                  width: globalRow.split ? 1 : parent.width
                  height: globalRow.split ? Math.max(0, globalRow.height - Style.space(4)) : 1
                  color: root.cardBorder
                }

                // Night Light (real hyprsunset Kelvin control + quick warmth presets)
                Column {
                  id: nightLightCol
                  x: globalRow.split ? (globalRow.halfW + globalRow.gap) : 0
                  y: globalRow.split ? 0 : (textSizeCol.implicitHeight + globalRow.gap)
                  width: globalRow.halfW
                  spacing: Style.space(5)

                  Item {
                    width: parent.width
                    implicitHeight: Math.max(nlLeft.implicitHeight, nlRight.implicitHeight)

                    Row {
                      id: nlLeft
                      anchors.left: parent.left
                      anchors.verticalCenter: parent.verticalCenter
                      spacing: Style.space(6)

                      Text {
                        textFormat: Text.PlainText
                        text: "Night Light"
                        color: root.fg
                        font.family: root.fontName
                        font.pixelSize: Style.font.caption
                        font.bold: true
                        anchors.verticalCenter: parent.verticalCenter
                      }

                      Text {
                        textFormat: Text.PlainText
                        text: root.shownNightLightOn
                          ? ((kelvinSlider.dragging ? Math.round(kelvinSlider.liveValue / 100) * 100 : root.shownKelvin) + "K")
                          : "Off"
                        color: root.shownNightLightOn ? root.alertColor : root.muted
                        font.family: root.fontName
                        font.pixelSize: Style.font.caption
                        font.bold: root.shownNightLightOn
                        anchors.verticalCenter: parent.verticalCenter
                      }
                    }

                    AccentSwitch {
                      id: nlRight
                      anchors.right: parent.right
                      anchors.verticalCenter: parent.verticalCenter
                      activeColor: root.alertColor
                      checked: root.shownNightLightOn
                      onToggled: root.setNightLightDraft(!root.shownNightLightOn, root.shownKelvin, true)
                    }
                  }

                  PanelSlider {
                    id: kelvinSlider
                    bar: root.bar
                    width: parent.width
                    enabled: root.shownNightLightOn
                    opacity: enabled ? 1 : 0.38
                    minimum: 1800
                    maximum: 6000
                    step: 100
                    integer: true
                    value: root.shownKelvin
                    trackColor: Qt.rgba(root.fg.r, root.fg.g, root.fg.b, 0.14)
                    fillColor: root.alertColor
                    knobColor: root.alertColor
                    onMoved: function(v) {
                      root.setNightLightDraft(true, Math.round(v / 100) * 100, false)
                    }
                    onReleased: function(v) {
                      root.setNightLightDraft(true, Math.round(v / 100) * 100, true)
                    }
                  }

                  Row {
                    spacing: Style.space(5)

                    Repeater {
                      model: root.nightLightPresets

                      TinyChipButton {
                        required property var modelData
                        text: modelData.label
                        accented: root.shownNightLightOn && Math.abs(root.shownKelvin - modelData.kelvin) < 60
                        useAlertColor: true
                        onClicked: root.setNightLightDraft(true, modelData.kelvin, true)
                      }
                    }
                  }
                }
              }
            }
          }

          Item { width: parent.width; height: Style.space(2) }
        }
      }
    }
  }

  // ==================== REUSABLE THEMED SUB-COMPONENTS ====================

  component CardBox: Rectangle {
    id: cardBox
    default property alias body: cardInner.data
    property int pad: Style.space(12)
    implicitHeight: cardInner.childrenRect.height + pad * 2
    radius: Style.cornerRadius
    color: root.cardBg
    border.color: root.cardBorder
    border.width: 1

    Item {
      id: cardInner
      anchors.left: parent.left
      anchors.right: parent.right
      anchors.top: parent.top
      anchors.margins: cardBox.pad
      height: childrenRect.height
    }
  }

  component MiniActionButton: Rectangle {
    id: miniBtn
    property string text: ""
    property bool accented: false
    signal clicked()

    implicitWidth: miniLbl.implicitWidth + Style.space(16)
    implicitHeight: Style.space(26)
    radius: Math.min(Style.cornerRadius, Style.space(6))
    color: accented
      ? root.accentSoft
      : (miniMouse.containsMouse ? root.trackBg : root.cardBg)
    border.color: accented || miniMouse.containsMouse ? root.accentBorder : root.cardBorder
    border.width: 1

    Behavior on color { ColorAnimation { duration: Style.duration(120) } }

    Text {
      id: miniLbl
      anchors.centerIn: parent
      textFormat: Text.PlainText
      text: miniBtn.text
      color: miniBtn.accented || miniMouse.containsMouse ? root.accent : root.fg
      font.family: root.fontName
      font.pixelSize: Style.font.caption
      font.bold: miniBtn.accented
    }

    MouseArea {
      id: miniMouse
      anchors.fill: parent
      hoverEnabled: true
      cursorShape: Qt.PointingHandCursor
      onClicked: miniBtn.clicked()
    }
  }

  component TinyChipButton: Rectangle {
    id: chipBtn
    property string text: ""
    property bool accented: false
    property bool useAlertColor: false
    signal clicked()

    readonly property color hlColor: useAlertColor ? root.alertColor : root.accent
    readonly property color hlSoft: useAlertColor ? root.alertSoft : root.accentSoft
    readonly property color hlBorder: useAlertColor ? root.alertBorder : root.accentBorder

    implicitWidth: chipLbl.implicitWidth + Style.space(10)
    implicitHeight: Math.max(Style.space(20), chipLbl.implicitHeight + Style.space(4))
    radius: Math.min(Style.cornerRadius, Style.space(5))
    color: accented ? hlSoft : (chipMouse.containsMouse ? root.trackBg : "transparent")
    border.color: accented || chipMouse.containsMouse ? hlBorder : root.cardBorder
    border.width: 1

    Behavior on color { ColorAnimation { duration: Style.duration(110) } }

    Text {
      id: chipLbl
      anchors.centerIn: parent
      textFormat: Text.PlainText
      text: chipBtn.text
      color: chipBtn.accented || chipMouse.containsMouse ? chipBtn.hlColor : root.muted
      font.family: root.fontName
      font.pixelSize: Math.max(9, Style.font.caption - 1)
      font.bold: chipBtn.accented
    }

    MouseArea {
      id: chipMouse
      anchors.fill: parent
      hoverEnabled: true
      cursorShape: Qt.PointingHandCursor
      onClicked: chipBtn.clicked()
    }
  }

  component AccentSwitch: Item {
    id: sw
    property bool checked: false
    property bool interactive: true
    property color activeColor: root.accent
    signal toggled()

    readonly property real activeLuma: 0.2126 * activeColor.r + 0.7152 * activeColor.g + 0.0722 * activeColor.b
    readonly property color knobInk: activeLuma > 0.45 ? Commons.Color.background : Commons.Color.foreground

    implicitWidth: Style.space(36)
    implicitHeight: Style.space(20)
    opacity: interactive ? 1 : 0.42

    Rectangle {
      id: swTrack
      anchors.fill: parent
      radius: Style.cornerRadius > 0 ? height / 2 : 0
      color: sw.checked ? sw.activeColor : root.trackBg
      border.color: sw.checked
        ? sw.activeColor
        : (swMouse.containsMouse && sw.interactive ? root.accentBorder : root.cardBorder)
      border.width: 1

      Behavior on color { ColorAnimation { duration: Style.duration(120) } }

      Rectangle {
        width: swTrack.height - Style.space(6)
        height: width
        radius: Style.cornerRadius > 0 ? height / 2 : 0
        anchors.verticalCenter: parent.verticalCenter
        x: sw.checked ? swTrack.width - width - Style.space(3) : Style.space(3)
        color: sw.checked ? sw.knobInk : root.muted

        Behavior on x { NumberAnimation { duration: Style.duration(120); easing.type: Easing.OutCubic } }
        Behavior on color { ColorAnimation { duration: Style.duration(120) } }
      }
    }

    MouseArea {
      id: swMouse
      anchors.fill: parent
      enabled: sw.interactive
      hoverEnabled: true
      cursorShape: Qt.PointingHandCursor
      onClicked: sw.toggled()
    }
  }

  component SegmentedBar: Rectangle {
    id: seg
    property var options: []
    property var currentValue: null
    property bool compareNumeric: false
    signal picked(var value)

    implicitHeight: Style.space(28)
    radius: Math.min(Style.cornerRadius, Style.space(6))
    color: root.trackBg
    border.color: root.cardBorder
    border.width: 1

    Row {
      anchors.fill: parent
      anchors.margins: Style.space(2)
      spacing: Style.space(2)

      Repeater {
        model: seg.options

        Rectangle {
          id: segItem
          required property var modelData
          required property int index
          readonly property bool isActive: seg.compareNumeric
            ? Math.abs(Number(seg.currentValue) - Number(modelData.value)) < 0.001
            : seg.currentValue === modelData.value

          width: seg.options.length > 0
            ? (parent.width - parent.spacing * (seg.options.length - 1)) / seg.options.length
            : 0
          height: parent.height
          radius: Math.max(0, seg.radius - Style.space(2))
          color: isActive
            ? root.accent
            : (segMouse.containsMouse ? Qt.rgba(root.fg.r, root.fg.g, root.fg.b, 0.08) : "transparent")

          Behavior on color { ColorAnimation { duration: Style.duration(110) } }

          Text {
            anchors.centerIn: parent
            width: parent.width - Style.space(4)
            horizontalAlignment: Text.AlignHCenter
            textFormat: Text.PlainText
            text: String(segItem.modelData.label)
            color: segItem.isActive ? root.accentInk : (segMouse.containsMouse ? root.fg : root.muted)
            font.family: root.fontName
            font.pixelSize: Style.font.caption
            font.bold: segItem.isActive
            elide: Text.ElideRight
          }

          MouseArea {
            id: segMouse
            anchors.fill: parent
            hoverEnabled: true
            cursorShape: Qt.PointingHandCursor
            onClicked: seg.picked(segItem.modelData.value)
          }
        }
      }
    }
  }

  component LayoutCanvas: Item {
    id: canvas
    clip: true

    readonly property real pad: Style.space(14)
    readonly property real footerPad: Style.space(22)
    property int dragIndex: -1
    property var dragBounds: null
    property var dragRect: null
    property var activeSnap: null
    property bool shiftFreeMove: false

    readonly property var view: dragBounds ? viewForBounds(dragBounds) : viewFor(root.draft)

    function boundsFor(outputs) {
      var minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity
      for (var i = 0; i < outputs.length; i++) {
        if (!outputs[i].enabled || outputs[i].autoOff || outputs[i].mirrorOf) continue
        var r = Model.rectOf(outputs[i])
        minX = Math.min(minX, r.x)
        minY = Math.min(minY, r.y)
        maxX = Math.max(maxX, r.x + r.w)
        maxY = Math.max(maxY, r.y + r.h)
      }
      if (minX === Infinity) return null
      return { minX: minX, minY: minY, maxX: maxX, maxY: maxY }
    }

    function viewForBounds(b) {
      var usableH = Math.max(Style.space(60), height - footerPad)
      if (!b) return { k: 1, ox: pad, oy: pad, minX: 0, minY: 0 }
      var spanX = Math.max(1, b.maxX - b.minX)
      var spanY = Math.max(1, b.maxY - b.minY)
      var k = Math.min((width - pad * 2) / (spanX * 1.24), (usableH - pad * 2) / (spanY * 1.20))
      return {
        k: k,
        ox: (width - spanX * k) / 2,
        oy: (usableH - spanY * k) / 2,
        minX: b.minX,
        minY: b.minY
      }
    }

    function viewFor(outputs) {
      return viewForBounds(boundsFor(outputs))
    }

    function expandDragBounds(r) {
      if (!r || !dragBounds) return
      var nx0 = Math.min(dragBounds.minX, r.x)
      var ny0 = Math.min(dragBounds.minY, r.y)
      var nx1 = Math.max(dragBounds.maxX, r.x + r.w)
      var ny1 = Math.max(dragBounds.maxY, r.y + r.h)
      if (nx0 !== dragBounds.minX || ny0 !== dragBounds.minY || nx1 !== dragBounds.maxX || ny1 !== dragBounds.maxY) {
        dragBounds = { minX: nx0, minY: ny0, maxX: nx1, maxY: ny1 }
      }
    }

    function others(index) {
      var list = []
      for (var i = 0; i < root.draft.length; i++)
        if (i !== index && root.draft[i].enabled && !root.draft[i].autoOff && !root.draft[i].mirrorOf)
          list.push(Model.rectOf(root.draft[i]))
      return list
    }

    Rectangle {
      id: canvasFrame
      anchors.fill: parent
      radius: Style.cornerRadius
      color: root.canvasBg
      border.color: root.cardBorder
      border.width: 1
      clip: true

      Canvas {
        id: dotGrid
        anchors.fill: parent
        property color dotColor: Qt.rgba(root.fg.r, root.fg.g, root.fg.b, 0.14)
        onDotColorChanged: requestPaint()
        onWidthChanged: requestPaint()
        onHeightChanged: requestPaint()
        onPaint: {
          var ctx = getContext("2d")
          ctx.clearRect(0, 0, width, height)
          ctx.fillStyle = dotColor
          var step = Math.max(14, Style.space(16))
          for (var y = step; y < height - canvas.footerPad; y += step) {
            for (var x = step; x < width; x += step) {
              ctx.fillRect(x, y, 1.5, 1.5)
            }
          }
        }
      }

      // Clipped stage above the canvas footer so tiles and guide lines can
      // never overflow onto the header or footer.
      Item {
        id: tilesStage
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        anchors.bottom: parent.bottom
        anchors.margins: 1
        anchors.bottomMargin: canvas.footerPad
        clip: true

        // Live magnetic alignment guide line (edge or H/V center)
        Rectangle {
          id: snapGuideLine
          visible: canvas.dragIndex >= 0 && canvas.activeSnap !== null
          z: 4
          color: root.accent
          opacity: 0.75
          x: visible && canvas.activeSnap.axis === "x"
            ? Math.round(canvas.view.ox + (canvas.activeSnap.guidePos - canvas.view.minX) * canvas.view.k)
            : 0
          y: visible && canvas.activeSnap.axis === "y"
            ? Math.round(canvas.view.oy + (canvas.activeSnap.guidePos - canvas.view.minY) * canvas.view.k)
            : 0
          width: visible && canvas.activeSnap.axis === "x" ? 1 : parent.width
          height: visible && canvas.activeSnap.axis === "y" ? 1 : parent.height
        }

        Repeater {
          model: root.draft

          Rectangle {
            id: tile
            required property var modelData
            required property int index
            readonly property bool shown: modelData.enabled && !modelData.autoOff && !modelData.mirrorOf
            readonly property var rect: canvas.dragIndex === index && canvas.dragRect ? canvas.dragRect : Model.rectOf(modelData)
            readonly property bool chosen: root.selected === index
            readonly property bool draggingThis: canvas.dragIndex === index
            readonly property var mirroredBy: root.mirrorsOf(modelData.name)

            visible: shown
            x: Math.round(canvas.view.ox + (rect.x - canvas.view.minX) * canvas.view.k)
            y: Math.round(canvas.view.oy + (rect.y - canvas.view.minY) * canvas.view.k)
            width: Math.max(Style.space(62), Math.round(rect.w * canvas.view.k))
            height: Math.max(Style.space(40), Math.round(rect.h * canvas.view.k))
            z: draggingThis ? 3 : (chosen ? 2 : 1)
            radius: Math.min(Style.cornerRadius, Style.space(6))
            color: chosen
              ? Qt.tint(Commons.Color.popups.background, root.accentSoft)
              : (tileMouse.containsMouse
                ? Qt.lighter(Commons.Color.popups.background, 1.25)
                : Qt.lighter(Commons.Color.popups.background, 1.12))
            border.color: chosen
              ? root.accent
              : (tileMouse.containsMouse ? root.accentBorder : Qt.rgba(root.fg.r, root.fg.g, root.fg.b, 0.34))
            border.width: chosen ? 2 : 1
            clip: true

            Item {
              id: tileTop
              anchors.left: parent.left
              anchors.right: parent.right
              anchors.top: parent.top
              anchors.margins: Style.space(6)
              height: tileName.implicitHeight

              Text {
                id: tileName
                anchors.left: parent.left
                anchors.right: tileScale.left
                anchors.rightMargin: Style.space(4)
                textFormat: Text.PlainText
                text: (tile.index + 1) + " · " + tile.modelData.name
                color: tile.chosen ? root.accent : root.fg
                font.family: root.fontName
                font.pixelSize: Style.font.caption
                font.bold: true
                elide: Text.ElideRight
              }

              Text {
                id: tileScale
                anchors.right: parent.right
                textFormat: Text.PlainText
                text: Commons.I18n.formatNumber(tile.modelData.scale) + "×"
                color: root.accent
                font.family: root.fontName
                font.pixelSize: Math.max(9, Style.font.caption - 1)
                font.bold: true
              }
            }

            Text {
              visible: tile.height >= Style.space(52)
              anchors.left: parent.left
              anchors.right: parent.right
              anchors.verticalCenter: parent.verticalCenter
              anchors.leftMargin: Style.space(6)
              anchors.rightMargin: Style.space(6)
              textFormat: Text.PlainText
              text: tile.mirroredBy.length
                ? ("⇄ Mirrored by " + tile.mirroredBy.join(", "))
                : root.shortModelName(tile.index)
              color: tile.mirroredBy.length ? root.accent : root.muted
              font.family: root.fontName
              font.pixelSize: Math.max(9, Style.font.caption - 1)
              font.bold: tile.mirroredBy.length > 0
              elide: Text.ElideRight
            }

            Item {
              visible: tile.height >= Style.space(64) && !!tile.modelData.mode
              anchors.left: parent.left
              anchors.right: parent.right
              anchors.bottom: parent.bottom
              anchors.margins: Style.space(6)
              height: tileRes.implicitHeight

              Text {
                id: tileRes
                anchors.left: parent.left
                anchors.right: tileHz.left
                anchors.rightMargin: Style.space(4)
                textFormat: Text.PlainText
                text: tile.modelData.mode ? (tile.modelData.mode.w + "×" + tile.modelData.mode.h) : ""
                color: root.muted
                font.family: root.fontName
                font.pixelSize: Math.max(9, Style.font.caption - 1)
                elide: Text.ElideRight
              }

              Text {
                id: tileHz
                anchors.right: parent.right
                textFormat: Text.PlainText
                text: tile.modelData.mode ? (Math.round(tile.modelData.mode.hz) + "Hz") : ""
                color: root.muted
                font.family: root.fontName
                font.pixelSize: Math.max(9, Style.font.caption - 1)
              }
            }

            MouseArea {
              id: tileMouse
              anchors.fill: parent
              hoverEnabled: true
              preventStealing: true
              cursorShape: canvas.dragIndex === tile.index ? Qt.ClosedHandCursor : Qt.OpenHandCursor
              property real grabFx: 0.5
              property real grabFy: 0.5
              property var start: null

              onPressed: function(mouse) {
                root.selected = tile.index
                grabFx = tile.width > 0 ? Math.max(0, Math.min(1, mouse.x / tile.width)) : 0.5
                grabFy = tile.height > 0 ? Math.max(0, Math.min(1, mouse.y / tile.height)) : 0.5
                start = Model.rectOf(tile.modelData)
                canvas.dragBounds = canvas.boundsFor(root.draft)
                canvas.dragRect = start
                canvas.activeSnap = null
                canvas.shiftFreeMove = !!(mouse.modifiers & Qt.ShiftModifier)
                canvas.dragIndex = tile.index
              }
              onPositionChanged: function(mouse) {
                if (canvas.dragIndex !== tile.index || !start) return
                var v = canvas.view
                var p = mapToItem(tilesStage, mouse.x, mouse.y)
                var wanted = {
                  x: Math.round((p.x - v.ox) / v.k + v.minX - grabFx * start.w),
                  y: Math.round((p.y - v.oy) / v.k + v.minY - grabFy * start.h),
                  w: start.w,
                  h: start.h
                }
                // Hold Shift to disable magnetic snapping for 1px fine positioning;
                // otherwise snap cleanly within 8px on-screen toward edges & H/V centers.
                var shiftHeld = !!(mouse.modifiers & Qt.ShiftModifier)
                canvas.shiftFreeMove = shiftHeld
                var snapThreshold = shiftHeld ? 0 : (Style.space(8) / v.k)
                var res = Model.attachWithGuides(wanted, canvas.others(tile.index), snapThreshold)
                canvas.dragRect = res.rect
                canvas.activeSnap = res.snap
                canvas.expandDragBounds(res.rect)
              }
              onReleased: {
                if (canvas.dragIndex !== tile.index) return
                var index = tile.index
                var r = canvas.dragRect
                var moved = r && start && (r.x !== start.x || r.y !== start.y)
                canvas.dragIndex = -1
                canvas.dragRect = null
                canvas.activeSnap = null
                canvas.dragBounds = null
                canvas.shiftFreeMove = false
                start = null
                if (!moved) return
                var next = root.draft.map(root.copyOutput)
                next[index].x = r.x
                next[index].y = r.y
                root.edited = true
                root.draft = Model.solve(next, index, 0)
              }
              onCanceled: {
                canvas.dragIndex = -1
                canvas.dragRect = null
                canvas.activeSnap = null
                canvas.dragBounds = null
                canvas.shiftFreeMove = false
                start = null
              }
            }
          }
        }
      }
    }

    Item {
      anchors.left: parent.left
      anchors.right: parent.right
      anchors.bottom: parent.bottom
      anchors.leftMargin: Style.space(10)
      anchors.rightMargin: Style.space(10)
      anchors.bottomMargin: Style.space(5)
      height: canvasHint.implicitHeight

      Text {
        id: canvasHint
        anchors.left: parent.left
        anchors.right: canvasCoord.left
        anchors.rightMargin: Style.space(8)
        textFormat: Text.PlainText
        text: canvas.dragIndex >= 0
          ? (canvas.shiftFreeMove
              ? "Fine drag (Shift held)"
              : (canvas.activeSnap ? (canvas.activeSnap.label + " · hold Shift for finer drag") : "Drag to place · hold Shift for finer drag"))
          : "Drag tiles · hold Shift for finer drag · 1-9 / ←→ · Enter apply"
        color: canvas.dragIndex >= 0 && (canvas.activeSnap || canvas.shiftFreeMove) ? root.accent : root.muted
        font.family: root.fontName
        font.pixelSize: Math.max(9, Style.font.caption - 1)
        elide: Text.ElideRight
      }

      Text {
        id: canvasCoord
        anchors.right: parent.right
        textFormat: Text.PlainText
        text: {
          if (canvas.dragIndex >= 0 && canvas.dragRect && root.draft[canvas.dragIndex])
            return root.draft[canvas.dragIndex].name + " @ " + canvas.dragRect.x + ", " + canvas.dragRect.y
          if (root.current) {
            if (root.current.mirrorOf) return root.current.name + " ⇄ " + root.current.mirrorOf
            return root.current.name + " @ " + Math.round(root.current.x) + ", " + Math.round(root.current.y)
          }
          return ""
        }
        color: root.accent
        font.family: root.fontName
        font.pixelSize: Math.max(9, Style.font.caption - 1)
        font.bold: true
      }
    }

    Text {
      anchors.centerIn: parent
      visible: root.draft.length === 0
      textFormat: Text.PlainText
      text: "No displays found"
      color: root.muted
      font.family: root.fontName
      font.pixelSize: Style.font.caption
    }
  }
}

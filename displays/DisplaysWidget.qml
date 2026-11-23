import QtQuick
import QtQuick.Controls
import Quickshell
import Quickshell.Io
import qs.Ui
import qs.Commons
import qs.Commons as Commons
import "DisplayModel.js" as Model

// Unified Displays bar widget (replaces stock `omarchy.monitor` and answers
// `dicey.displays`). Drag monitors into place, select any monitor to edit its
// own mode, scale, adaptive sync, HDR, brightness, text size, night light and
// auto-disable settings, and press Remember to apply and persist them.
Panel {
  id: root
  moduleName: "omarchy.monitor"
  ipcTarget: "omarchy.monitor"
  manageIpc: false

  readonly property var service: bar && bar.displayService ? bar.displayService : null
  readonly property var live: service ? service.livePlan : []
  readonly property var liveMonitors: service ? service.monitors : []

  // The arrangement being edited; it becomes the plan on Remember.
  property var draft: []
  property int selected: 0
  property bool edited: false

  // Opening values for live-previewed desktop properties so Discard reverts cleanly.
  property int openedTextSize: 0
  property bool textSizePreviewed: false
  property var openedNightLight: null
  property bool nightLightPreviewed: false

  readonly property bool dirty: {
    if (!draft.length || draft.length !== live.length) return false
    if (!Model.planMatchesLive(draft, live)) return true
    for (var i = 0; i < draft.length; i++) {
      if (draft[i].ddcWatch !== live[i].ddcWatch) return true
      if ((draft[i].textSize || null) !== (live[i].textSize || null)) return true
      if ((draft[i].brightness || null) !== (live[i].brightness || null)) return true
      if (!Model.sameNightLight(draft[i].nightLight, live[i].nightLight)) return true
    }
    return false
  }
  readonly property var current: selected >= 0 && selected < draft.length ? draft[selected] : null
  readonly property var currentMonitor: selected >= 0 && selected < liveMonitors.length ? liveMonitors[selected] : null
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
  readonly property int currentTextSize: {
    if (current && current.textSize !== null && current.textSize !== undefined)
      return Math.round(Number(current.textSize))
    return Style.font.baseSize
  }
  readonly property bool currentNightLightOn: {
    if (current && current.nightLight) return current.nightLight.enabled === true
    return !!nightlight && nightlight.enabled
  }
  readonly property int currentNightLightKelvin: {
    if (current && current.nightLight && current.nightLight.kelvin)
      return Math.round(Number(current.nightLight.kelvin))
    if (nightlight && nightlight.enabled && nightlight.temperature) {
      var t = Math.round(Number(nightlight.temperature))
      if (isFinite(t) && t >= 1500 && t <= 6500) return t
    }
    return 4000
  }

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

  function shortModelName(index) {
    var m = index >= 0 && index < liveMonitors.length ? liveMonitors[index] : null
    if (!m) return draft[index] ? draft[index].name : ""
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

  function discardDraft() {
    if (textSizePreviewed && openedTextSize >= 9 && openedTextSize <= 20) {
      applyTextSizeLive(openedTextSize)
      textSizePreviewed = false
    }
    if (nightLightPreviewed && openedNightLight && nightlight) {
      if (openedNightLight.enabled) {
        if (typeof nightlight.applyTemperature === "function") nightlight.applyTemperature(openedNightLight.kelvin)
        else nightlight.setNightlight(true)
      } else {
        nightlight.setNightlight(false)
      }
      nightLightPreviewed = false
    }
    resetDraft()
  }

  function copyOutput(o) {
    var c = {}
    for (var k in o) c[k] = o[k]
    if (o.mode) c.mode = { w: o.mode.w, h: o.mode.h, hz: o.mode.hz }
    if (o.nightLight) c.nightLight = { enabled: o.nightLight.enabled === true, kelvin: o.nightLight.kelvin }
    return c
  }

  function update(index, changes, rearrange) {
    if (index < 0 || index >= draft.length) return
    var next = draft.map(copyOutput)
    for (var k in changes) next[index][k] = changes[k]
    draft = rearrange ? Model.solve(next) : next
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

  function setMonitorTextSize(index, px) {
    var clean = Model.cleanTextSize(px)
    if (clean === null) return
    update(index, { textSize: clean }, false)
    textSizePreviewed = true
    applyTextSizeLive(clean)
  }

  function setMonitorNightLight(index, enabled, kelvin) {
    var nl = { enabled: enabled === true, kelvin: Math.round(Number(kelvin) || 4000) }
    update(index, { nightLight: nl }, false)
    nightLightPreviewed = true
    if (!nightlight) return
    if (nl.enabled) {
      if (typeof nightlight.applyTemperature === "function") nightlight.applyTemperature(nl.kelvin)
      else nightlight.setNightlight(true)
    } else {
      nightlight.setNightlight(false)
    }
  }

  function canTurnOff(index) {
    var o = draft[index]
    return !!o && (!o.enabled || enabledCount > 1)
  }

  function apply() {
    if (!service || !dirty || service.confirming) return
    var plan = Model.solve(draft)
    edited = false
    textSizePreviewed = false
    nightLightPreviewed = false
    service.applyFromPanel(plan, selected)
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
    return o.enabled ? "Active" : "Disabled"
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
      var target = root.focusedOutputName()
      if (target) {
        root.brightnessFor = target
        root.setBrightness(Number(percent), false)
      }
      return "got " + root.brightness
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

  Connections {
    target: root.service
    function onRefreshed() { if (!root.edited) root.resetDraft() }
  }

  onOpenedChanged: {
    if (!opened) return
    openedTextSize = Style.font.baseSize
    textSizePreviewed = false
    openedNightLight = nightlight
      ? { enabled: nightlight.enabled === true, kelvin: Math.round(Number(nightlight.temperature) || 4000) }
      : null
    nightLightPreviewed = false
    resetDraft()
    if (service) service.refresh()
    if (nightlight && typeof nightlight.refresh === "function") nightlight.refresh()
    readBrightness()
  }
  onSelectedChanged: readBrightness()

  // ---------------------------------------------------------------- brightness (per monitor)
  property int brightness: 0
  property bool brightnessAvailable: false
  property string brightnessFor: ""
  property bool brightnessQueued: false
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
    var pct = Math.max(1, Math.min(100, Math.round(value)))
    brightness = pct
    if (recordInDraft !== false && current && current.name === brightnessFor)
      update(selected, { brightness: pct }, false)
    if (brightnessWrite.running) {
      brightnessQueued = true
      return
    }
    brightnessQueued = false
    if (!brightnessFor) return
    brightnessWrite.command = ["omarchy-brightness-display", "--no-osd", "--monitor", brightnessFor, pct + "%"]
    brightnessWrite.running = true
  }

  Process {
    id: brightnessRead
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        var value = parseInt(String(text || "").trim(), 10)
        var asked = brightnessRead.command.length ? brightnessRead.command[brightnessRead.command.length - 1] : ""
        if (isFinite(value) && asked === root.brightnessFor) {
          root.brightness = Math.max(1, Math.min(100, value))
          root.brightnessAvailable = true
        }
      }
    }
    onRunningChanged: {
      if (running || !root.brightnessReadQueued) return
      root.brightnessReadQueued = false
      root.readBrightness()
    }
  }

  Process {
    id: brightnessWrite
    onRunningChanged: if (!running && root.brightnessQueued) root.setBrightness(root.brightness, false)
  }

  Timer {
    id: brightnessDebounce
    interval: 140
    onTriggered: root.setBrightness(root.brightness, true)
  }

  // ---------------------------------------------------------------- text size (per monitor)
  readonly property var textSizeStops: [9, 10, 11, 12, 14, 16, 20]
  Process { id: textSizeProc }

  function applyTextSizeLive(px) {
    textSizeProc.command = ["omarchy-display-text-size", String(px)]
    if (!textSizeProc.running) textSizeProc.running = true
  }

  // ---------------------------------------------------------------- night light (per monitor)
  readonly property var nightlight: bar && bar.shell ? bar.shell.firstPartyServiceFor("omarchy.nightlight") : null
  property int pendingKelvin: 4000

  Timer {
    id: kelvinDebounce
    interval: 120
    onTriggered: {
      if (root.current && root.currentNightLightOn)
        root.setMonitorNightLight(root.selected, true, root.pendingKelvin)
    }
  }

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  BarIconButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: Quickshell.screens.length > 1 ? "󰍺" : "󰍹"
    tooltipText: "Displays"
    onPressed: function(b) { root.toggle() }
  }

  KeyboardPanel {
    id: panel
    anchorItem: button
    owner: root
    bar: root.bar
    open: root.opened
    focusTarget: panel.loadedContent
    contentWidth: panel.fittedContentWidth(Style.space(620))
    contentHeight: panel.fittedContentHeight(panel.loadedContentHeight, Style.space(760))

    content: PanelKeyCatcher {
      id: keyCatcher
      implicitHeight: column.implicitHeight + footer.implicitHeight + Style.space(14)
      anchors.fill: parent
      onCloseRequested: {
        if (resDropdown.popupOpen) resDropdown.close()
        else if (hzDropdown.popupOpen) hzDropdown.close()
        else root.close()
      }
      onTabRequested: function(direction) { root.switchPanel(direction) }

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
              if (s.confirming) return "Confirming changes (" + s.confirmSeconds + "s)"
              if (root.dirty) return "Unsaved changes · press Remember to apply & save"
              return "Synced with monitors.lua · auto-applies on reconnect"
            }
            color: {
              var s = root.service
              if (s && (s.lastError || s.fallbacks.length)) return root.alertColor
              if (root.dirty || (s && s.confirming)) return root.accent
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
              id: rememberBtn
              readonly property bool canApply: root.dirty && !!root.service && !root.service.confirming
              implicitWidth: rememberLabel.implicitWidth + Style.space(22)
              implicitHeight: Style.space(28)
              radius: Style.cornerRadius
              color: canApply
                ? (rememberMouse.containsMouse ? Qt.lighter(root.accent, 1.08) : root.accent)
                : root.trackBg
              border.color: canApply ? root.accent : root.cardBorder
              border.width: 1
              opacity: canApply ? 1 : 0.55

              Behavior on color { ColorAnimation { duration: Style.duration(120) } }

              Text {
                id: rememberLabel
                anchors.centerIn: parent
                textFormat: Text.PlainText
                text: "Remember"
                color: rememberBtn.canApply ? root.accentInk : root.muted
                font.family: root.fontName
                font.pixelSize: Style.font.caption
                font.bold: true
              }

              MouseArea {
                id: rememberMouse
                anchors.fill: parent
                enabled: rememberBtn.canApply
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
                    visible: !tabPill.isActive
                    textFormat: Text.PlainText
                    text: tabPill.modelData.autoOff ? "AUTO OFF" : "OFF"
                    color: tabPill.modelData.autoOff ? root.alertColor : root.muted
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
          // Clearly scopes every setting in both cards below to this monitor.
          Rectangle {
            id: monitorBanner
            width: parent.width
            visible: root.current !== null
            implicitHeight: bannerRow.implicitHeight + Style.space(18)
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
                    var mode = root.current.mode
                      ? (root.current.mode.w + "×" + root.current.mode.h + " @ " + Model.formatHz(root.current.mode.hz) + " Hz")
                      : ""
                    var sc = Commons.I18n.formatNumber(root.current.scale) + "×"
                    return root.current.name + (mode ? " · " + mode : "") + " · " + sc
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
                spacing: Style.space(8)

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
                  onToggled: root.update(root.selected, { enabled: !root.current.enabled }, true)
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
                : "This display is turned off. Flip the switch above and press Remember to re-enable it."
              color: root.muted
              font.family: root.fontName
              font.pixelSize: Style.font.caption
            }
          }

          // ---------- Two-Column Bordered Cards (Both Scoped to Selected Monitor) ----------
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

            // ========== LEFT CARD: MODE & SIGNAL ==========
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
                  text: "MODE & SIGNAL"
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

                Rectangle { width: parent.width; height: 1; color: root.hairline }

                // Per-monitor HDR
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
              }
            }

            // ========== RIGHT CARD: PICTURE & AUTOMATION ==========
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

                // Per-monitor Brightness
                Column {
                  width: parent.width
                  spacing: Style.space(5)

                  Item {
                    width: parent.width
                    implicitHeight: Math.max(brightHead.implicitHeight, brightPct.implicitHeight)

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

                    Text {
                      id: brightPct
                      anchors.right: parent.right
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

                // Per-monitor Text Size
                Column {
                  width: parent.width
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
                      text: root.currentTextSize + "px · " + Math.round(root.currentTextSize * 9 / 12) + "pt term"
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
                    currentValue: root.currentTextSize
                    onPicked: function(val) { root.setMonitorTextSize(root.selected, val) }
                  }
                }

                Rectangle { width: parent.width; height: 1; color: root.hairline; visible: root.nightlight !== null }

                // Per-monitor Night Light (toggle + warm Kelvin slider)
                Column {
                  width: parent.width
                  spacing: Style.space(5)
                  visible: root.nightlight !== null

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
                        text: root.currentNightLightOn
                          ? (Math.round(kelvinSlider.dragging ? kelvinSlider.liveValue : root.currentNightLightKelvin) + "K")
                          : "Off"
                        color: root.currentNightLightOn ? root.alertColor : root.muted
                        font.family: root.fontName
                        font.pixelSize: Style.font.caption
                        font.bold: root.currentNightLightOn
                        anchors.verticalCenter: parent.verticalCenter
                      }
                    }

                    AccentSwitch {
                      id: nlRight
                      anchors.right: parent.right
                      anchors.verticalCenter: parent.verticalCenter
                      activeColor: root.alertColor
                      checked: root.currentNightLightOn
                      onToggled: root.setMonitorNightLight(root.selected, !root.currentNightLightOn, root.currentNightLightKelvin)
                    }
                  }

                  PanelSlider {
                    id: kelvinSlider
                    bar: root.bar
                    width: parent.width
                    enabled: root.currentNightLightOn
                    opacity: enabled ? 1 : 0.38
                    minimum: 1800
                    maximum: 6000
                    step: 100
                    integer: true
                    value: root.currentNightLightKelvin
                    trackColor: Qt.rgba(root.fg.r, root.fg.g, root.fg.b, 0.14)
                    fillColor: root.alertColor
                    knobColor: root.alertColor
                    onMoved: function(v) {
                      root.pendingKelvin = Math.round(v)
                      kelvinDebounce.restart()
                    }
                    onReleased: function(v) {
                      kelvinDebounce.stop()
                      root.pendingKelvin = Math.round(v)
                      if (root.currentNightLightOn)
                        root.setMonitorNightLight(root.selected, true, root.pendingKelvin)
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

                  // External display: DDC/CI input & power-off switch
                  Item {
                    width: parent.width
                    visible: !!root.current && !root.current.internal
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
                        text: root.current && root.current.ddcWatch
                          ? "Watching input via DDC/CI (VCP 0x60)"
                          : "Auto-disable when monitor switches HDMI/DP input"
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

    readonly property real pad: Style.space(14)
    readonly property real footerPad: Style.space(22)
    property int dragIndex: -1
    property var frozenView: null
    property var dragRect: null

    readonly property var view: frozenView || viewFor(root.draft)

    function viewFor(outputs) {
      var minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity
      for (var i = 0; i < outputs.length; i++) {
        if (!outputs[i].enabled || outputs[i].autoOff) continue
        var r = Model.rectOf(outputs[i])
        minX = Math.min(minX, r.x)
        minY = Math.min(minY, r.y)
        maxX = Math.max(maxX, r.x + r.w)
        maxY = Math.max(maxY, r.y + r.h)
      }
      var usableH = Math.max(Style.space(60), height - footerPad)
      if (minX === Infinity) return { k: 1, ox: pad, oy: pad, minX: 0, minY: 0 }
      var spanX = Math.max(1, maxX - minX)
      var spanY = Math.max(1, maxY - minY)
      var k = Math.min((width - pad * 2) / (spanX * 1.28), (usableH - pad * 2) / (spanY * 1.22))
      return {
        k: k,
        ox: (width - spanX * k) / 2,
        oy: (usableH - spanY * k) / 2,
        minX: minX,
        minY: minY
      }
    }

    function others(index) {
      var list = []
      for (var i = 0; i < root.draft.length; i++)
        if (i !== index && root.draft[i].enabled && !root.draft[i].autoOff) list.push(Model.rectOf(root.draft[i]))
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
    }

    Repeater {
      model: root.draft

      Rectangle {
        id: tile
        required property var modelData
        required property int index
        readonly property bool shown: modelData.enabled && !modelData.autoOff
        readonly property var rect: canvas.dragIndex === index && canvas.dragRect ? canvas.dragRect : Model.rectOf(modelData)
        readonly property bool chosen: root.selected === index
        readonly property bool draggingThis: canvas.dragIndex === index

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
          text: root.shortModelName(tile.index)
          color: root.muted
          font.family: root.fontName
          font.pixelSize: Math.max(9, Style.font.caption - 1)
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
          property point grab: Qt.point(0, 0)
          property var start: null

          onPressed: function(mouse) {
            root.selected = tile.index
            grab = Qt.point(mouse.x, mouse.y)
            start = Model.rectOf(tile.modelData)
            canvas.frozenView = canvas.view
            canvas.dragRect = start
            canvas.dragIndex = tile.index
          }
          onPositionChanged: function(mouse) {
            if (canvas.dragIndex !== tile.index || !start) return
            var v = canvas.frozenView
            var p = mapToItem(canvas, mouse.x, mouse.y)
            var wanted = {
              x: Math.round((p.x - grab.x - v.ox) / v.k + v.minX),
              y: Math.round((p.y - grab.y - v.oy) / v.k + v.minY),
              w: start.w,
              h: start.h
            }
            canvas.dragRect = Model.attach(wanted, canvas.others(tile.index), Style.space(12) / v.k)
          }
          onReleased: {
            if (canvas.dragIndex !== tile.index) return
            var index = tile.index
            var r = canvas.dragRect
            var moved = r && start && (r.x !== start.x || r.y !== start.y)
            canvas.dragIndex = -1
            canvas.dragRect = null
            canvas.frozenView = null
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
            canvas.frozenView = null
            start = null
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
        text: "Drag tiles to arrange · edges snap flush"
        color: root.muted
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
          if (root.current)
            return root.current.name + " @ " + Math.round(root.current.x) + ", " + Math.round(root.current.y)
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

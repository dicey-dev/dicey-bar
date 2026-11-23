import QtQuick
import QtQuick.Controls
import Quickshell
import Quickshell.Io
import qs.Ui
import qs.Commons
import qs.Commons as Commons
import "DisplayModel.js" as Model

// The Displays bar widget: drag monitors into place, choose each one's
// resolution, refresh rate, scale, HDR and adaptive sync, and press Apply.
// Apply also remembers every monitor's settings and this combination's
// layout; DisplayService brings them back when the monitors reconnect.
// Brightness, text size and night light take effect straight away.
Panel {
  id: root
  moduleName: "dicey.displays"
  ipcTarget: "dicey.displays"

  readonly property var service: bar && bar.displayService ? bar.displayService : null
  readonly property var live: service ? service.livePlan : []
  readonly property var liveMonitors: service ? service.monitors : []

  // The arrangement being edited; it becomes the plan on Apply.
  property var draft: []
  property int selected: 0
  property bool edited: false
  property bool resolutionsOpen: false

  readonly property bool dirty: {
    if (!draft.length || draft.length !== live.length) return false
    if (!Model.planMatchesLive(draft, live)) return true
    for (var i = 0; i < draft.length; i++) if (draft[i].ddcWatch !== live[i].ddcWatch) return true
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
  readonly property int enabledCount: {
    var n = 0
    for (var i = 0; i < draft.length; i++) if (draft[i].enabled && !draft[i].autoOff) n++
    return n
  }

  readonly property color fg: bar ? bar.foreground : Commons.Color.foreground
  readonly property color muted: Qt.darker(fg, 1.4)
  readonly property string fontName: bar ? bar.fontFamily : Style.font.family

  function resetDraft() {
    draft = live.map(copyOutput)
    edited = false
    if (selected >= draft.length) selected = 0
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

  function setResolution(index, group) {
    var o = draft[index]
    var hz = group.rates[0]
    for (var i = 0; i < group.rates.length; i++) if (Model.sameHz(group.rates[i], o.mode.hz)) hz = group.rates[i]
    update(index, {
      mode: { w: group.w, h: group.h, hz: hz },
      scale: Model.cleanScale(o.scale, group.w, group.h)
    }, true)
    resolutionsOpen = false
  }

  function canTurnOff(index) {
    var o = draft[index]
    return !!o && (!o.enabled || enabledCount > 1)
  }

  function apply() {
    if (!service || !dirty || service.confirming) return
    var plan = Model.solve(draft)
    edited = false
    service.applyFromPanel(plan)
  }

  function offText(o) {
    if (!o) return ""
    if (o.offReason === "lid") return "Off while the lid is closed"
    if (o.offReason === "input") return "Off: switched to another input"
    return ""
  }

  function modeLabel(mode) {
    return mode ? mode.w + " × " + mode.h : ""
  }

  Connections {
    target: root.service
    function onRefreshed() { if (!root.edited) root.resetDraft() }
  }

  onOpenedChanged: {
    if (!opened) return
    resolutionsOpen = false
    resetDraft()
    if (service) service.refresh()
    readBrightness()
  }
  onSelectedChanged: {
    resolutionsOpen = false
    readBrightness()
  }

  // ---------------------------------------------------------------- brightness
  property int brightness: 0
  property bool brightnessAvailable: false
  property string brightnessFor: ""
  property bool brightnessQueued: false

  property bool brightnessReadQueued: false

  function readBrightness() {
    // Read the draft directly: inside onSelectedChanged `current` may still
    // name the previously selected monitor.
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

  function setBrightness(value) {
    brightness = Math.max(1, Math.min(100, Math.round(value)))
    if (brightnessWrite.running) {
      brightnessQueued = true
      return
    }
    brightnessQueued = false
    brightnessWrite.command = ["omarchy-brightness-display", "--no-osd", "--monitor", brightnessFor, brightness + "%"]
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
    onRunningChanged: if (!running && root.brightnessQueued) root.setBrightness(root.brightness)
  }

  Timer {
    id: brightnessDebounce
    interval: 150
    onTriggered: root.setBrightness(root.brightness)
  }

  // ---------------------------------------------------------------- text size
  // The same notches and CLI as Omarchy's own display panel.
  readonly property var textSizeStops: [9, 10, 11, 12, 14, 16, 20]

  function nearestTextStop(px) {
    var best = 0
    for (var i = 1; i < textSizeStops.length; i++)
      if (Math.abs(textSizeStops[i] - px) < Math.abs(textSizeStops[best] - px)) best = i
    return best
  }

  Process { id: textSizeProc }

  // ---------------------------------------------------------------- night light
  readonly property var nightlight: bar && bar.shell ? bar.shell.firstPartyServiceFor("omarchy.nightlight") : null

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
    contentWidth: panel.fittedContentWidth(Style.space(420))
    contentHeight: panel.fittedContentHeight(panel.loadedContentHeight, Style.space(720))

    content: PanelKeyCatcher {
      implicitHeight: column.implicitHeight + footer.implicitHeight + Style.space(12)
      anchors.fill: parent
      onCloseRequested: root.close()
      onTabRequested: function(direction) { root.switchPanel(direction) }

      // ---------- Apply (pinned below the scrolling settings) ----------
      Item {
        id: footer
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        implicitHeight: Math.max(applyButton.implicitHeight, statusText.implicitHeight)
        height: implicitHeight

        Text {
          id: statusText
          anchors.left: parent.left
          anchors.right: applyButton.left
          anchors.rightMargin: Style.space(10)
          anchors.verticalCenter: parent.verticalCenter
          textFormat: Text.PlainText
          wrapMode: Text.Wrap
          text: {
            var s = root.service
            if (!s) return ""
            if (s.lastError) return s.lastError
            if (s.fallbacks.length) {
              var f = s.fallbacks[0]
              return f.name + " is running " + f.actual + " instead of " + f.wanted + " (likely link bandwidth)"
            }
            if (s.confirming) return "Waiting for you to keep or revert"
            if (root.dirty) return "Apply to use and remember these settings"
            return ""
          }
          color: root.service && (root.service.lastError || root.service.fallbacks.length) ? Commons.Color.bar.active : root.muted
          font.family: root.fontName
          font.pixelSize: Style.font.caption
        }

        Button {
          id: applyButton
          anchors.right: parent.right
          anchors.verticalCenter: parent.verticalCenter
          text: "Apply"
          bordered: true
          active: root.dirty
          enabled: root.dirty && !!root.service && !root.service.confirming
          opacity: enabled ? 1 : 0.45
          foreground: root.fg
          fontFamily: root.fontName
          onClicked: root.apply()
        }
      }

      ScrollView {
        id: scrollArea
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        anchors.bottom: footer.top
        anchors.bottomMargin: Style.space(12)
        clip: true
        ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
        ScrollBar.vertical.policy: column.implicitHeight > height ? ScrollBar.AsNeeded : ScrollBar.AlwaysOff
        // Keep the scrollbar off the switches and values on the right edge.
        rightPadding: ScrollBar.vertical.visible ? ScrollBar.vertical.width + Style.space(4) : 0
        Binding {
          target: scrollArea.contentItem
          property: "interactive"
          value: column.implicitHeight > scrollArea.height
        }

        Column {
          id: column
          width: scrollArea.availableWidth
          spacing: Style.space(12)

          // ---------- Header ----------
          Item {
            width: parent.width
            implicitHeight: Math.max(heroIcon.implicitHeight, heroText.implicitHeight)

            Text {
              id: heroIcon
              textFormat: Text.PlainText
              text: root.draft.length > 1 ? "󰍺" : "󰍹"
              color: root.fg
              font.family: root.fontName
              font.pixelSize: Style.font.display
              anchors.left: parent.left
              anchors.verticalCenter: parent.verticalCenter
            }

            Column {
              id: heroText
              anchors.left: heroIcon.right
              anchors.leftMargin: Style.space(14)
              anchors.right: identifyButton.left
              anchors.rightMargin: Style.space(8)
              anchors.verticalCenter: parent.verticalCenter
              spacing: Style.space(2)

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
                text: root.draft.length === 1 ? "1 CONNECTED" : root.draft.length + " CONNECTED"
                color: root.muted
                font.family: root.fontName
                font.pixelSize: Style.font.caption
                font.bold: true
                font.letterSpacing: 1.2
                elide: Text.ElideRight
              }
            }

            Button {
              id: identifyButton
              anchors.right: parent.right
              anchors.verticalCenter: parent.verticalCenter
              text: "Identify"
              bordered: true
              fontSize: Style.font.caption
              foreground: root.fg
              fontFamily: root.fontName
              tooltipText: "Show each display's number on it"
              onClicked: if (root.service) root.service.identify()
            }
          }

          // ---------- Layout canvas ----------
          LayoutCanvas {
            width: parent.width
            height: Style.space(180)
          }

          // ---------- Display tabs ----------
          Flow {
            width: parent.width
            spacing: Style.spacing.xs

            Repeater {
              model: root.draft

              Button {
                required property var modelData
                required property int index
                text: (index + 1) + "  " + modelData.name
                bordered: true
                active: root.selected === index
                fontSize: Style.font.caption
                foreground: root.fg
                fontFamily: root.fontName
                opacity: modelData.enabled && !modelData.autoOff ? 1 : 0.55
                onClicked: root.selected = index
              }
            }
          }

          PanelSeparator { foreground: root.fg }

          // ---------- Selected display ----------
          Column {
            width: parent.width
            spacing: Style.space(12)
            visible: root.current !== null

            Item {
              width: parent.width
              implicitHeight: Math.max(nameColumn.implicitHeight, powerSwitch.implicitHeight)

              Column {
                id: nameColumn
                anchors.left: parent.left
                anchors.right: powerSwitch.left
                anchors.rightMargin: Style.space(10)
                anchors.verticalCenter: parent.verticalCenter
                spacing: Style.space(2)

                Text {
                  width: parent.width
                  textFormat: Text.PlainText
                  text: root.currentMonitor ? String(root.currentMonitor.description || root.current.name) : ""
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
                    return off ? root.current.name + " · " + off : root.current.name
                  }
                  color: root.muted
                  font.family: root.fontName
                  font.pixelSize: Style.font.caption
                  elide: Text.ElideRight
                }
              }

              ToggleSwitch {
                id: powerSwitch
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
                foreground: root.fg
                checked: !!root.current && root.current.enabled && !root.current.autoOff
                interactive: !!root.current && !root.current.autoOff && root.canTurnOff(root.selected)
                opacity: interactive ? 1 : 0.45
                onToggled: root.update(root.selected, { enabled: !root.current.enabled }, true)
              }
            }

            Column {
              width: parent.width
              spacing: Style.space(12)
              visible: !!root.current && root.current.enabled && !root.current.autoOff

              // Resolution
              Column {
                width: parent.width
                spacing: Style.space(6)

                CursorSurface {
                  width: parent.width
                  height: resolutionRow.implicitHeight + Style.spacing.controlGap
                  foreground: root.fg
                  fill: Style.hoverFillFor(root.fg, Commons.Color.accent)
                  hasCursor: resolutionMouse.containsMouse

                  Item {
                    id: resolutionRow
                    anchors.fill: parent
                    anchors.leftMargin: Style.space(6)
                    anchors.rightMargin: Style.space(6)
                    implicitHeight: resolutionValue.implicitHeight

                    PanelSectionHeader {
                      text: "Resolution"
                      foreground: root.fg
                      fontFamily: root.fontName
                      anchors.left: parent.left
                      anchors.verticalCenter: parent.verticalCenter
                    }

                    Text {
                      id: resolutionValue
                      textFormat: Text.PlainText
                      text: root.modeLabel(root.current ? root.current.mode : null) + (root.resolutionsOpen ? "  󰅃" : "  󰅀")
                      color: root.fg
                      font.family: root.fontName
                      font.pixelSize: Style.font.body
                      anchors.right: parent.right
                      anchors.verticalCenter: parent.verticalCenter
                    }
                  }

                  MouseArea {
                    id: resolutionMouse
                    anchors.fill: parent
                    hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor
                    onClicked: root.resolutionsOpen = !root.resolutionsOpen
                  }
                }

                Column {
                  width: parent.width
                  visible: root.resolutionsOpen
                  spacing: 0

                  Repeater {
                    model: root.resolutionsOpen ? root.currentGroups : []

                    CursorSurface {
                      required property var modelData
                      readonly property bool chosen: !!root.current && root.current.mode
                        && root.current.mode.w === modelData.w && root.current.mode.h === modelData.h
                      width: parent.width
                      height: optionText.implicitHeight + Style.spacing.sm
                      foreground: root.fg
                      fill: Style.hoverFillFor(root.fg, Commons.Color.accent)
                      currentFill: Style.selectedFillFor(root.fg, Commons.Color.accent)
                      current: chosen
                      hasCursor: optionMouse.containsMouse

                      Text {
                        id: optionText
                        anchors.left: parent.left
                        anchors.leftMargin: Style.space(12)
                        anchors.verticalCenter: parent.verticalCenter
                        textFormat: Text.PlainText
                        text: modelData.w + " × " + modelData.h
                        color: root.fg
                        font.family: root.fontName
                        font.pixelSize: Style.font.body
                      }

                      Text {
                        anchors.right: parent.right
                        anchors.rightMargin: Style.space(12)
                        anchors.verticalCenter: parent.verticalCenter
                        textFormat: Text.PlainText
                        text: "up to " + Model.formatHz(modelData.rates[0]) + " Hz"
                        color: root.muted
                        font.family: root.fontName
                        font.pixelSize: Style.font.caption
                      }

                      MouseArea {
                        id: optionMouse
                        anchors.fill: parent
                        hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        onClicked: root.setResolution(root.selected, parent.modelData)
                      }
                    }
                  }
                }
              }

              // Refresh rate
              SettingRow {
                title: "Refresh rate"

                Repeater {
                  model: root.currentRates

                  Button {
                    required property var modelData
                    text: Model.formatHz(modelData) + " Hz"
                    bordered: true
                    active: !!root.current && Model.sameHz(root.current.mode.hz, modelData)
                    fontSize: Style.font.caption
                    foreground: root.fg
                    fontFamily: root.fontName
                    onClicked: root.update(root.selected, { mode: { w: root.current.mode.w, h: root.current.mode.h, hz: modelData } }, false)
                  }
                }
              }

              // Scale
              SettingRow {
                title: "Scale"

                Repeater {
                  model: root.current && root.current.mode ? Model.scaleOptions(root.current.mode.w, root.current.mode.h) : []

                  Button {
                    required property var modelData
                    text: Commons.I18n.formatNumber(modelData) + "x"
                    bordered: true
                    active: !!root.current && Math.abs(root.current.scale - modelData) < 0.001
                    fontSize: Style.font.caption
                    foreground: root.fg
                    fontFamily: root.fontName
                    onClicked: root.update(root.selected, { scale: modelData }, true)
                  }
                }
              }

              // Adaptive sync
              SettingRow {
                title: "Adaptive sync"

                Repeater {
                  model: [{ value: 0, label: "Off" }, { value: 1, label: "On" }, { value: 2, label: "Fullscreen" }]

                  Button {
                    required property var modelData
                    text: modelData.label
                    bordered: true
                    active: !!root.current && root.current.vrr === modelData.value
                    fontSize: Style.font.caption
                    foreground: root.fg
                    fontFamily: root.fontName
                    onClicked: root.update(root.selected, { vrr: modelData.value }, false)
                  }
                }
              }

              // HDR
              SwitchRow {
                title: "HDR"
                caption: root.currentMonitor && root.currentMonitor.hdrCapable ? "" : "This display does not report HDR support"
                checked: !!root.current && root.current.hdr
                interactive: !!root.currentMonitor && root.currentMonitor.hdrCapable === true
                onToggled: root.update(root.selected, { hdr: !root.current.hdr }, false)
              }

              // Brightness (live)
              Column {
                width: parent.width
                spacing: Style.space(6)
                visible: root.brightnessAvailable

                Item {
                  width: parent.width
                  implicitHeight: brightnessHeader.implicitHeight

                  PanelSectionHeader {
                    id: brightnessHeader
                    text: "Brightness"
                    foreground: root.fg
                    fontFamily: root.fontName
                    anchors.left: parent.left
                  }

                  Text {
                    anchors.right: parent.right
                    anchors.rightMargin: Style.space(6)
                    anchors.verticalCenter: parent.verticalCenter
                    textFormat: Text.PlainText
                    text: Math.round(brightnessSlider.dragging ? brightnessSlider.liveValue : root.brightness) + "%"
                    color: root.muted
                    font.family: root.fontName
                    font.pixelSize: Style.font.caption
                    font.bold: true
                  }
                }

                PanelSlider {
                  id: brightnessSlider
                  bar: root.bar
                  width: parent.width
                  minimum: 1
                  maximum: 100
                  step: 1
                  integer: true
                  value: root.brightness
                  onMoved: function(v) {
                    root.brightness = Math.round(v)
                    brightnessDebounce.restart()
                  }
                  onReleased: function(v) {
                    brightnessDebounce.stop()
                    root.setBrightness(v)
                  }
                }
              }

              // Input watch (external displays)
              SwitchRow {
                visible: !!root.current && !root.current.internal
                title: "Turn off when switched away"
                caption: "Turns this display off while it shows another input or is powered off, using DDC/CI"
                checked: !!root.current && root.current.ddcWatch
                onToggled: root.update(root.selected, { ddcWatch: !root.current.ddcWatch }, false)
              }
            }
          }

          PanelSeparator { foreground: root.fg }

          // ---------- Text size (all displays, live) ----------
          Column {
            width: parent.width
            spacing: Style.space(6)

            Item {
              width: parent.width
              implicitHeight: textSizeHeader.implicitHeight

              PanelSectionHeader {
                id: textSizeHeader
                text: "Text size"
                foreground: root.fg
                fontFamily: root.fontName
                anchors.left: parent.left
              }

              Text {
                anchors.right: parent.right
                anchors.rightMargin: Style.space(6)
                anchors.verticalCenter: parent.verticalCenter
                textFormat: Text.PlainText
                text: (textSizeSlider.dragging
                  ? root.textSizeStops[Math.round(textSizeSlider.liveValue)]
                  : Style.font.baseSize) + "px"
                color: root.muted
                font.family: root.fontName
                font.pixelSize: Style.font.caption
                font.bold: true
              }
            }

            PanelSlider {
              id: textSizeSlider
              bar: root.bar
              width: parent.width
              minimum: 0
              maximum: root.textSizeStops.length - 1
              step: 1
              integer: true
              tickCount: root.textSizeStops.length
              value: root.nearestTextStop(Style.font.baseSize)
              onReleased: function(v) {
                textSizeProc.command = ["omarchy-display-text-size", String(root.textSizeStops[Math.round(v)])]
                if (!textSizeProc.running) textSizeProc.running = true
              }
            }
          }

          SwitchRow {
            visible: root.nightlight !== null
            title: "Night light"
            caption: "Warmer colours on every display"
            checked: !!root.nightlight && root.nightlight.enabled
            onToggled: if (root.nightlight) root.nightlight.setNightlight(!root.nightlight.enabled)
          }

          Item { width: parent.width; height: Style.space(2) }
        }
      }
    }
  }

  // A titled row of option buttons that wraps when it runs out of width.
  component SettingRow: Column {
    id: settingRow
    property string title: ""
    default property alias buttons: flow.data
    width: parent ? parent.width : 0
    spacing: Style.space(6)

    PanelSectionHeader {
      text: settingRow.title
      foreground: root.fg
      fontFamily: root.fontName
    }

    Flow {
      id: flow
      width: parent.width
      spacing: Style.spacing.xs
    }
  }

  component SwitchRow: Item {
    id: switchRow
    property string title: ""
    property string caption: ""
    property bool checked: false
    property bool interactive: true
    signal toggled()
    width: parent ? parent.width : 0
    implicitHeight: Math.max(switchText.implicitHeight, rowSwitch.implicitHeight)

    Column {
      id: switchText
      anchors.left: parent.left
      anchors.right: rowSwitch.left
      anchors.rightMargin: Style.space(10)
      anchors.verticalCenter: parent.verticalCenter
      spacing: Style.space(2)

      PanelSectionHeader {
        text: switchRow.title
        foreground: root.fg
        fontFamily: root.fontName
      }

      Text {
        width: parent.width
        visible: switchRow.caption !== ""
        textFormat: Text.PlainText
        text: switchRow.caption
        wrapMode: Text.Wrap
        color: root.muted
        font.family: root.fontName
        font.pixelSize: Style.font.caption
      }
    }

    ToggleSwitch {
      id: rowSwitch
      anchors.right: parent.right
      anchors.verticalCenter: parent.verticalCenter
      foreground: root.fg
      checked: switchRow.checked
      interactive: switchRow.interactive
      opacity: interactive ? 1 : 0.45
      onToggled: switchRow.toggled()
    }
  }

  // The monitors drawn to scale. Drag one to move it; on release it snaps to
  // the nearest edge of the others, so no gap or overlap is left.
  component LayoutCanvas: Item {
    id: canvas

    readonly property real pad: Style.space(12)
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
      if (minX === Infinity) return { k: 1, ox: pad, oy: pad, minX: 0, minY: 0 }
      var spanX = Math.max(1, maxX - minX)
      var spanY = Math.max(1, maxY - minY)
      // Leave room around the layout so a monitor can be dragged past an edge.
      var k = Math.min((width - pad * 2) / (spanX * 1.35), (height - pad * 2) / (spanY * 1.35))
      return {
        k: k,
        ox: (width - spanX * k) / 2,
        oy: (height - spanY * k) / 2,
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
      anchors.fill: parent
      radius: Style.cornerRadius
      color: Qt.rgba(root.fg.r, root.fg.g, root.fg.b, 0.05)
      border.color: Qt.rgba(root.fg.r, root.fg.g, root.fg.b, 0.18)
      border.width: 1
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

        visible: shown
        x: canvas.view.ox + (rect.x - canvas.view.minX) * canvas.view.k
        y: canvas.view.oy + (rect.y - canvas.view.minY) * canvas.view.k
        width: Math.max(Style.space(24), rect.w * canvas.view.k)
        height: Math.max(Style.space(16), rect.h * canvas.view.k)
        z: canvas.dragIndex === index ? 2 : 1
        radius: Math.min(Style.cornerRadius, Style.space(6))
        color: chosen ? Style.selectedFillFor(root.fg, Commons.Color.accent) : Qt.rgba(root.fg.r, root.fg.g, root.fg.b, 0.10)
        border.color: chosen ? Commons.Color.accent : Qt.rgba(root.fg.r, root.fg.g, root.fg.b, 0.45)
        border.width: chosen ? Math.max(2, Style.space(2)) : 1

        Column {
          anchors.centerIn: parent
          width: parent.width - Style.space(8)
          spacing: 0

          Text {
            width: parent.width
            horizontalAlignment: Text.AlignHCenter
            textFormat: Text.PlainText
            text: String(tile.index + 1)
            color: tile.chosen ? Commons.Color.accent : root.fg
            font.family: root.fontName
            font.pixelSize: Style.font.title
            font.bold: true
          }

          Text {
            width: parent.width
            visible: tile.height > Style.space(46)
            horizontalAlignment: Text.AlignHCenter
            textFormat: Text.PlainText
            text: tile.modelData.name
            elide: Text.ElideRight
            color: root.muted
            font.family: root.fontName
            font.pixelSize: Style.font.caption
          }
        }

        MouseArea {
          anchors.fill: parent
          hoverEnabled: true
          // The panel scrolls; without this its flickable takes over the drag.
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
            // Snap edges into line within about a dozen screen pixels.
            canvas.dragRect = Model.attach(wanted, canvas.others(tile.index), Style.space(12) / v.k)
          }
          onReleased: {
            if (canvas.dragIndex !== tile.index) return
            var index = tile.index
            var r = canvas.dragRect
            var moved = r && start && (r.x !== start.x || r.y !== start.y)
            // Clear the drag first: replacing the draft rebuilds every tile,
            // this handler's own included, so nothing after it would run.
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

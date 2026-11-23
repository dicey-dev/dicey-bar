import QtQuick
import Quickshell
import Quickshell.Hyprland
import Quickshell.Io
import Quickshell.Wayland
import qs.Commons
import qs.Commons as Commons
import qs.Ui
import "DisplayModel.js" as Model

// One per bar (not per screen). Keeps the connected monitors and the saved
// display memory, re-applies remembered settings when monitors come and go,
// and carries out the Displays panel's Apply.
//
// Nothing is applied automatically until the panel's Apply has saved a
// memory at least once, so installing the bar changes no display by itself.
Item {
  id: service

  // The bar's theme surface (foreground, font), for the overlays.
  property var bar: null

  readonly property string helperPath: decodeURIComponent(String(Qt.resolvedUrl("display_ctl.py")).replace(/^(file|qs):\/\//, ""))

  property var monitors: []
  property var memory: ({})
  property bool lidClosed: false
  property bool loaded: false
  // Monitors left off for now (lid shut, switched to another input), keyed by
  // fingerprint, and the current state as a plan with those marked.
  readonly property var currentAutoOff: Model.autoOffFor(Model.planFromLive(monitors, memory), lidClosed, ddcOff)
  readonly property var livePlan: Model.planFromLive(monitors, memory, currentAutoOff)

  // Monitors left off because they were switched to another input (or off),
  // keyed by fingerprint, and the consecutive DDC misses behind that.
  property var ddcOff: ({})
  property var ddcMisses: ({})

  // Modes Hyprland did not take on the last apply, usually link bandwidth.
  property var fallbacks: []
  property string lastError: ""

  // After an Apply that changes a mode or turns a monitor on or off.
  property bool confirming: false
  property int confirmSeconds: 0
  property var pendingCommit: null
  property var revertPlan: null

  property bool identifying: false

  // Automatic applies of the same plan are not retried in a tight loop when
  // Hyprland keeps reporting something else (for instance a mode it cannot
  // drive), so record the last one.
  property var lastAuto: null

  signal refreshed()

  // ---------------------------------------------------------------- jobs
  // Helper calls run one at a time, in order.
  property var jobs: []
  property var currentJob: null
  property var refreshWaiters: []
  property bool refreshQueued: false

  function run(args, input, done) {
    jobs = jobs.concat([{ args: args, input: input, done: done || null }])
    pump()
  }

  function pump() {
    if (currentJob || jobs.length === 0) return
    var job = jobs[0]
    jobs = jobs.slice(1)
    currentJob = job
    helper.command = ["python3", "-I", service.helperPath].concat(job.args)
    helper.stdinEnabled = job.input !== undefined && job.input !== null
    helper.running = true
  }

  Process {
    id: helper
    stdout: StdioCollector { id: helperOut }
    stderr: StdioCollector { id: helperErr }
    onStarted: {
      var job = service.currentJob
      if (job && job.input !== undefined && job.input !== null) {
        helper.write(job.input)
        helper.stdinEnabled = false
      }
    }
    onExited: function(exitCode) {
      var job = service.currentJob
      service.currentJob = null
      var data = null
      try {
        data = JSON.parse(String(helperOut.text || "null"))
      } catch (e) {
        data = null
      }
      if (job && job.done) job.done(exitCode, data, String(helperErr.text || ""))
      Qt.callLater(service.pump)
    }
  }

  function errorText(data, stderr) {
    if (data && Array.isArray(data.errors) && data.errors.length) return data.errors.join("; ")
    return String(stderr || "").trim().split("\n").pop() || "The display helper failed"
  }

  // ---------------------------------------------------------------- state
  function refresh(then) {
    if (then) refreshWaiters = refreshWaiters.concat([then])
    if (refreshQueued) return
    refreshQueued = true
    run(["state"], null, function(code, data, stderr) {
      service.refreshQueued = false
      if (code === 0 && data && Array.isArray(data.monitors)) {
        service.monitors = data.monitors
        service.memory = Model.normalizeMemory(data.memory)
        service.lidClosed = data.lidClosed === true
        service.loaded = true
        service.forgetGoneMonitors()
      } else {
        service.lastError = service.errorText(data, stderr)
      }
      var waiters = service.refreshWaiters
      service.refreshWaiters = []
      for (var i = 0; i < waiters.length; i++) waiters[i]()
      service.refreshed()
    })
  }

  function forgetGoneMonitors() {
    var present = {}
    for (var i = 0; i < livePlan.length; i++) present[livePlan[i].fp] = true
    var off = {}
    var misses = {}
    for (var fp in ddcOff) if (present[fp]) off[fp] = ddcOff[fp]
    for (var mp in ddcMisses) if (present[mp]) misses[mp] = ddcMisses[mp]
    ddcOff = off
    ddcMisses = misses
  }

  // ---------------------------------------------------------------- per-monitor side effects
  property var brightnessRestoreQueue: []

  Process { id: textSizeApplyProc }
  Process {
    id: brightnessRestoreProc
    onRunningChanged: if (!running) service.pumpBrightnessRestore()
  }

  function pumpBrightnessRestore() {
    if (brightnessRestoreProc.running || brightnessRestoreQueue.length === 0) return
    var next = brightnessRestoreQueue[0]
    brightnessRestoreQueue = brightnessRestoreQueue.slice(1)
    brightnessRestoreProc.command = ["omarchy-brightness-display", "--no-osd", "--monitor", next.name, next.pct + "%"]
    brightnessRestoreProc.running = true
  }

  // Apply per-monitor remembered brightness, text size and night light after a
  // hotplug reconcile or panel Remember commit.
  function applyMonitorPreferences(plan, preferredIndex, restoreBrightness) {
    if (!Array.isArray(plan) || !plan.length) return
    if (restoreBrightness) {
      var queue = []
      for (var i = 0; i < plan.length; i++) {
        var o = plan[i]
        if (o && o.enabled && !o.autoOff && o.brightness !== null && o.brightness !== undefined)
          queue.push({ name: o.name, pct: Math.round(Number(o.brightness)) })
      }
      if (queue.length) {
        brightnessRestoreQueue = queue
        pumpBrightnessRestore()
      }
    }
    var lead = Model.primaryActiveOutput(plan, preferredIndex)
    if (!lead) return
    if (lead.textSize !== null && lead.textSize !== undefined && Number(lead.textSize) !== Style.font.baseSize) {
      textSizeApplyProc.command = ["omarchy-display-text-size", String(Math.round(Number(lead.textSize)))]
      if (!textSizeApplyProc.running) textSizeApplyProc.running = true
    }
    var nl = bar && bar.shell ? bar.shell.firstPartyServiceFor("omarchy.nightlight") : null
    if (nl && lead.nightLight) {
      if (lead.nightLight.enabled) {
        if (typeof nl.applyTemperature === "function") nl.applyTemperature(lead.nightLight.kelvin)
        else if (typeof nl.setNightlight === "function") nl.setNightlight(true)
      } else if (nl.enabled && typeof nl.setNightlight === "function") {
        nl.setNightlight(false)
      }
    }
  }

  // ---------------------------------------------------------------- automatic
  function reconcile() {
    if (!loaded || confirming || !Model.hasMemory(memory) || monitors.length === 0) return
    var plan = Model.derive(monitors, memory, currentAutoOff)
    if (Model.planMatchesLive(plan, livePlan)) return
    var signature = JSON.stringify(plan)
    var step = Model.autoApplyStep(lastAuto, signature, Date.now())
    lastAuto = step.last
    if (!step.allowed) return
    run(["apply"], signature, function(code, data, stderr) {
      service.fallbacks = data && Array.isArray(data.fallbacks) ? data.fallbacks : []
      service.lastError = code === 0 ? "" : service.errorText(data, stderr)
      // Keep monitors.lua in step so a config reload shows the same layout.
      // The memory itself only changes on Remember.
      if (code === 0) {
        service.applyMonitorPreferences(plan, -1, true)
        service.run(["persist"], JSON.stringify({ plan: plan }), null)
      }
      service.refresh()
    })
  }

  // Monitors connecting arrive as several events a few hundred milliseconds
  // apart (a dock brings up each of its outputs in turn), so wait for them to
  // settle and act once on the final set.
  Timer {
    id: settle
    interval: 1200
    repeat: false
    onTriggered: service.refresh(service.reconcile)
  }

  Connections {
    target: Hyprland
    function onRawEvent(event) {
      var name = event ? String(event.name || "") : ""
      if (name === "monitoradded" || name === "monitoraddedv2" || name === "monitorremoved"
          || name === "monitorremovedv2" || name === "configreloaded")
        settle.restart()
    }
  }

  Timer {
    interval: 1500
    running: true
    repeat: false
    onTriggered: service.refresh(service.reconcile)
  }

  // ---------------------------------------------------------------- DDC/CI
  readonly property var watchedOutputs: {
    var out = []
    var mem = Model.normalizeMemory(memory)
    for (var i = 0; i < livePlan.length; i++) {
      var o = livePlan[i]
      var profile = mem.monitors[o.fp]
      if (!o.internal && profile && profile.ddcWatch && profile.ddcInput) out.push({ name: o.name, fp: o.fp, input: profile.ddcInput })
    }
    return out
  }

  function pollInputs() {
    var watched = watchedOutputs
    if (!watched.length || currentJob) return
    run(["ddc-poll"].concat(watched.map(function(w) { return w.name })), null, function(code, data) {
      if (code !== 0 || !data) return
      var off = {}
      var misses = {}
      for (var k in service.ddcOff) off[k] = service.ddcOff[k]
      var changed = false
      for (var i = 0; i < watched.length; i++) {
        var w = watched[i]
        var step = Model.ddcStep(service.ddcMisses[w.fp], data[w.name], w.input)
        misses[w.fp] = step.misses
        if (step.off !== !!off[w.fp]) changed = true
        if (step.off) off[w.fp] = true
        else delete off[w.fp]
      }
      service.ddcMisses = misses
      if (!changed) return
      service.ddcOff = off
      service.refresh(service.reconcile)
    })
  }

  Timer {
    interval: 5000
    repeat: true
    running: service.watchedOutputs.length > 0 && !service.confirming
    onTriggered: service.pollInputs()
  }

  // ---------------------------------------------------------------- Apply
  // `plan` is the panel's full arrangement. Applying it saves each monitor's
  // settings and this combination's layout; a mode change first waits for
  // the user to keep it.
  function applyFromPanel(plan, preferredIndex) {
    if (confirming || !plan || !plan.length) return
    var before = livePlan
    var mustConfirm = Model.needsConfirmation(plan, before)
    var nextMemory = Model.remember(memory, plan, new Date().toISOString())
    run(["apply"], JSON.stringify(plan), function(code, data, stderr) {
      service.fallbacks = data && Array.isArray(data.fallbacks) ? data.fallbacks : []
      if (code !== 0) {
        service.lastError = service.errorText(data, stderr)
        // Part of the plan may have landed; put back what was there.
        service.run(["apply"], JSON.stringify(before), function() { service.refresh() })
        return
      }
      service.lastError = ""
      if (mustConfirm) {
        service.pendingCommit = { plan: plan, memory: nextMemory, preferredIndex: preferredIndex }
        service.revertPlan = before
        service.confirmSeconds = 10
        service.confirming = true
        confirmTimer.restart()
        service.refresh()
      } else {
        service.commit(plan, nextMemory, preferredIndex)
      }
    })
  }

  function commit(plan, nextMemory, preferredIndex) {
    memory = nextMemory
    applyMonitorPreferences(plan, preferredIndex, false)
    run(["persist"], JSON.stringify({ plan: plan, memory: nextMemory }), function(code, data, stderr) {
      service.lastError = code === 0 ? "" : service.errorText(data, stderr)
      service.refresh()
    })
  }

  function keep() {
    if (!confirming) return
    confirmTimer.stop()
    confirming = false
    var pending = pendingCommit
    pendingCommit = null
    revertPlan = null
    if (pending) commit(pending.plan, pending.memory, pending.preferredIndex)
  }

  function revert() {
    if (!confirming) return
    confirmTimer.stop()
    confirming = false
    var previous = revertPlan
    pendingCommit = null
    revertPlan = null
    if (previous) run(["apply"], JSON.stringify(previous), function() { service.refresh() })
  }

  Timer {
    id: confirmTimer
    interval: 1000
    repeat: true
    onTriggered: {
      service.confirmSeconds = service.confirmSeconds - 1
      if (service.confirmSeconds <= 0) service.revert()
    }
  }

  function identify() {
    identifying = true
    identifyTimer.restart()
  }

  Timer {
    id: identifyTimer
    interval: 3000
    onTriggered: service.identifying = false
  }

  function numberFor(name) {
    for (var i = 0; i < livePlan.length; i++) if (livePlan[i].name === name) return i + 1
    return 0
  }

  // ---------------------------------------------------------------- overlays
  readonly property color overlayForeground: bar ? bar.foreground : Commons.Color.popups.text
  readonly property string overlayFont: bar ? bar.fontFamily : Style.font.family

  Variants {
    model: Quickshell.screens

    PanelWindow {
      id: identifyWindow
      required property var modelData
      screen: modelData
      visible: service.identifying && service.numberFor(modelData.name) > 0
      color: "transparent"
      exclusionMode: ExclusionMode.Ignore
      WlrLayershell.layer: WlrLayer.Overlay
      WlrLayershell.keyboardFocus: WlrKeyboardFocus.None
      WlrLayershell.namespace: "dicey-bar-displays-identify"
      implicitWidth: identifyCard.implicitWidth
      implicitHeight: identifyCard.implicitHeight

      Rectangle {
        id: identifyCard
        anchors.fill: parent
        implicitWidth: Math.max(identifyColumn.implicitWidth + Style.space(64), Style.space(220))
        implicitHeight: identifyColumn.implicitHeight + Style.space(40)
        radius: Style.cornerRadius
        color: Commons.Color.popups.background
        border.color: Commons.Color.accent
        border.width: Math.max(2, Style.space(3))

        Column {
          id: identifyColumn
          anchors.centerIn: parent
          spacing: Style.space(4)

          Text {
            anchors.horizontalCenter: parent.horizontalCenter
            textFormat: Text.PlainText
            text: String(service.numberFor(identifyWindow.modelData.name))
            color: Commons.Color.accent
            font.family: service.overlayFont
            font.pixelSize: Style.font.display * 3
            font.bold: true
          }

          Text {
            anchors.horizontalCenter: parent.horizontalCenter
            textFormat: Text.PlainText
            text: String(identifyWindow.modelData.name || "")
            color: Commons.Color.popups.text
            font.family: service.overlayFont
            font.pixelSize: Style.font.title
          }

          Text {
            anchors.horizontalCenter: parent.horizontalCenter
            textFormat: Text.PlainText
            text: String(identifyWindow.modelData.model || "")
            visible: text !== ""
            color: Qt.darker(Commons.Color.popups.text, 1.4)
            font.family: service.overlayFont
            font.pixelSize: Style.font.caption
          }
        }
      }
    }
  }

  // Shown on every screen, so it can still be answered when the new mode
  // leaves one of them black.
  Variants {
    model: Quickshell.screens

    PanelWindow {
      required property var modelData
      screen: modelData
      visible: service.confirming
      color: "transparent"
      exclusionMode: ExclusionMode.Ignore
      WlrLayershell.layer: WlrLayer.Overlay
      WlrLayershell.keyboardFocus: WlrKeyboardFocus.None
      WlrLayershell.namespace: "dicey-bar-displays-confirm"
      implicitWidth: confirmCard.implicitWidth
      implicitHeight: confirmCard.implicitHeight

      Rectangle {
        id: confirmCard
        anchors.fill: parent
        implicitWidth: Math.max(confirmColumn.implicitWidth, Style.space(300)) + Style.space(48)
        implicitHeight: confirmColumn.implicitHeight + Style.space(40)
        radius: Style.cornerRadius
        color: Commons.Color.popups.background
        border.color: Commons.Color.accent
        border.width: Math.max(2, Style.space(2))

        readonly property real _accentLuma: 0.2126 * Commons.Color.accent.r
          + 0.7152 * Commons.Color.accent.g + 0.0722 * Commons.Color.accent.b
        readonly property color _accentInk: _accentLuma > 0.45 ? Commons.Color.background : Commons.Color.foreground

        Column {
          id: confirmColumn
          anchors.centerIn: parent
          spacing: Style.space(12)

          Text {
            anchors.horizontalCenter: parent.horizontalCenter
            textFormat: Text.PlainText
            text: "Keep these display settings?"
            color: Commons.Color.popups.text
            font.family: service.overlayFont
            font.pixelSize: Style.font.title
            font.bold: true
          }

          Text {
            anchors.horizontalCenter: parent.horizontalCenter
            textFormat: Text.PlainText
            text: "Reverting in " + service.confirmSeconds + "s"
            color: Commons.Color.accent
            font.family: service.overlayFont
            font.pixelSize: Style.font.body
            font.bold: true
          }

          Row {
            anchors.horizontalCenter: parent.horizontalCenter
            spacing: Style.space(10)

            Button {
              text: "Revert"
              bordered: true
              foreground: Commons.Color.popups.text
              fontFamily: service.overlayFont
              onClicked: service.revert()
            }

            Rectangle {
              id: keepPill
              implicitWidth: keepLabel.implicitWidth + Style.space(24)
              implicitHeight: keepLabel.implicitHeight + Style.space(12)
              radius: Style.cornerRadius
              color: keepMouse.containsMouse ? Qt.lighter(Commons.Color.accent, 1.08) : Commons.Color.accent

              Text {
                id: keepLabel
                anchors.centerIn: parent
                textFormat: Text.PlainText
                text: "Keep"
                color: confirmCard._accentInk
                font.family: service.overlayFont
                font.pixelSize: Style.font.body
                font.bold: true
              }

              MouseArea {
                id: keepMouse
                anchors.fill: parent
                hoverEnabled: true
                cursorShape: Qt.PointingHandCursor
                onClicked: service.keep()
              }
            }
          }
        }
      }
    }
  }
}

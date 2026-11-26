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
//
// External monitors are also paused automatically, with no setting, when they
// are switched to another input, and woken when they come back (or stop
// answering). DisplayModel.js holds that DDC/CI state machine; this service
// only runs its read-only probes and carries out what it decides.
Item {
  id: service

  // The bar's theme surface (foreground, font), for the overlays.
  property var bar: null

  readonly property string helperPath: decodeURIComponent(String(Qt.resolvedUrl("display_ctl.py")).replace(/^(file|qs):\/\//, ""))

  property var monitors: []
  property var memory: ({})
  property bool lidClosed: false
  property bool loaded: false
  property var liveNightLight: ({ enabled: false, kelvin: 4000 })
  // Monitors left off for now (lid shut, switched to another input), keyed by
  // fingerprint, and the current state as a plan with those marked.
  readonly property var currentAutoOff: Model.autoOffFor(Model.planFromLive(monitors, memory), lidClosed, ddcOff)
  readonly property var livePlan: Model.planFromLive(monitors, memory, currentAutoOff, ddcPauseModes, appliedParks)
  // Mirror-parks this service applied ({ connector: [target, ...] }), recorded
  // before each apply and narrowed to what Hyprland shows on every refresh, so
  // a live park (paused, woken and not yet undone, or left behind by a failed
  // repark) is never taken for a mirror the user set up, and a user's mirror
  // is never taken for a park. Live only, never saved.
  property var appliedParks: ({})
  // The newest DPMS state per enabled monitor ({ name: on }), from refresh
  // and from every ddc-read, and whether the whole desktop is DPMS-idle.
  property var liveDpms: ({})
  property bool liveGlobalIdle: false
  readonly property var liveMonitors: Model.ddcLiveDpms(monitors, liveDpms)

  // DDC/CI auto-pause: learned inputs (ddc.json), one state per external
  // monitor, and the pauses derived from those states alone, so a pause can
  // never outlive the state behind it.
  property var ddcMemory: ({ version: 1, monitors: {} })
  property var ddcStates: ({})
  readonly property var ddcOff: Model.ddcOffMap(ddcStates)
  // How each paused monitor is kept off: "mirror" (HDMI SCDC: parked lit as a
  // mirror, live only) or "disable" (DDC/CI). Never decides whether a live
  // mirror is a park; that is `appliedParks`.
  readonly property var ddcPauseModes: Model.ddcPauseModes(ddcStates)
  // Bumped whenever an in-flight reading must be ignored (wake, forget, apply).
  property int ddcPollEpoch: 0
  property int ddcPollStartEpoch: 0
  // Connectors seen plugged in since the last refresh; they relearn their input.
  property var hotpluggedConnectors: ({})
  // When each connector last disconnected, so brief HPD pulses on input switch
  // are never mistaken for a physical cable swap that overwrites ddc.json.
  property var removedConnectorsAt: ({})
  // Cumulative suspend time reported by CLOCK_BOOTTIME - CLOCK_MONOTONIC (ms).
  property double lastSuspendMs: -1
  property double resumeAt: 0
  property bool reconcilePending: false
  // A deferred hotplug/startup reconcile still owes monitors.lua a write;
  // automatic DDC pause/wake reconciles are live only and never persist.
  property bool reconcileNeedsPersist: false
  property bool reconcileInFlight: false
  property var reconcileIdleWaiters: []
  readonly property bool wakePending: reconcileInFlight
    || (reconcilePending && !liveGlobalIdle && loaded && Model.hasMemory(memory)
        && Model.planWakes(Model.derive(monitors, memory, currentAutoOff, ddcPauseModes, appliedParks), livePlan))

  // Modes Hyprland did not take on the last apply, usually link bandwidth.
  property var fallbacks: []
  property string lastError: ""

  // After an Apply that changes a mode, rotation, HDR, mirror, or power state.
  property bool applyInFlight: false
  property bool persistInFlight: false
  // A safety rollback is running: no reconcile, no DDC read, no reading kept.
  property bool revertInFlight: false
  property bool confirming: false
  property int confirmSeconds: 0
  property var pendingCommit: null
  property var revertPlan: null
  property var revertPrefs: null

  property bool identifying: false

  // Automatic applies of the same plan are not retried in a tight loop when
  // Hyprland keeps reporting something else (for instance a mode it cannot
  // drive), so record the last one.
  property var lastAuto: null
  // Tracks which monitor topology last had its saved preferences restored so
  // login/hotplug restores brightness/text/nightlight even when monitors.lua
  // already matched the geometry.
  property string lastPrefsTopology: ""

  signal refreshed()
  // Emitted on confirmed serialized brightness read/step/set for a monitor.
  // source: "read" | "step" | "set"; prevPct: prior known brightness (or 0).
  signal brightnessUpdated(string monitorName, int pct, string source, int prevPct)
  // Emitted synchronously when a scroll step is queued so open panels track the edit immediately.
  signal brightnessStepQueued(string monitorName)
  // Emitted when a pre-write baseline read finishes (even if a write was queued after it).
  signal brightnessBaseline(string monitorName, int pct)
  signal brightnessReadFailed(string monitorName)
  // Emitted when a panel Apply commits or rolls back so open panels advance or restore baselines cleanly.
  signal panelApplyCommitted()
  signal panelApplyReverted(var revertedSnap)

  // ---------------------------------------------------------------- jobs
  // Helper calls run one at a time, in order. `runFront` prioritizes safety
  // rollbacks ahead of queued refreshes or night-light updates.
  property var jobs: []
  property var currentJob: null
  property var refreshWaiters: []
  property bool refreshQueued: false

  function run(args, input, done) {
    jobs = jobs.concat([{ args: args, input: input, done: done || null }])
    pump()
  }

  function runFront(args, input, done) {
    jobs = [{ args: args, input: input, done: done || null }].concat(jobs)
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
    if (!helper.running) {
      // Spawn failed synchronously without firing onExited; never wedge queue.
      service.currentJob = null
      if (job && job.done) job.done(1, null, "Failed to start display helper")
      Qt.callLater(service.pump)
    }
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

  function hasSavedProfile(fp) {
    return !!(memory && memory.monitors && fp && Object.prototype.hasOwnProperty.call(memory.monitors, fp))
  }

  // ---------------------------------------------------------------- state
  function refresh(then) {
    if (then) refreshWaiters = refreshWaiters.concat([then])
    if (refreshQueued) return
    refreshQueued = true
    run(["state"], null, function(code, data, stderr) {
      service.refreshQueued = false
      var refreshOk = code === 0 && data && Array.isArray(data.monitors)
      if (refreshOk) {
        var previousNames = null
        if (service.loaded) {
          previousNames = {}
          for (var p = 0; p < service.monitors.length; p++)
            if (service.monitors[p] && service.monitors[p].name) previousNames[service.monitors[p].name] = true
        }
        service.monitors = data.monitors
        var dpms = {}
        for (var d = 0; d < data.monitors.length; d++) {
          var dm = data.monitors[d]
          if (dm && dm.name && dm.disabled !== true) dpms[dm.name] = dm.dpmsStatus !== false
        }
        service.liveDpms = dpms
        service.liveGlobalIdle = Model.ddcGlobalIdle(data.monitors)
        var pruned = Model.pruneAppliedParks(service.appliedParks, data.monitors)
        if (pruned.changed) service.appliedParks = pruned.parks
        service.memory = Model.normalizeMemory(data.memory)
        service.lidClosed = data.lidClosed === true
        if (data.nightLight && typeof data.nightLight === "object") {
          var prevK = service.liveNightLight && service.liveNightLight.kelvin >= 1500 && service.liveNightLight.kelvin <= 6400
            ? service.liveNightLight.kelvin : 4000
          var k = Number(data.nightLight.kelvin)
          service.liveNightLight = {
            enabled: data.nightLight.enabled === true,
            kelvin: (data.nightLight.enabled === true && isFinite(k) && k >= 1500 && k <= 6400) ? Math.round(k) : prevK
          }
        }
        if (data.ddcMemory && typeof data.ddcMemory === "object")
          service.ddcMemory = Model.normalizeDdcMemory(data.ddcMemory)
        service.loaded = true
        service.checkSuspendClock(data.suspendMs, Date.now())
        service.syncDdcStates(previousNames)
      } else {
        service.lastError = service.errorText(data, stderr)
      }
      var waiters = service.refreshWaiters
      service.refreshWaiters = []
      for (var i = 0; i < waiters.length; i++) waiters[i](refreshOk)
      if (refreshOk && service.reconcilePending) service.reconcile(true)
      service.refreshed()
    })
  }

  // Re-probe: monitors that stopped watching (unsupported, suspended,
  // snoozed, waiting) start over and may learn the input they show now.
  function reprobe(then) {
    ddcStates = Model.ddcReprobe(ddcStates, Date.now())
    ddcPollEpoch = ddcPollEpoch + 1
    refresh(function(ok) {
      if (ok) service.reconcile(true)
      if (then) then()
    })
  }

  // ---------------------------------------------------------------- DDC/CI auto-pause
  // The layout with no monitor paused for an input switch: what monitors.lua
  // holds, so pausing and waking never rewrite it (and never trigger a reload).
  function unpausedPlan(mem) {
    var m = mem || memory
    var identified = Model.planFromLive(monitors, m, {})
    return Model.derive(monitors, m, Model.autoOffFor(identified, lidClosed, {}), ddcPauseModes, appliedParks)
  }

  // Records the parks an apply of `plan` sets: before the apply is queued (so
  // the old and new park are both the bar's while it runs, even if it fails)
  // and again once it has run (a refresh in between may have narrowed it).
  function noteAppliedParks(plan) {
    appliedParks = Model.recordAppliedParks(appliedParks, plan)
  }

  // `plan` for monitors.lua: input-paused outputs go back to their unpaused
  // entry; every other output keeps exactly what `plan` (e.g. an edit) says.
  function persistablePlan(plan, mem) {
    return Model.persistablePlan(plan, unpausedPlan(mem))
  }

  // Keeps one state per connected external monitor after every refresh, and
  // wakes any paused monitor it is no longer safe to keep dark.
  function syncDdcStates(previousNames) {
    var now = Date.now()
    var plugged = {}
    for (var h in hotpluggedConnectors) plugged[h] = true
    hotpluggedConnectors = ({})
    var currentNames = {}
    for (var ci = 0; ci < monitors.length; ci++) {
      var cn = monitors[ci] ? String(monitors[ci].name || "") : ""
      if (cn) currentNames[cn] = true
    }
    if (previousNames && now - resumeAt > 30000) {
      for (var i = 0; i < monitors.length; i++) {
        var name = monitors[i] ? String(monitors[i].name || "") : ""
        if (name && previousNames[name] !== true && Model.ddcIsHotplug(removedConnectorsAt[name], now))
          plugged[name] = true
      }
    }
    var nextRemoved = {}
    for (var r in removedConnectorsAt) {
      if (!currentNames[r]) nextRemoved[r] = removedConnectorsAt[r]
    }
    if (previousNames) {
      for (var prev in previousNames) {
        if (!currentNames[prev] && !nextRemoved[prev]) nextRemoved[prev] = now
      }
    }
    removedConnectorsAt = nextRemoved
    var before = Model.ddcOffMap(ddcStates)
    var identified = Model.planFromLive(monitors, memory, {})
    var next = Model.ddcPrune(ddcStates, identified, plugged, now)
    // A read in flight from before a connect, disconnect, port move or
    // same-port replug belongs to the old connection: drop it.
    if (Model.ddcConnectionsChanged(ddcStates, next, plugged)) ddcPollEpoch = ddcPollEpoch + 1
    var ready = Model.hasMemory(memory)
    var plan = unpausedPlan()
    for (var fp in next) {
      if (!next[fp] || next[fp].state !== "PAUSED") continue
      // A desktop gone DPMS-idle is never a reason to wake (ddcCanPause).
      if (!ready || !Model.ddcCanPause(plan, fp, lidClosed, Model.ddcOffMap(next), liveMonitors, appliedParks))
        next = Model.ddcWake(next, fp, now, false)
    }
    ddcStates = next
    var heldNow = Model.ddcHold(ddcStates, ddcPollableMap(), now)
    if (heldNow.changed) ddcStates = heldNow.states
    var after = Model.ddcOffMap(ddcStates)
    if (JSON.stringify(before) !== JSON.stringify(after)) reconcilePending = true
    // A mirror-parked monitor whose target went away, changed, or can no
    // longer be parked on is re-parked (or woken) by the next reconcile.
    var modes = Model.ddcPauseModes(next)
    var off = Model.autoOffFor(Model.planFromLive(monitors, memory), lidClosed, after)
    if (ready && Model.parkedMirrorsStale(Model.derive(monitors, memory, off, modes, appliedParks),
                                          Model.planFromLive(monitors, memory, off, modes, appliedParks)))
      reconcilePending = true
  }

  function noteMonitorRemoved(connector) {
    var name = String(connector || "")
    if (!name || !loaded) return
    var next = {}
    for (var k in removedConnectorsAt) next[k] = removedConnectorsAt[k]
    if (!next[name]) next[name] = Date.now()
    removedConnectorsAt = next
  }

  // A monitor that shows up re-enabled after being off (our own wake, or the
  // user turning it back on) or after a brief HPD pulse (<8s) is not a fresh
  // plug-in; only a newly connected or genuinely unplugged connector relearns.
  function noteMonitorAdded(connector) {
    var name = String(connector || "")
    var now = Date.now()
    if (!name || !loaded || now - resumeAt < 30000) return
    var removedAt = Number(removedConnectorsAt[name] || 0)
    if (!Model.ddcIsHotplug(removedAt, now)) return
    for (var i = 0; i < monitors.length; i++) {
      if (monitors[i] && monitors[i].name === name) {
        if (monitors[i].disabled === true || removedAt <= 0) return
      }
    }
    var next = {}
    for (var k in hotpluggedConnectors) next[k] = true
    next[name] = true
    hotpluggedConnectors = next
  }

  // Suspend shows up directly as CLOCK_BOOTTIME - CLOCK_MONOTONIC advancing
  // (catches any suspend regardless of duration or poll interval, without
  // NTP wall-clock false positives): paused monitors wake and every monitor
  // re-probes without relearning.
  function checkSuspendClock(suspendMs, now) {
    var ms = Number(suspendMs)
    if (!isFinite(ms) || ms < 0) return false
    if (lastSuspendMs < 0) {
      lastSuspendMs = ms
      return false
    }
    // Never move the high-water mark backward on an older out-of-order sample.
    if (ms - lastSuspendMs < 500) return false
    lastSuspendMs = ms
    resumeAt = now
    hotpluggedConnectors = ({})
    var wasPaused = Object.keys(Model.ddcOffMap(ddcStates)).length > 0
    ddcStates = Model.ddcResume(ddcStates, now)
    ddcPollEpoch = ddcPollEpoch + 1
    // The wake must survive a settle or confirmation that skips this refresh.
    if (wasPaused) reconcilePending = true
    return true
  }

  // Which monitors can be read now. While the whole desktop is DPMS-idle no
  // unpaused monitor is (its evidence is held), and paused ones stay paused.
  function ddcPollableMap() {
    var out = {}
    var plan = unpausedPlan()
    var lm = liveMonitors
    var intended = {}
    for (var p = 0; p < plan.length; p++) intended[plan[p].fp] = plan[p]
    var identified = Model.planFromLive(monitors, memory, {})
    for (var i = 0; i < identified.length; i++) {
      var o = identified[i]
      if (!o || o.internal) continue
      var st = ddcStates[o.fp]
      // Paused monitors are always read: dark (DDC/CI) or mirror-parked
      // (SCDC, which Hyprland reports as a mirror).
      if (st && st.state === "PAUSED") {
        out[o.fp] = true
        continue
      }
      var m = lm[i]
      var want = intended[o.fp]
      out[o.fp] = !liveGlobalIdle && !!m && m.disabled !== true && m.dpmsStatus !== false
        && !(want && want.enabled === false) && !o.mirrorOf
    }
    return out
  }

  property var ddcPollTargets: []
  property bool ddcPollBusy: false
  property bool ddcPollExited: false
  property bool ddcPollStreamDone: false
  property int ddcPollExitCode: 0
  property string ddcPollText: ""

  function pollInputs() {
    if (!loaded || !Model.hasMemory(memory)) return
    if (confirming || applyInFlight || persistInFlight || revertInFlight || brightnessBusy || ddcPollBusy || ddcPollProc.running) return
    var now = Date.now()
    var pollable = ddcPollableMap()
    var held = Model.ddcHold(ddcStates, pollable, now)
    if (held.changed) ddcStates = held.states
    // While idle every monitor is still asked at its pace: the helper reads
    // no bus then, and its answer is how the end of the idle is noticed.
    var duePollable = pollable
    if (liveGlobalIdle) {
      duePollable = {}
      for (var fp in ddcStates) duePollable[fp] = true
    }
    var due = Model.ddcDue(ddcStates, duePollable, now)
    var targets = []
    for (var i = 0; i < due.length; i++) {
      var st = ddcStates[due[i]]
      if (!st || !st.connector) continue
      targets.push({ fp: due[i], name: st.connector, mode: String(st.mode || "") })
    }
    // Nothing due, but a DPMS wake is awaited (idle, or one monitor asleep on
    // its own): a power-state-only read (no connector, no I2C) notices it.
    if (!targets.length && !Model.ddcNeedsDpmsProbe(ddcStates, liveDpms, liveGlobalIdle)) return
    ddcPollTargets = targets
    ddcPollStartEpoch = ddcPollEpoch
    ddcPollBusy = true
    ddcPollExited = false
    ddcPollStreamDone = false
    ddcPollExitCode = 0
    ddcPollText = ""
    // Each connector with the bus a detect found for it, `@scdc` for monitors
    // watched through HDMI SCDC; `--detect` only for DDC connectors still
    // probing with no bus known.
    ddcPollProc.command = ["python3", "-I", service.helperPath, "ddc-read"]
      .concat(Model.ddcReadArgs(targets, ddcStates))
    ddcPollProc.running = true
    if (!ddcPollProc.running) {
      ddcPollTargets = []
      ddcPollBusy = false
      pumpBrightness()
    }
  }

  function finishDdcPoll() {
    if (!ddcPollBusy || !ddcPollExited || !ddcPollStreamDone) return
    var exitCode = ddcPollExitCode
    var rawText = ddcPollText
    var targets = ddcPollTargets
    ddcPollTargets = []
    ddcPollBusy = false
    ddcPollExited = false
    ddcPollStreamDone = false
    ddcPollText = ""
    var stale = ddcPollStartEpoch !== ddcPollEpoch
      || confirming || applyInFlight || persistInFlight
      || revertInFlight || !Model.hasMemory(memory)
    var data = null
    try {
      data = JSON.parse(String(rawText || "null"))
    } catch (e) {
      data = null
    }
    var good = exitCode === 0 && data && data.readings && typeof data.readings === "object"
    // Hyprland's power state unknown: the helper read nothing, and unknown
    // is never taken for awake (or for a failed read).
    var known = good && data.dpmsKnown !== false
    var wasIdle = liveGlobalIdle
    if (known && data.dpms && typeof data.dpms === "object") {
      var dpms = {}
      for (var k in liveDpms) dpms[k] = liveDpms[k]
      for (var n in data.dpms) dpms[n] = data.dpms[n] !== false
      liveDpms = dpms
    }
    if (known && typeof data.globalIdle === "boolean") liveGlobalIdle = data.globalIdle
    var resumed = good && checkSuspendClock(data.suspendMs, Date.now())
    if (resumed) {
      if (reconcilePending && !liveGlobalIdle) refresh(function(ok) { if (ok) service.reconcile(true) })
    } else if (good && !known) {
      // Nothing was read: no evidence either way.
    } else if (!good && wasIdle) {
      // A failed read during a known idle: paused monitors stay as they are.
    } else if (good && (liveGlobalIdle || wasIdle || !targets.length)) {
      // Idle, or a power-state-only probe (no bus was read): hold every
      // unpaused monitor's evidence and keep paused ones as they are. Back
      // from idle, or a monitor woken from DPMS on its own: a fresh settle
      // grace starts at once (ddcHold), and an idle batch is dropped.
      var held = Model.ddcHold(ddcStates, ddcPollableMap(), Date.now())
      if (held.changed) ddcStates = held.states
      if (wasIdle && !liveGlobalIdle && reconcilePending)
        refresh(function(ok) { if (ok) service.reconcile(true) })
    } else if (!stale) {
      // Sync hold state against the fresh DPMS snapshot first so any output
      // that just went asleep or woke on its own gets an immediate hold/grace.
      var syncHeld = Model.ddcHold(ddcStates, ddcPollableMap(), Date.now())
      if (syncHeld.changed) ddcStates = syncHeld.states
      // A failed read is never dropped: it counts as ambiguous, so a paused
      // monitor still fails open and a pause streak is broken.
      observeReadings(targets, good ? data.readings : Model.ddcFailedReadings(targets))
    }
    pumpBrightness()
    flushBrightnessIdleWaiters()
  }

  // Dedicated Process so a slow I2C read never blocks Apply, Revert, or
  // refresh; it shares the bus with brightness, so the two take turns.
  // Waits for BOTH process exit and stdout EOF (StdioCollector.onStreamFinished)
  // because fast <1ms SCDC reads can fire onExited before stdout finishes flushing.
  Process {
    id: ddcPollProc
    stdout: StdioCollector {
      id: ddcPollOut
      waitForEnd: true
      onStreamFinished: {
        service.ddcPollText = String(ddcPollOut.text || "")
        service.ddcPollStreamDone = true
        service.finishDdcPoll()
      }
    }
    onExited: function(exitCode) {
      service.ddcPollExitCode = exitCode
      service.ddcPollExited = true
      service.finishDdcPoll()
    }
  }

  function observeReadings(targets, readings) {
    var now = Date.now()
    var plan = unpausedPlan()
    var states = ddcStates
    var offChanged = false
    var learns = []
    for (var i = 0; i < targets.length; i++) {
      var t = targets[i]
      var st = states[t.fp]
      if (!st || st.connector !== t.name) continue
      var reading = readings[t.name] || { status: "ambiguous" }
      var res = Model.ddcObserve(states, t.fp, reading, {
        now: now,
        savedL: Model.ddcSavedInput(ddcMemory, t.fp, t.name),
        canPause: Model.ddcCanPause(plan, t.fp, lidClosed, Model.ddcOffMap(states), liveMonitors, appliedParks)
      })
      states = res.states
      if (res.offChanged) offChanged = true
      if (res.learn) learns.push(res.learn)
    }
    ddcStates = states
    for (var l = 0; l < learns.length; l++) learnDdcInput(learns[l])
    if (offChanged) service.reconcile(true)
  }

  function learnDdcInput(learn) {
    var mem = Model.normalizeDdcMemory(ddcMemory)
    mem.monitors[learn.fp] = { input: learn.code, connector: learn.connector, learnedAt: "" }
    ddcMemory = mem
    run(["ddc-learn", learn.connector, learn.fp, learn.code], null, function(code, data, stderr) {
      // A Forget queued behind this write supplies the final ddc.json.
      if (!service.ddcStates[learn.fp]) return
      if (code === 0 && data && data.ddcMemory) service.ddcMemory = Model.normalizeDdcMemory(data.ddcMemory)
      else service.lastError = service.errorText(data, stderr)
    })
  }

  // Wake by hand (the panel's Wake now / power switch, or IPC): the monitor
  // stays on, snoozed, until this PC's input is seen again.
  function wakeDisplay(fp) {
    var st = fp ? ddcStates[fp] : null
    if (!st || st.state !== "PAUSED") return false
    ddcStates = Model.ddcWake(ddcStates, fp, Date.now(), true)
    ddcPollEpoch = ddcPollEpoch + 1
    // A wake by hand is never held back by the hotplug retry limit.
    lastAuto = null
    service.reconcile(true)
    return true
  }

  function wakeAllDisplays() {
    var now = Date.now()
    var next = ddcStates
    var count = 0
    for (var fp in ddcStates) {
      if (!ddcStates[fp] || ddcStates[fp].state !== "PAUSED") continue
      next = Model.ddcWake(next, fp, now, true)
      count++
    }
    if (!count) return 0
    ddcStates = next
    ddcPollEpoch = ddcPollEpoch + 1
    lastAuto = null
    service.reconcile(true)
    return count
  }

  // Read-only status for the panel: { state, mode, code, pausedCode, label, pausedLabel, text }.
  function ddcStatusFor(fp) {
    var st = fp ? ddcStates[fp] : null
    var status = {
      state: st ? String(st.state) : "",
      mode: st ? String(st.mode || "") : "",
      code: st ? String(st.expected || "") : "",
      pausedCode: st ? String(st.pausedCode || "") : "",
      connector: st ? String(st.connector || "") : ""
    }
    status.label = Model.ddcInputLabel(status.code)
    status.pausedLabel = Model.ddcInputLabel(status.pausedCode)
    status.text = Model.ddcStatusText(status, Model.hasMemory(memory))
    return status
  }

  Timer {
    id: ddcTimer
    interval: Math.max(1000, Model.ddcPollInterval(service.ddcStates, 0, service.liveDpms, service.liveGlobalIdle) || 5000)
    repeat: true
    running: true
    onTriggered: {
      if (service.reconcilePending && !service.reconcileInFlight && !service.liveGlobalIdle
          && !service.confirming && !service.applyInFlight && !service.persistInFlight && !service.revertInFlight)
        service.refresh(function(ok) { if (ok) service.reconcile(true) })
      service.pollInputs()
    }
  }

  // ---------------------------------------------------------------- unified brightness & text-size queues
  // Single serialized, generation-stamped queue ({ name, pct, gen } |
  // { name, delta, gen } | { name, readOnly, baseline?, afterStep?, gen })
  // so panel reads, slider drags, bar-icon scroll steps, Sync all, and IPC
  // never race or overwrite newer values with stale readbacks.
  property var brightnessQueue: []
  property var activeBrightnessJob: null
  property var brightnessGen: ({})
  property var liveBrightness: ({})
  property var brightnessIdleWaiters: []
  readonly property bool brightnessBusy: brightnessProc.running || brightnessQueue.length > 0
  property int pendingTextSize: 0

  Process {
    id: textSizeProc
    onExited: {
      if (service.pendingTextSize >= 9 && service.pendingTextSize <= 20) {
        var nextPx = service.pendingTextSize
        service.pendingTextSize = 0
        service.applyTextSize(nextPx)
      }
    }
  }

  function applyTextSize(px) {
    var clean = Model.cleanTextSize(px)
    if (clean === null) return
    if (textSizeProc.running) {
      pendingTextSize = clean
      return
    }
    textSizeProc.command = ["omarchy-display-text-size", String(clean)]
    textSizeProc.running = true
  }

  function flushBrightnessIdleWaiters() {
    if (brightnessProc.running || brightnessQueue.length > 0 || brightnessIdleWaiters.length === 0) return
    var waiters = brightnessIdleWaiters
    brightnessIdleWaiters = []
    for (var i = 0; i < waiters.length; i++) waiters[i]()
  }

  function whenBrightnessIdle(fn) {
    if (!fn) return
    if (!brightnessProc.running && brightnessQueue.length === 0) {
      fn()
      return
    }
    brightnessIdleWaiters = brightnessIdleWaiters.concat([fn])
  }

  function recordLiveBrightness(name, pct) {
    var next = Object.assign({}, liveBrightness)
    next[name] = pct
    liveBrightness = next
  }

  Process {
    id: brightnessProc
    stdout: StdioCollector { id: brightnessOut; waitForEnd: true }
    onExited: function(exitCode) {
      var doneJob = service.activeBrightnessJob
      service.activeBrightnessJob = null
      if (doneJob) {
        var curGen = Number(service.brightnessGen[doneJob.name]) || 0
        var isCurrent = doneJob.gen === curGen
        if (doneJob.readOnly) {
          var val = exitCode === 0 ? parseInt(String(brightnessOut.text || "").trim(), 10) : NaN
          if (isFinite(val)) {
            var clamped = Math.max(1, Math.min(100, val))
            var prev = Object.prototype.hasOwnProperty.call(service.liveBrightness, doneJob.name)
              ? Number(service.liveBrightness[doneJob.name]) : 0
            if (doneJob.baseline) {
              // Baseline ran strictly before any later write; update liveBrightness and notify Discard snapshot.
              service.recordLiveBrightness(doneJob.name, clamped)
              service.brightnessBaseline(doneJob.name, clamped)
            }
            if (isCurrent) {
              service.recordLiveBrightness(doneJob.name, clamped)
              service.brightnessUpdated(doneJob.name, clamped, doneJob.afterStep ? "step" : "read", prev)
            }
          } else if (isCurrent && !doneJob.afterStep) {
            service.brightnessReadFailed(doneJob.name)
          }
        } else if (doneJob.pct !== undefined && exitCode === 0 && isCurrent) {
          var prevSet = Object.prototype.hasOwnProperty.call(service.liveBrightness, doneJob.name)
            ? Number(service.liveBrightness[doneJob.name]) : 0
          service.recordLiveBrightness(doneJob.name, doneJob.pct)
          service.brightnessUpdated(doneJob.name, doneJob.pct, "set", prevSet)
        }
      }
      service.pumpBrightness()
      service.flushBrightnessIdleWaiters()
    }
  }

  function pumpBrightness() {
    // DDC/CI brightness and input reads share the monitor's I2C bus.
    if (brightnessProc.running || brightnessQueue.length === 0 || ddcPollBusy || ddcPollProc.running) return
    var next = brightnessQueue[0]
    brightnessQueue = brightnessQueue.slice(1)
    activeBrightnessJob = next
    if (next.readOnly) {
      brightnessProc.command = ["omarchy-brightness-display", "--monitor", next.name]
    } else if (next.delta !== undefined) {
      var stepArg = next.delta > 0 ? ("+" + next.delta + "%") : (Math.abs(next.delta) + "%-")
      brightnessProc.command = ["omarchy-brightness-display", "--monitor", next.name, stepArg]
    } else {
      brightnessProc.command = ["omarchy-brightness-display", "--no-osd", "--monitor", next.name, next.pct + "%"]
    }
    brightnessProc.running = true
    if (!brightnessProc.running) {
      activeBrightnessJob = null
      Qt.callLater(function() {
        service.pumpBrightness()
        service.flushBrightnessIdleWaiters()
      })
    }
  }

  function requestBrightnessRead(name, baseline) {
    var n = String(name || "").trim()
    if (!n) return false
    var res = Model.brightnessQueueRead(brightnessQueue, brightnessGen, n, !!baseline)
    brightnessQueue = res.queue
    pumpBrightness()
    return true
  }

  function enqueueBrightness(name, pct) {
    var res = Model.brightnessQueueSet(brightnessQueue, brightnessGen, name, pct)
    if (!res.gen) return false
    brightnessGen = res.gens
    brightnessQueue = res.queue
    pumpBrightness()
    return true
  }

  function enqueueBrightnessStep(name, deltaPct) {
    var res = Model.brightnessQueueStep(brightnessQueue, brightnessGen, liveBrightness, name, deltaPct)
    if (!res.gen) return false
    brightnessGen = res.gens
    brightnessQueue = res.queue
    brightnessStepQueued(String(name || "").trim())
    pumpBrightness()
    return true
  }

  function discardBrightnessPreviews(restoreMap) {
    var res = Model.brightnessQueueDiscard(brightnessQueue, brightnessGen, activeBrightnessJob, restoreMap)
    brightnessGen = res.gens
    brightnessQueue = res.queue
    pumpBrightness()
  }

  function setAllBrightness(pct, outputs) {
    var v = Number(pct)
    if (!isFinite(v)) return
    var clamped = Math.max(1, Math.min(100, Math.round(v)))
    var list = Array.isArray(outputs) ? outputs : livePlan
    for (var i = 0; i < list.length; i++) {
      var o = list[i]
      if (o && o.enabled && !o.autoOff) enqueueBrightness(o.name, clamped)
    }
  }

  function setNightLight(enabled, kelvin, done) {
    var k = Math.max(1500, Math.min(6400, Math.round(Number(kelvin) || 4000)))
    var args = enabled ? ["nightlight", "on", String(k)] : ["nightlight", "off"]
    run(args, null, function(code, data, stderr) {
      if (code === 0 && data && data.nightLight) {
        var actual = data.nightLight
        var prevK = k
        service.liveNightLight = {
          enabled: actual.enabled === true,
          kelvin: (actual.enabled === true && actual.kelvin >= 1500 && actual.kelvin <= 6400) ? Math.round(actual.kelvin) : prevK
        }
        service.lastError = ""
      } else {
        service.lastError = service.errorText(data, stderr)
      }
      if (done) done(code === 0, data, stderr)
    })
  }

  // Restore per-monitor brightness and per-setup OS-wide text size & night
  // light after a hotplug reconcile or Apply commit.
  function applyMonitorPreferences(plan, savedMemory, restoreSavedPrefs) {
    if (!Array.isArray(plan) || !plan.length) return
    if (restoreSavedPrefs) {
      for (var i = 0; i < plan.length; i++) {
        var o = plan[i]
        if (o && o.enabled && !o.autoOff && o.brightness !== null && o.brightness !== undefined)
          enqueueBrightness(o.name, Math.round(Number(o.brightness)))
      }
    }
    var mem = savedMemory || memory
    var textSize = Model.layoutTextSize(mem, plan)
    if (textSize !== null && textSize !== Style.font.baseSize) {
      applyTextSize(textSize)
    }
    if (restoreSavedPrefs) {
      var savedNl = Model.layoutNightLight(mem, plan)
      if (savedNl && !Model.sameNightLight(savedNl, liveNightLight)) {
        setNightLight(savedNl.enabled, savedNl.kelvin, null)
      }
    }
  }

  // ---------------------------------------------------------------- memory actions
  function forgetMonitor(fp, done) {
    if (!fp || confirming || applyInFlight || revertInFlight) return
    // Any reading in flight belongs to the monitor as it was before Forget.
    ddcPollEpoch = ddcPollEpoch + 1
    var nextMemory = Model.forgetMonitor(memory, fp)
    var now = Date.now()
    var before = Model.ddcOffMap(ddcStates)
    var states = {}
    for (var k in ddcStates) if (k !== fp) states[k] = ddcStates[k]
    // With nothing remembered, automation stops: nothing may stay paused.
    if (!Model.hasMemory(nextMemory)) {
      for (var p in states) if (states[p] && states[p].state === "PAUSED") states = Model.ddcWake(states, p, now, false)
    }
    ddcStates = states
    var previousDdcMemory = ddcMemory
    var ddcMem = Model.normalizeDdcMemory(ddcMemory)
    delete ddcMem.monitors[fp]
    ddcMemory = ddcMem
    var after = Model.ddcOffMap(states)
    var woke = Object.keys(before).some(function(f) { return !after[f] })
    var payload = { memory: nextMemory, forgetDdcFp: fp }
    if (!Model.hasMemory(nextMemory)) payload.removeManagedBlock = true
    var persistNow = function() {
      service.run(["persist"], JSON.stringify(payload), function(code, data, stderr) {
        service.persistInFlight = false
        if (code === 0) {
          service.memory = nextMemory
          if (data && data.ddcMemory) service.ddcMemory = Model.normalizeDdcMemory(data.ddcMemory)
          service.lastError = ""
        } else {
          service.lastError = service.errorText(data, stderr)
        }
        service.refresh(function() {
          if (done) done(code === 0)
        })
      })
    }
    persistInFlight = true
    if (woke) {
      // Turn the woken monitor back on first, and forget only once it is lit:
      // with memory gone, no automatic reconcile could turn it on later.
      var wakePlan = Model.derive(monitors, memory, Model.autoOffFor(Model.planFromLive(monitors, memory), lidClosed, after),
                                  Model.ddcPauseModes(states), appliedParks)
      // monitors.lua gets the whole unpaused layout for what is still
      // remembered, never the live geometry packed around a pause.
      if (Model.hasMemory(nextMemory)) payload.plan = unpausedPlan(nextMemory)
      noteAppliedParks(wakePlan)
      run(["apply"], JSON.stringify(wakePlan), function(code, data, stderr) {
        service.noteAppliedParks(wakePlan)
        if (code === 0) {
          persistNow()
          return
        }
        // Forget is abandoned: memory stays, and the next reconcile retries the wake.
        service.persistInFlight = false
        service.lastError = service.errorText(data, stderr)
        service.ddcMemory = previousDdcMemory
        service.reconcilePending = true
        service.refresh(function() {
          if (done) done(false)
        })
      })
      return
    }
    if (Model.hasMemory(nextMemory) && livePlan.length) payload.plan = unpausedPlan(nextMemory)
    persistNow()
  }

  // ---------------------------------------------------------------- automatic
  function topologySignature(plan) {
    return plan.filter(function(o) { return o && !o.autoOff })
      .map(function(o) { return o.fp })
      .sort()
      .join("\n")
  }

  function flushReconcileIdleWaiters(ready) {
    if (reconcileInFlight || reconcileIdleWaiters.length === 0) return
    var waiters = reconcileIdleWaiters
    reconcileIdleWaiters = []
    for (var i = 0; i < waiters.length; i++) waiters[i](ready === true)
  }

  function whenReconcileIdle(done) {
    if (!done) return
    if (!reconcileInFlight && !wakePending) {
      done(true)
      return
    }
    if (!reconcileInFlight && wakePending) {
      reconcile(true)
      if (!reconcileInFlight) {
        done(!wakePending)
        return
      }
    }
    reconcileIdleWaiters = reconcileIdleWaiters.concat([done])
  }

  // `liveOnly` (automatic DDC pause, wake, resume): apply live and never write
  // monitors.lua, which holds the unpaused layout anyway, so an input switch
  // can never race a panel preview or Forget on disk. Hotplug and startup
  // reconciles (no argument) also persist the layout for the new set.
  function reconcile(liveOnly) {
    if (!loaded || !Model.hasMemory(memory) || monitors.length === 0) return
    if (liveOnly !== true) reconcileNeedsPersist = true
    if (confirming || applyInFlight || persistInFlight || revertInFlight || reconcileInFlight || liveGlobalIdle) {
      // Picked up again once the panel's change has finished or DPMS wakes.
      reconcilePending = true
      return
    }
    reconcilePending = false
    var persistToo = reconcileNeedsPersist
    reconcileNeedsPersist = false
    var plan = Model.derive(monitors, memory, currentAutoOff, ddcPauseModes, appliedParks)
    var topo = topologySignature(plan)
    if (Model.planMatchesLive(plan, livePlan)) {
      // Even when monitors.lua already restored geometry at login, still apply
      // remembered brightness, text size, and night light once per topology.
      if (topo && topo !== lastPrefsTopology) {
        lastPrefsTopology = topo
        applyMonitorPreferences(plan, memory, true)
      }
      return
    }
    var signature = JSON.stringify(plan)
    var step = Model.autoApplyStep(lastAuto, signature, Date.now())
    lastAuto = step.last
    if (!step.allowed) {
      if (persistToo) reconcileNeedsPersist = true
      // A monitor left dark keeps being retried once the limit allows it.
      if (Model.planWakes(plan, livePlan)) reconcilePending = true
      return
    }
    reconcileInFlight = true
    noteAppliedParks(plan)
    run(["apply"], signature, function(code, data, stderr) {
      // Even a failed apply may have parked some outputs; unseen ones are pruned.
      service.noteAppliedParks(plan)
      service.fallbacks = data && Array.isArray(data.fallbacks) ? data.fallbacks : []
      service.lastError = code === 0 ? "" : service.errorText(data, stderr)
      if (code === 0) {
        service.lastPrefsTopology = topo
        service.applyMonitorPreferences(plan, service.memory, true)
        // monitors.lua gets the unpaused layout itself (never the positions a
        // paused plan packed the others into), so an input pause or wake
        // leaves the file (and Hyprland's config) untouched.
        if (persistToo)
          service.run(["persist"], JSON.stringify({ plan: service.unpausedPlan(service.memory) }), null)
      } else {
        // Never lose a wake (or a change) to a failed apply: retry next tick.
        service.reconcilePending = true
        if (persistToo) service.reconcileNeedsPersist = true
      }
      service.refresh(function(refreshOk) {
        if (!refreshOk) service.reconcilePending = true
        service.reconcileInFlight = false
        var confirmed = refreshOk === true && !service.wakePending
        Qt.callLater(function() { service.flushReconcileIdleWaiters(confirmed) })
      })
    })
  }

  // Monitors connecting arrive as several events a few hundred milliseconds
  // apart (a dock brings up each of its outputs in turn), so wait for them to
  // settle and act once on the final set.
  Timer {
    id: settle
    interval: 1200
    repeat: false
    onTriggered: if (!service.confirming) service.refresh(function(ok) { if (ok) service.reconcile() })
  }

  Connections {
    target: Hyprland
    function onRawEvent(event) {
      var name = event ? String(event.name || "") : ""
      if (name === "monitorremoved" || name === "monitorremovedv2") {
        service.noteMonitorRemoved(Model.monitorAddedName(name, event.data))
      }
      if (name === "monitoradded" || name === "monitoraddedv2") {
        service.noteMonitorAdded(Model.monitorAddedName(name, event.data))
      }
      if (name === "monitoradded" || name === "monitoraddedv2" || name === "monitorremoved"
          || name === "monitorremovedv2" || name === "configreloaded")
        settle.restart()
    }
  }

  Timer {
    interval: 1500
    running: true
    repeat: false
    onTriggered: service.refresh(function(ok) { if (ok) service.reconcile() })
  }

  // ---------------------------------------------------------------- Apply
  // `plan` is the panel's full arrangement. Applying it saves each monitor's
  // settings and this combination's layout; a mode/power/rotation/mirror change
  // first waits for the user to keep it, and safely reverts both displays and
  // any previewed OS-wide preferences if rejected or timed out.
  function applyFromPanel(plan, options, revertSnapshot) {
    if (confirming || applyInFlight || !plan || !plan.length) return
    // A mirror-parked monitor never goes out parked on a target the edit
    // turned off or mirrored: it is re-parked, or turned on with none left.
    plan = Model.settleParkedMirrors(plan)
    ddcPollEpoch = ddcPollEpoch + 1
    applyInFlight = true
    var before = livePlan
    var mustConfirm = Model.needsConfirmation(plan, before)
    var opts = options || {}
    noteAppliedParks(plan)
    run(["apply"], JSON.stringify(plan), function(code, data, stderr) {
      service.applyInFlight = false
      service.noteAppliedParks(plan)
      service.fallbacks = data && Array.isArray(data.fallbacks) ? data.fallbacks : []
      if (code !== 0) {
        service.lastError = service.errorText(data, stderr)
        service.restorePreviewPrefs(revertSnapshot)
        service.panelApplyReverted(revertSnapshot)
        service.revertInFlight = true
        service.noteAppliedParks(before)
        service.runFront(["revert"], JSON.stringify(before), function() {
          service.revertInFlight = false
          service.noteAppliedParks(before)
          service.refresh()
        })
        return
      }
      service.lastError = ""
      if (mustConfirm) {
        service.pendingCommit = { plan: plan, options: opts }
        service.revertPlan = before
        service.revertPrefs = revertSnapshot || null
        service.confirmSeconds = 10
        service.confirming = true
        confirmTimer.restart()
        service.refresh()
      } else {
        service.commit(plan, opts)
      }
    })
  }

  function restorePreviewPrefs(snap) {
    if (!snap) return
    if (snap.textSizeEdited && snap.openedTextSize >= 9 && snap.openedTextSize <= 20)
      applyTextSize(snap.openedTextSize)
    if (snap.nightLightEdited && snap.openedNightLight)
      setNightLight(snap.openedNightLight.enabled, snap.openedNightLight.kelvin, null)
    if (snap.openedBrightness) {
      for (var name in snap.openedBrightness)
        enqueueBrightness(name, snap.openedBrightness[name])
    }
  }

  function commit(plan, options) {
    // Recompute against the latest memory at commit time so any concurrent
    // changes aren't lost.
    var nextMemory = Model.remember(memory, plan, new Date().toISOString(), options)
    lastPrefsTopology = topologySignature(plan)
    persistInFlight = true
    ddcPollEpoch = ddcPollEpoch + 1
    // A monitor paused for an input switch keeps its unpaused place in monitors.lua.
    var filePlan = persistablePlan(plan, nextMemory)
    run(["persist"], JSON.stringify({ plan: filePlan, memory: nextMemory }), function(code, data, stderr) {
      service.persistInFlight = false
      if (code === 0) {
        service.memory = nextMemory
        service.lastError = ""
        service.applyMonitorPreferences(plan, nextMemory, false)
        service.panelApplyCommitted()
      } else {
        service.lastError = service.errorText(data, stderr)
      }
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
    revertPrefs = null
    if (pending) commit(pending.plan, pending.options)
  }

  function revert() {
    if (!confirming) return
    confirmTimer.stop()
    confirming = false
    ddcPollEpoch = ddcPollEpoch + 1
    var previous = revertPlan
    var snap = revertPrefs
    pendingCommit = null
    revertPlan = null
    revertPrefs = null
    restorePreviewPrefs(snap)
    panelApplyReverted(snap)
    // Uses `runFront` + `revert` so safety rollback jumps ahead of any queued
    // refresh/nightlight job and never disables the last connected display.
    if (previous) {
      revertInFlight = true
      noteAppliedParks(previous)
      runFront(["revert"], JSON.stringify(previous), function() {
        service.revertInFlight = false
        service.noteAppliedParks(previous)
        service.refresh()
      })
    }
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

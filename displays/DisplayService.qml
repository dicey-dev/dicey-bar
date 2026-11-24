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
  // Development deployments (`dicey.bar.dev`) skip automatic startup/hotplug
  // reconcile so starting dev mode never mutates real displays unasked.
  readonly property bool isDevInstance: String(Qt.resolvedUrl(".")).indexOf("dicey.bar.dev") !== -1

  property var monitors: []
  property var memory: ({})
  property bool lidClosed: false
  property bool loaded: false
  property var liveNightLight: ({ enabled: false, kelvin: 4000 })
  // Monitors left off for now (lid shut, switched to another input), keyed by
  // fingerprint, and the current state as a plan with those marked.
  readonly property var currentAutoOff: Model.autoOffFor(Model.planFromLive(monitors, memory), lidClosed, ddcOff)
  readonly property var livePlan: Model.planFromLive(monitors, memory, currentAutoOff)

  // Monitors left off because they were switched to another input (or off),
  // keyed by fingerprint, and the consecutive DDC misses behind that.
  property var ddcOff: ({})
  property var ddcMisses: ({})
  // Per-fingerprint auto-relearn state ({ connector, inFlight, failures, nextRetryAt })
  // so transient wake-up timeouts back off and retry instead of sticking forever.
  property var autoRelearnState: ({})

  // Modes Hyprland did not take on the last apply, usually link bandwidth.
  property var fallbacks: []
  property string lastError: ""

  // After an Apply that changes a mode, rotation, HDR, mirror, or power state.
  property bool applyInFlight: false
  property bool persistInFlight: false
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

  function hasSavedDdcWatch(fp) {
    return !!(hasSavedProfile(fp) && memory.monitors[fp] && memory.monitors[fp].ddcWatch === true)
  }

  function savedDdcInput(fp) {
    if (!memory || !memory.monitors || !fp || !memory.monitors[fp]) return ""
    return String(memory.monitors[fp].ddcInput || "")
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
        if (data.nightLight && typeof data.nightLight === "object") {
          var prevK = service.liveNightLight && service.liveNightLight.kelvin >= 1500 && service.liveNightLight.kelvin <= 6400
            ? service.liveNightLight.kelvin : 4000
          var k = Number(data.nightLight.kelvin)
          service.liveNightLight = {
            enabled: data.nightLight.enabled === true,
            kelvin: (data.nightLight.enabled === true && isFinite(k) && k >= 1500 && k <= 6400) ? Math.round(k) : prevK
          }
        }
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

  function reprobe(then) {
    autoRelearnState = Model.ddcRelearnClearBackoff(autoRelearnState)
    refresh(function() {
      reconcile()
      if (then) then()
    })
  }

  function forgetGoneMonitors() {
    // Identify connected monitors independently of stale ddcOff so moving an
    // auto-disabled monitor to a new PC connector clears its stale ddcOff/misses
    // immediately before reconcile() computes currentAutoOff.
    var identified = Model.planFromLive(monitors, memory, {})
    var pruned = Model.pruneDdcState(identified, memory, ddcOff, ddcMisses, autoRelearnState)
    ddcOff = pruned.ddcOff
    ddcMisses = pruned.ddcMisses
    autoRelearnState = pruned.relearnState
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
    if (brightnessProc.running || brightnessQueue.length === 0) return
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

  // ---------------------------------------------------------------- memory & DDC actions
  function forgetMonitor(fp, done) {
    if (!fp || confirming || applyInFlight) return
    // Invalidate and abort any in-flight/queued DDC probe before persisting
    // the Forget so a slow probe can never resurrect the deleted profile.
    relearnEpoch = relearnEpoch + 1
    cancelQueuedAutoRelearn()
    var nextMemory = Model.forgetMonitor(memory, fp)
    var off = {}
    var misses = {}
    var relearn = {}
    for (var k in ddcOff) if (k !== fp) off[k] = ddcOff[k]
    for (var m in ddcMisses) if (m !== fp) misses[m] = ddcMisses[m]
    for (var r in autoRelearnState) if (r !== fp) relearn[r] = autoRelearnState[r]
    ddcOff = off
    ddcMisses = misses
    autoRelearnState = relearn
    var payload = { memory: nextMemory }
    if (!Model.hasMemory(nextMemory)) {
      payload.removeManagedBlock = true
    } else if (livePlan.length) {
      payload.plan = livePlan
    }
    persistInFlight = true
    run(["persist"], JSON.stringify(payload), function(code, data, stderr) {
      service.persistInFlight = false
      if (code === 0) {
        service.memory = nextMemory
        service.lastError = ""
      } else {
        service.lastError = service.errorText(data, stderr)
      }
      service.refresh(function() {
        if (done) done(code === 0)
      })
    })
  }

  // Dedicated read-only DDC probe Process for BOTH manual and automatic
  // re-learning. Slow I2C probes run here (never on `helper`) so they can
  // never block Apply, Keep, or safety Revert; only the fast, serialized
  // `ddc-save` write touches displays.json after validating current state.
  property var autoRelearnQueue: []
  property var activeAutoRelearn: null
  property int relearnEpoch: 0

  function relearnDdcInput(name, fp, done) {
    if (!name || !fp) return
    if (confirming || applyInFlight || persistInFlight) {
      var busyErr = "Wait for pending display changes to finish first"
      if (done) done(false, { error: busyErr })
      return
    }
    if (!hasSavedDdcWatch(fp)) {
      if (done) done(false, { error: "Apply Auto-Disable first before re-learning input" })
      return
    }
    autoRelearnQueue = autoRelearnQueue.concat([{
      name: name,
      fp: fp,
      manual: true,
      epoch: relearnEpoch,
      wasOff: !!(ddcOff && ddcOff[fp]),
      done: done || null
    }])
    pumpAutoRelearn()
  }

  Process {
    id: ddcRelearnProc
    stdout: StdioCollector { id: ddcRelearnOut }
    onExited: function(exitCode) {
      var job = service.activeAutoRelearn
      service.activeAutoRelearn = null
      var reading = ""
      if (exitCode === 0) {
        try {
          var parsed = JSON.parse(String(ddcRelearnOut.text || "null"))
          if (parsed && job && parsed[job.name]) reading = String(parsed[job.name])
        } catch (e) {
          reading = ""
        }
      }
      if (job) {
        var invalidated = job.cancelled || job.epoch !== service.relearnEpoch
          || service.confirming || service.applyInFlight || service.persistInFlight
          || !service.hasSavedDdcWatch(job.fp)
        if (invalidated) {
          if (!job.manual) {
            service.autoRelearnState = Model.ddcRelearnCancelQueued(service.autoRelearnState, [job.fp])
          }
          if (job.done) job.done(false, { error: "Cancelled while display changes were in progress" })
        } else if (!reading) {
          var readErr = "Could not read DDC/CI input for " + job.name
          if (job.manual) service.lastError = readErr
          else service.autoRelearnState = Model.ddcRelearnFinish(service.autoRelearnState, job.fp, job.name, false, Date.now())
          if (job.done) job.done(false, { error: readErr })
        } else {
          // Fast, serialized memory update on `helper`; re-checks ddcWatch on disk.
          ;(function(savedJob, codeHex) {
            service.run(["ddc-save", savedJob.name, savedJob.fp, codeHex], null, function(code, data, stderr) {
              var ok = code === 0 && data && data.ok
              if (ok) {
                if (data.memory) service.memory = Model.normalizeMemory(data.memory)
                var off = {}
                var misses = {}
                for (var k in service.ddcOff) if (k !== savedJob.fp) off[k] = service.ddcOff[k]
                for (var m in service.ddcMisses) if (m !== savedJob.fp) misses[m] = service.ddcMisses[m]
                service.ddcOff = off
                service.ddcMisses = misses
                if (savedJob.manual) service.lastError = ""
                else service.autoRelearnState = Model.ddcRelearnFinish(service.autoRelearnState, savedJob.fp, savedJob.name, true, Date.now())
                if (savedJob.done) savedJob.done(true, data)
                if (savedJob.wasOff && !service.confirming && !service.applyInFlight) {
                  service.refresh(service.reconcile)
                }
              } else {
                var err = service.errorText(data, stderr)
                if (savedJob.manual) service.lastError = err
                else service.autoRelearnState = Model.ddcRelearnFinish(service.autoRelearnState, savedJob.fp, savedJob.name, false, Date.now())
                if (savedJob.done) savedJob.done(false, { error: err })
              }
            })
          })(job, reading)
        }
      }
      Qt.callLater(service.pumpAutoRelearn)
    }
  }

  function cancelQueuedAutoRelearn() {
    var queued = autoRelearnQueue
    autoRelearnQueue = []
    var fps = []
    for (var i = 0; i < queued.length; i++) {
      if (!queued[i].manual) fps.push(queued[i].fp)
      if (queued[i].done) queued[i].done(false, { error: "Cancelled while display changes were in progress" })
    }
    if (activeAutoRelearn && ddcRelearnProc.running) {
      activeAutoRelearn.cancelled = true
      if (!activeAutoRelearn.manual) fps.push(activeAutoRelearn.fp)
      ddcRelearnProc.running = false
    }
    if (fps.length) autoRelearnState = Model.ddcRelearnCancelQueued(autoRelearnState, fps)
  }

  function pumpAutoRelearn() {
    if (ddcRelearnProc.running || autoRelearnQueue.length === 0) return
    if (confirming || applyInFlight || persistInFlight) {
      cancelQueuedAutoRelearn()
      return
    }
    var job = autoRelearnQueue[0]
    autoRelearnQueue = autoRelearnQueue.slice(1)
    if (!job.manual && isDevInstance) {
      autoRelearnState = Model.ddcRelearnCancelQueued(autoRelearnState, [job.fp])
      Qt.callLater(service.pumpAutoRelearn)
      return
    }
    activeAutoRelearn = job
    ddcRelearnProc.command = ["python3", "-I", service.helperPath, "ddc-poll", "--fresh", job.name]
    ddcRelearnProc.running = true
    if (!ddcRelearnProc.running) {
      activeAutoRelearn = null
      if (!job.manual) autoRelearnState = Model.ddcRelearnFinish(autoRelearnState, job.fp, job.name, false, Date.now())
      if (job.done) job.done(false, { error: "Failed to start DDC probe" })
      Qt.callLater(service.pumpAutoRelearn)
    }
  }

  function autoRelearnMovedConnectors(plan) {
    if (isDevInstance || confirming || applyInFlight || persistInFlight || !Array.isArray(plan) || !memory || !memory.monitors) return
    var now = Date.now()
    for (var i = 0; i < plan.length; i++) {
      var o = plan[i]
      // Skip internal panels and manually disabled monitors, but allow relearning
      // even if an output had been auto-disabled before a port swap.
      if (!o || o.internal || (!o.enabled && !o.autoOff)) continue
      var profile = memory.monitors[o.fp]
      if (!Model.ddcNeedsRelearn(profile, o.name)) continue
      var step = Model.ddcRelearnBegin(autoRelearnState, o.fp, o.name, now)
      if (!step.allowed) continue
      autoRelearnState = step.state
      autoRelearnQueue = autoRelearnQueue.concat([{
        name: o.name,
        fp: o.fp,
        manual: false,
        epoch: relearnEpoch,
        wasOff: !!(service.ddcOff && service.ddcOff[o.fp]),
        done: null
      }])
    }
    pumpAutoRelearn()
  }

  // ---------------------------------------------------------------- automatic
  function topologySignature(plan) {
    return plan.filter(function(o) { return o && !o.autoOff })
      .map(function(o) { return o.fp })
      .sort()
      .join("\n")
  }

  function reconcile() {
    if (isDevInstance || !loaded || confirming || !Model.hasMemory(memory) || monitors.length === 0) return
    var plan = Model.derive(monitors, memory, currentAutoOff)
    var topo = topologySignature(plan)
    if (Model.planMatchesLive(plan, livePlan)) {
      // Even when monitors.lua already restored geometry at login, still apply
      // remembered brightness, text size, and night light once per topology.
      if (topo && topo !== lastPrefsTopology) {
        lastPrefsTopology = topo
        applyMonitorPreferences(plan, memory, true)
      }
      autoRelearnMovedConnectors(plan)
      return
    }
    var signature = JSON.stringify(plan)
    var step = Model.autoApplyStep(lastAuto, signature, Date.now())
    lastAuto = step.last
    if (!step.allowed) return
    run(["apply"], signature, function(code, data, stderr) {
      service.fallbacks = data && Array.isArray(data.fallbacks) ? data.fallbacks : []
      service.lastError = code === 0 ? "" : service.errorText(data, stderr)
      if (code === 0) {
        service.lastPrefsTopology = topo
        service.applyMonitorPreferences(plan, service.memory, true)
        service.autoRelearnMovedConnectors(plan)
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
    onTriggered: if (!service.confirming) service.refresh(service.reconcile)
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

  // ---------------------------------------------------------------- DDC/CI (dedicated Process)
  // Runs in its own Process so a slow DDC bus probe never blocks Apply, Revert,
  // refresh, or Night Light.
  readonly property var watchedOutputs: {
    var out = []
    var mem = Model.normalizeMemory(memory)
    for (var i = 0; i < livePlan.length; i++) {
      var o = livePlan[i]
      var profile = mem.monitors[o.fp]
      // Poll only once connector tracking & input are in sync; moved connectors
      // and legacy profiles without ddcConnector are migrated via auto-relearn first.
      if (!o.internal && profile && profile.ddcWatch && profile.ddcInput
          && !Model.ddcNeedsRelearn(profile, o.name))
        out.push({ name: o.name, fp: o.fp, input: profile.ddcInput })
    }
    return out
  }

  readonly property var pendingRelearnOutputs: {
    var out = []
    var mem = Model.normalizeMemory(memory)
    for (var i = 0; i < livePlan.length; i++) {
      var o = livePlan[i]
      if (!o || o.internal || (!o.enabled && !o.autoOff)) continue
      var profile = mem.monitors[o.fp]
      if (Model.ddcNeedsRelearn(profile, o.name)) out.push(o)
    }
    return out
  }

  property var ddcPollTargets: []

  Process {
    id: ddcPollProc
    stdout: StdioCollector { id: ddcPollOut }
    onExited: function(exitCode) {
      var watched = service.ddcPollTargets
      service.ddcPollTargets = []
      if (exitCode !== 0 || service.confirming) return
      var data = null
      try {
        data = JSON.parse(String(ddcPollOut.text || "null"))
      } catch (e) {
        data = null
      }
      if (!data) return
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
    }
  }

  function pollInputs() {
    var watched = watchedOutputs
    if (!watched.length || ddcPollProc.running || confirming || isDevInstance) return
    ddcPollTargets = watched
    ddcPollProc.command = ["python3", "-I", service.helperPath, "ddc-poll"].concat(watched.map(function(w) { return w.name }))
    ddcPollProc.running = true
  }

  Timer {
    interval: 5000
    repeat: true
    running: (service.watchedOutputs.length > 0 || service.pendingRelearnOutputs.length > 0)
      && !service.confirming && !service.applyInFlight && !service.persistInFlight && !service.isDevInstance
    onTriggered: {
      if (service.pendingRelearnOutputs.length > 0) service.autoRelearnMovedConnectors(service.livePlan)
      service.pollInputs()
    }
  }

  // ---------------------------------------------------------------- Apply
  // `plan` is the panel's full arrangement. Applying it saves each monitor's
  // settings and this combination's layout; a mode/power/rotation/mirror change
  // first waits for the user to keep it, and safely reverts both displays and
  // any previewed OS-wide preferences if rejected or timed out.
  function applyFromPanel(plan, options, revertSnapshot) {
    if (confirming || applyInFlight || !plan || !plan.length) return
    relearnEpoch = relearnEpoch + 1
    cancelQueuedAutoRelearn()
    applyInFlight = true
    var before = livePlan
    var mustConfirm = Model.needsConfirmation(plan, before)
    var opts = options || {}
    run(["apply"], JSON.stringify(plan), function(code, data, stderr) {
      service.applyInFlight = false
      service.fallbacks = data && Array.isArray(data.fallbacks) ? data.fallbacks : []
      if (code !== 0) {
        service.lastError = service.errorText(data, stderr)
        service.restorePreviewPrefs(revertSnapshot)
        service.panelApplyReverted(revertSnapshot)
        service.runFront(["revert"], JSON.stringify(before), function() { service.refresh() })
        return
      }
      service.lastError = ""
      if (mustConfirm) {
        service.cancelQueuedAutoRelearn()
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
    relearnEpoch = relearnEpoch + 1
    cancelQueuedAutoRelearn()
    run(["persist"], JSON.stringify({ plan: plan, memory: nextMemory }), function(code, data, stderr) {
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
    relearnEpoch = relearnEpoch + 1
    cancelQueuedAutoRelearn()
    var previous = revertPlan
    var snap = revertPrefs
    pendingCommit = null
    revertPlan = null
    revertPrefs = null
    restorePreviewPrefs(snap)
    panelApplyReverted(snap)
    // Uses `runFront` + `revert` so safety rollback jumps ahead of any queued
    // refresh/nightlight job and never disables the last connected display.
    if (previous) runFront(["revert"], JSON.stringify(previous), function() { service.refresh() })
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

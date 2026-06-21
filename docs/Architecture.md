# Architecture: hardware abstraction

This note describes how haptics flow from a game event to a physical controller, and the
refactor that makes it easy to support hardware with very different requirements (multi-motor
pads, LRAs that need frequency, pattern-upload devices, force-feedback sticks, and so on).

## The pipeline

```
EventType  ->  semantic Effect descriptor  ->  Renderer(capabilities)  ->  Device backend
 (game)          (device-independent)            (how to play it)            (transport)
```

- **Event**: what happened in the game (gun, missile, kill, ...). Already modelled in
  `events.py`.
- **Effect descriptor**: a device-independent description of the haptic to play. Not a stream
  of motor levels; a sequence of primitives with intensity, optional frequency / sharpness,
  duration, and a channel *role* (`primary`, `trigger`, `left`, `right`, ...).
- **Renderer**: turns a descriptor into device output for one device, choosing the play model
  that device needs.
- **Device backend**: the transport (USB HID, Bluetooth, an SDK) and the device's own play
  contract.

## Why the old shape blocks new hardware

The original engine is hardwired to one hardware model: a single ERM motor driven by writing a
native `0-255` level every few milliseconds on a heartbeat thread, with priority arbitration so
a one-shot stomps the sustained gun rumble. That assumes:

- one actuator (no channels),
- intensity only (no frequency / sharpness),
- the *host* owns timing (it streams levels),
- a fixed keep-alive cadence baked into the engine.

Devices that break any of those assumptions (DualSense, bHaptics, FFB sticks, multi-motor
pads) cannot be expressed.

## The target design

### 1. Normalized intensity
Effects are authored in normalized `0.0-1.0` intensity. Each device maps that to its native
range in `set_level()`. The engine never emits native values. (The Winwing maps `1.0 -> 255`.)

### 2. Semantic effects + renderers
An effect is a **descriptor**, not a level stream. A renderer plays it:

- **Streaming devices** (Winwing): the renderer ticks `set_level` over the descriptor's
  timeline, exactly like the original engine loop.
- **Pattern / effect devices**: the renderer uploads the whole descriptor once and the *device*
  owns timing. The engine must not assume it drives the clock.

So the device contract is `play(effect)` + `stop()`, not `vib(level)`.

### 3. Capability negotiation with graceful degradation
Effects are authored at the richest level (channels, frequency, sharpness). Each device
advertises `Capabilities` and downsamples what it can't do: a single-motor ERM collapses all
channels to one and ignores frequency; a dual-rumble pad maps roles to motors; an LRA uses
frequency. One effect definition, many faithful renderings, no per-device effect tables.

### 4. Device-owned keep-alive
The 2.5s arm heartbeat is Winwing-specific. Timing moves into the device, started on `open()`
and driven by `Capabilities.needs_heartbeat` / `heartbeat_interval`. Devices that don't need it
make it a no-op instead of carrying dead engine code.

### 5. Capability-driven arbitration
The gun-vs-one-shot priority stomp exists because there is one motor. Devices with multiple
channels should **mix** (sustained gun on one actuator, missile transient on another).
Arbitration becomes a strategy the renderer picks from capabilities: `mix` when channels are
available, else the `priority` fallback.

### 6. Device registry + discovery
Replace the hardcoded `Stick()` in the controller with a registry. Each backend registers a
`probe()` (VID / usage scan, SDK presence, ...). The controller enumerates registered backends,
opens the first that probes successfully, and exposes selection in the UI. Adding hardware
becomes "drop in a module and register it", never an edit to the controller.

## Migration order

Each step keeps the `tests/` suite green and the felt output on the Winwing unchanged.

1. **Normalize the engine** (#1). Engine drives `set_level(0..1)`; library authored normalized.
   Prerequisite for everything else.
2. **Effect descriptor + default renderer** (#2). Promote effects to descriptors; a streaming
   renderer reproduces today's behavior. Device contract becomes `play(effect)` / `stop()`.
3. **Device-owned keep-alive** (#4).
4. **Device registry** (#6).
5. **Capability negotiation + mixing** (#3, #5) as multi-channel devices are added.

The smallest step that unlocks the rest is 1 + 2: once a device is asked to *play an effect*
rather than *set a level*, a very different controller is a new renderer plus backend, not an
engine rewrite.

## Status

Refactor in progress on `hardware-abstraction-refactor`. This document is the plan of record;
update it as phases land.

Landed:

- **Phase 1 - normalized engine.** The engine drives devices via `set_level(0.0-1.0)`; the
  library is authored normalized and round-trips to the original 0-255 envelope exactly.
- **Phase 2 - effect descriptors + renderer.** Effects are `Effect`/`Segment` descriptors
  (`effects/model.py`); a renderer plays them (`effects/renderer.py`). `StreamingRenderer` is
  the default host-timed playback; `renderer_for()` lets a device supply its own.
- **Phase 3 - device-owned keep-alive.** `HapticDevice.start_keepalive()` / `keepalive()`,
  driven by `Capabilities`. The engine no longer hardcodes the 2.5s arm cadence.
- **Phase 4 - device registry.** `hardware/registry.py`: backends register and expose
  `probe()`; the controller calls `select_device()` instead of hardcoding the Winwing.

Still open:

- **Capability negotiation + mixing** (#3, #5): multi-channel roles, frequency/sharpness, and
  `mix`-vs-`priority` arbitration. These land when the first multi-channel device is added; the
  `Channel` role and `Segment.frequency` field already exist as the seam for it.
- **Multi-backend hot-plug.** `select_device()` runs once at controller construction. With a
  single backend this matches the original retry-open behavior exactly. Once a second backend
  exists, swapping to a device plugged in later needs re-running detection AND rebinding the
  effects engine's device reference (it captures the device + renderer at construction), so it's
  deferred until there's more than one backend to choose between.

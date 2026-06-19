# tests/ — A/B regression suite

The safety net for changing the detector / tracker. It measures two **independent failure
tracks** against ground truth over the recordings and compares to a committed baseline, so a
change can only land if it does not regress either track.

## The two tracks

| Track | Layer | Instance | Fails when | Runs on |
|-------|-------|----------|-----------|---------|
| **Event failures** | `TemporalTracker` (decision) | one frame | a **false fire** (event in a silent region) **or** a **missed fire** (a real fire onset with no event within ±2 frames) | every clip with frozen per-frame reads |
| **Misreads** | `read_counts` (vision) | one (frame × weapon) | the re-detected read ≠ ground-truth value | clips that carry `calib.json` ("faithful" tier) |

`event_failure_rate = failed_frames / total_frames`.
`misread_rate = misread_cells / scored_cells`.
A frame counts as failed if **any** weapon fails on it. The two tracks are reported,
baselined and gated **separately** — a change that fixes the vision (misread_rate down)
without changing felt behavior (event rate flat) is visible, and vice-versa.

## Two layers of testing

### 1. Tracker suite (frozen reads) — `tools/ab_report.py`, `pytest tests/`
Replays the tracker over the reads SAVED in each clip's telemetry. Fast and deterministic,
but it measures whatever detector build RECORDED the clip (often obsolete), not the current
detector. Good for tracker-logic regressions; blind to detector changes.

### 2. Platform (re-detection) — `tools/platform_report.py`  ← the higher-level one
RE-RUNS the **current** detector on every PNG of every recording and scores three tracks.
This is what surfaces the **missed-row** problem (a weapon row present on the HUD but read as
nothing) that the frozen-read view hid. Treats **each recording as a respawn/life** (tracker
reset between clips); the whole corpus is one sequence of respawns.

| Track | Fails when | Why it matters |
|-------|-----------|----------------|
| **ROW** | a GT-present weapon row reads None (`missed_row`), or a whole row never calibrates (`calib_missing_rows`), or a value appears where the row is absent (`false_row`) | the "no fire / missed" you feel — the row was never read |
| **VALUE** | read value ≠ GT on a present, read, stable frame (`misread`) | wrong counter → wrong/!fire decision |
| **EVENT** | per real fire EPISODE: HIT / MISS / FALSE (event-denominated, not per-frame) | matches lived experience ("did my rocket buzz?") |

```powershell
python tools/platform_report.py                 # run all clips as respawns, compare baseline
python tools/platform_report.py --update-baseline
python tools/platform_report.py --no-cache      # force full re-detection
```

**Calibration & determinism.** Re-detection needs a calibration:
- clips WITH `calib.json` → the exact live calibration (faithful, gold).
- clips WITHOUT (older) → a **pinned** OCR calibration: computed once (best of several
  whole-clip samples, by rows-learned then read-coverage), saved under
  `tests/pinned_calib/` and reused. Pinning freezes the geometry so a `read_counts`/tracker
  A/B is fair — only detector logic moves the number, not recalibration noise. (Offline
  recalibration is noisy: an unpinned calib swung 101336 between 3% and 29% missed rows, so
  pinning is essential for old clips.)
- Results are cached under `tests/.cache/`, keyed by a hash of `src/hud_detect.py`, so any
  detector change auto-invalidates and recomputes. First run is slow (~2-3 min); cached
  reruns ~1 s.

`tests/.cache/` and `tests/pinned_calib/` are gitignored (regenerated locally; need the
recordings). `tests/platform_baseline.json` is the committed contract.

**Honesty note.** Absolute platform numbers are trustworthy on the faithful (`calib.json`)
tier. On the older clips they use a pinned OCR calibration that is representative but not
identical to live, so treat their absolute rates as indicative and the A/B DELTA as the
reliable signal. Recording new clips (current build writes `calib.json`) upgrades them to
faithful.

### 3. Calibration-quality track — `tools/calib_report.py`, `pytest tests/`
Runs the **real runtime auto-calibration** (`calibrate_from_grays` on a consecutive 24-frame
window, exactly as the app does on (re)calibrate — no best-of-N, no spreading, no pinning) at
several moments across every recording, treating each clip as a respawn. Scores how well the
*actual* calibration learns the rows on the HUD, so calibration failures show up honestly.

Per clip, over N windows:
- **rows_learned** = learned-present / GT-present (mean + worst) — how complete calibration is
- **fail_rate** — fraction of windows where calibration outright failed
- **always_missed** — rows present in ≥1 window but learned in NONE (systematic blind spots,
  e.g. a single-digit BMB row)
- **count_x_spread** — geometry instability across windows

```powershell
python tools/calib_report.py
python tools/calib_report.py --update-baseline
python tools/calib_report.py --no-cache
```

Gate: rows_learned must not drop, fail_rate must not rise, no NEW always-missed rows.
Baseline: `tests/calib_baseline.json` (committed). Cache: `tests/.cache_calib/` (gitignored,
keyed by detector hash). This track exposed that calibrating at the **respawn moment (frame 0)
fails or under-learns** repeatedly — the start-of-match tooltip problem — which is the next
calibration improvement target.

---

## Ground truth

Per clip, `tests/ground_truth/<clip_key>.json` holds **dense stable segments**:
`weapon -> [[start, end, value], ...]` (inclusive frame indices). Gaps between stable
segments are transition zones; a DOWNWARD step = a real fire episode. This one model drives
all tracks: row-presence (a weapon is present from its first to its last segment, optionally
overridden by an explicit `_present` map), per-frame values (misread track), and silent
plateaus vs fire zones (event track).

- `"_unverified": false` → **VERIFIED**, hand-checked. Gates the build + feeds the baseline.
- `"_unverified": true`  → **ADVISORY**, auto-derived (`tools/derive_gt.py`). Shown for
  information only; never gates, never written to the baseline, until a human verifies it
  and flips the flag.

## Running it

```powershell
# standalone gate (no pytest needed) — exits non-zero on regression
python tools/ab_report.py

# after a CONFIRMED improvement, re-snapshot the bar to beat
python tools/ab_report.py --update-baseline

# same gate via pytest (nicer per-recording output / CI)
python -m pytest tests/
```

The committed contract is `tests/baseline_metrics.json` (small; committed even though the
~145 MB recordings are gitignored). A run PASSES iff, for each track independently, the
aggregate rate ≤ baseline AND no recording regresses.

## Data dependency

The suite needs clips under `recordings/` (gitignored). With no recordings present it simply
reports nothing to do; with no `calib.json` clips the misread track stays inactive. Record a
clip with the current build (it writes `calib.json`) to activate the faithful misread tier.

## Layout

```
tests/
  lib/recordings.py    clip discovery + telemetry/PNG/calib loading
  lib/groundtruth.py   dense-segment GT model (values, fire zones, silent frames)
  lib/metrics.py       both tracks' scoring (uses src/hud_detect)
  ground_truth/*.json  per-clip GT (verified + advisory)
  baseline_metrics.json   committed regression contract
  test_event_failures.py  pytest wrapper (event track)
  test_misreads.py        pytest wrapper (misread track)
tools/ab_report.py     standalone gate + A/B table
tools/derive_gt.py     auto-derive candidate GT for a clip (then hand-verify)
```

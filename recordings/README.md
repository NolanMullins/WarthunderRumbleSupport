# recordings/ — local test data (gitignored)

This folder holds **HUD capture clips** used by the offline analysis tools in `tools/`.
Its contents are **never committed** (see the local `.gitignore` here) because the clips are
large binary captures. Only this README and the `.gitignore` are tracked.

## Layout

Each clip the app produces (via **Record 30s**) is a folder that nests one more level:

```
recordings/
  <clip_name>/
    <clip_name>/
      f0000.png, f0001.png, ...   captured HUD frames
      telemetry.jsonl             per-frame reads + live dispatched events + header
      calib.json                  full live calibration (newer builds only)
```

A single clip folder may contain **more than one** inner capture (e.g. a session that
spanned two recordings).

The tools reference clips by the nested path, for example:

```
hud_rec_20260618_153642\hud_rec_20260618_153552
hud_rec_20260618_153642\hud_rec_20260618_153642
```

## Using the tools

From the repo root:

```powershell
python tools\live_vs_current.py     # current tracker vs what fired live in-game
python tools\miss_audit.py          # missed / late detections on real saved reads
python tools\all_frames_audit.py    # faithful detector A/B (clips with calib.json)
```

To add your own data, drop a Record-30s capture here so the path becomes
`recordings/<clip>/<clip>/`, then update the `RECS` list in the relevant tool if needed.

## Current local clips (not in git)

| Clip | Notes |
|------|-------|
| `hud_rec_20260618_101336` | missiles / flares (pre-`dispatched` build) |
| `hud_rec_20260618_111503` | gun-heavy |
| `hud_rec_20260618_140000` | 6 weapons / rockets |
| `hud_rec_20260618_153642` | two inner clips: `_153552` (gun 120) + `_153642` (gun 89 / 84 flicker) |
| `hud_rec_20260618_155235` | 6 weapons; 248↔242 CNN flicker + AAM launch |

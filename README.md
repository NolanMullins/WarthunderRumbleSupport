# Warthunder Rumble Support

This app uses visual detection to spot in-game events in War Thunder and translates them into
controller vibration on supported hardware.

Windows only. It reads the game's local telemetry and the on-screen HUD, and sends vibration
to the controller over USB HID. No game files are touched.

## Supported hardware

| Device | Status |
|---|---|
| Winwing Ursa Minor Fighter | Supported |

More devices can be added. If you want one supported, open an issue.

## Triggers

| Trigger | Fires when |
|---|---|
| Gun | You hold the trigger |
| Missile | A missile launches |
| Rocket | A rocket fires |
| Bomb | A bomb releases |
| Countermeasures | Flares or chaff go out |
| Kill | You destroy an enemy |
| Hit | An enemy damages you |
| Death | You get destroyed |

Guns, kills, hits and deaths can be toggled on or off. Kill, hit and death only fire for you,
so set your in-game callsign in the app.

## Usage

An exe release is planned. For now, run from source on Windows (Python 3.10+, 64-bit):

```powershell
python -m pip install -r requirements.txt
python run.py
```

In the app:

1. Plug in the controller. The Joystick status goes green when it's found.
2. Start War Thunder and get into a match. The War Thunder status goes green.
3. Tick HUD auto-detect. It learns your HUD the first time it sees the weapon counters.
4. Optionally set your callsign and pick which effects you want.

If detection looks off, use Set Region to box the weapon counters, or Re-learn HUD to redo it.

## How it works

Weapon fires come from reading the HUD ammo counters: when a counter drops, that weapon fired.
A noise filter rejects misreads so only real shots trigger. Gun input and the kill/death feed
come from War Thunder's local telemetry. All of it is sent to the controller over USB HID.

Developer and build details are in the `tests/` folder and the source under
`src/winwinghaptics/`.

"""Effect UI spec — the data-driven list the Effects tab renders from.

One ENTRY per trigger the user can feel, describing how to present and control it: icon, label,
group, the enable key in the controller's state, the test action (which engine call the "Test"
button makes), and the live-state key used to light the row when that effect is firing.

Adding or reordering triggers is a data edit here, not a layout change in the GUI. The entries map
onto the existing effects engine triggers and the controller enable flags, so the UI stays a thin
view over the domain model.
"""
from collections import namedtuple

# name      : stable id / enable suffix (en_<name>) and effect-library key where applicable
# label     : display name
# icon      : vendored Lucide icon name (ui/assets/icons/lucide/<icon>.svg)
# group     : section header on the Effects tab
# desc      : short sub-label (may be "")
# test      : engine method name the Test button calls
# firing    : True if the row can show a live "firing" highlight (sustained/rapid weapons)
EffectSpec = namedtuple("EffectSpec", "name label icon group desc test firing")

# Group ids -> display titles (ordered).
GROUPS = [("weapons", "Weapons"), ("outcomes", "Outcomes")]

SPECS = [
    EffectSpec("gun",     "Gun",            "crosshair",    "weapons",  "cannon / MG",   "gun_active", True),
    EffectSpec("missile", "Missile",        "rocket",       "weapons",  "AAM launch",    "missile",    False),
    EffectSpec("rocket",  "Rocket",         "flame",        "weapons",  "RKT fire",      "rocket",     False),
    EffectSpec("bomb",    "Bomb",           "bomb",         "weapons",  "release",       "bomb",       False),
    EffectSpec("flare",   "Countermeasures","sparkles",     "weapons",  "flare / chaff", "flare",      False),
    EffectSpec("kill",    "Kill",           "target",       "outcomes", "you score a kill", "kill",    False),
    EffectSpec("hit",     "Took a hit",     "shield-alert", "outcomes", "enemy damages you", "hit",    False),
    EffectSpec("death",   "Death",          "skull",        "outcomes", "you are destroyed", "death",  False),
]

# name -> spec, and the canonical enable-key list (en_<name>), for the controller/config.
BY_NAME = {s.name: s for s in SPECS}
ENABLE_KEYS = [s.name for s in SPECS]


def specs_in_group(group):
    """Specs belonging to a group id, in declared order."""
    return [s for s in SPECS if s.group == group]

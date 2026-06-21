"""Device-independent effect model.

An Effect is WHAT to play, described independently of any device: an ordered list of Segments,
each a normalized intensity held for a duration on a channel ROLE (with optional frequency for
devices that support it). It is deliberately NOT a stream of motor levels -- how an Effect is
turned into device output is a renderer's job (see renderer.py), so a single Effect definition
can drive a level-streamed ERM, a multi-motor pad, an LRA that needs frequency, or a device that
uploads the whole pattern and owns its own timing.
"""
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional


class Channel(str, Enum):
    """Where on the device a segment plays. Single-actuator devices collapse every role to one
    motor; multi-actuator devices map roles to physical motors."""
    PRIMARY = "primary"
    # future roles as multi-channel devices are added: TRIGGER, LEFT, RIGHT, ...


@dataclass(frozen=True)
class Segment:
    level: float                       # normalized intensity 0.0-1.0
    duration_ms: int
    channel: Channel = Channel.PRIMARY
    frequency: Optional[float] = None  # Hz; None = device default (ignored by intensity-only devices)


@dataclass(frozen=True)
class Effect:
    name: str
    segments: List[Segment] = field(default_factory=list)
    log: Optional[str] = None          # activity-log line emitted when playback starts (None = silent)

    @property
    def duration_ms(self) -> int:
        """Total wall-clock length of the effect. Lets a device that owns its own timing (a
        pattern-upload backend) hold priority for the right span without streaming."""
        return sum(s.duration_ms for s in self.segments)

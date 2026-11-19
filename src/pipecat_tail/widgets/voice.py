#
# Copyright (c) 2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""The voice timeline: the conversation as two scrolling audio tracks.

The user's audio grows up, the bot's hangs down, and between them sit raw
VAD, the end of each user turn, the response time of each answer as a
bracket, backchannels and interruptions. The gap you feel in a call shows up
as a gap on screen.
"""

from typing import Optional

from rich.console import Group
from rich.style import Style
from rich.text import Text
from textual.widget import Widget

from pipecat_tail.state import THINKING, SessionState, WorkerSession
from pipecat_tail.widgets import palette
from pipecat_tail.widgets.graphics import block_wave, overlay, pad

LABEL_WIDTH = 6
WINDOWS = (10.0, 20.0, 40.0, 60.0)
# Once the call is over the timelines stop this long after its last event.
CALL_END_MARGIN_SECS = 1.0


def timeline_end(state: SessionState, session: WorkerSession) -> tuple[float, bool]:
    """Where a timeline ends, and whether that is the end of the call.

    While the call is on, timelines end now and scroll. Once it is over they
    stop where it ended, so they keep showing it.
    """
    now = state.now()
    end = state.call_end(session)
    if end is None:
        return now, False
    return min(now, end + CALL_END_MARGIN_SECS), True


def wave_rows_for(height: int) -> tuple[int, bool]:
    """Rows per audio track and whether the time axis fits, for a content height.

    You already hear the audio in the call, so the tracks give way first: the
    speech lane and the response brackets between them always stay. With two
    rows or fewer, they are all there is (no tracks).
    """
    if height <= 2:
        return 0, False
    if height >= 9:
        return 3, True
    if height >= 7:
        return 2, True
    if height >= 5:
        return 1, True
    return 1, False


def _bins(wave, t0: float, t1: float, cells: int) -> list[float]:
    values = [0.0] * cells
    if cells <= 0:
        return values
    dt = (t1 - t0) / cells
    for t, level in wave:
        if t < t0 or t > t1:
            continue
        i = min(cells - 1, int((t - t0) / dt))
        values[i] = max(values[i], level)
    return values


def _scale(level: float) -> float:
    return max(0.0, min(1.0, (level / 0.75) ** 0.85)) if level > 0 else 0.0


def response_brackets(session: WorkerSession) -> list[tuple[float, float]]:
    """Pairs of (user stopped, bot started) from the speech events."""
    pairs = []
    last_stop: Optional[float] = None
    for event in session.speech:
        if event.kind == "user_speech_stopped":
            last_stop = event.started_at or event.timestamp
        elif event.kind == "user_turn_stopped" and last_stop is None:
            last_stop = event.timestamp
        elif event.kind == "bot_speech_started":
            if last_stop is not None and 0 < event.timestamp - last_stop < 15:
                pairs.append((last_stop, event.timestamp))
            last_stop = None
        elif event.kind in ("user_speech_started", "user_turn_started"):
            last_stop = None
    return pairs


class VoiceTimeline(Widget):
    """Two audio tracks with speech lanes between them, scrolling with time.

    The app's stylesheet sets the height by terminal size; the timeline draws
    as much as the height allows.
    """

    DEFAULT_CSS = """
    VoiceTimeline {
        border-subtitle-color: $text-muted;
        padding: 0 1;
    }
    """

    def __init__(self, state: SessionState, *, id: str = "voice"):
        """Create the timeline."""
        super().__init__(id=id)
        self.state = state
        self.window = 20.0
        self.border_title = "voice"

    def zoom(self, direction: int) -> float:
        """Show a shorter (-1) or longer (+1) stretch of time and return it."""
        index = WINDOWS.index(self.window) if self.window in WINDOWS else 1
        index = max(0, min(len(WINDOWS) - 1, index + direction))
        self.window = WINDOWS[index]
        self.refresh()
        return self.window

    def frame_secs(self) -> float:
        """How often the timeline needs redrawing: once per column of time."""
        cells = max(4, self.size.width - LABEL_WIDTH)
        return max(1 / 12, min(0.5, self.window / cells))

    def has_motion(self, now: float) -> bool:
        """Whether anything in the window moves: speech, audio or a wait."""
        session = self.state.selected
        if self.state.call_end(session) is not None:
            return False
        t0 = now - self.window
        if session.is_waiting(now) or session.user_speaking or session.bot_speaking:
            return True
        if session.speech and session.speech[-1].timestamp >= t0:
            return True
        for wave in (session.user_wave, session.bot_wave):
            for t, level in reversed(wave):
                if t < t0:
                    break
                if level > 0.01:
                    return True
        return False

    def render(self):
        """Draw the timeline."""
        p = palette.current()
        session = self.state.selected
        width = max(10, self.size.width)
        cells = max(4, width - LABEL_WIDTH)
        now, over = timeline_end(self.state, session)
        t1 = now
        t0 = now - self.window
        dt = self.window / cells

        def x(t: float) -> int:
            return int((t - t0) / dt)

        wave_rows, with_axis = wave_rows_for(self.size.height)
        user_rows: list[Text] = []
        bot_rows: list[Text] = []
        if wave_rows:
            user = [_scale(v) for v in _bins(session.user_wave, t0, t1, cells)]
            bot = [_scale(v) for v in _bins(session.bot_wave, t0, t1, cells)]
            user_rows = block_wave(user, wave_rows, p.user_wave)
            bot_rows = block_wave(bot, wave_rows, p.bot_wave, down=True)

        # Lane: raw VAD spans, turn ends and backchannels.
        lane = Text(" " * cells)
        vad_open: Optional[float] = None
        spans: list[tuple[float, float]] = []
        turn_ends: list[float] = []
        interruptions: list[float] = []
        for event in session.speech:
            if event.kind == "user_speech_started":
                vad_open = event.timestamp
            elif event.kind == "user_speech_stopped":
                start = vad_open if vad_open is not None else (event.started_at or event.timestamp)
                spans.append((start, event.timestamp))
                vad_open = None
            elif event.kind == "user_turn_stopped":
                turn_ends.append(event.timestamp)
            elif event.kind == "interruption":
                interruptions.append(event.timestamp)
        if vad_open is not None:
            spans.append((vad_open, now))
        for a, b in spans:
            if b < t0 or a > t1:
                continue
            xa, xb = max(0, x(a)), min(cells, max(x(a) + 1, x(b)))
            lane = overlay(lane, xa, Text("━" * (xb - xa), style=Style(color=p.vad)))
        # Turns Pipecat ended before the user had finished are marked in amber.
        early = {e.ruled_at for turn in session.turns for e in turn.early_ends}
        for t in turn_ends:
            if t0 <= t <= t1:
                color = p.warn if t in early else p.user
                lane = overlay(lane, x(t), Text("┃", style=Style(color=color, bold=True)))
        for turn in session.turns:
            for b in turn.backchannels:
                if t0 <= b.timestamp <= t1:
                    color = p.user if b.who == "user" else p.bot
                    label = Text(f"‹{b.text}›", style=Style(color=color, italic=True))
                    lane = overlay(lane, x(b.timestamp), label)

        # Brackets: how long each answer took, and the one being waited on.
        # Interruptions go first so the brackets' figures stay readable.
        brackets = Text(" " * cells)
        for t in interruptions:
            if t0 <= t <= t1:
                brackets = overlay(brackets, x(t), Text("↯", style=Style(color=p.err, bold=True)))
        for start, end in response_brackets(session):
            if end < t0:
                continue
            xa, xb = max(0, x(start)), min(cells - 1, x(end))
            secs = end - start
            color = palette.latency_color(secs)
            span = max(2, xb - xa + 1)
            label = f"{secs:.2f}s" if secs >= 1 else f"{secs * 1000:.0f}ms"
            line = "╰" + "─" * (span - 2) + "╯"
            brackets = overlay(brackets, xa, Text(line, style=Style(color=color)))
            mid = xa + max(1, (span - len(label)) // 2)
            if span >= len(label) + 2:
                brackets = overlay(brackets, mid, Text(label, style=Style(color=color, bold=True)))
            else:
                brackets = overlay(
                    brackets, xb + 1, Text(label, style=Style(color=color, bold=True))
                )
        if session.is_waiting(now) and session.bot_state_at(now) == THINKING:
            # The wait still open: an arrow to now. The HUD's clock counts it.
            xa = max(0, x(session.waiting_since or now))
            span = max(2, min(cells - 1, x(now)) - xa + 1)
            text = Text("╰" + "─" * (span - 2) + "▸", style=Style(color=p.llm))
            brackets = overlay(brackets, min(xa, cells - text.cell_len), text)
        for t in sorted(early):
            # Name the early end, left of its mark, where there is room for it.
            word = Text("early ", style=Style(color=p.warn, bold=True))
            at = x(t) - len(word) + 1
            if t0 <= t <= t1 and at >= 0 and not brackets.plain[at : at + len(word)].strip():
                brackets = overlay(brackets, at, word)
        for t in interruptions:
            # Name the interruption where there is room for it.
            word = Text(" interrupted", style=Style(color=p.err))
            at = x(t) + 1
            if (
                t0 <= t <= t1
                and at + len(word) < cells
                and not brackets.plain[at : at + len(word)].strip()
            ):
                brackets = overlay(brackets, at, word)

        # Axis.
        axis = Text(" " * cells, style=Style(color=p.muted))
        step = 5.0 if self.window <= 20 else (10.0 if self.window <= 40 else 15.0)
        tick = step
        while tick < self.window:
            pos = x(now - tick)
            axis = overlay(axis, pos, Text(f"╵-{tick:.0f}s", style=Style(color=p.muted)))
            tick += step
        edge = "end╵" if over else "now╵"
        axis = overlay(axis, cells - 4, Text(edge, style=Style(color=p.accent, bold=True)))

        def name(text: str, color: str) -> Text:
            return pad(Text(text, style=Style(color=color, bold=True)), LABEL_WIDTH)

        # Without raw VAD (no VAD in the pipeline) the lane only has turn ends.
        has_vad = "vad" in self.state.capabilities or bool(spans)
        last = "the call's last" if over else "last"
        self.border_title = f"voice · {last} {self.window:.0f} s"
        if not wave_rows:
            # Only the lanes: the turn ends and response times stay readable.
            return Group(
                name("vad" if has_vad else "turns", p.vad) + lane, name("", p.muted) + brackets
            )
        rows = [name("", p.user) + row for row in user_rows[:-1]]
        rows.append(name("you", p.user) + user_rows[-1])
        rows.append(name("vad" if has_vad else "turns", p.vad) + lane)
        rows.append(name("", p.muted) + brackets)
        rows.append(name("bot", p.bot) + bot_rows[0])
        rows.extend(name("", p.bot) + row for row in bot_rows[1:])
        if with_axis:
            rows.append(name("", p.muted) + axis)
        return Group(*rows)

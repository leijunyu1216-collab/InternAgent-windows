#!/usr/bin/env python3
"""Terminal widgets for the internagent wizard: an arrow-key choice list, the
line it collapses to once answered, and the start-confirmation card.

The look is the one Claude Code / Codex CLI use: no box around the question, a
single ``›`` cursor, dim metadata in a second column, and the whole list
disappearing as soon as an answer is given, so the scrollback keeps one line per
decision instead of one screen per step.

Key reading is *injected* (a ``read_key`` callable) rather than probed here.
The platform decision needs ``_is_win_console()`` from internagent_cli — a
Git-Bash/MSYS pty has no console screen buffer, so neither prompt_toolkit nor
msvcrt's console input works there; the caller then passes ``read_key=None``,
which selects the numbered fallback. Keeping that branch outside this module
gives the widget one job and makes it testable by feeding it a list of keys.
"""

import os
import sys

from rich.box import ROUNDED
from rich.cells import cell_len, set_cell_size
from rich.live import Live
from rich.panel import Panel
from rich.text import Text

from cli_live import pad_cell

CURSOR = "›"
CHECK = "✓"
INK = os.environ.get("CLI_BANNER_COLOR", "#4EC9B0")

# msvcrt reports an extended key as a NUL/0xE0 prefix byte followed by the scan
# code; these are the ones the list cares about.
_EXT_WIN = {72: "up", 80: "down", 75: "left", 77: "right",
            73: "pgup", 81: "pgdn", 71: "home", 79: "end"}

_ACTION = {"\r": "enter", "\n": "enter", "\x03": "ctrl-c", "\x1b": "esc",
           "\x08": "backspace", "\x7f": "backspace",
           "\x04": "ctrl-d", "\x14": "ctrl-d"}


# -- key readers ---------------------------------------------------------

def win_key_reader():
    """Arrow keys from a real Win32 console (cmd / PowerShell / Windows
    Terminal). Same two-byte shape _masked_console_input already skips."""
    import msvcrt

    def read():
        ch = msvcrt.getwch()
        if ch in ("\x00", "\xe0"):
            return _EXT_WIN.get(ord(msvcrt.getwch()), "")
        return _ACTION.get(ch, ch)

    return read


def posix_key_reader():
    """Arrow keys on Linux/macOS. Raw mode is entered per keystroke rather than
    for the whole selection, so a crash mid-list cannot leave the terminal with
    echo switched off."""
    import select
    import termios
    import tty

    fd = sys.stdin.fileno()

    def _byte(timeout):
        ready, _, _ = select.select([fd], [], [], timeout)
        return os.read(fd, 1) if ready else b""

    def read():
        old = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            b = os.read(fd, 1)
            if b == b"\x1b":
                if _byte(0.05) != b"[":
                    return "esc"          # a bare Escape
                final = _byte(0.05)
                return {b"A": "up", b"B": "down", b"D": "left", b"C": "right",
                        b"H": "home", b"F": "end"}.get(final, "")
            if b and b[0] >= 0x80:
                # A multi-byte character: take the continuation bytes, or the
                # next read() would mistake them for an escape sequence.
                lead = b[0]
                rest = b""
                for _ in (3 if lead >= 0xF0 else 2 if lead >= 0xE0 else 1):
                    more = _byte(0.05)
                    if not more:
                        break
                    rest += more
                try:
                    return (b + rest).decode("utf-8")
                except UnicodeDecodeError:
                    return ""
            return _ACTION.get(b.decode("latin-1"), "")
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)

    return read


# -- display helpers -----------------------------------------------------

def _cut(text, width):
    """Cut to *width* display columns. len() is not enough: a CJK glyph is two
    cells wide, so a 40-character Chinese detail still wraps an 80-column row."""
    text = str(text)
    return text if cell_len(text) <= width else set_cell_size(text, max(0, width - 1)) + "…"


def _label_col(options, rows, width):
    """Where the dim detail column starts: one step past the longest label on
    screen, but never so far right that the detail has no room left."""
    longest = max([cell_len(options[i][0]) for i in rows] or [0])
    return min(longest + 2, max(10, width // 3))


def chosen(console, label, value, extra=""):
    """The permanent one-line record a step leaves behind once answered."""
    line = Text()
    line.append(f"{CHECK} ", style=INK)
    line.append(pad_cell(label, cell_len(label) + 2), style="dim")
    line.append(_cut(value, 40), style="bold")
    if extra:
        line.append("  " + _cut(extra, 46), style="dim")
    console.print(line)


def _header(console, title, sub, note):
    console.print()
    line = Text()
    line.append(title, style="bold")
    if sub:
        line.append("  " + sub, style="dim")
    console.print(line)
    if note:
        console.print(Text("  " + note, style="dim"))


def read_value(console, title, ask, default="", note="", sub="", echo=True):
    """A free-text step, drawn the same way as a choice list.

    ``ask`` takes the remembered default and returns what the user gave.
    ``echo`` is off for values the caller still has to validate, so a path that
    does not exist does not get a ✓ printed before the error about it.
    """
    _header(console, title, sub, note)
    value = ask(default)
    if value and echo:
        chosen(console, title, _cut(value, 60))
    return value


# -- the choice list -----------------------------------------------------

def select(console, title, options, default=0, read_key=None, ask=None,
           sub="", note="", footer=None, filterable=False, page=12,
           echo=True):
    """Ask one question with a list of options.

    ``options`` is a sequence of ``(label, detail)`` pairs; ``detail`` is the dim
    second column. Returns ``(index, cancelled)`` — cancelled is True after Esc
    or Ctrl+D, and the caller decides what quitting means.

    ``ask`` is a ``(prompt, default) -> str`` callable that backs the numbered
    fallback, the only path available when there is no console to read keys
    from. A list the user may also want to type into (model names) should pass
    an explicit "自己填" option and handle it by index — the return contract
    stays ``(index, cancelled)`` either way.
    """
    options = [(o[0], o[1] if len(o) > 1 else "") for o in options]
    n = len(options)
    if n == 0:
        return -1, True
    default = default if 0 <= default < n else 0
    _header(console, title, sub, note)
    if read_key is None:
        return _numbered(console, title, options, default, ask, echo)
    return _arrows(console, title, options, default, read_key, footer,
                   filterable, page, echo)


def _numbered(console, title, options, default, ask, echo):
    """Print the whole list and take a number. Typing the label works too,
    which is what keeps a 200-entry list usable without a filter box."""
    width = console.width
    col = _label_col(options, range(len(options)), width)
    for i, (label, detail) in enumerate(options):
        line = Text()
        line.append(f"  {(i + 1):>2}  ", style="dim")
        line.append(pad_cell(label, col), style="bold" if i == default else "")
        if detail:
            line.append(_cut(detail, width - col - 6), style="dim")
        console.print(line)
    if len(options) > 1:
        console.print(Text("  输入序号，或直接输入名字", style="dim"))

    labels = [o[0] for o in options]
    while True:
        raw = (ask(f"  {CURSOR} ", str(default + 1)) or "").strip()
        if not raw:
            raw = str(default + 1)
        if raw.isdigit():
            num = int(raw)
            if 1 <= num <= len(options):
                return _pick(console, title, options, num - 1, echo)
            console.print(Text(f"  没有第 {raw} 项", style="yellow"))
            continue
        hits = [i for i, s in enumerate(labels) if s.lower() == raw.lower()]
        if len(hits) == 1:
            return _pick(console, title, options, hits[0], echo)
        if len(hits) > 1:
            console.print(Text(f"  {raw} 匹配到多个，请输入序号", style="yellow"))
            continue
        console.print(Text(f"  列表里没有 {raw}", style="yellow"))


def _pick(console, title, options, i, echo):
    if echo:
        chosen(console, title, options[i][0], options[i][1] if len(options[i]) > 1 else "")
    return i, False


def _arrows(console, title, options, default, read_key, footer, filterable,
            page, echo):
    """Redraw the list in place until Enter."""
    width = console.width
    state = {"i": default, "q": ""}

    def rows():
        if not filterable or not state["q"]:
            return list(range(len(options)))
        q = state["q"].lower()
        return [i for i, (label, detail) in enumerate(options)
                if q in label.lower() or q in (detail or "").lower()]

    def clamp():
        r = rows()
        state["i"] = min(max(0, state["i"]), max(0, len(r) - 1))
        return r

    def build():
        r = rows()
        start = 0
        if len(r) > page:
            start = max(0, min(state["i"] - page // 2, len(r) - page))
        shown = r[start:start + page]
        col = _label_col(options, shown, width)
        out = Text()
        if filterable:
            out.append(f"  {CURSOR} ", style=INK)
            out.append((state["q"] or " ") + "  ", style="bold")
            out.append("输入字母过滤 · Backspace 删", style="dim")
            out.append("\n")
        for pos, i in enumerate(shown):
            label, detail = options[i][0], options[i][1]
            sel = (start + pos) == state["i"]
            out.append(f"  {CURSOR} " if sel else "    ", style=INK if sel else "dim")
            out.append(pad_cell(label, col), style="bold" if sel else "")
            if detail:
                out.append(_cut(detail, width - col - 6), style="dim")
            out.append("\n")
        if not shown:
            out.append("  没有匹配项\n", style="dim")
        elif len(r) > page:
            out.append(f"  … 共 {len(r)} 项，方向键继续往下\n", style="dim")
        out.append(footer or "↑↓ 选择 · Enter 确认 · Esc 退出", style="dim")
        return out

    picked = None
    with Live(build(), console=console, transient=True,
              auto_refresh=False) as live:
        while picked is None:
            key = read_key()
            if key == "ctrl-c":
                live.stop()
                console.print()
                raise KeyboardInterrupt
            if key in ("esc", "ctrl-d"):
                return -1, True
            r = rows()
            if key == "enter":
                if r:
                    picked = r[state["i"]]
                continue
            if key == "up":
                state["i"] -= 1
            elif key == "down":
                state["i"] += 1
            elif key == "pgup":
                state["i"] -= page
            elif key == "pgdn":
                state["i"] += page
            elif key == "home":
                state["i"] = 0
            elif key == "end":
                state["i"] = len(r) - 1
            elif key == "backspace":
                state["q"] = state["q"][:-1]
            elif key.isdigit() and not filterable and len(options) <= 9:
                num = int(key) - 1
                if 0 <= num < len(options):
                    picked = num
                    break
            elif filterable and len(key) == 1 and key.isprintable():
                state["q"] += key
                state["i"] = 0
            else:
                continue
            clamp()
            live.update(build(), refresh=True)

    if echo:
        chosen(console, title, options[picked][0], options[picked][1])
    return picked, False


# -- the confirmation card -----------------------------------------------

def card(console, title, rows, footer=""):
    """One rounded box that says what is about to happen.

    ``rows`` are ``(label, value)`` pairs. A value is cut rather than wrapped: a
    wrapped card is taller than the terminal and stops being a summary. The
    border and padding cost six more cells than the text inside, so they come
    off the width before anything is measured.
    """
    width = max(20, min(console.width, 78) - 6)
    col = max([cell_len(l) for l, _ in rows] or [0]) + 2
    body = Text(justify="left")
    for pos, (label, value) in enumerate(rows):
        body.append(pad_cell(label, col), style="dim")
        body.append(_cut(value, width - col))
        if pos + 1 < len(rows):
            body.append("\n")
    console.print()
    console.print(Panel(body, title=Text(title, style="bold"), title_align="left",
                        box=ROUNDED, border_style="dim", padding=(0, 1),
                        expand=False))
    if footer:
        console.print(Text("  " + footer, style="dim"))

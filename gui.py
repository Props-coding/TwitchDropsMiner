from __future__ import annotations

import os
import re
import sys
import shlex
import ctypes
import asyncio
import logging
import plistlib
import tkinter as tk
from pathlib import Path
from collections import abc
from textwrap import dedent
from math import log10, ceil
from dataclasses import dataclass
from functools import cached_property
from datetime import datetime, timedelta, timezone
from tkinter import Tk, StringVar, IntVar
from typing import Any, TypedDict, NoReturn, TYPE_CHECKING

import pystray
from yarl import URL
from PIL import Image as Image_module

if sys.platform == "win32":
    import win32api
    import win32con
    import win32gui

if sys.platform == "darwin":
    import AppKit

from translate import _
from cache import ImageCache
from version import __version__
from exceptions import MinerException, ExitRequest
from utils import resource_path, set_root_icon, webopen, task_wrapper, Game, _T
from gui_kit import (
    theme,
    icon,
    Frame,
    Label,
    IconLabel,
    Separator,
    Dot,
    Surface,
    Panel,
    Button,
    Toggle,
    Checkbox,
    ProgressBar,
    Chip,
    ScrollArea,
    ScrollBar,
    Dropdown,
    TextField,
    Tooltip,
    draw_rounded,
    match_parent_bg,
    bind_click,
    descendants,
    pointer_inside,
    set_window_colors,
)
from constants import (
    SELF_PATH,
    IS_PACKAGED,
    SCRIPTS_PATH,
    WINDOW_TITLE,
    DEFAULT_LANG,
    LOGGING_LEVELS,
    MAX_WEBSOCKETS,
    WS_TOPICS_LIMIT,
    OUTPUT_FORMATTER,
    State,
    PriorityMode,
)
if sys.platform == "win32":
    from registry import RegistryKey, ValueType, ValueNotFound


if TYPE_CHECKING:
    from twitch import Twitch
    from channel import Channel
    from settings import Settings
    from inventory import DropsCampaign, TimedDrop


logger = logging.getLogger("TwitchDrops")
DIGITS = ceil(log10(WS_TOPICS_LIMIT))
px = theme.px


def T(*path: str) -> str:
    # strings introduced with the redesigned interface
    return _("gui", "ui", *path)


def clean(text: str) -> str:
    """
    Strip the decorations older translation strings carry (trailing colons, status glyphs),
    so they can be reused as plain labels.
    """
    text = re.sub(r"[✔❌⏳🌐]", '', text)
    return text.strip().rstrip(':').strip()


def fmt_duration(minutes: int) -> str:
    hours, minutes = divmod(max(minutes, 0), 60)
    if hours:
        return f"{hours}h {minutes:02}m"
    return f"{minutes}m"


def fmt_watch_for(minutes: int) -> str:
    hours, rest = divmod(minutes, 60)
    if hours and not rest:
        return T("overview", "hours").format(hours=hours) if hours > 1 else T("overview", "hour")
    if hours:
        return fmt_duration(minutes)
    return T("overview", "minutes").format(minutes=minutes)


def fmt_viewers(viewers: int | None) -> str:
    if viewers is None:
        return "—"
    if viewers >= 1_000_000:
        return f"{viewers / 1_000_000:.1f}M"
    if viewers >= 1_000:
        return f"{viewers / 1_000:.1f}K"
    return str(viewers)


def fmt_date(stamp: datetime) -> str:
    local = stamp.astimezone()
    return f"{local:%b} {local.day}"


def fmt_datetime(stamp: datetime) -> str:
    return stamp.astimezone().replace(microsecond=0, tzinfo=None).strftime("%Y-%m-%d %H:%M")


def autowrap(label: tk.Label, offset: int = 0) -> None:
    """
    Keep a label's wraplength in sync with the width of its container.
    """
    def update(event: tk.Event[tk.Misc]) -> None:
        width = max(event.width - offset, px(80))
        if int(str(label.cget("wraplength")) or 0) != width:
            label.configure(wraplength=width)

    label.master.bind("<Configure>", update, add=True)


Placement = list[tuple[tk.Misc, dict[str, Any]]]


def responsive(container: tk.Misc, breakpoint: int, wide: Placement, narrow: Placement) -> None:
    """
    Switch a container's children between a wide and a narrow grid layout,
    depending on the container's width.
    """
    state: dict[str, bool | None] = {"wide": None}

    def apply(width: int) -> None:
        is_wide = width >= breakpoint
        if is_wide is state["wide"]:
            return
        state["wide"] = is_wide
        for widget, options in (wide if is_wide else narrow):
            widget.grid_forget()
            widget.grid(**options)

    apply(px(1200))
    container.bind("<Configure>", lambda e: apply(e.width), add=True)


def section_title(master: tk.Misc, title: str, subtitle: str | None = None) -> tk.Frame:
    frame = Frame(master)
    Label(frame, title, font="h2").grid(column=0, row=0, sticky="w")
    if subtitle:
        Label(frame, subtitle, fg="muted").grid(column=0, row=1, sticky="w", pady=(px(2), 0))
    return frame


######################
# GUI ELEMENTS START #
######################


class _TKOutputHandler(logging.Handler):
    def __init__(self, output: GUIManager):
        super().__init__()
        self._output = output

    def emit(self, record):
        self._output.print(self.format(record))


class StatusBadge(tk.Canvas):
    """
    Small circular badge with an icon glyph inside, used by the activity feed and the help page.
    """
    def __init__(
        self,
        master: tk.Misc,
        glyph: str,
        *,
        size: int = 22,
        fill: str = "success",
        fg: str = "panel",
        font: str = "icon_sm",
    ):
        d = px(size)
        super().__init__(master, width=d, height=d, highlightthickness=0, borderwidth=0)
        self._glyph, self._fill, self._fg, self._font = glyph, fill, fg, font
        self.bind("<Configure>", lambda e: self.redraw(), add=True)
        theme.painter(self, self.redraw)

    def redraw(self) -> None:
        self.delete("all")
        match_parent_bg(self)
        d = int(self.cget("width"))
        draw_rounded(self, 0, 0, d, d, radius=d // 2, fill=theme.color(self._fill))
        self.create_text(
            d / 2, d / 2, text=self._glyph, font=theme.font(self._font),
            fill=theme.color(self._fg),
        )


###########################################
# GUI ELEMENTS END / GUI DEFINITION START #
###########################################


class StatusBar:
    """
    The main status line. Shown in the footer and on the overview when not mining.
    """
    def __init__(self, manager: GUIManager):
        self._manager = manager
        self.var = StringVar(manager._root)

    def update(self, text: str):
        self.var.set(text)

    def clear(self):
        self.var.set('')


class _WSEntry(TypedDict):
    status: str
    topics: int


class WebsocketStatus:
    def __init__(self, manager: GUIManager):
        self._manager = manager
        self._items: dict[int, _WSEntry | None] = {i: None for i in range(MAX_WEBSOCKETS)}

    def update(self, idx: int, status: str | None = None, topics: int | None = None):
        if status is None and topics is None:
            raise TypeError("You need to provide at least one of: status, topics")
        entry = self._items.get(idx)
        if entry is None:
            entry = self._items[idx] = _WSEntry(
                status=_("gui", "websocket", "disconnected"), topics=0
            )
        if status is not None:
            entry["status"] = status
        if topics is not None:
            entry["topics"] = topics
        self._update()

    def remove(self, idx: int):
        if idx in self._items:
            del self._items[idx]
            self._update()

    def entries(self) -> list[tuple[int, _WSEntry | None]]:
        return [(idx, self._items.get(idx)) for idx in range(MAX_WEBSOCKETS)]

    def connected_count(self) -> int:
        connected = _("gui", "websocket", "connected")
        return sum(
            1 for item in self._items.values() if item is not None and item["status"] == connected
        )

    def _update(self):
        self._manager.footer.update_connections()
        self._manager.log_page.update_connections()


@dataclass
class LoginData:
    username: str
    password: str
    token: str


class LoginForm:
    """
    Drives the sign-in card on the overview and the account block in the sidebar.
    """
    def __init__(self, manager: GUIManager):
        self._manager = manager
        self._confirm = asyncio.Event()
        self.status: str = ''
        self.user_id: int | None = None
        # True while the backend waits for the user to start or finish the sign-in
        self.requested: bool = False
        self.code: str | None = None
        self.page_url: URL | None = None
        self.update(_("gui", "login", "logged_out"), None)

    def clear(self, login: bool = False, password: bool = False, token: bool = False):
        # credentials form isn't used by the device code flow - kept for API compatibility
        pass

    async def wait_for_login_press(self) -> None:
        self._confirm.clear()
        overview = self._manager.overview
        try:
            overview.set_login_button(True)
            await self._manager.coro_unless_closed(self._confirm.wait())
        finally:
            overview.set_login_button(False)

    def confirm(self) -> None:
        self._confirm.set()

    async def ask_login(self) -> LoginData:
        # username/password login is no longer offered by Twitch to this client,
        # the device code flow below is used instead
        raise ExitRequest()

    async def ask_enter_code(self, page_url: URL, user_code: str) -> None:
        self.update(_("gui", "login", "required"), None)
        self.requested = True
        self.code = None
        self.page_url = page_url
        self._manager.overview.refresh_mode()
        # ensure the window isn't hidden into tray when this runs
        self._manager.grab_attention(sound=False)
        self._manager.print(_("gui", "login", "request"))
        await self.wait_for_login_press()
        self.code = user_code
        self._manager.overview.refresh_mode()
        self._manager.print(f"Enter this code on the Twitch's device activation page: {user_code}")
        await asyncio.sleep(4)
        webopen(page_url)

    def update(self, status: str, user_id: int | None):
        self.status = status
        self.user_id = user_id
        if user_id is not None:
            self.requested = False
            self.code = None
        manager = self._manager
        if getattr(manager, "built", False):
            manager.sidebar.update_account()
            manager.help.update_account()
            manager.overview.refresh_mode()


class CampaignProgress:
    ALMOST_DONE_SECONDS = 10

    def __init__(self, manager: GUIManager):
        self._manager = manager
        self._drop: TimedDrop | None = None
        self._seconds: int = 0
        self._timer_task: asyncio.Task[None] | None = None

    def _divmod(self, minutes: int) -> tuple[int, int]:
        if self._seconds < 60 and minutes > 0:
            minutes -= 1
        hours, minutes = divmod(minutes, 60)
        return (hours, minutes)

    def _update_time(self, seconds: int | None = None):
        if seconds is not None:
            self._seconds = seconds
        drop = self._drop
        if drop is not None:
            drop_minutes = drop.remaining_minutes
            campaign_minutes = drop.campaign.remaining_minutes
        else:
            drop_minutes = 0
            campaign_minutes = 0
        dseconds = self._seconds % 60
        hours, minutes = self._divmod(drop_minutes)
        if hours:
            drop_text = f"{hours}h {minutes:02}m {dseconds:02}s"
        else:
            drop_text = f"{minutes}m {dseconds:02}s"
        hours, minutes = self._divmod(campaign_minutes)
        campaign_text = f"{hours}h {minutes:02}m" if hours else f"{minutes}m"
        self._manager.overview.set_remaining(drop_text, campaign_text)

    async def _timer_loop(self):
        self._update_time(60)
        while self._seconds > 0:
            await asyncio.sleep(1)
            self._seconds -= 1
            self._update_time()
        self._timer_task = None

    def start_timer(self):
        if self._timer_task is None:
            if self._drop is None or self._drop.remaining_minutes <= 0:
                # if we're starting the timer at 0 drop minutes,
                # all we need is a single instant time update setting seconds to 60,
                # to avoid substracting a minute from campaign minutes
                self._update_time(60)
            else:
                self._timer_task = asyncio.create_task(self._timer_loop())

    def stop_timer(self):
        if self._timer_task is not None:
            self._timer_task.cancel()
            self._timer_task = None

    def minute_almost_done(self) -> bool:
        # already or almost done
        return self._timer_task is None or self._seconds <= self.ALMOST_DONE_SECONDS

    def display(self, drop: TimedDrop | None, *, countdown: bool = True, subone: bool = False):
        self._drop = drop
        self.stop_timer()
        self._manager.overview.show_drop(drop)
        self._manager.inv.set_mining(drop.campaign if drop is not None else None)
        if drop is None:
            self._update_time(0)
            return
        if countdown:
            # restart our seconds update timer
            self.start_timer()
        elif subone:
            # display the current remaining time at 0 seconds (after substracting the minute)
            # this is because the watch loop will substract this minute
            # right after the first watch payload returns with a time update
            self._update_time(0)
        else:
            # display full time with no substracting
            self._update_time(60)


class ConsoleOutput:
    """
    Full application output, shown on the Log page.
    """
    def __init__(self, manager: GUIManager, master: tk.Misc):
        self._text = tk.Text(
            master,
            wrap="word",
            state="disabled",
            borderwidth=0,
            highlightthickness=0,
            exportselection=False,
            font=theme.font("mono"),
            padx=px(4),
            spacing1=px(2),
            spacing3=px(2),
            takefocus=1,
        )
        theme.bind(
            self._text,
            background="panel",
            foreground="text2",
            insertbackground="text",
            selectbackground="accent_soft",
            selectforeground="text",
        )
        self._text.tag_configure("stamp")
        theme.painter(
            self._text, lambda: self._text.tag_configure("stamp", foreground=theme.color("dim"))
        )
        self._text.tag_configure("stamp", foreground=theme.color("dim"))
        self.widget = self._text

    def print(self, message: str):
        stamp = datetime.now().strftime("%X")
        lines = message.split('\n')
        self._text.config(state="normal")
        for line in lines:
            self._text.insert("end", f"{stamp}  ", ("stamp",))
            self._text.insert("end", f"{line}\n")
        self._text.see("end")  # scroll to the newly added line
        self._text.config(state="disabled")


class ChannelRow(Surface):
    def __init__(self, channel_list: ChannelList, master: tk.Misc, channel: Channel):
        super().__init__(master, fill=None, border=None, radius=8, takefocus=1, cursor="hand2")
        self._list = channel_list
        self.channel = channel
        self.watching = False
        self.selected = False
        self._hover = False
        self._focused = False
        pad = px(14)
        self._dot = Dot(self, "dim")
        self._dot.grid(column=0, row=0, padx=(pad, px(12)), pady=px(14))
        name_box = Frame(self, bg="panel")
        name_box.grid(column=1, row=0, sticky="w")
        self._name = Label(name_box, channel.name, font="body_sb")
        self._name.grid(column=0, row=0, sticky="w")
        self._chip = Chip(name_box, T("overview", "watching"), variant="accent")
        self._chip.grid(column=1, row=0, sticky="w", padx=(px(8), 0))
        self._chip.grid_remove()
        self._acl = IconLabel(name_box, "people", size="icon_sm", fg="dim")
        self._acl_tip = Tooltip(self._acl, T("overview", "acl_channel"))
        game_box = Frame(self, bg="panel")
        game_box.grid(column=2, row=0, sticky="w", padx=(px(10), 0))
        self._game_icon = IconLabel(game_box, "chat", size="icon_sm", fg="accent_text")
        self._game_icon.grid(column=0, row=0, padx=(0, px(8)))
        self._game = Label(game_box, '', font="small", fg="muted")
        self._game.grid(column=1, row=0, sticky="w")
        self._viewers = Label(self, '', font="body", fg="text2")
        self._viewers.grid(column=3, row=0, sticky="e", padx=(px(10), pad))
        self.columnconfigure(1, minsize=px(170))
        self.columnconfigure(2, weight=1)
        self._containers = [name_box, game_box]
        self.bind("<Configure>", lambda e: self._fit(e.width), add=True)
        self._tooltip = Tooltip(self, self._tooltip_text)
        for widget in (self, *descendants(self)):
            widget.bind("<Enter>", lambda e: self._set_hover(True), add=True)
            widget.bind("<Leave>", lambda e: self._set_hover(pointer_inside(self)), add=True)
            widget.bind("<Button-1>", lambda e: self._click(), add=True)
            widget.bind("<Double-Button-1>", lambda e: self._list.switch_to(self), add=True)
        self.bind("<FocusIn>", lambda e: self._set_focus(True), add=True)
        self.bind("<FocusOut>", lambda e: self._set_focus(False), add=True)
        self.bind("<space>", lambda e: self._click(), add=True)
        self.bind("<Return>", lambda e: self._list.switch_to(self), add=True)
        self.bind("<Up>", lambda e: self._list.move_focus(self, -1), add=True)
        self.bind("<Down>", lambda e: self._list.move_focus(self, 1), add=True)
        self.refresh()

    def _fit(self, width: int) -> None:
        game_box = self._containers[1]
        if width < px(400):
            game_box.grid_remove()
        else:
            game_box.grid()

    def _tooltip_text(self) -> str:
        channel = self.channel
        lines = [channel.name]
        if channel.game is not None:
            lines.append(str(channel.game))
        if channel.online:
            lines.append(clean(_("gui", "channels", "online")))
            drops = T("overview", "drops_enabled" if channel.drops_enabled else "drops_disabled")
            lines.append(drops)
        elif channel.pending_online:
            lines.append(clean(_("gui", "channels", "pending")))
        else:
            lines.append(clean(_("gui", "channels", "offline")))
        if channel.acl_based:
            lines.append(T("overview", "acl_channel"))
        return '\n'.join(lines)

    def _click(self) -> None:
        self.focus_set()
        self._list.select(None if self.selected else self)

    def _set_hover(self, hover: bool) -> None:
        if self._hover != hover:
            self._hover = hover
            self._restyle()

    def _set_focus(self, focused: bool) -> None:
        self._focused = focused
        self._restyle()

    def _restyle(self) -> None:
        if self.selected:
            fill, border = "selected", "accent_border"
        elif self._hover:
            fill, border = "hover", None
        else:
            fill, border = None, None
        if self._focused:
            border = "focus"
        bg = fill or "panel"
        for widget in (*self._containers, *self.winfo_children(), *(
            child for c in self._containers for child in c.winfo_children()
        )):
            if isinstance(widget, tk.Label | tk.Frame):
                theme.bind(widget, background=bg)
            elif isinstance(widget, tk.Canvas):
                widget.event_generate("<Configure>")
        self._bg_token = bg
        self.set_style(fill=fill, border=border)

    def refresh(self) -> None:
        channel = self.channel
        if channel.online:
            dot = "success"
        elif channel.pending_online:
            dot = "warning"
        else:
            dot = "dim"
        theme.bind(self._dot, foreground=dot)
        if channel.online and channel.game is not None:
            game = str(channel.game)
            if not channel.drops_enabled:
                game += f"  ·  {T('overview', 'no_drops')}"
            self._game.configure(text=game)
            self._game_icon.grid()
        else:
            status = "pending" if channel.pending_online else "offline"
            text = clean(_("gui", "channels", status))
            self._game.configure(text=text.capitalize() if text.isupper() else text)
            self._game_icon.grid_remove()
        self._viewers.configure(text=fmt_viewers(channel.viewers) if channel.online else "—")
        theme.bind(self._viewers, foreground="text2" if channel.online else "dim")
        if channel.acl_based:
            self._acl.grid(column=2, row=0, padx=(px(6), 0))
        else:
            self._acl.grid_remove()

    def set_watching(self, watching: bool) -> None:
        self.watching = watching
        if watching:
            self._chip.grid()
        else:
            self._chip.grid_remove()

    def set_selected(self, selected: bool) -> None:
        self.selected = selected
        self._restyle()

    def matches(self, query: str) -> bool:
        if not query:
            return True
        query = query.casefold()
        game = str(self.channel.game or '')
        return query in self.channel.name.casefold() or query in game.casefold()


class ChannelList:
    def __init__(self, manager: GUIManager, master: tk.Misc):
        self._manager = manager
        self._rows: dict[str, ChannelRow] = {}
        self._order: list[str] = []
        self._selected: ChannelRow | None = None
        self._query: str = ''
        panel = self.panel = Panel(master, padding=(0, 18, 0, 10))
        body = panel.body
        body.columnconfigure(0, weight=1)
        body.rowconfigure(2, weight=1)
        header = Frame(body)
        header.grid(column=0, row=0, sticky="ew", padx=px(22))
        header.columnconfigure(0, weight=1)
        Label(header, clean(_("gui", "channels", "name")), font="h2").grid(
            column=0, row=0, sticky="w"
        )
        self._count = Label(header, '', fg="muted")
        self._count.grid(column=0, row=1, sticky="w")
        self._search = TextField(
            header,
            placeholder=T("overview", "search_channels"),
            leading_icon="search",
            clearable=True,
            width=18,
            on_change=self._filter,
        )
        self._search.grid(column=1, row=0, rowspan=2, sticky="e")
        columns = Frame(body)
        columns.grid(column=0, row=1, sticky="ew", padx=px(22), pady=(px(16), px(6)))
        columns.columnconfigure(0, weight=1)
        Label(columns, clean(_("gui", "channels", "headings", "channel")), font="small",
              fg="muted").grid(column=0, row=0, sticky="w")
        Label(columns, clean(_("gui", "channels", "headings", "viewers")), font="small",
              fg="muted").grid(column=1, row=0, sticky="e")
        self._scroll = ScrollArea(body, bg="panel")
        self._scroll.grid(column=0, row=2, sticky="nsew", padx=(px(8), px(2)))
        self._inner = self._scroll.inner
        self._inner.columnconfigure(0, weight=1)
        self._empty = Label(self._inner, T("overview", "no_channels"), fg="muted")
        self._empty.grid(column=0, row=0, pady=px(30))
        self._update_count()

    # helpers

    def _filter(self, query: str) -> None:
        self._query = query.strip()
        self._regrid()

    def _regrid(self) -> None:
        shown = 0
        for i, iid in enumerate(self._order):
            row = self._rows[iid]
            if row.matches(self._query):
                row.grid(column=0, row=i + 1, sticky="ew", pady=px(1))
                shown += 1
            else:
                row.grid_remove()
        if shown:
            self._empty.grid_remove()
        else:
            self._empty.configure(
                text=T("overview", "no_match" if self._order else "no_channels")
            )
            self._empty.grid()

    def _update_count(self) -> None:
        online = sum(1 for row in self._rows.values() if row.channel.online)
        self._count.configure(text=T("overview", "online_count").format(count=online))

    def select(self, row: ChannelRow | None) -> None:
        if self._selected is not None and self._selected is not row:
            self._selected.set_selected(False)
        self._selected = row
        if row is not None:
            row.set_selected(True)
        self._manager.overview.update_switch_target()

    def switch_to(self, row: ChannelRow) -> None:
        self.select(row)
        self._manager._twitch.change_state(State.CHANNEL_SWITCH)

    def move_focus(self, row: ChannelRow, delta: int) -> str:
        visible = [self._rows[i] for i in self._order if self._rows[i].winfo_ismapped()]
        if row in visible:
            idx = visible.index(row) + delta
            if 0 <= idx < len(visible):
                visible[idx].focus_set()
        return "break"

    def focus_first(self) -> None:
        for iid in self._order:
            row = self._rows[iid]
            if row.winfo_ismapped():
                row.focus_set()
                return

    # backend API

    def shrink(self):
        pass

    def clear_watching(self):
        for row in self._rows.values():
            row.set_watching(False)
        self._manager.overview.set_watching(None)

    def set_watching(self, channel: Channel):
        previous = next((r.channel for r in self._rows.values() if r.watching), None)
        self.clear_watching()
        row = self._rows.get(channel.iid)
        if row is not None:
            row.set_watching(True)
            self._scroll.ensure_visible(row)
        self._manager.overview.set_watching(channel)
        if previous is None or previous.id != channel.id:
            self._manager.activity.add_watching(channel)

    def get_selection(self) -> Channel | None:
        if self._selected is None:
            return None
        return self._selected.channel

    def clear_selection(self):
        self.select(None)

    def clear(self):
        for row in self._rows.values():
            row.destroy()
        self._rows.clear()
        self._order.clear()
        self._selected = None
        self._manager.overview.update_switch_target()
        self._regrid()
        self._update_count()

    def display(self, channel: Channel, *, add: bool = False):
        iid = channel.iid
        row = self._rows.get(iid)
        if row is None:
            if not add:
                # the channel isn't on the list and we're not supposed to add it
                return
            row = self._rows[iid] = ChannelRow(self, self._inner, channel)
            self._order.append(iid)
            self._regrid()
        else:
            row.channel = channel
            row.refresh()
        self._update_count()

    def remove(self, channel: Channel):
        iid = channel.iid
        row = self._rows.pop(iid, None)
        if row is None:
            return
        self._order.remove(iid)
        if self._selected is row:
            self.select(None)
        row.destroy()
        self._regrid()
        self._update_count()


class TrayIcon:
    TITLE = "Twitch Drops Miner"

    def __init__(self, manager: GUIManager):
        self._manager = manager
        self.icon: pystray.Icon | None = None  # type: ignore[unused-ignore]
        self._icon_images: dict[str, Image_module.Image] = {
            "pickaxe": Image_module.open(resource_path("icons/pickaxe.ico")),
            "active": Image_module.open(resource_path("icons/active.ico")),
            "idle": Image_module.open(resource_path("icons/idle.ico")),
            "error": Image_module.open(resource_path("icons/error.ico")),
            "maint": Image_module.open(resource_path("icons/maint.ico")),
        }
        self._icon_state: str = "pickaxe"

    def __del__(self) -> None:
        self.stop()
        for icon_image in self._icon_images.values():
            icon_image.close()

    @property
    def state(self) -> str:
        return self._icon_state

    def _shorten(self, text: str, by_len: int, min_len: int) -> str:
        if (text_len := len(text)) <= min_len + 3 or by_len <= 0:
            # cannot shorten
            return text
        return text[:-min(by_len + 3, text_len - min_len)] + "..."

    def get_title(self, drop: TimedDrop | None) -> str:
        if drop is None:
            return self.TITLE
        campaign = drop.campaign
        title_parts: list[str] = [
            f"{self.TITLE}\n",
            f"{campaign.game.name}\n",
            drop.rewards_text(),
            f" {drop.progress:.1%} ({campaign.claimed_drops}/{campaign.total_drops})"
        ]
        min_len: int = 30
        max_len: int = 127
        missing_len = len(''.join(title_parts)) - max_len
        if missing_len > 0:
            # try shortening the reward text
            title_parts[2] = self._shorten(title_parts[2], missing_len, min_len)
            missing_len = len(''.join(title_parts)) - max_len
        if missing_len > 0:
            # try shortening the game name
            title_parts[1] = self._shorten(title_parts[1], missing_len, min_len)
            missing_len = len(''.join(title_parts)) - max_len
        if missing_len > 0:
            raise MinerException(f"Title couldn't be shortened: {''.join(title_parts)}")
        return ''.join(title_parts)

    def _start(self):
        loop = asyncio.get_running_loop()
        drop = self._manager.progress._drop

        # we need this because tray icon lives in a separate thread
        def bridge(func):
            return lambda: loop.call_soon_threadsafe(func)

        menu = pystray.Menu(
            pystray.MenuItem(_("gui", "tray", "show"), bridge(self.restore), default=True),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(_("gui", "tray", "quit"), bridge(self.quit)),
        )
        self.icon = pystray.Icon(
            "twitch_miner", self._icon_images[self._icon_state], self.get_title(drop), menu
        )
        # self.icon.run_detached()
        loop.run_in_executor(None, self.icon.run)

    def stop(self):
        if self.icon is not None:
            self.icon.stop()
            self.icon = None

    def quit(self):
        self._manager.close()

    def minimize(self):
        if sys.platform == "darwin":
            return
        if self.icon is None:
            self._start()
        else:
            self.icon.visible = True
        self._manager._root.withdraw()

    def restore(self):
        if self.icon is not None:
            # self.stop()
            self.icon.visible = False
        self._manager._root.deiconify()

    def notify(
        self, message: str, title: str | None = None, duration: float = 10
    ) -> asyncio.Task[None] | None:
        # do nothing if the user disabled notifications
        if not self._manager._twitch.settings.tray_notifications:
            return None
        if self.icon is not None:
            icon = self.icon  # nonlocal scope bind

            async def notifier():
                icon.notify(message, title)
                await asyncio.sleep(duration)
                icon.remove_notification()

            return asyncio.create_task(notifier())
        return None

    def update_title(self, drop: TimedDrop | None):
        if self.icon is not None:
            self.icon.title = self.get_title(drop)

    def change_icon(self, state: str):
        if state not in self._icon_images:
            raise ValueError("Invalid icon state")
        self._icon_state = state
        if self.icon is not None:
            self.icon.icon = self._icon_images[state]
        self._manager.header.update_state()


#############
# APP SHELL #
#############


class NavItem(Surface):
    def __init__(self, sidebar: Sidebar, master: tk.Misc, name: str, glyph: str, text: str):
        super().__init__(master, fill=None, border=None, radius=8, takefocus=1, cursor="hand2")
        self._sidebar = sidebar
        self.name = name
        self.selected = False
        self._hover = False
        self._focused = False
        self._icon = IconLabel(self, glyph, size="icon", fg="muted", bg="sidebar")
        self._icon.grid(column=0, row=0, padx=(px(16), px(14)), pady=px(10))
        self._label = Label(self, text, font="nav", fg="text2", bg="sidebar")
        self._label.grid(column=1, row=0, sticky="w", padx=(0, px(16)))
        self.columnconfigure(1, weight=1)
        for widget in (self, self._icon, self._label):
            widget.bind("<Enter>", lambda e: self._set_hover(True), add=True)
            widget.bind("<Leave>", lambda e: self._set_hover(pointer_inside(self)), add=True)
            widget.bind("<Button-1>", lambda e: sidebar.navigate(self.name), add=True)
        self.bind("<FocusIn>", lambda e: self._set_focus(True), add=True)
        self.bind("<FocusOut>", lambda e: self._set_focus(False), add=True)
        self.bind("<Return>", lambda e: sidebar.navigate(self.name), add=True)
        self.bind("<space>", lambda e: sidebar.navigate(self.name), add=True)
        self.bind("<Up>", lambda e: sidebar.move_focus(self, -1), add=True)
        self.bind("<Down>", lambda e: sidebar.move_focus(self, 1), add=True)

    def _set_hover(self, hover: bool) -> None:
        if self._hover != hover:
            self._hover = hover
            self._restyle()

    def _set_focus(self, focused: bool) -> None:
        self._focused = focused
        self._restyle()

    def set_selected(self, selected: bool) -> None:
        self.selected = selected
        self._restyle()

    def _restyle(self) -> None:
        if self.selected:
            fill = "selected"
        elif self._hover:
            fill = "hover"
        else:
            fill = None
        bg = fill or "sidebar"
        theme.bind(self._icon, background=bg, foreground="accent_text" if self.selected else "muted")
        theme.bind(
            self._label, background=bg, foreground="accent_text" if self.selected else "text2"
        )
        self._label.configure(font=theme.font("nav_sb" if self.selected else "nav"))
        self.set_style(fill=fill, border="focus" if self._focused else None)

    def redraw(self) -> None:
        super().redraw()
        if getattr(self, "selected", False):
            h = self.winfo_height()
            bar = px(3)
            draw_rounded(
                self, 0, px(8), bar, h - px(8), radius=bar // 2,
                fill=theme.color("accent"), tags="shape",
            )


class Sidebar:
    PRIMARY = ("overview", "inventory", "log")
    SECONDARY = ("settings", "help")

    def __init__(self, manager: GUIManager, master: tk.Misc):
        self._manager = manager
        frame = self.frame = Frame(master, bg="sidebar", width=px(222))
        frame.grid_propagate(False)
        frame.columnconfigure(0, weight=1)
        brand = Frame(frame)
        brand.grid(column=0, row=0, sticky="ew", padx=px(22), pady=(px(26), px(30)))
        logo = tk.Label(brand, image=theme.logo(px(40)), borderwidth=0)
        theme.bind(logo, background="sidebar")
        logo.grid(column=0, row=0, padx=(0, px(14)))
        Label(brand, "Twitch\nDrops Miner", font="brand", justify="left").grid(
            column=1, row=0, sticky="w"
        )
        nav = Frame(frame)
        nav.grid(column=0, row=1, sticky="ew", padx=px(12))
        nav.columnconfigure(0, weight=1)
        titles = {
            "overview": ("home", T("nav", "overview")),
            "inventory": ("inventory", clean(_("gui", "tabs", "inventory"))),
            "log": ("log", T("nav", "log")),
            "settings": ("settings", clean(_("gui", "tabs", "settings"))),
            "help": ("help", clean(_("gui", "tabs", "help"))),
        }
        self.items: dict[str, NavItem] = {}
        irow = 0
        for name in self.PRIMARY:
            item = self.items[name] = NavItem(self, nav, name, *titles[name])
            item.grid(column=0, row=irow, sticky="ew", pady=px(2))
            irow += 1
        Separator(nav).grid(column=0, row=irow, sticky="ew", padx=px(10), pady=px(16))
        irow += 1
        for name in self.SECONDARY:
            item = self.items[name] = NavItem(self, nav, name, *titles[name])
            item.grid(column=0, row=irow, sticky="ew", pady=px(2))
            irow += 1
        frame.rowconfigure(2, weight=1)
        account = Frame(frame)
        account.grid(column=0, row=3, sticky="ew", padx=px(24), pady=(0, px(22)))
        self._account_dot = Dot(account, "dim")
        self._account_dot.grid(column=0, row=0, padx=(0, px(10)))
        self._account_status = Label(account, '', font="body_sb")
        self._account_status.grid(column=1, row=0, sticky="w")
        self._account_link = Button(
            account,
            T("sidebar", "account_settings"),
            icon_after="arrow_right",
            variant="link",
            font="small",
            command=lambda: manager.show_page("help", focus=manager.help.sign_out_button),
        )
        self._account_link.grid(column=1, row=1, sticky="w")

    def navigate(self, name: str) -> None:
        self._manager.show_page(name)
        self.items[name].focus_set()

    def move_focus(self, item: NavItem, delta: int) -> str:
        names = [*self.PRIMARY, *self.SECONDARY]
        idx = names.index(item.name) + delta
        if 0 <= idx < len(names):
            self.items[names[idx]].focus_set()
        return "break"

    def select(self, name: str) -> None:
        for item_name, item in self.items.items():
            item.set_selected(item_name == name)

    def update_account(self) -> None:
        login = self._manager.login
        if login.user_id is not None:
            text = T("sidebar", "connected")
            color = "success"
        else:
            text = login.status
            color = "warning" if login.requested else "dim"
        self._account_status.configure(text=text)
        theme.bind(self._account_dot, foreground=color)


class Header:
    def __init__(self, manager: GUIManager, master: tk.Misc):
        self._manager = manager
        frame = self.frame = Frame(master)
        frame.columnconfigure(0, weight=1)
        self._title = Label(frame, '', font="title")
        self._title.grid(column=0, row=0, sticky="w")
        self._subtitle = Label(frame, '', fg="muted", font="nav")
        self._subtitle.grid(column=0, row=1, sticky="w")
        state = Frame(frame)
        state.grid(column=1, row=0, rowspan=2, padx=(0, px(24)))
        self._dot = Dot(state, "dim")
        self._dot.grid(column=0, row=0, padx=(0, px(10)))
        self._state = Label(state, '', font="nav")
        self._state.grid(column=1, row=0)
        self._pause = Button(
            frame,
            T("header", "pause"),
            icon="pause",
            variant="outline",
            padding=(18, 11),
            font="nav",
            command=self.toggle_pause,
        )
        self._pause.grid(column=2, row=0, rowspan=2)

    def set_page(self, title: str, subtitle: str) -> None:
        self._title.configure(text=title)
        self._subtitle.configure(text=subtitle)

    def toggle_pause(self) -> None:
        twitch = self._manager._twitch
        twitch.set_paused(not twitch.paused)
        self.update_state()

    def update_state(self) -> None:
        twitch = self._manager._twitch
        paused = twitch.paused
        if paused:
            text, color = T("header", "paused"), "warning"
        else:
            text, color = {
                "active": (T("header", "active"), "success"),
                "idle": (T("header", "idle"), "dim"),
                "maint": (T("header", "updating"), "accent_text"),
                "error": (T("header", "stopped"), "danger"),
            }.get(self._manager.tray.state, (T("header", "starting"), "dim"))
        self._state.configure(text=text)
        theme.bind(self._dot, foreground=color)
        # pausing makes no sense once the miner has stopped
        self._pause.set_disabled(self._manager.tray.state == "error")
        self._pause._icon = "play" if paused else "pause"
        self._pause.configure(text=T("header", "resume" if paused else "pause"))
        self._pause.redraw()


class Footer:
    def __init__(self, manager: GUIManager, master: tk.Misc):
        self._manager = manager
        frame = self.frame = Frame(master)
        frame.columnconfigure(4, weight=1)
        self._dot = Dot(frame, "dim")
        self._dot.grid(column=0, row=0, padx=(0, px(10)))
        self._connected = Label(frame, '', font="small")
        self._connected.grid(column=1, row=0)
        Separator(frame, color="border_strong", vertical=True).grid(
            column=2, row=0, sticky="ns", padx=px(16), pady=px(2)
        )
        self._connections = Label(frame, '', font="small", fg="muted")
        self._connections.grid(column=3, row=0)
        status = Label(frame, '', font="small", fg="muted", anchor="w")
        status.configure(textvariable=manager.status.var)
        status.grid(column=4, row=0, sticky="ew", padx=(px(24), px(12)))
        if sys.platform != "darwin":
            Button(
                frame,
                clean(_("gui", "tray", "minimize")),
                icon="tray",
                variant="ghost",
                font="small",
                padding=(10, 5),
                command=manager.tray.minimize,
            ).grid(column=5, row=0)
        self.update_connections()

    def update_connections(self) -> None:
        count = self._manager.websockets.connected_count()
        if count:
            text, color = clean(_("gui", "websocket", "connected")), "success"
        else:
            text, color = clean(_("gui", "websocket", "disconnected")), "dim"
        self._connected.configure(text=text)
        theme.bind(self._dot, foreground=color)
        key = "connection" if count == 1 else "connections"
        self._connections.configure(text=T("footer", key).format(count=count))


#################
# OVERVIEW PAGE #
#################


class StepTrack(tk.Canvas):
    """
    Horizontal campaign progress track: one node per drop.
    """
    MAX_STEPS = 6

    def __init__(self, master: tk.Misc):
        super().__init__(master, width=px(200), highlightthickness=0, borderwidth=0)
        self._steps: list[tuple[str, str]] = []
        line = theme.font("small").metrics("linespace")
        self.configure(height=px(34) + px(12) + line * 2)
        self.bind("<Configure>", lambda e: self.redraw(), add=True)
        theme.painter(self, self.redraw)

    def set_steps(self, steps: list[tuple[str, str]]) -> None:
        # (label, state) where state is one of: claimed, current, pending, locked
        if len(steps) > self.MAX_STEPS:
            current = next(
                (i for i, (_, s) in enumerate(steps) if s == "current"),
                next((i for i, (_, s) in enumerate(steps) if s != "claimed"), 0),
            )
            start = min(max(current - 1, 0), len(steps) - self.MAX_STEPS)
            steps = steps[start:start + self.MAX_STEPS]
        self._steps = steps
        self.redraw()

    def redraw(self) -> None:
        self.delete("all")
        match_parent_bg(self)
        steps = self._steps
        w = self.winfo_width()
        if not steps or w <= 1:
            return
        n = len(steps)
        seg = w / n
        d = px(30)
        cy = px(2) + d // 2
        xs = [int(seg * i + seg / 2) for i in range(n)]
        for i in range(n - 1):
            done = steps[i][1] == "claimed"
            self.create_rectangle(
                xs[i] + d // 2 + px(6), cy, xs[i + 1] - d // 2 - px(6), cy + max(px(1), 1),
                fill=theme.color("success" if done else "border_strong"), width=0,
            )
        for x, (label, state) in zip(xs, steps):
            x0, y0 = x - d // 2, cy - d // 2
            if state == "claimed":
                draw_rounded(self, x0, y0, x0 + d, y0 + d, radius=d // 2,
                             fill=theme.color("success"), tags="node")
                self.create_text(x, cy, text=icon("check"), font=theme.font("icon_sm"),
                                 fill=theme.color("panel"))
            elif state == "current":
                halo = px(4)
                draw_rounded(self, x0 - halo, y0 - halo, x0 + d + halo, y0 + d + halo,
                             radius=d // 2 + halo, fill=theme.color("accent_soft"), tags="node")
                draw_rounded(self, x0, y0, x0 + d, y0 + d, radius=d // 2,
                             fill=theme.color("panel"), border=theme.color("accent"),
                             border_width=px(2), tags="node")
                inner = px(12)
                ix = x - inner // 2
                draw_rounded(self, ix, cy - inner // 2, ix + inner, cy - inner // 2 + inner,
                             radius=inner // 2, fill=theme.color("accent"), tags="node")
            else:
                draw_rounded(self, x0, y0, x0 + d, y0 + d, radius=d // 2,
                             fill=theme.color("panel"), border=theme.color("border_strong"),
                             border_width=px(1), tags="node")
                if state == "locked":
                    self.create_text(x, cy, text=icon("lock"), font=theme.font("icon_sm"),
                                     fill=theme.color("muted"))
            self.create_text(
                x, cy + d // 2 + px(12), text=label, anchor="n", justify="center",
                width=max(int(seg) - px(10), px(40)),
                font=theme.font("small_sb" if state == "current" else "small"),
                fill=theme.color("text" if state == "current" else "muted"),
            )


class OverviewPage:
    def __init__(self, manager: GUIManager, master: tk.Misc):
        self._manager = manager
        self.frame = scroll = ScrollArea(master, fill=True)
        body = scroll.inner
        body.columnconfigure(0, weight=11, uniform="overview")
        body.columnconfigure(1, weight=9, uniform="overview")
        body.rowconfigure(0, weight=3)
        body.rowconfigure(1, weight=2, minsize=px(160))
        gap = px(8)
        self.card = Panel(body, padding=(24, 18))
        self.card.grid(column=0, row=0, sticky="nsew", padx=(px(22), gap), pady=(0, gap))
        self.card.body.columnconfigure(0, weight=1)
        self.card.body.rowconfigure(0, weight=1)
        self._drop_id: str | None = None
        self._campaign_id: str | None = None
        self._watching: Channel | None = None
        self._build_drop_view(self.card.body)
        self._build_empty_view(self.card.body)
        self._build_login_view(self.card.body)
        self._channels_master = body
        self._activity_master = body

    def place_channels(self, panel: Panel) -> None:
        panel.grid(column=1, row=0, sticky="nsew", padx=(px(8), px(22)), pady=(0, px(8)))

    def place_activity(self, panel: Panel) -> None:
        panel.grid(
            column=0, row=1, columnspan=2, sticky="nsew", padx=px(22), pady=(px(8), px(18))
        )

    # views

    def _build_drop_view(self, master: tk.Misc) -> None:
        view = self._drop_view = Frame(master)
        view.columnconfigure(0, weight=1)
        head = Frame(view)
        head.grid(column=0, row=0, sticky="ew")
        head.columnconfigure(1, weight=1)
        self._art = tk.Label(head, borderwidth=0, width=px(54), height=px(54))
        theme.bind(self._art, background="panel")
        self._art.grid(column=0, row=0, rowspan=2, padx=(0, px(18)))
        self._game = Label(head, '', font="h2")
        self._game.grid(column=1, row=0, sticky="sw")
        self._campaign = Label(head, '', fg="muted", font="nav")
        self._campaign.grid(column=1, row=1, sticky="nw")
        Separator(view).grid(column=0, row=1, sticky="ew", pady=px(14))
        Label(view, T("overview", "current_drop").upper(), font="caption", fg="muted").grid(
            column=0, row=2, sticky="w"
        )
        reward = Frame(view)
        reward.grid(column=0, row=3, sticky="ew", pady=(px(10), 0))
        reward.columnconfigure(1, weight=1)
        tile = Surface(
            reward, fill="panel_alt", border="border", radius=10,
            width=px(92), height=px(92),
        )
        tile.grid(column=0, row=0, rowspan=2)
        self._reward_image = tk.Label(tile, borderwidth=0)
        theme.bind(self._reward_image, background="panel_alt")
        self._reward_image.place(relx=0.5, rely=0.5, anchor="center")
        self._reward = Label(reward, '', font="big", justify="left", anchor="w")
        self._reward.grid(column=1, row=0, sticky="sew", padx=(px(20), 0))
        autowrap(self._reward, px(130))
        self._watch_for = Label(reward, '', fg="muted", font="nav")
        self._watch_for.grid(column=1, row=1, sticky="nw", padx=(px(20), 0), pady=(px(4), 0))
        self._bar = ProgressBar(view, height=10)
        self._bar.grid(column=0, row=4, sticky="ew", pady=(px(16), px(8)))
        stats = Frame(view)
        stats.grid(column=0, row=5, sticky="ew")
        stats.columnconfigure(0, weight=1)
        self._percent = Label(stats, '', font="stat", fg="accent_text")
        self._percent.grid(column=0, row=0, sticky="w")
        self._remaining = Label(stats, '', fg="muted", font="nav")
        self._remaining.grid(column=1, row=0, sticky="e")
        Separator(view).grid(column=0, row=6, sticky="ew", pady=px(12))
        watching = Frame(view)
        watching.grid(column=0, row=7, sticky="ew")
        watching.columnconfigure(1, weight=1)
        self._watch_dot = Dot(watching, "dim")
        self._watch_dot.grid(column=0, row=0, padx=(0, px(12)))
        self._watch_label = Label(watching, '', font="nav")
        self._watch_label.grid(column=1, row=0, sticky="w")
        self._switch = Button(
            watching,
            T("overview", "change_channel"),
            icon_after="chevron_right",
            variant="link",
            font="body_sb",
            command=self._change_channel,
        )
        self._switch.grid(column=2, row=0, sticky="e")
        self._switch_tip = Tooltip(self._switch, T("overview", "change_channel_hint"))
        Separator(view).grid(column=0, row=8, sticky="ew", pady=px(12))
        progress_head = Frame(view)
        progress_head.grid(column=0, row=9, sticky="ew")
        progress_head.columnconfigure(0, weight=1)
        Label(progress_head, T("overview", "campaign_progress"), font="nav_sb").grid(
            column=0, row=0, sticky="w"
        )
        self._claimed = Label(progress_head, '', font="nav")
        self._claimed.grid(column=1, row=0, sticky="e")
        self._steps = StepTrack(view)
        self._steps.grid(column=0, row=10, sticky="ew", pady=(px(14), 0))
        self._total = Label(view, '', fg="muted", font="body")
        self._total.grid(column=0, row=11, sticky="e")

    def _build_empty_view(self, master: tk.Misc) -> None:
        view = self._empty_view = Frame(master)
        view.columnconfigure(0, weight=1)
        view.rowconfigure(0, weight=1)
        view.rowconfigure(5, weight=1)
        IconLabel(view, "game", size="icon_xl", fg="dim").grid(column=0, row=1, pady=(0, px(16)))
        Label(view, T("overview", "not_mining"), font="h2").grid(column=0, row=2)
        status = Label(view, '', fg="muted", font="nav", justify="center")
        status.configure(textvariable=self._manager.status.var)
        status.grid(column=0, row=3, pady=(px(6), 0))
        self._paused_hint = Label(view, T("overview", "paused_hint"), fg="warning",
                                  justify="center")
        self._paused_hint.grid(column=0, row=4, pady=(px(10), 0))
        autowrap(status, px(40))

    def _build_login_view(self, master: tk.Misc) -> None:
        view = self._login_view = Frame(master)
        view.columnconfigure(0, weight=1)
        view.rowconfigure(0, weight=1)
        view.rowconfigure(9, weight=1)
        StatusBadge(view, icon("people"), size=56, fill="accent_soft", fg="accent_text",
                    font="icon_lg").grid(column=0, row=1, pady=(0, px(18)))
        Label(view, T("login", "title"), font="h2").grid(column=0, row=2)
        text = Label(view, T("login", "text"), fg="muted", font="nav", justify="center")
        text.grid(column=0, row=3, pady=(px(6), px(20)))
        autowrap(text, px(60))
        self._login_button = Button(
            view,
            T("login", "button"),
            icon="link",
            variant="primary",
            padding=(22, 11),
            font="nav_sb",
            state="disabled",
            command=self._manager.login.confirm,
        )
        self._login_button.grid(column=0, row=4)
        code = self._code_box = Frame(view)
        code.grid(column=0, row=5)
        code.columnconfigure(0, weight=1)
        Label(code, T("login", "enter_code"), fg="muted", font="nav").grid(column=0, row=0)
        tile = Panel(code, fill="panel_alt", padding=(28, 12), radius=10)
        tile.grid(column=0, row=1, pady=px(12))
        self._code = Label(tile.body, '', font="mono_lg", fg="accent_text")
        self._code.grid()
        buttons = Frame(code)
        buttons.grid(column=0, row=2)
        Button(
            buttons, T("login", "open_page"), icon="open", variant="primary",
            command=self._open_activation,
        ).grid(column=0, row=0, padx=px(4))
        Button(
            buttons, T("login", "copy_code"), icon="copy", variant="secondary",
            command=self._copy_code,
        ).grid(column=1, row=0, padx=px(4))
        Label(code, T("login", "waiting"), fg="dim", font="small").grid(
            column=0, row=3, pady=(px(14), 0)
        )

    def _open_activation(self) -> None:
        login = self._manager.login
        if login.page_url is not None:
            webopen(login.page_url)

    def _copy_code(self) -> None:
        login = self._manager.login
        if login.code:
            root = self._manager._root
            root.clipboard_clear()
            root.clipboard_append(login.code)

    def set_login_button(self, enabled: bool) -> None:
        self._login_button.configure(state="normal" if enabled else "disabled")
        if enabled:
            self._login_button.focus_set()

    def refresh_mode(self) -> None:
        login = self._manager.login
        views = (self._drop_view, self._empty_view, self._login_view)
        if login.requested and login.user_id is None:
            shown = self._login_view
            if login.code:
                self._code.configure(text=login.code)
                self._code_box.grid()
                self._login_button.grid_remove()
            else:
                self._code_box.grid_remove()
                self._login_button.grid()
        elif self._manager.progress._drop is not None:
            shown = self._drop_view
        else:
            shown = self._empty_view
            if self._manager._twitch.paused:
                self._paused_hint.grid()
            else:
                self._paused_hint.grid_remove()
        for view in views:
            if view is shown:
                view.grid(column=0, row=0, sticky="nsew")
            else:
                view.grid_remove()

    # data

    def show_drop(self, drop: TimedDrop | None) -> None:
        if drop is not None:
            campaign = drop.campaign
            self._game.configure(text=campaign.game.name)
            self._campaign.configure(text=campaign.name)
            self._reward.configure(text=drop.rewards_text())
            self._watch_for.configure(
                text=T("overview", "watch_for").format(time=fmt_watch_for(drop.required_minutes))
            )
            self._bar.set(drop.progress)
            self._percent.configure(text=f"{drop.progress:.0%}")
            self._claimed.configure(
                text=T("overview", "claimed").format(
                    claimed=campaign.claimed_drops, total=campaign.total_drops
                )
            )
            self._steps.set_steps(self._build_steps(drop))
            if drop.id != self._drop_id or campaign.id != self._campaign_id:
                self._drop_id = drop.id
                self._campaign_id = campaign.id
                asyncio.create_task(task_wrapper(self._load_images)(drop))
        else:
            self._drop_id = self._campaign_id = None
        self.refresh_mode()
        self.update_switch_target()

    async def _load_images(self, drop: TimedDrop) -> None:
        cache = self._manager._cache
        art = await cache.get(drop.campaign.image_url, size=(px(54), px(72)), radius=px(8))
        if drop.id != self._drop_id:
            return
        self._art.configure(image=art, width=0, height=0)
        if drop.benefits:
            reward = await cache.get(drop.benefits[0].image_url, size=(px(76), px(76)))
            if drop.id == self._drop_id:
                self._reward_image.configure(image=reward)
        else:
            self._reward_image.configure(image='')

    @staticmethod
    def _build_steps(current: TimedDrop) -> list[tuple[str, str]]:
        now = datetime.now(timezone.utc)
        drops = sorted(
            current.campaign.drops, key=lambda d: (d.total_required_minutes, d.starts_at)
        )
        steps: list[tuple[str, str]] = []
        for i, drop in enumerate(drops, start=1):
            if drop.is_claimed:
                state = "claimed"
            elif drop is current:
                state = "current"
            elif not drop.preconditions_met or drop.starts_at > now:
                state = "locked"
            else:
                state = "pending"
            steps.append((f"{i}. {drop.rewards_text()}", state))
        return steps

    def set_remaining(self, drop_text: str, campaign_text: str) -> None:
        self._remaining.configure(text=T("overview", "remaining").format(time=drop_text))
        self._total.configure(text=T("overview", "total_remaining").format(time=campaign_text))

    def set_watching(self, channel: Channel | None) -> None:
        self._watching = channel
        if channel is None:
            self._watch_label.configure(text=T("overview", "not_watching"))
            theme.bind(self._watch_dot, foreground="dim")
        else:
            self._watch_label.configure(text=T("overview", "watching_channel").format(
                channel=channel.name
            ))
            theme.bind(self._watch_dot, foreground="success")

    def update_switch_target(self) -> None:
        selection = self._manager.channels.get_selection()
        if selection is not None and (
            self._watching is None or selection.id != self._watching.id
        ):
            self._switch.configure(
                text=T("overview", "switch_to").format(channel=selection.name)
            )
        else:
            self._switch.configure(text=T("overview", "change_channel"))

    def _change_channel(self) -> None:
        channels = self._manager.channels
        if channels.get_selection() is not None:
            self._manager._twitch.change_state(State.CHANNEL_SWITCH)
        else:
            channels.focus_first()


class ActivityFeed:
    MAX_ENTRIES = 50

    def __init__(self, manager: GUIManager, master: tk.Misc):
        self._manager = manager
        self._entries: list[tuple[str, str, str, str]] = []
        panel = self.panel = Panel(master, padding=(0, 16, 0, 8))
        body = panel.body
        body.columnconfigure(0, weight=1)
        body.rowconfigure(1, weight=1)
        header = Frame(body)
        header.grid(column=0, row=0, sticky="ew", padx=px(24), pady=(0, px(8)))
        header.columnconfigure(0, weight=1)
        Label(header, T("activity", "title"), font="h2").grid(column=0, row=0, sticky="w")
        Button(
            header, T("activity", "view_log"), icon_after="arrow_right", variant="link",
            command=lambda: manager.show_page("log"),
        ).grid(column=1, row=0, sticky="e")
        self._scroll = ScrollArea(body, bg="panel")
        self._scroll.grid(column=0, row=1, sticky="nsew", padx=(px(24), px(4)))
        self._list = self._scroll.inner
        self._list.columnconfigure(1, weight=3, uniform="activity")
        self._list.columnconfigure(2, weight=2, uniform="activity")
        self._render()

    def _add(self, kind: str, text: str, detail: str) -> None:
        self._entries.insert(0, (kind, text, detail, datetime.now().strftime("%H:%M")))
        del self._entries[self.MAX_ENTRIES:]
        self._render()

    def add_claim(self, drop: TimedDrop) -> None:
        self._add(
            "claim",
            T("activity", "claimed").format(reward=drop.rewards_text()),
            drop.campaign.game.name,
        )

    def add_watching(self, channel: Channel) -> None:
        self._add(
            "watch",
            T("activity", "watching").format(channel=channel.name),
            str(channel.game or ''),
        )

    def add_event(self, text: str, detail: str = '', kind: str = "info") -> None:
        self._add(kind, text, detail)

    def _render(self) -> None:
        for child in self._list.winfo_children():
            child.destroy()
        if not self._entries:
            Label(self._list, T("activity", "empty"), fg="muted").grid(
                column=0, row=0, columnspan=4, pady=px(24)
            )
            return
        badges = {
            "claim": (icon("check"), "success", "panel"),
            "watch": (icon("play"), "accent_soft", "accent_text"),
            "pause": (icon("pause"), "warning_soft", "warning"),
            "info": (icon("live"), "panel_alt", "muted"),
        }
        for i, (kind, text, detail, stamp) in enumerate(self._entries):
            row = i * 2
            if i:
                Separator(self._list).grid(column=0, row=row - 1, columnspan=4, sticky="ew")
            glyph, fill, fg = badges.get(kind, badges["info"])
            StatusBadge(self._list, glyph, fill=fill, fg=fg).grid(
                column=0, row=row, padx=(0, px(16)), pady=px(9)
            )
            Label(self._list, text, font="body_sb", anchor="w").grid(
                column=1, row=row, sticky="ew"
            )
            Label(self._list, detail, font="small", fg="muted", anchor="w").grid(
                column=2, row=row, sticky="ew", padx=px(12)
            )
            Label(self._list, stamp, font="small", fg="muted").grid(
                column=3, row=row, sticky="e", padx=(0, px(18))
            )


##################
# INVENTORY PAGE #
##################


class DropCard(Panel):
    def __init__(self, master: tk.Misc, drop: TimedDrop):
        super().__init__(master, padding=(14, 14), radius=10)
        self.drop = drop
        body = self.body
        body.columnconfigure(0, weight=1)
        self._title = Label(body, drop.rewards_text(), font="body_sb", justify="center")
        self._title.grid(column=0, row=0, sticky="ew")
        autowrap(self._title)
        self.images = Frame(body)
        self.images.grid(column=0, row=1, pady=px(12))
        self.images.rowconfigure(0, minsize=px(88))
        self._bar = ProgressBar(body, height=6)
        self._bar.grid(column=0, row=2, sticky="ew", padx=px(4))
        self._status = Label(body, '', font="small", fg="muted", justify="center")
        self._status.grid(column=0, row=3, pady=(px(10), 0))
        autowrap(self._status)
        body.rowconfigure(1, weight=1)
        self._tooltip = Tooltip(self, '')
        self.update_progress()

    def update_progress(self) -> None:
        drop = self.drop
        status_fg = "muted"
        bar_color = "accent"
        extra: list[str] = []
        if drop.is_claimed:
            status = "✓  " + clean(_("gui", "inventory", "status", "claimed"))
            status_fg = bar_color = "success"
            progress = 1.0
        elif drop.can_claim:
            status = clean(_("gui", "inventory", "status", "ready_to_claim"))
            status_fg = bar_color = "warning"
            progress = 1.0
        elif drop.current_minutes or drop.can_earn():
            status = T("inventory", "progress").format(
                percent=f"{drop.progress:.1%}", minutes=drop.required_minutes
            )
            progress = drop.progress
            if drop.ends_at < drop.campaign.ends_at:
                # this drop becomes unavailable earlier than the campaign ends
                extra.append(clean(_("gui", "inventory", "ends").format(
                    time=fmt_datetime(drop.ends_at)
                )))
        else:
            progress = drop.progress
            if drop.required_minutes > 0:
                status = T("inventory", "progress").format(
                    percent=f"{drop.progress:.0%}", minutes=drop.required_minutes
                )
            else:
                # required_minutes is zero for subscription-based drops
                status = T("inventory", "subscription")
            if datetime.now(timezone.utc) < drop.starts_at > drop.campaign.starts_at:
                # this drop can only be earned later than the campaign start
                extra.append(_("gui", "inventory", "starts").format(
                    time=fmt_datetime(drop.starts_at)
                ))
            elif drop.ends_at < drop.campaign.ends_at:
                # this drop becomes unavailable earlier than the campaign ends
                extra.append(_("gui", "inventory", "ends").format(
                    time=fmt_datetime(drop.ends_at)
                ))
        self._status.configure(text='\n'.join([status, *extra]))
        theme.bind(self._status, foreground=status_fg)
        self._bar.set(progress, bar_color)
        names = [benefit.name for benefit in drop.benefits]
        self._tooltip.set_text('\n'.join(names) if len(names) > 1 else '')


class CampaignCard(Panel):
    def __init__(self, inventory: InventoryOverview, master: tk.Misc, campaign: DropsCampaign):
        super().__init__(master, padding=(18, 16))
        self.campaign = campaign
        self._inventory = inventory
        body = self.body
        body.columnconfigure(0, weight=1)
        head = Frame(body)
        head.grid(column=0, row=0, sticky="ew")
        head.columnconfigure(1, weight=1)
        self.art = tk.Label(head, borderwidth=0, width=px(60), height=px(80))
        theme.bind(self.art, background="panel")
        self.art.grid(column=0, row=0, rowspan=3, padx=(0, px(18)), sticky="n")
        Label(head, campaign.game.name, font="h3").grid(column=1, row=0, sticky="sw")
        Label(head, campaign.name, fg="muted").grid(column=1, row=1, sticky="nw")
        chips = Frame(head)
        chips.grid(column=1, row=2, sticky="w", pady=(px(8), 0))
        self._status = Chip(chips, '', dot="success")
        self._status.grid(column=0, row=0, padx=(0, px(6)))
        if campaign.eligible:
            Chip(
                chips, clean(_("gui", "inventory", "status", "linked")), dot="success"
            ).grid(column=1, row=0, padx=(0, px(6)))
        else:
            link = Chip(
                chips,
                clean(_("gui", "inventory", "status", "not_linked")),
                dot="danger",
                command=lambda: webopen(campaign.link_url),
            )
            link.grid(column=1, row=0, padx=(0, px(6)))
            Tooltip(link, T("inventory", "link_account"))
        self._mining = Chip(chips, T("inventory", "mining"), variant="accent", icon="game")
        self._mining.grid(column=2, row=0)
        self._mining.grid_remove()
        meta = Frame(head)
        meta.grid(column=2, row=0, rowspan=2, sticky="ne")
        IconLabel(meta, "calendar", size="icon_sm", fg="muted").grid(column=0, row=0)
        self._dates = Label(meta, '', fg="text2")
        self._dates.grid(column=1, row=0, padx=(px(8), 0))
        Tooltip(self._dates, lambda: '\n'.join((
            _("gui", "inventory", "starts").format(time=fmt_datetime(campaign.starts_at)),
            _("gui", "inventory", "ends").format(time=fmt_datetime(campaign.ends_at)),
        )))
        Separator(meta, color="border_strong", vertical=True).grid(
            column=2, row=0, sticky="ns", padx=px(16), pady=px(2)
        )
        IconLabel(meta, "people", size="icon_sm", fg="muted").grid(column=3, row=0)
        acl = campaign.allowed_channels
        if not acl:
            acl_text = T("inventory", "all_channels")
        elif len(acl) == 1:
            acl_text = T("inventory", "channel").format(channel=acl[0].name)
        else:
            acl_text = T("inventory", "channels").format(
                channel=acl[0].name, count=len(acl) - 1
            )
        acl_label = Label(meta, acl_text, fg="text2")
        acl_label.grid(column=4, row=0, padx=(px(8), 0))
        if len(acl) > 1:
            names = [ch.name for ch in acl[:20]]
            if len(acl) > 20:
                names.append(_("gui", "inventory", "and_more").format(amount=len(acl) - 20))
            Tooltip(acl_label, '\n'.join(names))
        self.grid_frame = Frame(body)
        self.grid_frame.grid(column=0, row=1, sticky="ew", pady=(px(16), 0))
        self.cards: list[DropCard] = []
        self._columns = 0
        self.grid_frame.bind("<Configure>", self._relayout, add=True)
        self.refresh_status()

    def add_drop_card(self, drop: TimedDrop) -> DropCard:
        card = DropCard(self.grid_frame, drop)
        self.cards.append(card)
        self._columns = 0  # force a relayout
        self._relayout()
        return card

    def _relayout(self, event: tk.Event[tk.Misc] | None = None) -> None:
        width = self.grid_frame.winfo_width()
        if width <= 1:
            width = self._inventory.content_width()
        gap = px(12)
        columns = max(1, min(len(self.cards), (width + gap) // (px(196) + gap)))
        if columns == self._columns:
            return
        for c in range(max(self._columns, columns, 1)):
            self.grid_frame.columnconfigure(c, weight=0, uniform='')
        for c in range(columns):
            self.grid_frame.columnconfigure(c, weight=1, uniform="drop")
        self._columns = columns
        for i, card in enumerate(self.cards):
            row, col = divmod(i, columns)
            card.grid(
                column=col, row=row, sticky="nsew",
                padx=(0 if col == 0 else gap // 2, 0 if col == columns - 1 else gap // 2),
                pady=(0 if row == 0 else gap // 2, gap // 2),
            )

    def refresh_status(self) -> None:
        campaign = self.campaign
        if campaign.active:
            text, dot = clean(_("gui", "inventory", "status", "active")), "success"
        elif campaign.upcoming:
            text, dot = clean(_("gui", "inventory", "status", "upcoming")), "warning"
        else:
            text, dot = clean(_("gui", "inventory", "status", "expired")), "danger"
        self._status.update_chip(text, dot=dot)
        if campaign.upcoming:
            self._dates.configure(text=T("inventory", "starts").format(
                date=fmt_date(campaign.starts_at)
            ))
        else:
            self._dates.configure(text=T("inventory", "ends").format(
                date=fmt_date(campaign.ends_at)
            ))

    def set_mining(self, mining: bool) -> None:
        if mining:
            self._mining.grid()
        else:
            self._mining.grid_remove()


class InventoryOverview:
    def __init__(self, manager: GUIManager, master: tk.Misc):
        self._manager = manager
        self._cache: ImageCache = manager._cache
        self._settings: Settings = manager._twitch.settings
        self._filters = {
            "not_linked": IntVar(
                master, self._settings.priority_mode is PriorityMode.PRIORITY_ONLY
            ),
            "upcoming": IntVar(master, 1),
            "expired": IntVar(master, 0),
            "excluded": IntVar(master, 0),
            "finished": IntVar(master, 0),
        }
        frame = self.frame = Frame(master)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(1, weight=1)
        bar = Panel(frame, padding=(18, 10), radius=10)
        bar.grid(column=0, row=0, sticky="ew", padx=px(22), pady=(0, px(14)))
        bar.body.columnconfigure(6, weight=1)
        Label(bar.body, clean(_("gui", "inventory", "filter", "show")), fg="text2").grid(
            column=0, row=0, padx=(0, px(16))
        )
        for i, key in enumerate(self._filters, start=1):
            Checkbox(
                bar.body,
                clean(_("gui", "inventory", "filter", key)),
                variable=self._filters[key],
                command=self.refresh,
            ).grid(column=i, row=0, padx=(0, px(18)))
        Button(
            bar.body,
            clean(_("gui", "inventory", "filter", "refresh")),
            icon="refresh",
            command=self.refresh,
        ).grid(column=7, row=0, sticky="e")
        self._scroll = ScrollArea(frame)
        self._scroll.grid(column=0, row=1, sticky="nsew")
        self._main_frame = self._scroll.inner
        self._main_frame.columnconfigure(0, weight=1)
        self._empty = Label(self._main_frame, '', fg="muted", font="nav")
        self._campaigns: dict[DropsCampaign, CampaignCard] = {}
        self._drops: dict[str, DropCard] = {}
        self._mining: DropsCampaign | None = None
        self._update_empty()

    def content_width(self) -> int:
        return max(self._scroll.canvas.winfo_width() - px(44) - px(36), px(200))

    def _update_empty(self) -> None:
        visible = any(card.winfo_manager() for card in self._campaigns.values())
        if visible:
            self._empty.grid_remove()
        else:
            key = "no_campaigns" if self._campaigns else "loading"
            self._empty.configure(text=T("inventory", key))
            self._empty.grid(column=0, row=0, pady=px(60))

    def _update_visibility(self, campaign: DropsCampaign):
        # True if the campaign is supposed to show, False makes it hidden.
        card = self._campaigns[campaign]
        not_linked = bool(self._filters["not_linked"].get())
        expired = bool(self._filters["expired"].get())
        excluded = bool(self._filters["excluded"].get())
        upcoming = bool(self._filters["upcoming"].get())
        finished = bool(self._filters["finished"].get())
        priority_only = self._settings.priority_mode is PriorityMode.PRIORITY_ONLY
        if (
            campaign.required_minutes > 0  # don't show sub-only campaigns
            and (not_linked or campaign.eligible)
            and (campaign.active or upcoming and campaign.upcoming or expired and campaign.expired)
            and (
                excluded or (
                    campaign.game.name not in self._settings.exclude
                    and not priority_only or campaign.game.name in self._settings.priority
                )
            )
            and (finished or not campaign.finished)
        ):
            card.grid()
        else:
            card.grid_remove()

    def on_show(self) -> None:
        self.refresh()

    def refresh(self):
        for campaign, card in self._campaigns.items():
            card.refresh_status()
            self._update_visibility(campaign)
        self._update_empty()

    def set_mining(self, campaign: DropsCampaign | None) -> None:
        if self._mining is not None and (card := self._campaigns.get(self._mining)) is not None:
            card.set_mining(False)
        self._mining = campaign
        if campaign is not None and (card := self._campaigns.get(campaign)) is not None:
            card.set_mining(True)

    async def add_campaign(self, campaign: DropsCampaign) -> None:
        card = CampaignCard(self, self._main_frame, campaign)
        card.grid(
            column=0, row=len(self._campaigns) + 1, sticky="ew", padx=(px(22), px(14)),
            pady=(0, px(14)),
        )
        # NOTE: We have to save the campaign's card before any awaits happen,
        # otherwise the len(self._campaigns) call may overwrite an existing row,
        # if the campaigns are added concurrently.
        self._campaigns[campaign] = card
        if campaign is self._mining or (
            self._mining is not None and campaign.id == self._mining.id
        ):
            card.set_mining(True)
        if self._manager.current_page != "inventory":
            card.grid_remove()
        self._manager.game_art[campaign.game.name] = campaign.image_url
        # Image
        art = await self._cache.get(campaign.image_url, size=(px(60), px(80)), radius=px(8))
        if not card.winfo_exists():
            # the inventory has been cleared in the meantime
            return
        card.art.configure(image=art, width=0, height=0)
        # Drops display
        for drop in campaign.drops:
            drop_card = self._drops[drop.id] = card.add_drop_card(drop)
            size = px(84) if len(drop.benefits) <= 1 else px(60)
            benefit_images = await asyncio.gather(
                *(self._cache.get(benefit.image_url, (size, size)) for benefit in drop.benefits)
            )
            if not card.winfo_exists():
                return
            for i, image in enumerate(benefit_images):
                image_label = tk.Label(drop_card.images, image=image, borderwidth=0)
                theme.bind(image_label, background="panel")
                image_label.grid(column=i % 3, row=i // 3, padx=px(3), pady=px(3))
        if self._manager.current_page == "inventory":
            self._update_visibility(campaign)
            self._update_empty()

    def clear(self) -> None:
        for card in self._campaigns.values():
            card.destroy()
        self._drops.clear()
        self._campaigns.clear()
        self._update_empty()

    def update_drop(self, drop: TimedDrop) -> None:
        card = self._drops.get(drop.id)
        if card is None:
            return
        card.update_progress()


#################
# SETTINGS PAGE #
#################


def proxy_validate(field: TextField, settings: Settings) -> bool:
    raw_url = field.get().strip()
    if raw_url in ('', "http://"):
        field.clear()
        field.set_error(False)
        settings.proxy = URL()
        return True
    field.set(raw_url)
    url = URL(raw_url)
    valid = url.host is not None and url.port is not None
    field.set_error(not valid)
    if not valid:
        url = URL()
    settings.proxy = url
    return valid


class _SettingsVars(TypedDict):
    tray: IntVar
    autostart: IntVar
    dark_mode: IntVar
    language: StringVar
    priority_mode: StringVar
    tray_notifications: IntVar
    enable_badges_emotes: IntVar
    available_drops_check: IntVar


class GameList:
    """
    List of game names inside a bordered panel. Optionally ordered, with drag-and-drop
    and keyboard reordering.
    """
    def __init__(
        self,
        settings_panel: SettingsPanel,
        master: tk.Misc,
        *,
        ordered: bool,
        on_remove: abc.Callable[[str], Any],
        on_move: abc.Callable[[int, int], Any] | None = None,
        empty_icon: str | None = None,
        empty_text: str = '',
    ):
        self._settings_panel = settings_panel
        self._ordered = ordered
        self._on_remove = on_remove
        self._on_move = on_move
        self.panel = Panel(master, padding=(0, 0), radius=10)
        self._body = self.panel.body
        self._body.columnconfigure(0, weight=1)
        self._items: list[str] = []
        self._rows: list[tk.Frame] = []
        self._drag: int | None = None
        self._focus_name: str | None = None
        self._empty = Frame(self._body)
        self._empty.columnconfigure(0, weight=1)
        if empty_icon:
            IconLabel(self._empty, empty_icon, size="icon_xl", fg="dim").grid(
                column=0, row=0, pady=(px(30), px(10))
            )
        Label(self._empty, empty_text, fg="muted").grid(column=0, row=1, pady=(0, px(30)))

    def set_items(self, items: list[str], *, focus: str | None = None) -> None:
        self._items = list(items)
        for row in self._rows:
            row.destroy()
        self._rows = []
        if not items:
            self._empty.grid(column=0, row=0, sticky="nsew", padx=px(16))
            return
        self._empty.grid_remove()
        for i, name in enumerate(items):
            row = self._make_row(i, name)
            row.grid(column=0, row=i * 2 + 1, sticky="ew")
            if i:
                sep = Separator(self._body)
                sep.grid(column=0, row=i * 2, sticky="ew", padx=px(12))
                self._rows.append(sep)
            self._rows.append(row)
            if name == focus:
                row.after_idle(row.focus_set)

    def _make_row(self, index: int, name: str) -> tk.Frame:
        row = Frame(self._body, bg="panel", takefocus=1)
        row.game_name = name  # type: ignore[attr-defined]
        col = 0
        pady = px(9)
        if self._ordered:
            handle = tk.Canvas(
                row, width=px(14), height=px(20), highlightthickness=0, borderwidth=0,
                cursor="fleur",
            )
            theme.bind(handle, background="panel")
            self._draw_handle(handle)
            theme.painter(handle, lambda h=handle: self._draw_handle(h))
            handle.grid(column=col, row=0, padx=(px(14), px(10)), pady=pady)
            Tooltip(handle, T("settings", "drag_hint"))
            handle.bind("<ButtonPress-1>", lambda e, n=name: self._drag_start(n), add=True)
            handle.bind("<B1-Motion>", self._drag_motion, add=True)
            handle.bind("<ButtonRelease-1>", self._drag_end, add=True)
            col += 1
            Label(row, str(index + 1), fg="muted", width=2, anchor="e").grid(
                column=col, row=0, padx=(0, px(14))
            )
            col += 1
            art = tk.Label(row, borderwidth=0)
            theme.bind(art, background="panel")
            art.grid(column=col, row=0, padx=(0, px(14)))
            self._settings_panel.load_art(art, name)
            col += 1
        name_label = Label(row, name, anchor="w")
        name_label.grid(
            column=col, row=0, sticky="ew", padx=(0 if self._ordered else px(16), px(8)),
            pady=pady,
        )
        row.columnconfigure(col, weight=1)
        Button(
            row, icon="close", variant="icon_ghost", padding=(8, 4), radius=6, takefocus=False,
            command=lambda: self._on_remove(name),
        ).grid(column=col + 1, row=0, padx=(0, px(8)))
        row.bind("<FocusIn>", lambda e: self._highlight(row, True), add=True)
        row.bind("<FocusOut>", lambda e: self._highlight(row, False), add=True)
        row.bind("<Delete>", lambda e: self._on_remove(name), add=True)
        row.bind("<BackSpace>", lambda e: self._on_remove(name), add=True)
        row.bind("<Up>", lambda e: self._focus_step(name, -1), add=True)
        row.bind("<Down>", lambda e: self._focus_step(name, 1), add=True)
        for widget in (row, name_label):
            widget.bind("<Button-1>", lambda e: row.focus_set(), add=True)
        if self._ordered:
            for seq, delta in (("<Alt-Up>", -1), ("<Alt-Down>", 1),
                               ("<Control-Up>", -1), ("<Control-Down>", 1)):
                row.bind(seq, lambda e, d=delta: self._keyboard_move(name, d), add=True)
        return row

    @staticmethod
    def _draw_handle(handle: tk.Canvas) -> None:
        handle.delete("all")
        size = max(px(2), 2)
        color = theme.color("dim")
        for cx in (px(3), px(9)):
            for cy in (px(4), px(10), px(16)):
                handle.create_rectangle(cx, cy, cx + size, cy + size, fill=color, width=0)

    def _highlight(self, row: tk.Frame, on: bool) -> None:
        token = "selected" if on else "panel"
        for widget in (row, *row.winfo_children()):
            if isinstance(widget, tk.Label | tk.Frame | tk.Canvas):
                theme.bind(widget, background=token)
            if isinstance(widget, Button):
                widget.redraw()
        if isinstance(row.winfo_children()[0], tk.Canvas):
            self._draw_handle(row.winfo_children()[0])  # type: ignore[arg-type]

    def _row_for(self, name: str) -> tk.Frame | None:
        for row in self._rows:
            if getattr(row, "game_name", None) == name:
                return row
        return None

    def _focus_step(self, name: str, delta: int) -> str:
        idx = self._items.index(name) + delta
        if 0 <= idx < len(self._items):
            row = self._row_for(self._items[idx])
            if row is not None:
                row.focus_set()
        return "break"

    def _keyboard_move(self, name: str, delta: int) -> str:
        idx = self._items.index(name)
        target = idx + delta
        if self._on_move is not None and 0 <= target < len(self._items):
            self._on_move(idx, target)
            row = self._row_for(name)
            if row is not None:
                row.focus_set()
        return "break"

    def _drag_start(self, name: str) -> None:
        self._drag = self._items.index(name)
        row = self._row_for(name)
        if row is not None:
            row.focus_set()

    def _drag_motion(self, event: tk.Event[tk.Misc]) -> None:
        if self._drag is None or self._on_move is None:
            return
        y = event.widget.winfo_pointery()
        for i, name in enumerate(self._items):
            row = self._row_for(name)
            if row is None:
                continue
            top = row.winfo_rooty()
            if top <= y < top + row.winfo_height() and i != self._drag:
                dragged = self._items[self._drag]
                self._on_move(self._drag, i)
                self._drag = i
                moved = self._row_for(dragged)
                if moved is not None:
                    moved.focus_set()
                break

    def _drag_end(self, event: tk.Event[tk.Misc]) -> None:
        self._drag = None


class SettingsPanel:
    AUTOSTART_NAME: str = "TwitchDropsMiner"
    AUTOSTART_KEY: str = "HKCU/Software/Microsoft/Windows/CurrentVersion/Run"

    @cached_property
    def PRIORITY_MODES(self) -> dict[PriorityMode, str]:
        # NOTE: Translation calls have to be deferred here,
        # to allow changing the language before the settings panel is initialized.
        return {
            PriorityMode.PRIORITY_ONLY: _("gui", "settings", "priority_modes", "priority_only"),
            PriorityMode.ENDING_SOONEST: _("gui", "settings", "priority_modes", "ending_soonest"),
            PriorityMode.LOW_AVBL_FIRST: _(
                "gui", "settings", "priority_modes", "low_availability"
            ),
        }

    def __init__(self, manager: GUIManager, master: tk.Misc):
        self._manager = manager
        self._settings: Settings = manager._twitch.settings
        priority_mode = self._settings.priority_mode
        if priority_mode not in self.PRIORITY_MODES:
            priority_mode = PriorityMode.PRIORITY_ONLY
            self._settings.priority_mode = priority_mode
        self._vars: _SettingsVars = {
            "autostart": IntVar(master, 0),
            "language": StringVar(master, _.current),
            "tray": IntVar(master, self._settings.autostart_tray),
            "dark_mode": IntVar(master, int(self._settings.dark_mode)),
            "priority_mode": StringVar(master, self.PRIORITY_MODES[priority_mode]),
            "tray_notifications": IntVar(master, self._settings.tray_notifications),
            "enable_badges_emotes": IntVar(
                master, int(self._settings.enable_badges_emotes)
            ),
            "available_drops_check": IntVar(
                master, int(self._settings.available_drops_check)
            ),
        }
        self._game_names: set[str] = set()
        self.frame = scroll = ScrollArea(master, fill=True)
        body = scroll.inner
        body.columnconfigure(0, weight=11, uniform="settings")
        body.columnconfigure(1, weight=9, uniform="settings")
        left = Frame(body)
        left.columnconfigure(0, weight=1)
        right = Frame(body)
        right.columnconfigure(0, weight=1)
        right.rowconfigure(2, weight=1)
        body.rowconfigure(0, weight=1)
        responsive(
            body,
            px(900),
            wide=[
                (left, dict(column=0, row=0, sticky="nsew", padx=(px(22), px(8)))),
                (right, dict(column=1, row=0, sticky="nsew", padx=(px(8), px(22)))),
            ],
            narrow=[
                (left, dict(column=0, row=0, columnspan=2, sticky="nsew", padx=px(22))),
                (right, dict(column=0, row=1, columnspan=2, sticky="nsew", padx=px(22))),
            ],
        )

        # General section
        general = Panel(left, padding=(22, 16))
        general.grid(column=0, row=0, sticky="ew")
        g = general.body
        g.columnconfigure(1, weight=1)
        Label(g, clean(_("gui", "settings", "general", "name")), font="h2").grid(
            column=0, row=0, columnspan=2, sticky="w", pady=(0, px(6))
        )
        rows: list[tuple[str, str, abc.Callable[[tk.Misc], tk.Misc]]] = [
            (T("settings", "language"), T("settings", "requires_restart"), lambda m: Dropdown(
                m,
                values=list(_.languages),
                variable=self._vars["language"],
                command=lambda value: setattr(self._settings, "language", value),
            )),
            (clean(_("gui", "settings", "general", "autostart")), T("settings", "autostart_desc"),
             lambda m: Toggle(m, variable=self._vars["autostart"], command=self.update_autostart)),
        ]
        if sys.platform != "darwin":
            rows.extend([
                (clean(_("gui", "settings", "general", "tray")), T("settings", "tray_desc"),
                 lambda m: Toggle(
                     m, variable=self._vars["tray"], command=self.update_autostart
                 )),
                (clean(_("gui", "settings", "general", "tray_notifications")),
                 T("settings", "notifications_desc"),
                 lambda m: Toggle(
                     m,
                     variable=self._vars["tray_notifications"],
                     command=lambda: setattr(
                         self._settings,
                         "tray_notifications",
                         bool(self._vars["tray_notifications"].get()),
                     ),
                 )),
            ])
        rows.extend([
            (clean(_("gui", "settings", "general", "dark_mode")), T("settings", "dark_mode_desc"),
             lambda m: Toggle(
                 m, variable=self._vars["dark_mode"], command=self.update_dark_mode
             )),
            (clean(_("gui", "settings", "general", "priority_mode")),
             T("settings", "priority_mode_desc"),
             lambda m: Dropdown(
                 m,
                 values=list(self.PRIORITY_MODES.values()),
                 variable=self._vars["priority_mode"],
                 command=self.priority_mode,
             )),
            (T("settings", "proxy"), T("settings", "requires_restart"), self._make_proxy),
        ])
        self._setting_rows(g, rows, start=1)
        self._vars["autostart"].set(self._query_autostart())

        # Advanced section
        advanced = Panel(left, padding=(22, 16))
        advanced.grid(column=0, row=1, sticky="ew", pady=(px(16), px(18)))
        a = advanced.body
        a.columnconfigure(1, weight=1)
        Label(a, clean(_("gui", "settings", "advanced", "name")), font="h2").grid(
            column=0, row=0, columnspan=2, sticky="w", pady=(0, px(12))
        )
        warning = Panel(a, fill="warning_soft", border="warning_border", radius=8,
                        padding=(16, 12))
        warning.grid(column=0, row=1, columnspan=2, sticky="ew", pady=(0, px(6)))
        warning.body.columnconfigure(1, weight=1)
        IconLabel(warning.body, "warning", size="icon", fg="warning").grid(
            column=0, row=0, sticky="n", padx=(0, px(14)), pady=(px(2), 0)
        )
        warning_text = Label(
            warning.body, _("gui", "settings", "advanced", "warning_text"), fg="warning",
            justify="left", anchor="w",
        )
        warning_text.grid(column=1, row=0, sticky="ew")
        autowrap(warning_text, px(50))
        self._setting_rows(a, [
            (clean(_("gui", "settings", "advanced", "enable_badges_emotes")),
             T("settings", "badges_desc"),
             lambda m: Toggle(
                 m,
                 variable=self._vars["enable_badges_emotes"],
                 command=lambda: setattr(
                     self._settings,
                     "enable_badges_emotes",
                     bool(self._vars["enable_badges_emotes"].get()),
                 ),
             )),
            (clean(_("gui", "settings", "advanced", "available_drops_check")),
             T("settings", "drops_check_desc"),
             lambda m: Toggle(
                 m,
                 variable=self._vars["available_drops_check"],
                 command=lambda: setattr(
                     self._settings,
                     "available_drops_check",
                     bool(self._vars["available_drops_check"].get()),
                 ),
             )),
        ], start=2)

        # Priority section
        priority = Panel(right, padding=(22, 18))
        priority.grid(column=0, row=0, sticky="ew")
        p = priority.body
        p.columnconfigure(0, weight=1)
        section_title(
            p, T("settings", "priority_title"), T("settings", "priority_desc")
        ).grid(column=0, row=0, columnspan=2, sticky="w", pady=(0, px(14)))
        self._priority_entry = TextField(
            p,
            placeholder=clean(_("gui", "settings", "game_name")),
            clearable=True,
            suggestions=lambda text: self._suggest(text, self._settings.priority),
            on_submit=lambda text: self.priority_add(),
        )
        self._priority_entry.grid(column=0, row=1, sticky="ew", padx=(0, px(10)))
        Button(
            p, icon="add", variant="icon", square=True, icon_size="icon",
            command=self.priority_add,
        ).grid(column=1, row=1)
        self._priority_list = GameList(
            self,
            p,
            ordered=True,
            on_remove=self.priority_delete,
            on_move=self.priority_move_to,
            empty_text=T("settings", "priority_empty"),
        )
        self._priority_list.panel.grid(column=0, row=2, columnspan=2, sticky="ew",
                                       pady=(px(14), 0))
        self._priority_list.set_items(self._settings.priority)

        # Exclude section
        exclude = Panel(right, padding=(22, 18))
        exclude.grid(column=0, row=1, sticky="ew", pady=(px(16), 0))
        e = exclude.body
        e.columnconfigure(0, weight=1)
        section_title(
            e, T("settings", "exclude_title"), T("settings", "exclude_desc")
        ).grid(column=0, row=0, columnspan=2, sticky="w", pady=(0, px(14)))
        self._exclude_entry = TextField(
            e,
            placeholder=T("settings", "exclude_search"),
            leading_icon="search",
            clearable=True,
            suggestions=lambda text: self._suggest(text, self._settings.exclude),
            on_submit=lambda text: self.exclude_add(),
        )
        self._exclude_entry.grid(column=0, row=1, sticky="ew", padx=(0, px(10)))
        Button(
            e, icon="add", variant="icon", square=True, icon_size="icon",
            command=self.exclude_add,
        ).grid(column=1, row=1)
        self._exclude_list = GameList(
            self,
            e,
            ordered=False,
            on_remove=self.exclude_delete,
            empty_icon="blocked",
            empty_text=T("settings", "exclude_empty"),
        )
        self._exclude_list.panel.grid(column=0, row=2, columnspan=2, sticky="ew",
                                      pady=(px(14), 0))
        self._exclude_list.set_items(sorted(self._settings.exclude))

        # Reload button
        reload = Frame(right)
        reload.grid(column=0, row=3, sticky="ew", pady=(px(18), px(18)))
        reload.columnconfigure(0, weight=1)
        Label(reload, clean(_("gui", "settings", "reload_text")), fg="muted").grid(
            column=0, row=0, sticky="e", padx=(0, px(18))
        )
        Button(
            reload,
            clean(_("gui", "settings", "reload")),
            icon="refresh",
            variant="primary",
            padding=(24, 11),
            font="nav_sb",
            command=self._manager._twitch.state_change(State.INVENTORY_FETCH),
        ).grid(column=1, row=0)

    def _setting_rows(
        self,
        master: tk.Misc,
        rows: list[tuple[str, str, abc.Callable[[tk.Misc], tk.Misc]]],
        *,
        start: int,
    ) -> None:
        irow = start
        for i, (title, desc, factory) in enumerate(rows):
            if i:
                Separator(master).grid(column=0, row=irow, columnspan=2, sticky="ew")
                irow += 1
            texts = Frame(master)
            texts.grid(column=0, row=irow, sticky="w", pady=px(11))
            Label(texts, title, font="nav").grid(column=0, row=0, sticky="w")
            Label(texts, desc, font="small", fg="muted").grid(column=0, row=1, sticky="w")
            control = factory(master)
            control.grid(
                column=1, row=irow, sticky="ew" if isinstance(control, TextField) else "e",
                padx=(px(24), 0),
            )
            irow += 1

    def _make_proxy(self, master: tk.Misc) -> tk.Misc:
        proxy = str(self._settings.proxy)
        self._proxy = TextField(
            master,
            placeholder="http://username:password@address:port",
            width=16,
            on_submit=lambda text: proxy_validate(self._proxy, self._settings),
        )
        if proxy:
            self._proxy.set(proxy)

        def focus_in(event: tk.Event[tk.Misc]) -> None:
            if not self._proxy.get():
                self._proxy.set("http://")
                self._proxy.entry.icursor("end")

        self._proxy.entry.bind("<FocusIn>", focus_in, add=True)
        self._proxy.entry.bind(
            "<FocusOut>", lambda e: proxy_validate(self._proxy, self._settings), add=True
        )
        return self._proxy

    def _suggest(self, text: str, existing: abc.Collection[str]) -> list[str]:
        query = text.strip().casefold()
        return [
            name for name in sorted(self._game_names.difference(existing), key=str.casefold)
            if query in name.casefold()
        ]

    def load_art(self, label: tk.Label, game_name: str) -> None:
        url = self._manager.game_art.get(game_name)
        if url is None:
            label.configure(
                text=game_name[:1].upper(), font=theme.font("small_sb"), width=3, height=1
            )
            theme.bind(label, foreground="accent_text")
            return

        async def load() -> None:
            image = await self._manager._cache.get(url, size=(px(24), px(32)), radius=px(4))
            if label.winfo_exists():
                label.configure(image=image, text='', width=0, height=0)

        asyncio.create_task(task_wrapper(load)())

    def clear_selection(self) -> None:
        self._manager._root.focus_set()

    def update_dark_mode(self) -> None:
        self._settings.dark_mode = bool(self._vars["dark_mode"].get())
        self._manager.apply_theme(self._settings.dark_mode)

    def _get_self_path(self) -> str:
        # NOTE: we need double quotes in case the path contains spaces
        return f'"{SELF_PATH.resolve()!s}"'

    def _get_autostart_path(self) -> str:
        flags: list[str] = []
        # if applicable, include the current logging level as well
        for lvl_idx, lvl_value in LOGGING_LEVELS.items():
            if lvl_value == self._settings.logging_level:
                if lvl_idx > 0:
                    flags.append(f"-{'v' * lvl_idx}")
                break
        if self._vars["tray"].get():
            flags.append("--tray")
        if not IS_PACKAGED:
            # non-packaged autostart has to be done through the venv path pythonw
            return f"\"{SCRIPTS_PATH / 'pythonw'!s}\" {self._get_self_path()} {' '.join(flags)}"
        return f"{self._get_self_path()} {' '.join(flags)}"

    def _get_linux_autostart_filepath(self) -> Path:
        autostart_folder: Path = Path("~/.config/autostart").expanduser()
        if (config_home := os.environ.get("XDG_CONFIG_HOME")) is not None:
            config_autostart: Path = Path(config_home, "autostart").expanduser()
            if config_autostart.exists():
                autostart_folder = config_autostart
        return autostart_folder / f"{self.AUTOSTART_NAME}.desktop"

    def _get_mac_autostart_filepath(self) -> Path:
        return Path(
            Path.home(), f"Library/LaunchAgents/com.devilxd.{self.AUTOSTART_NAME.lower()}.plist"
        )

    def _query_autostart(self) -> bool:
        if sys.platform == "win32":
            with RegistryKey(self.AUTOSTART_KEY, read_only=True) as key:
                try:
                    value_type, value = key.get(self.AUTOSTART_NAME)
                except ValueNotFound:
                    return False
                # TODO: Consider deleting the old value to avoid autostart errors
                return (
                    value_type is ValueType.REG_SZ
                    and self._get_self_path() in value
                )
        elif sys.platform == "linux":
            autostart_file: Path = self._get_linux_autostart_filepath()
            if not autostart_file.exists():
                return False
            with autostart_file.open('r', encoding="utf8") as file:
                # TODO: Consider deleting the old file to avoid autostart errors
                return self._get_self_path() in file.read()
        elif sys.platform == "darwin":
            plist_file = self._get_mac_autostart_filepath()
            if not plist_file.exists():
                return False
            with plist_file.open('r', encoding="utf8") as file:
                return str(SELF_PATH.resolve()) in file.read()
        return False

    def update_autostart(self) -> None:
        enabled = bool(self._vars["autostart"].get())
        self._settings.autostart_tray = bool(self._vars["tray"].get())
        if sys.platform == "win32":
            if enabled:
                with RegistryKey(self.AUTOSTART_KEY) as key:
                    key.set(
                        self.AUTOSTART_NAME,
                        ValueType.REG_SZ,
                        self._get_autostart_path(),
                    )
            else:
                with RegistryKey(self.AUTOSTART_KEY) as key:
                    key.delete(self.AUTOSTART_NAME, silent=True)
        elif sys.platform == "linux":
            autostart_file: Path = self._get_linux_autostart_filepath()
            if enabled:
                file_contents: str = dedent(
                    f"""
                    [Desktop Entry]
                    Type=Application
                    Name=Twitch Drops Miner
                    Description=Mine timed drops on Twitch
                    Exec=sh -c '{self._get_autostart_path()}'
                    """
                )
                with autostart_file.open('w', encoding="utf8") as file:
                    file.write(file_contents)
            else:
                autostart_file.unlink(missing_ok=True)
        elif sys.platform == "darwin":
            plist_file = self._get_mac_autostart_filepath()

            if enabled:
                command_parts = shlex.split(self._get_autostart_path())
                plist_data = {
                    "Label": f"com.devilxd.{self.AUTOSTART_NAME.lower()}",
                    "ProgramArguments": command_parts,
                    "RunAtLoad": True,
                }
                plist_file.parent.mkdir(parents=True, exist_ok=True)
                with plist_file.open("wb") as file:
                    plistlib.dump(plist_data, file)
            else:
                plist_file.unlink(missing_ok=True)

    def set_games(self, games: set[Game]) -> None:
        self._game_names.update(game.name for game in games)
        # game art may have become available
        self._priority_list.set_items(self._settings.priority)

    def priority_add(self) -> None:
        game_name: str = self._priority_entry.get().strip()
        if not game_name:
            # prevent adding empty strings
            return
        self._priority_entry.clear()
        # add it preventing duplicates
        if game_name not in self._settings.priority:
            self._settings.priority.append(game_name)
            self._settings.alter()
        self._priority_list.set_items(self._settings.priority, focus=game_name)

    def priority_move_to(self, idx: int, insert_idx: int) -> None:
        priority = self._settings.priority
        if not 0 <= idx < len(priority) or not 0 <= insert_idx < len(priority):
            return
        item = priority.pop(idx)
        priority.insert(insert_idx, item)
        self._settings.alter()
        self._priority_list.set_items(priority)

    def priority_delete(self, game_name: str) -> None:
        if game_name in self._settings.priority:
            idx = self._settings.priority.index(game_name)
            self._settings.priority.remove(game_name)
            self._settings.alter()
            remaining = self._settings.priority
            focus = remaining[min(idx, len(remaining) - 1)] if remaining else None
            self._priority_list.set_items(remaining, focus=focus)

    def priority_mode(self, mode_name: str) -> None:
        for value, name in self.PRIORITY_MODES.items():
            if mode_name == name:
                self._settings.priority_mode = value
                break

    def exclude_add(self) -> None:
        game_name: str = self._exclude_entry.get().strip()
        if not game_name:
            # prevent adding empty strings
            return
        self._exclude_entry.clear()
        if game_name not in self._settings.exclude:
            self._settings.exclude.add(game_name)
            self._settings.alter()
        self._exclude_list.set_items(sorted(self._settings.exclude), focus=game_name)

    def exclude_delete(self, game_name: str) -> None:
        if game_name in self._settings.exclude:
            self._settings.exclude.discard(game_name)
            self._settings.alter()
            self._exclude_list.set_items(sorted(self._settings.exclude))


#############
# HELP PAGE #
#############


class HelpTab:
    def __init__(self, manager: GUIManager, master: tk.Misc):
        self._manager = manager
        self._twitch = manager._twitch
        self.frame = scroll = ScrollArea(master, fill=True)
        body = scroll.inner
        body.columnconfigure(0, weight=11, uniform="help")
        body.columnconfigure(1, weight=9, uniform="help")
        gap = px(8)

        # About
        about = Panel(body, padding=(24, 18))
        about.grid(column=0, row=0, columnspan=2, sticky="ew", padx=px(22), pady=(0, gap * 2))
        wide: Placement = []
        narrow: Placement = []
        a = about.body
        a.columnconfigure(1, weight=1)
        tile = Panel(a, fill="panel_alt", padding=(14, 14), radius=12)
        tile.grid(column=0, row=0, rowspan=3, padx=(0, px(22)))
        tk_logo = tk.Label(tile.body, image=theme.logo(px(44)), borderwidth=0)
        theme.bind(tk_logo, background="panel_alt")
        tk_logo.grid()
        texts = Frame(a)
        texts.grid(column=1, row=0, rowspan=3, sticky="ew")
        texts.columnconfigure(0, weight=1)
        Label(texts, "Twitch Drops Miner", font="h2").grid(column=0, row=0, sticky="sw")
        credit = Frame(texts)
        credit.grid(column=0, row=1, sticky="w", pady=(px(4), 0))
        Label(credit, T("help", "created_by"), fg="muted", font="nav").grid(column=0, row=0)
        Button(
            credit, "DevilXD", variant="link", font="nav",
            command=lambda: webopen("https://github.com/DevilXD"),
        ).grid(column=1, row=0, padx=(px(2), px(12)))
        Chip(credit, f"v{__version__}", variant="mono", font="mono").grid(column=2, row=0)
        donate = Label(
            texts,
            "If you like the application and found it useful, "
            "please consider donating a small amount of money to support me. Thank you!",
            fg="dim",
            font="small",
            justify="left",
            anchor="w",
        )
        donate.grid(column=0, row=2, sticky="ew", pady=(px(6), 0))
        autowrap(donate)
        links = Frame(a)
        links.grid(column=2, row=0, rowspan=3, sticky="e", padx=(px(16), 0))
        Button(
            links, T("help", "repository"), icon="open", padding=(18, 11), font="nav",
            command=lambda: webopen("https://github.com/DevilXD/TwitchDropsMiner"),
        ).grid(column=0, row=0, padx=(0, px(12)))
        Button(
            links, T("help", "support"), icon="heart", padding=(18, 11), font="nav",
            command=lambda: webopen("https://www.buymeacoffee.com/DevilXD"),
        ).grid(column=1, row=0)

        # Useful links
        for col, (glyph, title, subtitle, url) in enumerate((
            ("inventory", T("help", "inventory_title"),
             clean(_("gui", "help", "links", "inventory")),
             "https://www.twitch.tv/drops/inventory"),
            ("game", T("help", "campaigns_title"),
             clean(_("gui", "help", "links", "campaigns")),
             "https://www.twitch.tv/drops/campaigns"),
        )):
            card = self._link_card(body, glyph, title, subtitle, url)
            wide.append((card, dict(
                column=col, row=1, sticky="nsew",
                padx=(px(22), gap) if col == 0 else (gap, px(22)), pady=(0, gap * 2),
            )))
            narrow.append((card, dict(
                column=0, row=1 + col, columnspan=2, sticky="nsew", padx=px(22),
                pady=(0, gap * 2),
            )))

        # Getting started
        started = Panel(body, padding=(24, 18))
        wide.append((started, dict(
            column=0, row=2, rowspan=2, sticky="nsew", padx=(px(22), gap), pady=(0, px(18))
        )))
        narrow.append((started, dict(
            column=0, row=3, columnspan=2, sticky="nsew", padx=px(22), pady=(0, gap * 2)
        )))
        s = started.body
        s.columnconfigure(1, weight=1)
        Label(s, clean(_("gui", "help", "getting_started")), font="h2").grid(
            column=0, row=0, columnspan=2, sticky="w", pady=(0, px(8))
        )
        english = _.current == DEFAULT_LANG
        steps: list[tuple[str, str]]
        if english:
            steps = [(title, desc) for title, desc in T("help", "steps")]  # type: ignore[misc]
        else:
            steps = [
                ('', re.sub(r"^\d+\.\s*", '', line))
                for line in _("gui", "help", "getting_started_text").split('\n') if line.strip()
            ]
        irow = 1
        for i, (title, desc) in enumerate(steps, start=1):
            Separator(s).grid(column=0, row=irow, columnspan=2, sticky="ew")
            irow += 1
            StatusBadge(s, str(i), size=30, fill="accent_soft", fg="accent_text",
                        font="body_sb").grid(column=0, row=irow, padx=(0, px(18)), pady=px(9))
            texts = Frame(s)
            texts.grid(column=1, row=irow, sticky="ew")
            texts.columnconfigure(0, weight=1)
            if title:
                Label(texts, title, font="nav_sb").grid(column=0, row=0, sticky="w")
            desc_label = Label(texts, desc, fg="muted", justify="left", anchor="w")
            desc_label.grid(column=0, row=1, sticky="ew")
            autowrap(desc_label)
            irow += 1

        # How it works
        how = Panel(body, padding=(24, 18))
        wide.append((how, dict(
            column=1, row=2, sticky="nsew", padx=(gap, px(22)), pady=(0, gap * 2)
        )))
        narrow.append((how, dict(
            column=0, row=4, columnspan=2, sticky="nsew", padx=px(22), pady=(0, gap * 2)
        )))
        h = how.body
        h.columnconfigure(1, weight=1)
        Label(h, clean(_("gui", "help", "how_it_works")), font="h2").grid(
            column=0, row=0, columnspan=2, sticky="w", pady=(0, px(8))
        )
        if english:
            items = [
                ("search", T("help", "how_checks"), T("help", "how_checks_desc")),
                ("video", T("help", "how_video"), T("help", "how_video_desc")),
                ("live", T("help", "how_live"), T("help", "how_live_desc")),
            ]
            for i, (glyph, title, desc) in enumerate(items):
                row = i * 2 + 2
                Separator(h).grid(column=0, row=row - 1, columnspan=2, sticky="ew")
                StatusBadge(h, icon(glyph), size=40, fill="panel_alt", fg="text2",
                            font="icon").grid(column=0, row=row, sticky="n",
                                              padx=(0, px(18)), pady=px(12))
                texts = Frame(h)
                texts.grid(column=1, row=row, sticky="ew", pady=px(10))
                texts.columnconfigure(0, weight=1)
                Label(texts, title, font="nav_sb").grid(column=0, row=0, sticky="w")
                desc_label = Label(texts, desc, fg="muted", justify="left", anchor="w")
                desc_label.grid(column=0, row=1, sticky="ew")
                autowrap(desc_label)
        else:
            text = Label(
                h, _("gui", "help", "how_it_works_text"), fg="muted", justify="left", anchor="w"
            )
            text.grid(column=0, row=1, columnspan=2, sticky="ew")
            autowrap(text)

        # Account session
        account = Panel(body, padding=(24, 18))
        wide.append((account, dict(
            column=1, row=3, sticky="nsew", padx=(gap, px(22)), pady=(0, px(18))
        )))
        narrow.append((account, dict(
            column=0, row=5, columnspan=2, sticky="nsew", padx=px(22), pady=(0, px(18))
        )))
        responsive(body, px(900), wide, narrow)
        ac = account.body
        ac.columnconfigure(0, weight=1)
        Label(ac, T("help", "account_title"), font="h2").grid(column=0, row=0, sticky="w")
        account_text = Label(ac, T("help", "account_desc"), fg="muted", justify="left",
                             anchor="w")
        account_text.grid(column=0, row=1, sticky="ew", pady=(px(4), px(4)))
        autowrap(account_text)
        self._account = Label(ac, '', fg="dim", font="small")
        self._account.grid(column=0, row=2, sticky="w", pady=(0, px(14)))
        self._invalidate_button: Button = Button(
            ac,
            T("help", "sign_out"),
            icon="sign_out",
            variant="danger",
            padding=(22, 11),
            font="nav",
            command=self.invalidate_token,
            state="disabled",
        )
        self._invalidate_button.grid(column=0, row=3, sticky="w")
        self.sign_out_button = self._invalidate_button

    def _link_card(
        self, master: tk.Misc, glyph: str, title: str, subtitle: str, url: str
    ) -> Panel:
        card = Panel(master, padding=(22, 16), takefocus=1, cursor="hand2")
        b = card.body
        b.columnconfigure(1, weight=1)
        IconLabel(b, glyph, size="icon_lg", fg="accent_text").grid(
            column=0, row=0, rowspan=2, padx=(0, px(20))
        )
        Label(b, title, font="nav_sb").grid(column=1, row=0, sticky="w")
        Label(b, subtitle, fg="muted").grid(column=1, row=1, sticky="w")
        IconLabel(b, "open", size="icon", fg="muted").grid(column=2, row=0, rowspan=2)
        bind_click([card, *descendants(card)], lambda: webopen(url))
        card.bind("<Return>", lambda e: webopen(url), add=True)
        card.bind("<space>", lambda e: webopen(url), add=True)
        for widget in (card, *descendants(card)):
            widget.bind("<Enter>", lambda e: card.set_style(border="accent_border"), add=True)
            widget.bind("<Leave>", lambda e: card.set_style(border="border"), add=True)
        card.bind("<FocusIn>", lambda e: card.set_style(border="focus"), add=True)
        card.bind("<FocusOut>", lambda e: card.set_style(border="border"), add=True)
        return card

    def update_account(self) -> None:
        login = self._manager.login
        if login.user_id is not None:
            self._account.configure(text=T("help", "signed_in").format(user_id=login.user_id))
        else:
            self._account.configure(text=login.status)

    def invalidate_token(self) -> None:
        # sync to async bridge
        asyncio.create_task(task_wrapper(self._invalidate_token)())

    async def _invalidate_token(self) -> None:
        auth_state = await self._twitch.get_auth()
        async with self._twitch.request(
            "POST",
            "https://id.twitch.tv/oauth2/revoke",
            data={
                "client_id": self._twitch._client_type.CLIENT_ID,
                "token": auth_state.access_token,
            }
        ) as response:
            if response.status == 200:
                auth_state.invalidate(delete_cookies=True)
            else:
                logger.error(f"Failed to invalidate the auth token: {response.status}")
        self._twitch.change_state(State.RESTART)


############
# LOG PAGE #
############


class LogPage:
    def __init__(self, manager: GUIManager, master: tk.Misc):
        self._manager = manager
        frame = self.frame = Frame(master)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        output = Panel(frame, padding=(20, 16, 8, 12))
        output.grid(column=0, row=0, sticky="nsew", padx=(px(22), px(8)), pady=(0, px(18)))
        o = output.body
        o.columnconfigure(0, weight=1)
        o.rowconfigure(1, weight=1)
        Label(o, clean(_("gui", "output")), font="h2").grid(
            column=0, row=0, sticky="w", pady=(0, px(10))
        )
        self.output = ConsoleOutput(manager, o)
        self.output.widget.grid(column=0, row=1, sticky="nsew")
        scroll = ScrollBar(o, command=self.output.widget.yview)
        scroll.grid(column=1, row=1, sticky="ns", padx=(px(6), 0))
        self.output.widget.configure(yscrollcommand=scroll.set)
        connections = Panel(frame, padding=(20, 16))
        connections.grid(column=1, row=0, sticky="nsew", padx=(px(8), px(22)), pady=(0, px(18)))
        c = connections.body
        c.columnconfigure(1, weight=1)
        Label(c, clean(_("gui", "websocket", "name")), font="h2").grid(
            column=0, row=0, columnspan=3, sticky="w", pady=(0, px(12))
        )
        self._rows: list[tuple[tk.Label, tk.Label, tk.Label]] = []
        for idx in range(MAX_WEBSOCKETS):
            dot = Dot(c, "dim")
            dot.grid(column=0, row=idx + 1, padx=(0, px(10)), pady=px(5))
            status = Label(c, '', font="small", anchor="w")
            status.grid(column=1, row=idx + 1, sticky="w")
            topics = Label(c, '', font="mono", fg="muted")
            topics.grid(column=2, row=idx + 1, sticky="e", padx=(px(16), 0))
            self._rows.append((dot, status, topics))

    def update_connections(self) -> None:
        connected = _("gui", "websocket", "connected")
        for (idx, entry), (dot, status, topics) in zip(
            self._manager.websockets.entries(), self._rows
        ):
            name = clean(_("gui", "websocket", "websocket").format(id=idx + 1))
            if entry is None:
                status.configure(text=f"{name}  —")
                topics.configure(text='')
                theme.bind(dot, foreground="border_strong")
                continue
            status.configure(text=f"{name}  {entry['status']}")
            topics.configure(text=f"{entry['topics']:>{DIGITS}}/{WS_TOPICS_LIMIT}")
            theme.bind(dot, foreground="success" if entry["status"] == connected else "warning")


##########################################
# GUI DEFINITION END / GUI MANAGER START #
##########################################


class GUIManager:
    PAGES = ("overview", "inventory", "log", "settings", "help")

    def __init__(self, twitch: Twitch):
        self._twitch: Twitch = twitch
        self._poll_task: asyncio.Task[NoReturn] | None = None
        self._close_requested = asyncio.Event()
        self._root = root = Tk(className=WINDOW_TITLE)
        # withdraw immediately to prevent the window from flashing
        self._root.withdraw()
        set_root_icon(root, resource_path("icons/pickaxe.ico"))
        root.title(WINDOW_TITLE)  # window title
        root.bind_all("<KeyPress-Escape>", self.unfocus)  # pressing ESC unfocuses selection
        theme.setup(root)
        theme.set_dark(twitch.settings.dark_mode)
        theme.bind(root, background="bg")
        # Image cache for displaying images
        self._cache = ImageCache(self)
        # game name -> box art URL, filled in as campaigns are added
        self.game_art: dict[str, str] = {}
        self.current_page: str = ''

        # non-visual components the backend talks to
        self.status = StatusBar(self)
        self.tray = TrayIcon(self)
        self.progress = CampaignProgress(self)
        self.websockets = WebsocketStatus(self)
        self.login = LoginForm(self)

        # shell layout: sidebar | main (header, page, footer)
        self.sidebar = Sidebar(self, root)
        self.sidebar.frame.grid(column=0, row=0, sticky="ns")
        Separator(root, vertical=True).grid(column=1, row=0, sticky="ns")
        main = Frame(root, bg="bg")
        main.grid(column=2, row=0, sticky="nsew")
        root.columnconfigure(2, weight=1)
        root.rowconfigure(0, weight=1)
        main.columnconfigure(0, weight=1)
        main.rowconfigure(1, weight=1)
        self.header = Header(self, main)
        self.header.frame.grid(column=0, row=0, sticky="ew", padx=(px(34), px(24)),
                               pady=(px(20), px(16)))
        pages = Frame(main)
        pages.grid(column=0, row=1, sticky="nsew")
        pages.columnconfigure(0, weight=1)
        pages.rowconfigure(0, weight=1)
        Separator(main).grid(column=0, row=2, sticky="ew")
        self.footer = Footer(self, main)
        self.footer.frame.grid(column=0, row=3, sticky="ew", padx=(px(26), px(18)),
                               pady=px(9))

        # pages
        self.overview = OverviewPage(self, pages)
        self.channels = ChannelList(self, self.overview._channels_master)
        self.overview.place_channels(self.channels.panel)
        self.activity = ActivityFeed(self, self.overview._activity_master)
        self.overview.place_activity(self.activity.panel)
        self.inv = InventoryOverview(self, pages)
        self.log_page = LogPage(self, pages)
        self.output = self.log_page.output
        self.settings = SettingsPanel(self, pages)
        self.help = HelpTab(self, pages)
        self._pages: dict[str, tuple[tk.Misc, str, str]] = {
            "overview": (self.overview.frame, T("nav", "overview"), T("pages", "overview")),
            "inventory": (
                self.inv.frame, clean(_("gui", "tabs", "inventory")), T("pages", "inventory")
            ),
            "log": (self.log_page.frame, T("nav", "log"), T("pages", "log")),
            "settings": (
                self.settings.frame, clean(_("gui", "tabs", "settings")), T("pages", "settings")
            ),
            "help": (self.help.frame, T("pages", "help_title"), T("pages", "help")),
        }
        self.built = True
        self.login.update(self.login.status, self.login.user_id)
        self.overview.set_watching(None)
        self.overview.refresh_mode()
        self.header.update_state()
        self.websockets._update()
        self.show_page("overview")
        for i, name in enumerate(self.PAGES, start=1):
            root.bind_all(f"<Control-Key-{i}>", lambda e, n=name: self.show_page(n))
        root.bind_all("<MouseWheel>", self._on_mousewheel, add=True)
        root.bind_all("<Button-4>", self._on_mousewheel, add=True)
        root.bind_all("<Button-5>", self._on_mousewheel, add=True)
        root.bind_all("<FocusIn>", self._on_focus, add=True)
        # clamp minimum window size
        width = min(px(1280), root.winfo_screenwidth() - px(40))
        height = min(px(860), root.winfo_screenheight() - px(80))
        root.geometry(f"{width}x{height}")
        root.minsize(width=px(1000), height=px(640))
        # register logging handler
        self._handler = _TKOutputHandler(self)
        self._handler.setFormatter(OUTPUT_FORMATTER)
        logger = logging.getLogger("TwitchDrops")
        logger.addHandler(self._handler)
        if (logging_level := logger.getEffectiveLevel()) < logging.ERROR:
            self.print(f"Logging level: {logging.getLevelName(logging_level)}")
        # gracefully handle Windows shutdown closing the application
        if sys.platform == "win32":
            # NOTE: this root.update() is required for the below to work - don't remove
            root.update()
            self._message_map = {
                # window close request
                win32con.WM_CLOSE: self.close,
                # shutdown request
                win32con.WM_QUERYENDSESSION: self.close,
            }
            # This hooks up the wnd_proc function as the message processor for the root window.
            self.old_wnd_proc = win32gui.SetWindowLong(
                self._handle, win32con.GWL_WNDPROC, self.wnd_proc
            )
            # This ensures all of this works when the application is withdrawn or iconified
            ctypes.windll.user32.ShutdownBlockReasonCreate(
                self._handle, ctypes.c_wchar_p(_("gui", "status", "exiting"))
            )
            # DEV NOTE: use this to remove the reason in the future
            # ctypes.windll.user32.ShutdownBlockReasonDestroy(self._handle)
        else:
            # use old-style window closing protocol for non-windows platforms
            root.protocol("WM_DELETE_WINDOW", self.close)
            root.protocol("WM_DESTROY_WINDOW", self.close)
        self.apply_theme(self._twitch.settings.dark_mode)
        # stay hidden in tray if needed, otherwise show the window when everything's ready
        if self._twitch.settings.tray and sys.platform != "darwin":
            # NOTE: this starts the tray icon thread
            self._root.after_idle(self.tray.minimize)
        else:
            self._root.after_idle(self._root.deiconify)

    def wnd_proc(self, hwnd, msg, w_param, l_param):
        """
        This function serves as a message processor for all messages sent
        to the application by Windows.
        """
        if msg == win32con.WM_DESTROY:
            win32api.SetWindowLong(self._handle, win32con.GWL_WNDPROC, self.old_wnd_proc)
        if msg in self._message_map:
            return self._message_map[msg](w_param, l_param)
        return win32gui.CallWindowProc(self.old_wnd_proc, hwnd, msg, w_param, l_param)

    @cached_property
    def _handle(self) -> int:
        return int(self._root.wm_frame(), 16)

    # navigation and input

    def show_page(self, name: str, *, focus: tk.Misc | None = None) -> None:
        if name not in self._pages:
            return
        for page_name, (frame, _title, _subtitle) in self._pages.items():
            if page_name == name:
                frame.grid(column=0, row=0, sticky="nsew")
            else:
                frame.grid_remove()
        frame, title, subtitle = self._pages[name]
        self.header.set_page(title, subtitle)
        self.sidebar.select(name)
        self.current_page = name
        if name == "inventory":
            self.inv.on_show()
        if focus is not None:
            focus.focus_set()

    @staticmethod
    def _scroll_area_of(widget: tk.Misc | None) -> abc.Iterator[ScrollArea]:
        while widget is not None and not isinstance(widget, tk.Toplevel):
            if isinstance(widget, ScrollArea):
                yield widget
            widget = widget.master

    def _on_mousewheel(self, event: tk.Event[tk.Misc]) -> None:
        try:
            widget = self._root.winfo_containing(event.x_root, event.y_root)
        except (tk.TclError, KeyError):
            return
        if event.num == 4:
            units = -3
        elif event.num == 5:
            units = 3
        elif event.delta:
            steps = event.delta / 120 if abs(event.delta) >= 120 else (1 if event.delta > 0 else -1)
            units = -int(steps * 3)
        else:
            return
        for area in self._scroll_area_of(widget):
            if area.can_scroll():
                area.scroll(units)
                return

    def _on_focus(self, event: tk.Event[tk.Misc]) -> None:
        widget = event.widget
        if not isinstance(widget, tk.Misc):
            return
        for area in self._scroll_area_of(widget.master):
            area.ensure_visible(widget)
            break

    @property
    def running(self) -> bool:
        return self._poll_task is not None

    @property
    def close_requested(self) -> bool:
        return self._close_requested.is_set()

    async def wait_until_closed(self):
        # wait until the user closes the window
        await self._close_requested.wait()

    async def coro_unless_closed(self, coro: abc.Awaitable[_T]) -> _T:
        # In Python 3.11, we need to explicitly wrap awaitables
        tasks = [asyncio.ensure_future(coro), asyncio.ensure_future(self._close_requested.wait())]
        done: set[asyncio.Task[Any]]
        pending: set[asyncio.Task[Any]]
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        if self._close_requested.is_set():
            raise ExitRequest()
        return await next(iter(done))

    def prevent_close(self):
        self._close_requested.clear()

    def start(self):
        if self._poll_task is None:
            self._poll_task = asyncio.create_task(self._poll())

    def stop(self):
        self.progress.stop_timer()
        if self._poll_task is not None:
            self._poll_task.cancel()
            self._poll_task = None

    async def _poll(self):
        """
        This runs the Tkinter event loop via asyncio instead of calling mainloop.
        0.05s gives similar performance and CPU usage.
        Not ideal, but the simplest way to avoid threads, thread safety,
        loop.call_soon_threadsafe, futures and all of that.

        Uses TKINTER_DONT_WAIT to prevent Tcl/Tk from hanging inside native
        system calls (e.g. X11/Wayland input contexts) during heavy UI redraws.
        """
        do_one_event = self._root.dooneevent
        # TKINTER_DONT_WAIT (1 << 1) tells Tcl to return immediately
        # if no events are ready in the queue.
        DONT_WAIT = 1 << 1

        while True:
            try:
                # Drain pending Tk events non-blockingly
                while do_one_event(DONT_WAIT):
                    pass
            except tk.TclError:
                # Root window was destroyed
                break
            await asyncio.sleep(0.05)

        self._poll_task = None

    def close(self, *args) -> int:
        """
        Requests the GUI application to close.
        The window itself will be closed in the closing sequence later.
        """
        self._close_requested.set()
        # notify client we're supposed to close
        self._twitch.close()
        return 0

    def close_window(self):
        """
        Closes the window. Invalidates the logger.
        """
        self.tray.stop()
        logging.getLogger("TwitchDrops").removeHandler(self._handler)
        self._root.destroy()

    def unfocus(self, event):
        # support pressing ESC to unfocus
        self._root.focus_set()
        self.channels.clear_selection()
        self.settings.clear_selection()

    # these are here to interface with underlaying GUI components
    def save(self, *, force: bool = False) -> None:
        self._cache.save(force=force)

    def grab_attention(self, *, sound: bool = True):
        self.tray.restore()
        self._root.focus_set()
        if sound:
            self._root.bell()

    def set_games(self, games: set[Game]) -> None:
        self.settings.set_games(games)

    def display_drop(
        self, drop: TimedDrop, *, countdown: bool = True, subone: bool = False
    ) -> None:
        self.progress.display(drop, countdown=countdown, subone=subone)  # overview
        # inventory overview is updated from within drops themselves via change events
        self.tray.update_title(drop)  # tray

    def clear_drop(self):
        self.progress.display(None)
        self.tray.update_title(None)

    def drop_claimed(self, drop: TimedDrop) -> None:
        self.activity.add_claim(drop)

    def paused_changed(self, paused: bool) -> None:
        self.activity.add_event(
            T("activity", "paused" if paused else "resumed"), kind="pause" if paused else "info"
        )
        self.header.update_state()
        self.overview.refresh_mode()

    def print(self, message: str):
        # print to our custom output
        self.output.print(message)

    def apply_theme(self, dark: bool) -> None:
        """
        Switch between the dark and light palettes.
        """
        # Setting theme for macOS
        if sys.platform == "darwin":
            app = AppKit.NSApplication.sharedApplication()
            if dark:
                appearance = AppKit.NSAppearance.appearanceNamed_(AppKit.NSAppearanceNameDarkAqua)
            else:
                appearance = AppKit.NSAppearance.appearanceNamed_(AppKit.NSAppearanceNameAqua)
            app.setAppearance_(appearance)
        theme.set_dark(dark)
        set_window_colors(self._root, dark=dark)


###################
# GUI MANAGER END #
###################


if __name__ == "__main__":
    # Everything below is for debug purposes only
    import aiohttp
    from functools import partial
    from types import SimpleNamespace
    from gui_kit import enable_dpi_awareness

    class StrNamespace(SimpleNamespace):
        __hash__ = object.__hash__  # type: ignore

        def __str__(self):
            if hasattr(self, "_str__"):
                return self._str__(self)
            return super().__str__()

    class HashNamespace(SimpleNamespace):
        __hash__ = object.__hash__  # type: ignore

    def create_game(id: int, name: str):
        return StrNamespace(name=name, id=id, _str__=lambda s: s.name)

    iid = 0

    def create_channel(
        name: str,
        status: int,
        game: str | None,
        drops: bool,
        viewers: int,
        acl_based: bool,
    ):
        # status: 0 -> OFFLINE, 1 -> PENDING_ONLINE, 2 -> ONLINE
        if status == 1:
            status = False
            pending = True
        else:
            pending = False
        if game is not None:
            game_obj: StrNamespace | None = create_game(0, game)
        else:
            game_obj = None
        global iid
        iid += 1
        return SimpleNamespace(
            name=name,
            id=iid,
            iid=str(iid),
            online=bool(status),
            pending_online=pending,
            game=game_obj,
            drops_enabled=drops,
            viewers=viewers,
            acl_based=acl_based,
        )

    def create_drop(
        campaign_name: str,
        game_name: str,
        rewards: list[str],
        claimed_drops: int,
        total_drops: int,
        current_minutes: int,
        total_minutes: int,
    ):
        cd = claimed_drops
        td = total_drops
        cm = current_minutes
        tm = total_minutes
        ref_stamp = datetime.now(timezone.utc)
        drop_image_url = (
            "https://static-cdn.jtvnw.net/twitch-quests-assets/"
            "REWARD/e0ede26e-b071-47f0-af5f-b80b26fa9fb4.png"
        )
        campaign_image_url = "https://static-cdn.jtvnw.net/ttv-boxart/515025-120x160.jpg"
        benefits = [SimpleNamespace(name=name, image_url=drop_image_url) for name in rewards]
        mock = SimpleNamespace(
            id="0",
            campaign=HashNamespace(
                name=campaign_name,
                id="campaign",
                game=create_game(0, game_name),
                expired=False,
                active=False,
                upcoming=True,
                eligible=False,
                finished=False,
                link_url="https://google.com",
                image_url=campaign_image_url,
                allowed_channels=[],
                starts_at=ref_stamp,
                ends_at=ref_stamp + timedelta(days=7),
                timed_drops={},
                claimed_drops=cd,
                total_drops=td,
                required_minutes=tm,
                remaining_drops=td - cd,
                progress=(cd * tm + cm) / (td * tm),
                remaining_minutes=(td - cd) * tm - cm,
            ),
            image_url=drop_image_url,
            can_claim=False,
            can_earn=lambda: False,
            is_claimed=False,
            preconditions_met=True,
            benefits=benefits,
            rewards_text=lambda: ', '.join(b.name for b in benefits),
            starts_at=ref_stamp + timedelta(seconds=2),
            ends_at=ref_stamp + timedelta(days=7) - timedelta(seconds=2),
            progress=cm/tm,
            current_minutes=cm,
            required_minutes=tm,
            total_required_minutes=tm,
            remaining_minutes=tm-cm,
        )
        mock.campaign.timed_drops["0"] = mock
        mock.campaign.drops = mock.campaign.timed_drops.values()
        return mock

    async def main(exit_event: asyncio.Event):
        # Initialize GUI debug
        mock = SimpleNamespace(
            paused=False,
            settings=SimpleNamespace(
                tray=False,
                priority=[],
                proxy=URL(),
                dark_mode=True,
                alter=lambda: None,
                language="English",
                autostart_tray=False,
                exclude={"Lit Game"},
                tray_notifications=True,
                enable_badges_emotes=False,
                available_drops_check=False,
                logging_level=LOGGING_LEVELS[0],
                priority_mode=PriorityMode.PRIORITY_ONLY,
            )
        )
        mock.change_state = lambda state: mock.gui.print(f"State change: {state.value}")
        mock.state_change = lambda state: partial(mock.change_state, state)

        def set_paused(paused: bool) -> None:
            mock.paused = paused
            mock.gui.paused_changed(paused)

        mock.set_paused = set_paused
        mock.request = aiohttp.request
        # _.set_language("Русский")
        gui = GUIManager(mock)  # type: ignore
        mock.gui = gui
        mock.close = gui.stop
        gui.start()
        assert gui._poll_task is not None
        gui._poll_task.add_done_callback(lambda t: exit_event.set())
        # Login form
        gui.login.update("Login required", None)
        # Game selector and settings panel games
        gui.set_games(set([
            create_game(420690, "Lit Game"),
            create_game(123456, "Best Game"),
            create_game(654321, "My Game Very Long Name"),
        ]))
        # Channel list
        gui.channels.display(
            create_channel(
                name="Thomus",
                status=0,
                game=None,
                drops=False,
                viewers=0,
                acl_based=True,
            ),
            add=True,
        )
        channel = create_channel(
            name="Traitus", status=1, game=None, drops=False, viewers=0, acl_based=True
        )
        gui.channels.display(channel, add=True)
        gui.channels.set_watching(channel)
        gui.channels.display(
            create_channel(
                name="Testus",
                status=2,
                game="Best Game",
                drops=True,
                viewers=42,
                acl_based=False,
            ),
            add=True,
        )
        gui.channels.display(
            create_channel(
                name="Livus",
                status=2,
                game="Best Game",
                drops=True,
                viewers=69,
                acl_based=False,
            ),
            add=True,
        )
        gui._root.update()
        gui.channels.get_selection()
        # Inventory overview
        drop = create_drop(
            "Wardrobe Cleaning", "Cleaning Masters", ["Fancy Pants"], 2, 7, 0, 240
        )
        campaign = drop.campaign
        await gui.inv.add_campaign(campaign)

        gui.print("Single-line test message")
        await asyncio.sleep(1)
        gui.print("Multi-line\ntest\nmessage")

        # Tray
        # gui.tray.minimize()
        await asyncio.sleep(2)
        claim_text = (
            f"{campaign.game.name}\n"
            f"{drop.rewards_text()} ({campaign.claimed_drops}/{campaign.total_drops})"
        )
        gui.tray.notify(claim_text, "Mined Drop")
        gui.drop_claimed(drop)

        # Drop progress
        gui.display_drop(drop, countdown=False)
        await asyncio.sleep(3)

        gui.progress.start_timer()
        await asyncio.sleep(5)

        gui.clear_drop()
        await asyncio.sleep(5)

        campaign.can_earn = lambda: True
        gui.inv.update_drop(drop)
        gui.display_drop(drop)
        await asyncio.sleep(10)

        drop.current_minutes = 239
        drop.remaining_minutes = 1
        drop.progress = 239/240
        campaign.remaining_minutes -= 1
        gui.inv.update_drop(drop)
        gui.display_drop(drop)
        await asyncio.sleep(63)

        drop.current_minutes = 240
        drop.remaining_minutes = 0
        drop.progress = 1.0
        campaign.remaining_minutes -= 1
        campaign.progress = 3/7
        campaign.claimed_drops = 3
        campaign.remaining_drops = 4
        gui.inv.update_drop(drop)
        gui.display_drop(drop)

    def main_exit(task: asyncio.Task[None]) -> None:
        if task.exception() is not None:
            exit_event.set()

    enable_dpi_awareness()
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    exit_event = asyncio.Event()
    main_task = loop.create_task(main(exit_event))
    main_task.add_done_callback(main_exit)
    loop.run_until_complete(exit_event.wait())
    if main_task.done():
        loop.run_until_complete(main_task)

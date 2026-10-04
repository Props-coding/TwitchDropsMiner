"""
Shared look-and-feel for the GUI: theme tokens, fonts, icons and reusable widgets.

Everything here is plain Tkinter. Rounded shapes are painted on canvases using
anti-aliased corner images rendered with Pillow, since the Tk canvas itself
doesn't anti-alias. Colors are referenced by token names and re-applied when
the theme changes, so widgets never hold on to hard-coded colors.
"""
from __future__ import annotations

import sys
import ctypes
import tkinter as tk
from collections import abc
from typing import Any, Union
from tkinter.font import Font, families

from PIL import Image as Image_module, ImageDraw
from PIL.ImageTk import PhotoImage

from utils import resource_path


Token = Union[str, None]

PALETTES: dict[str, dict[str, str]] = {
    "dark": {
        "bg": "#111015",
        "sidebar": "#141318",
        "panel": "#18171d",
        "panel_alt": "#1e1d24",
        "field": "#131217",
        "border": "#2a2831",
        "border_strong": "#3a3743",
        "hover": "#211f28",
        "selected": "#231e30",
        "text": "#efedf4",
        "text2": "#d2cedb",
        "muted": "#a29eae",
        "dim": "#7a7682",
        "disabled": "#5f5b68",
        "accent": "#8b5cf6",
        "accent_hover": "#9b72f8",
        "accent_text": "#b99bff",
        "accent_soft": "#241c38",
        "accent_border": "#6a4ac0",
        "on_accent": "#ffffff",
        "focus": "#c4a8ff",
        "track": "#2d2b35",
        "success": "#3dcc6d",
        "success_soft": "#15261b",
        "warning": "#f0b44c",
        "warning_soft": "#2a2113",
        "warning_border": "#5c4520",
        "danger": "#f0646a",
        "danger_soft": "#2d181b",
        "danger_border": "#7a3036",
        "scroll": "#3b3844",
        "knob_off": "#a29eae",
    },
    "light": {
        "bg": "#f3f2f7",
        "sidebar": "#ebe9f1",
        "panel": "#ffffff",
        "panel_alt": "#f7f6fa",
        "field": "#ffffff",
        "border": "#e0dde8",
        "border_strong": "#c9c5d4",
        "hover": "#f0eef5",
        "selected": "#efe9fd",
        "text": "#1c1a22",
        "text2": "#36333f",
        "muted": "#5f5b6b",
        "dim": "#85818f",
        "disabled": "#b1adbb",
        "accent": "#7c4dff",
        "accent_hover": "#6b3cf0",
        "accent_text": "#6438e0",
        "accent_soft": "#f1ebff",
        "accent_border": "#b9a1fb",
        "on_accent": "#ffffff",
        "focus": "#7c4dff",
        "track": "#e4e1ec",
        "success": "#1b9a4c",
        "success_soft": "#e5f5eb",
        "warning": "#965f00",
        "warning_soft": "#fff6de",
        "warning_border": "#ecd08f",
        "danger": "#d3343b",
        "danger_soft": "#fdecee",
        "danger_border": "#f0a6aa",
        "scroll": "#c9c5d4",
        "knob_off": "#85818f",
    },
}

# Segoe Fluent Icons (Windows 11) / Segoe MDL2 Assets (Windows 10) code points,
# with plain unicode fallbacks for systems that don't have either font.
_ICONS: dict[str, tuple[str, str]] = {
    "home": ("", "⌂"),
    "inventory": ("", "▤"),
    "game": ("", "◆"),
    "settings": ("", "⚙"),
    "help": ("", "?"),
    "log": ("", "≡"),
    "search": ("", "⌕"),
    "refresh": ("", "↻"),
    "pause": ("", "❚❚"),
    "play": ("", "▶"),
    "chevron_right": ("", "›"),
    "chevron_down": ("", "▾"),
    "arrow_right": ("", "→"),
    "check": ("", "✓"),
    "lock": ("", "•"),
    "calendar": ("", "▦"),
    "people": ("", "☺"),
    "open": ("", "↗"),
    "heart": ("", "♥"),
    "sign_out": ("", "⇥"),
    "add": ("", "+"),
    "close": ("", "✕"),
    "warning": ("", "⚠"),
    "blocked": ("", "⊘"),
    "tray": ("", "▁"),
    "chat": ("", "▭"),
    "link": ("", "∞"),
    "copy": ("", "⧉"),
    "video": ("", "▶"),
    "live": ("", "◉"),
}


def enable_dpi_awareness() -> None:
    """
    Opt into per-monitor DPI awareness on Windows, so that the window is rendered crisply
    under display scaling instead of being bitmap-stretched. Must run before Tk is created.
    """
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def _pick(available: set[str], *names: str) -> str | None:
    for name in names:
        if name in available:
            return name
    return None


class Theme:
    """
    Holds the active palette, fonts and the DPI scale factor.
    Widgets register the options they take from the palette, and are repainted on change.
    """
    def __init__(self) -> None:
        self.dark: bool = True
        self.c: dict[str, str] = dict(PALETTES["dark"])
        self.scale: float = 1.0
        self.fonts: dict[str, Font] = {}
        self.icon_font: bool = False
        # keyed by widget path, so re-binding a widget updates its entry in place
        self._bound: dict[str, tuple[tk.Misc, dict[str, str]]] = {}
        self._painters: dict[str, tuple[tk.Misc, abc.Callable[[], Any]]] = {}
        self._prune_at: int = 1024
        self._shapes: dict[tuple[Any, ...], list[PhotoImage]] = {}
        self._images: dict[tuple[Any, ...], PhotoImage] = {}
        self.root: tk.Tk | None = None

    def setup(self, root: tk.Tk) -> None:
        self.root = root
        self.scale = max(root.winfo_fpixels("1i") / 96, 1.0)
        available = set(families(root))
        text = _pick(
            available,
            "Segoe UI Variable Text", "Segoe UI", "Inter", "Cantarell", "Ubuntu",
            "Helvetica Neue", "DejaVu Sans",
        )
        text_sb = _pick(available, "Segoe UI Variable Text Semibold", "Segoe UI Semibold")
        display_sb = _pick(available, "Segoe UI Variable Display Semib", "Segoe UI Semibold")
        mono = _pick(
            available, "JetBrains Mono", "Cascadia Mono", "Consolas", "DejaVu Sans Mono", "Menlo"
        )
        icon = _pick(available, "Segoe Fluent Icons", "Segoe MDL2 Assets")
        self.icon_font = icon is not None
        base = text or "TkDefaultFont"

        def make(family: str | None, size: int, *, semibold: bool = False) -> Font:
            if family is None:
                # no dedicated semibold face available - emulate it with bold
                return Font(
                    root, family=base, size=size, weight="bold" if semibold else "normal"
                )
            return Font(root, family=family, size=size)

        self.fonts = {
            "title": make(display_sb, 20, semibold=True),
            "h2": make(display_sb, 14, semibold=True),
            "h3": make(text_sb, 12, semibold=True),
            "big": make(display_sb, 18, semibold=True),
            "brand": make(display_sb, 13, semibold=True),
            "stat": make(display_sb, 13, semibold=True),
            "nav": make(base, 11),
            "nav_sb": make(text_sb, 11, semibold=True),
            "body": make(base, 10),
            "body_sb": make(text_sb, 10, semibold=True),
            "small": make(base, 9),
            "small_sb": make(text_sb, 9, semibold=True),
            "caption": make(text_sb, 8, semibold=True),
            "mono": make(mono or "TkFixedFont", 9),
            "mono_lg": make(mono or "TkFixedFont", 22, semibold=True),
            "icon_sm": make(icon or base, 9),
            "icon": make(icon or base, 12),
            "icon_lg": make(icon or base, 16),
            "icon_xl": make(icon or base, 30),
        }
        if mono is not None:
            self.fonts["mono_lg"].configure(weight="bold")

    def px(self, value: float) -> int:
        return int(round(value * self.scale))

    def font(self, name: str) -> Font:
        return self.fonts[name]

    def color(self, token: Token) -> str:
        if token is None:
            return ''
        if token.startswith('#'):
            return token
        return self.c[token]

    # widget registration

    def bind(self, widget: tk.Misc, **options: str) -> tk.Misc:
        """
        Configure widget options from palette tokens, and keep them updated on theme changes.
        """
        key = str(widget)
        if key in self._bound:
            self._bound[key][1].update(options)
        else:
            self._bound[key] = (widget, dict(options))
        widget.configure(**{k: self.color(v) for k, v in options.items()})  # type: ignore[call-arg]
        if len(self._bound) > self._prune_at:
            self._prune()
        return widget

    def painter(self, widget: tk.Misc, callback: abc.Callable[[], Any]) -> None:
        """
        Register a repaint callback for widgets that draw themselves.
        """
        self._painters[str(widget)] = (widget, callback)

    def _prune(self) -> None:
        self._bound = {k: v for k, v in self._bound.items() if _alive(v[0])}
        self._painters = {k: v for k, v in self._painters.items() if _alive(v[0])}
        self._prune_at = max(1024, len(self._bound) * 2)

    def set_dark(self, dark: bool) -> None:
        self.dark = dark
        self.c = dict(PALETTES["dark" if dark else "light"])
        self._prune()
        # containers first, so that self-drawn widgets can read their parent's new background
        for widget, options in list(self._bound.values()):
            try:
                widget.configure(  # type: ignore[call-arg]
                    **{k: self.color(v) for k, v in options.items()}
                )
            except tk.TclError:
                pass
        for widget, callback in list(self._painters.values()):
            try:
                callback()
            except tk.TclError:
                pass

    # images

    def shape(
        self, radius: int, fill: str, border: str, border_width: int
    ) -> list[PhotoImage]:
        """
        Returns 4 anti-aliased corner images (NW, NE, SW, SE) for a rounded rectangle.
        Empty fill/border strings are transparent.
        """
        key = (radius, fill, border, border_width)
        if (corners := self._shapes.get(key)) is not None:
            return corners
        ss = 4  # supersampling
        size = radius * 2
        big = Image_module.new("RGBA", (size * ss, size * ss), (0, 0, 0, 0))
        draw = ImageDraw.Draw(big)
        outer = border if border and border_width else fill
        if outer:
            draw.rounded_rectangle(
                (0, 0, size * ss - 1, size * ss - 1), radius=radius * ss, fill=outer
            )
        if border and border_width:
            inset = border_width * ss
            draw.rounded_rectangle(
                (inset, inset, size * ss - 1 - inset, size * ss - 1 - inset),
                radius=max(radius - border_width, 0) * ss,
                fill=fill or (0, 0, 0, 0),
            )
        small = big.resize((size, size), Image_module.Resampling.LANCZOS)
        assert self.root is not None
        corners = [
            PhotoImage(master=self.root, image=small.crop(box))
            for box in (
                (0, 0, radius, radius),
                (radius, 0, size, radius),
                (0, radius, radius, size),
                (radius, radius, size, size),
            )
        ]
        self._shapes[key] = corners
        return corners

    def logo(self, size: int) -> PhotoImage:
        key = ("logo", size)
        if (image := self._images.get(key)) is None:
            with Image_module.open(resource_path("icons/pickaxe.ico")) as ico:
                ico.size = max(ico.ico.sizes())  # type: ignore[attr-defined]
                source = ico.convert("RGBA")
            image = PhotoImage(
                master=self.root,
                image=source.resize((size, size), Image_module.Resampling.LANCZOS),
            )
            self._images[key] = image
        return image


def _alive(widget: tk.Misc) -> bool:
    try:
        return bool(widget.winfo_exists())
    except tk.TclError:
        return False


theme = Theme()


def icon(name: str) -> str:
    fluent, fallback = _ICONS[name]
    return fluent if theme.icon_font else fallback


def draw_rounded(
    canvas: tk.Canvas,
    x0: int,
    y0: int,
    x1: int,
    y1: int,
    *,
    radius: int,
    fill: str = '',
    border: str = '',
    border_width: int = 1,
    tags: str = "shape",
) -> None:
    w, h = x1 - x0, y1 - y0
    if w <= 0 or h <= 0:
        return
    r = max(0, min(radius, w // 2, h // 2))
    if not border:
        border_width = 0
    if r == 0:
        if fill:
            canvas.create_rectangle(x0, y0, x1, y1, fill=fill, width=0, tags=tags)
        if border_width:
            canvas.create_rectangle(
                x0, y0, x1 - 1, y1 - 1, outline=border, width=border_width, tags=tags
            )
        return
    if fill:
        canvas.create_rectangle(x0 + r, y0, x1 - r, y1, fill=fill, width=0, tags=tags)
        canvas.create_rectangle(x0, y0 + r, x1, y1 - r, fill=fill, width=0, tags=tags)
    if border_width:
        bw = border_width
        canvas.create_rectangle(x0 + r, y0, x1 - r, y0 + bw, fill=border, width=0, tags=tags)
        canvas.create_rectangle(x0 + r, y1 - bw, x1 - r, y1, fill=border, width=0, tags=tags)
        canvas.create_rectangle(x0, y0 + r, x0 + bw, y1 - r, fill=border, width=0, tags=tags)
        canvas.create_rectangle(x1 - bw, y0 + r, x1, y1 - r, fill=border, width=0, tags=tags)
    nw, ne, sw, se = theme.shape(r, fill, border, border_width)
    canvas.create_image(x0, y0, image=nw, anchor="nw", tags=tags)
    canvas.create_image(x1 - r, y0, image=ne, anchor="nw", tags=tags)
    canvas.create_image(x0, y1 - r, image=sw, anchor="nw", tags=tags)
    canvas.create_image(x1 - r, y1 - r, image=se, anchor="nw", tags=tags)


#####################
# BASIC CONSTRUCTORS #
#####################


def match_parent_bg(widget: tk.Misc) -> bool:
    """
    Copy the parent's background onto a self-drawn widget. Returns False if it's gone.

    NOTE: Only reconfigure on an actual change - configuring a canvas re-requests its size,
    which for canvases sized by their grid children causes an endless resize loop.
    """
    try:
        color = widget.master.cget("background")
        if widget.cget("background") != color:
            widget.configure(background=color)  # type: ignore[call-arg]
    except tk.TclError:
        return False
    return True


def bg_token(widget: tk.Misc) -> str:
    return getattr(widget, "_bg_token", "bg")


def Frame(master: tk.Misc, *, bg: str | None = None, **kwargs: Any) -> tk.Frame:
    token = bg or bg_token(master)
    frame = tk.Frame(master, borderwidth=0, highlightthickness=0, **kwargs)
    frame._bg_token = token  # type: ignore[attr-defined]
    theme.bind(frame, background=token)
    return frame


def Label(
    master: tk.Misc,
    text: str = '',
    *,
    font: str = "body",
    fg: str = "text",
    bg: str | None = None,
    **kwargs: Any,
) -> tk.Label:
    label = tk.Label(
        master,
        text=text,
        font=theme.font(font),
        borderwidth=0,
        highlightthickness=0,
        padx=0,
        pady=0,
        **kwargs,
    )
    theme.bind(label, background=bg or bg_token(master), foreground=fg)
    return label


def IconLabel(
    master: tk.Misc, name: str, *, size: str = "icon", fg: str = "muted", **kwargs: Any
) -> tk.Label:
    return Label(master, icon(name), font=size, fg=fg, **kwargs)


def Separator(master: tk.Misc, *, color: str = "border", vertical: bool = False) -> tk.Frame:
    sep = tk.Frame(master, borderwidth=0, highlightthickness=0)
    if vertical:
        sep.configure(width=1)
    else:
        sep.configure(height=1)
    theme.bind(sep, background=color)
    return sep


class Dot(tk.Canvas):
    """
    Anti-aliased status dot. Recolor with `theme.bind(dot, foreground=token)`.
    """
    def __init__(self, master: tk.Misc, color: str = "success", *, size: int = 8):
        d = theme.px(size)
        super().__init__(master, width=d, height=d, highlightthickness=0, borderwidth=0)
        self._color = theme.color(color)
        theme.painter(self, self.redraw)
        theme.bind(self, foreground=color)

    def configure(self, cnf: Any = None, **kwargs: Any) -> Any:  # type: ignore[override]
        if cnf:
            kwargs.update(cnf)
        color = kwargs.pop("foreground", None)
        result = super().configure(**kwargs) if kwargs else None
        if color is not None:
            self._color = color
            self.redraw()
        return result

    config = configure  # type: ignore[assignment]

    def redraw(self) -> None:
        self.delete("all")
        if not match_parent_bg(self):
            return
        d = int(self.cget("width"))
        draw_rounded(self, 0, 0, d, d, radius=d // 2, fill=self._color)


#####################
# SELF-DRAWN WIDGETS #
#####################


class Surface(tk.Canvas):
    """
    A canvas that paints a rounded, optionally bordered box behind whatever is placed on it.
    """
    def __init__(
        self,
        master: tk.Misc,
        *,
        fill: Token = "panel",
        border: Token = "border",
        radius: int = 12,
        border_width: int = 1,
        **kwargs: Any,
    ):
        super().__init__(master, highlightthickness=0, borderwidth=0, **kwargs)
        self._fill: Token = fill
        self._border: Token = border
        self._radius: int = radius
        self._border_width: int = border_width
        self._bg_token = fill or bg_token(master)
        self.bind("<Configure>", lambda e: self.redraw(), add=True)
        theme.painter(self, self.redraw)
        match_parent_bg(self)

    def set_style(self, *, fill: Token = "", border: Token = "") -> None:
        # empty string means "don't change", None means "transparent"
        if fill != '':
            self._fill = fill
        if border != '':
            self._border = border
        self.redraw()

    def redraw(self) -> None:
        match_parent_bg(self)
        self.delete("shape")
        draw_rounded(
            self,
            0,
            0,
            self.winfo_width(),
            self.winfo_height(),
            radius=theme.px(self._radius),
            fill=theme.color(self._fill),
            border=theme.color(self._border),
            border_width=theme.px(self._border_width) if self._border_width else 0,
        )
        self.tag_lower("shape")


class Panel(Surface):
    """
    Rounded card with a content frame available as `.body`.
    """
    def __init__(
        self,
        master: tk.Misc,
        *,
        padding: tuple[int, int] | tuple[int, int, int, int] = (20, 18),
        fill: str = "panel",
        border: Token = "border",
        radius: int = 12,
        **kwargs: Any,
    ):
        super().__init__(master, fill=fill, border=border, radius=radius, **kwargs)
        if len(padding) == 2:
            pl = pr = padding[0]
            pt = pb = padding[1]
        else:
            pl, pt, pr, pb = padding  # type: ignore[misc]
        self.body = Frame(self, bg=fill)
        self.body.grid(
            column=0,
            row=0,
            sticky="nsew",
            padx=(theme.px(pl), theme.px(pr)),
            pady=(theme.px(pt), theme.px(pb)),
        )
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)

    def set_fill(self, fill: str) -> None:
        self._bg_token = fill
        self.body._bg_token = fill  # type: ignore[attr-defined]
        theme.bind(self.body, background=fill)
        self.set_style(fill=fill)


class _Focusable(tk.Canvas):
    """
    Base for small self-drawn controls: handles hover, press, focus ring and keyboard activation.
    """
    RING = 2  # focus ring thickness, in unscaled pixels

    def __init__(self, master: tk.Misc, *, takefocus: bool = True, **kwargs: Any):
        super().__init__(
            master,
            highlightthickness=0,
            borderwidth=0,
            takefocus=1 if takefocus else 0,
            **kwargs,
        )
        self._hover = False
        self._pressed = False
        self._focused = False
        self._disabled = False
        self.bind("<Enter>", self._on_enter, add=True)
        self.bind("<Leave>", self._on_leave, add=True)
        self.bind("<ButtonPress-1>", self._on_press, add=True)
        self.bind("<ButtonRelease-1>", self._on_release, add=True)
        self.bind("<FocusIn>", self._on_focus, add=True)
        self.bind("<FocusOut>", self._on_focus, add=True)
        self.bind("<KeyPress-space>", self._on_key, add=True)
        self.bind("<KeyPress-Return>", self._on_key, add=True)
        self.bind("<Configure>", lambda e: self.redraw(), add=True)
        theme.painter(self, self.redraw)

    @property
    def ring(self) -> int:
        return theme.px(self.RING)

    def _on_enter(self, event: tk.Event[Any]) -> None:
        self._hover = True
        self.redraw()

    def _on_leave(self, event: tk.Event[Any]) -> None:
        self._hover = False
        self._pressed = False
        self.redraw()

    def _on_press(self, event: tk.Event[Any]) -> None:
        if self._disabled:
            return
        self._pressed = True
        self.redraw()

    def _on_release(self, event: tk.Event[Any]) -> None:
        if self._disabled:
            return
        was_pressed = self._pressed
        self._pressed = False
        self.redraw()
        inside = 0 <= event.x < self.winfo_width() and 0 <= event.y < self.winfo_height()
        if was_pressed and inside:
            self.invoke()

    def _on_focus(self, event: tk.Event[Any]) -> None:
        self._focused = event.type == tk.EventType.FocusIn
        self.redraw()

    def _on_key(self, event: tk.Event[Any]) -> str | None:
        if not self._disabled:
            self.invoke()
        return "break"

    def set_disabled(self, disabled: bool) -> None:
        self._disabled = disabled
        self.configure(takefocus=0 if disabled else 1, cursor='' if disabled else "hand2")
        self.redraw()

    def invoke(self) -> None:
        raise NotImplementedError

    def redraw(self) -> None:
        raise NotImplementedError

    def draw_ring(self, radius: int) -> None:
        if self._focused and not self._disabled:
            draw_rounded(
                self,
                0,
                0,
                self.winfo_width(),
                self.winfo_height(),
                radius=radius + self.ring,
                border=theme.color("focus"),
                border_width=self.ring,
            )


_BUTTON_VARIANTS: dict[str, dict[str, Token]] = {
    # fill, border, foreground, hover fill, hover border
    "primary": dict(
        fill="accent", border=None, fg="on_accent", hfill="accent_hover", hborder=None
    ),
    "outline": dict(
        fill=None, border="accent_border", fg="text", hfill="accent_soft", hborder="accent"
    ),
    "secondary": dict(
        fill=None, border="border_strong", fg="text", hfill="hover", hborder="border_strong"
    ),
    "danger": dict(
        fill=None, border="danger_border", fg="danger", hfill="danger_soft", hborder="danger"
    ),
    "ghost": dict(fill=None, border=None, fg="text2", hfill="hover", hborder=None),
    "link": dict(fill=None, border=None, fg="accent_text", hfill=None, hborder=None),
    "icon": dict(fill="accent", border=None, fg="on_accent", hfill="accent_hover", hborder=None),
    "icon_ghost": dict(fill=None, border=None, fg="muted", hfill="hover", hborder=None),
}


class Button(_Focusable):
    def __init__(
        self,
        master: tk.Misc,
        text: str = '',
        *,
        icon: str | None = None,
        icon_after: str | None = None,
        command: abc.Callable[[], Any] | None = None,
        variant: str = "secondary",
        font: str = "body_sb",
        icon_size: str = "icon_sm",
        padding: tuple[int, int] = (14, 8),
        radius: int = 8,
        square: bool = False,
        state: str = "normal",
        **kwargs: Any,
    ):
        super().__init__(master, cursor="hand2", **kwargs)
        self._text = text
        self._icon = icon
        self._icon_after = icon_after
        self._command = command
        self._variant = _BUTTON_VARIANTS[variant]
        self._font = font
        self._icon_size = icon_size
        self._padding = padding
        self._radius = radius
        self._square = square
        self._link = variant == "link"
        self._resize()
        if state == "disabled":
            self.set_disabled(True)

    def _resize(self) -> None:
        font = theme.font(self._font)
        ifont = theme.font(self._icon_size)
        gap = theme.px(8)
        width = font.measure(self._text) if self._text else 0
        for glyph in (self._icon, self._icon_after):
            if glyph:
                width += ifont.measure(icon(glyph)) + (gap if self._text else 0)
        padx, pady = (theme.px(p) for p in self._padding)
        if self._link:
            padx = 0
            pady = theme.px(3)
        height = max(font.metrics("linespace"), ifont.metrics("linespace")) + pady * 2
        width += padx * 2
        if self._square:
            width = height
        r = self.ring
        self.configure(width=width + 2 * r, height=height + 2 * r)

    def configure(self, cnf: Any = None, **kwargs: Any) -> Any:  # type: ignore[override]
        # supports the subset of ttk.Button options the backend relies upon
        if cnf:
            kwargs.update(cnf)
        state = kwargs.pop("state", None)
        text = kwargs.pop("text", None)
        command = kwargs.pop("command", None)
        result = super().configure(**kwargs) if kwargs else None
        if command is not None:
            self._command = command
        if text is not None and text != self._text:
            self._text = text
            self._resize()
            self.redraw()
        if state is not None:
            self.set_disabled(state == "disabled")
        return result

    config = configure  # type: ignore[assignment]

    def invoke(self) -> None:
        if self._command is not None and not self._disabled:
            self._command()

    def redraw(self) -> None:
        self.delete("all")
        if not match_parent_bg(self):
            return
        v = self._variant
        active = (self._hover or self._pressed) and not self._disabled
        fill = v["hfill"] if active else v["fill"]
        border = v["hborder"] if active else v["border"]
        fg = v["fg"]
        if self._disabled:
            fg = "disabled"
            if v["fill"] is not None:
                fill = "track"
        elif self._link and active:
            fg = "focus"
        r = self.ring
        w, h = self.winfo_width(), self.winfo_height()
        radius = theme.px(self._radius)
        draw_rounded(
            self,
            r,
            r,
            w - r,
            h - r,
            radius=radius,
            fill=theme.color(fill),
            border=theme.color(border),
            border_width=theme.px(1),
        )
        self.draw_ring(radius)
        font = theme.font(self._font)
        ifont = theme.font(self._icon_size)
        gap = theme.px(8)
        parts: list[tuple[str, Font]] = []
        if self._icon:
            parts.append((icon(self._icon), ifont))
        if self._text:
            parts.append((self._text, font))
        if self._icon_after:
            parts.append((icon(self._icon_after), ifont))
        total = sum(f.measure(t) for t, f in parts) + gap * (len(parts) - 1)
        x = (w - total) / 2
        color = theme.color(fg)
        for text, f in parts:
            self.create_text(x, h / 2, text=text, font=f, fill=color, anchor="w")
            x += f.measure(text) + gap


class Toggle(_Focusable):
    def __init__(
        self,
        master: tk.Misc,
        *,
        variable: tk.IntVar | tk.BooleanVar,
        command: abc.Callable[[], Any] | None = None,
        **kwargs: Any,
    ):
        super().__init__(master, cursor="hand2", **kwargs)
        self._var = variable
        self._command = command
        r = self.ring
        self.configure(width=theme.px(42) + 2 * r, height=theme.px(24) + 2 * r)
        variable.trace_add("write", lambda *args: self.redraw())

    def invoke(self) -> None:
        self._var.set(not self._var.get())
        if self._command is not None:
            self._command()

    def redraw(self) -> None:
        self.delete("all")
        if not match_parent_bg(self):
            return
        on = bool(self._var.get())
        r = self.ring
        w, h = self.winfo_width(), self.winfo_height()
        th = h - 2 * r
        if on:
            fill = "accent_hover" if self._hover else "accent"
            border: Token = None
        else:
            fill = "track"
            border = "border_strong" if not self._hover else "muted"
        draw_rounded(
            self,
            r,
            r,
            w - r,
            h - r,
            radius=th // 2,
            fill=theme.color(fill),
            border=theme.color(border),
            border_width=theme.px(1),
        )
        self.draw_ring(th // 2)
        pad = theme.px(4)
        d = th - 2 * pad
        x = (w - r - pad - d) if on else (r + pad)
        draw_rounded(
            self,
            x,
            r + pad,
            x + d,
            r + pad + d,
            radius=d // 2,
            fill=theme.color("on_accent" if on else "knob_off"),
            tags="knob",
        )


class Checkbox(_Focusable):
    def __init__(
        self,
        master: tk.Misc,
        text: str,
        *,
        variable: tk.IntVar | tk.BooleanVar,
        command: abc.Callable[[], Any] | None = None,
        **kwargs: Any,
    ):
        super().__init__(master, cursor="hand2", **kwargs)
        self._text = text
        self._var = variable
        self._command = command
        font = theme.font("body")
        r = self.ring
        box = theme.px(18)
        self._box = box
        self.configure(
            width=box + theme.px(10) + font.measure(text) + 2 * r + theme.px(4),
            height=max(box, font.metrics("linespace")) + 2 * r,
        )
        variable.trace_add("write", lambda *args: self.redraw())

    def invoke(self) -> None:
        self._var.set(not self._var.get())
        if self._command is not None:
            self._command()

    def redraw(self) -> None:
        self.delete("all")
        if not match_parent_bg(self):
            return
        on = bool(self._var.get())
        r = self.ring
        h = self.winfo_height()
        box = self._box
        y0 = (h - box) // 2
        if on:
            fill, border = "accent_hover" if self._hover else "accent", None
        else:
            fill, border = "field", "focus" if self._hover else "border_strong"
        draw_rounded(
            self,
            r,
            y0,
            r + box,
            y0 + box,
            radius=theme.px(4),
            fill=theme.color(fill),
            border=theme.color(border),
            border_width=theme.px(1),
        )
        if self._focused:
            draw_rounded(
                self,
                0,
                y0 - r,
                box + 2 * r,
                y0 + box + r,
                radius=theme.px(4) + r,
                border=theme.color("focus"),
                border_width=r,
            )
        if on:
            self.create_text(
                r + box / 2,
                y0 + box / 2,
                text=icon("check"),
                font=theme.font("icon_sm"),
                fill=theme.color("on_accent"),
            )
        self.create_text(
            r + box + theme.px(10),
            h / 2,
            text=self._text,
            anchor="w",
            font=theme.font("body"),
            fill=theme.color("text2"),
        )


class ProgressBar(tk.Canvas):
    def __init__(
        self, master: tk.Misc, *, height: int = 8, color: str = "accent", **kwargs: Any
    ):
        super().__init__(
            master,
            width=theme.px(40),
            height=theme.px(height),
            highlightthickness=0,
            borderwidth=0,
            **kwargs,
        )
        self._value: float = 0.0
        self._color = color
        self.bind("<Configure>", lambda e: self.redraw(), add=True)
        theme.painter(self, self.redraw)

    def set(self, value: float, color: str | None = None) -> None:
        self._value = min(max(value, 0.0), 1.0)
        if color is not None:
            self._color = color
        self.redraw()

    def redraw(self) -> None:
        self.delete("all")
        if not match_parent_bg(self):
            return
        w, h = self.winfo_width(), self.winfo_height()
        if w <= 1:
            return
        draw_rounded(self, 0, 0, w, h, radius=h // 2, fill=theme.color("track"))
        if self._value > 0:
            fw = max(h, int(round(w * self._value)))
            draw_rounded(self, 0, 0, fw, h, radius=h // 2, fill=theme.color(self._color))


_CHIP_VARIANTS: dict[str, tuple[Token, Token, str]] = {
    # fill, border, text
    "neutral": ("panel_alt", "border", "text2"),
    "accent": ("accent_soft", "accent_border", "accent_text"),
    "success": ("success_soft", None, "success"),
    "warning": ("warning_soft", None, "warning"),
    "danger": ("danger_soft", None, "danger"),
    "mono": ("panel_alt", "border", "text2"),
}


class Chip(_Focusable):
    """
    Small pill with an optional status dot or icon. Clickable when given a command.
    """
    def __init__(
        self,
        master: tk.Misc,
        text: str,
        *,
        variant: str = "neutral",
        dot: str | None = None,
        icon: str | None = None,
        command: abc.Callable[[], Any] | None = None,
        font: str = "small_sb",
        **kwargs: Any,
    ):
        super().__init__(
            master,
            takefocus=command is not None,
            cursor="hand2" if command is not None else '',
            **kwargs,
        )
        self._command = command
        self.update_chip(text, variant=variant, dot=dot, icon=icon, font=font)

    def update_chip(
        self,
        text: str,
        *,
        variant: str | None = None,
        dot: str | None = None,
        icon: str | None = None,
        font: str | None = None,
    ) -> None:
        self._text = text
        if variant is not None:
            self._variant = variant
        self._dot = dot
        self._icon_name = icon
        if font is not None:
            self._font = font
        f = theme.font(self._font)
        extra = 0
        if dot or icon:
            extra = theme.font("icon_sm").measure("●") + theme.px(6)
        r = self.ring
        self.configure(
            width=f.measure(text) + extra + theme.px(22) + 2 * r,
            height=f.metrics("linespace") + theme.px(10) + 2 * r,
        )
        self.redraw()

    def invoke(self) -> None:
        if self._command is not None:
            self._command()

    def redraw(self) -> None:
        self.delete("all")
        if not match_parent_bg(self):
            return
        fill, border, fg = _CHIP_VARIANTS[self._variant]
        if self._hover and self._command is not None:
            border = "accent"
        r = self.ring
        w, h = self.winfo_width(), self.winfo_height()
        radius = (h - 2 * r) // 2
        draw_rounded(
            self,
            r,
            r,
            w - r,
            h - r,
            radius=radius,
            fill=theme.color(fill),
            border=theme.color(border),
            border_width=theme.px(1),
        )
        if self._command is not None:
            self.draw_ring(radius)
        x = r + theme.px(11)
        if self._dot or self._icon_name:
            glyph = "●" if self._dot else icon(self._icon_name)  # type: ignore[arg-type]
            f = theme.font("small") if self._dot else theme.font("icon_sm")
            self.create_text(
                x, h / 2, text=glyph, anchor="w", font=f,
                fill=theme.color(self._dot or fg),
            )
            x += theme.font("icon_sm").measure("●") + theme.px(6)
        self.create_text(
            x, h / 2, text=self._text, anchor="w", font=theme.font(self._font),
            fill=theme.color(fg),
        )


class ScrollBar(tk.Canvas):
    """
    Thin overlay-style scrollbar that hides itself when there's nothing to scroll.
    """
    def __init__(self, master: tk.Misc, *, command: abc.Callable[..., Any]):
        super().__init__(
            master, width=theme.px(10), height=theme.px(40), highlightthickness=0, borderwidth=0,
            takefocus=0,
        )
        self._command = command
        self._first, self._last = 0.0, 1.0
        self._drag: float | None = None
        self._hover = False
        self.bind("<Configure>", lambda e: self.redraw(), add=True)
        self.bind("<ButtonPress-1>", self._press)
        self.bind("<B1-Motion>", self._motion)
        self.bind("<ButtonRelease-1>", lambda e: setattr(self, "_drag", None))
        self.bind("<Enter>", lambda e: self._set_hover(True))
        self.bind("<Leave>", lambda e: self._set_hover(False))
        theme.painter(self, self.redraw)

    def _set_hover(self, hover: bool) -> None:
        self._hover = hover
        self.redraw()

    def set(self, first: str | float, last: str | float) -> None:
        self._first, self._last = float(first), float(last)
        self.redraw()

    def redraw(self) -> None:
        self.delete("all")
        if not match_parent_bg(self):
            return
        if self._first <= 0 and self._last >= 1:
            return
        h = self.winfo_height()
        w = self.winfo_width()
        bar = theme.px(6 if self._hover or self._drag is not None else 4)
        x0 = w - bar - theme.px(2)
        y0 = int(self._first * h)
        y1 = max(int(self._last * h), y0 + theme.px(24))
        draw_rounded(
            self, x0, y0, x0 + bar, y1, radius=bar // 2,
            fill=theme.color("muted" if self._hover else "scroll"),
        )

    def _press(self, event: tk.Event[Any]) -> None:
        h = max(self.winfo_height(), 1)
        pos = event.y / h
        if self._first <= pos <= self._last:
            self._drag = pos - self._first
        else:
            self._command("scroll", 1 if pos > self._last else -1, "pages")

    def _motion(self, event: tk.Event[Any]) -> None:
        if self._drag is None:
            return
        h = max(self.winfo_height(), 1)
        self._command("moveto", event.y / h - self._drag)


class ScrollArea(tk.Frame):
    """
    Vertically scrollable container; put content into `.inner`.

    With fill=True, the content is stretched to at least the visible height,
    so layouts with weighted rows still fill the window when there's room.
    """
    def __init__(self, master: tk.Misc, *, bg: str = "bg", fill: bool = False):
        super().__init__(master, borderwidth=0, highlightthickness=0)
        self._bg_token = bg
        theme.bind(self, background=bg)
        self._fill = fill
        # NOTE: small requested size, so the area flexes with its grid cell instead of
        # demanding the default canvas size
        self.canvas = tk.Canvas(
            self,
            width=theme.px(80),
            height=theme.px(60),
            highlightthickness=0,
            borderwidth=0,
            yscrollincrement=theme.px(24),
            takefocus=0,
        )
        theme.bind(self.canvas, background=bg)
        self.inner = Frame(self.canvas, bg=bg)
        self._window = self.canvas.create_window(0, 0, window=self.inner, anchor="nw")
        self.bar = ScrollBar(self, command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.bar.set)
        self.canvas.grid(column=0, row=0, sticky="nsew")
        self.bar.grid(column=1, row=0, sticky="ns")
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        self.inner.bind("<Configure>", lambda e: self._sync(), add=True)
        self.canvas.bind("<Configure>", lambda e: self._sync(), add=True)
        self._poll_job: str | None = None
        if fill:
            self._poll()
            self.bind("<Destroy>", self._on_destroy, add=True)

    def _poll(self) -> None:
        # ponytail: polls requested height every 250ms; Tk emits no event on reqheight changes
        # of a canvas-embedded window whose height is forced
        self._sync()
        self._poll_job = self.after(250, self._poll)

    def _on_destroy(self, event: tk.Event[tk.Misc]) -> None:
        if event.widget is self and self._poll_job is not None:
            self.after_cancel(self._poll_job)
            self._poll_job = None

    def _sync(self) -> None:
        cw = self.canvas.winfo_width()
        ch = self.canvas.winfo_height()
        if cw <= 1:
            return
        height = max(self.inner.winfo_reqheight(), ch) if self._fill else 0
        if self._fill:
            self.canvas.itemconfigure(self._window, width=cw, height=height)
        else:
            self.canvas.itemconfigure(self._window, width=cw)
            height = self.inner.winfo_reqheight()
        self.canvas.configure(scrollregion=(0, 0, cw, max(height, 1)))

    def can_scroll(self) -> bool:
        first, last = self.canvas.yview()
        return first > 0 or last < 1

    def scroll(self, units: int) -> None:
        self.canvas.yview_scroll(units, "units")

    def to_top(self) -> None:
        self.canvas.yview_moveto(0)

    def ensure_visible(self, widget: tk.Misc) -> None:
        if not self.can_scroll():
            return
        top = widget.winfo_rooty() - self.inner.winfo_rooty()
        bottom = top + widget.winfo_height()
        view_h = self.canvas.winfo_height()
        total = max(int(self.canvas.cget("scrollregion").split()[3]), 1)
        first = self.canvas.yview()[0] * total
        margin = theme.px(12)
        if top - margin < first:
            self.canvas.yview_moveto(max(top - margin, 0) / total)
        elif bottom + margin > first + view_h:
            self.canvas.yview_moveto((bottom + margin - view_h) / total)


class PopupList:
    """
    Themed dropdown list shown under an anchor widget. Used by Dropdown and TextField suggestions.
    """
    def __init__(self, anchor: tk.Misc, on_select: abc.Callable[[str], Any]):
        self._anchor = anchor
        self._on_select = on_select
        self._top: tk.Toplevel | None = None
        self._list: tk.Listbox | None = None
        self._items: list[str] = []

    @property
    def visible(self) -> bool:
        return self._top is not None

    def show(self, items: list[str], *, selected: str | None = None, focus: bool = False) -> None:
        self._items = items
        if not items:
            self.hide()
            return
        if self._top is None:
            top = self._top = tk.Toplevel(self._anchor)
            top.overrideredirect(True)
            top.attributes("-topmost", True)
            frame = tk.Frame(top, borderwidth=0, highlightthickness=1)
            theme.bind(frame, highlightbackground="border_strong", highlightcolor="border_strong")
            frame.pack(fill="both", expand=True)
            lb = self._list = tk.Listbox(
                frame,
                activestyle="none",
                borderwidth=0,
                highlightthickness=0,
                exportselection=False,
                font=theme.font("body"),
                selectmode="browse",
            )
            theme.bind(
                lb,
                background="panel_alt",
                foreground="text",
                selectbackground="accent_soft",
                selectforeground="text",
            )
            lb.pack(fill="both", expand=True, padx=theme.px(4), pady=theme.px(4))
            lb.bind("<ButtonRelease-1>", lambda e: self.choose())
            lb.bind("<Return>", lambda e: self.choose())
            lb.bind("<space>", lambda e: self.choose())
            lb.bind("<Escape>", lambda e: self.hide(refocus=True))
            lb.bind("<Motion>", self._on_motion)
            lb.bind("<FocusOut>", lambda e: self._anchor.after(100, self._focus_check))
            toplevel = self._anchor.winfo_toplevel()
            # NOTE: toplevel bindings also receive events of every child widget
            toplevel.bind(
                "<Configure>", lambda e: e.widget is toplevel and self.hide(), add=True
            )
        assert self._top is not None and self._list is not None
        lb = self._list
        lb.delete(0, "end")
        lb.insert("end", *items)
        rows = min(len(items), 8)
        lb.configure(height=rows)
        idx = items.index(selected) if selected in items else 0
        lb.selection_clear(0, "end")
        lb.selection_set(idx)
        lb.activate(idx)
        lb.see(idx)
        a = self._anchor
        width = a.winfo_width()
        line = theme.font("body").metrics("linespace") + theme.px(2)
        height = rows * line + theme.px(10)
        x, y = a.winfo_rootx(), a.winfo_rooty() + a.winfo_height() + theme.px(2)
        self._top.geometry(f"{width}x{height}+{x}+{y}")
        self._top.deiconify()
        self._top.lift()
        if focus:
            lb.focus_force()

    def _on_motion(self, event: tk.Event[tk.Listbox]) -> None:
        assert self._list is not None
        idx = self._list.nearest(event.y)
        self._list.selection_clear(0, "end")
        self._list.selection_set(idx)
        self._list.activate(idx)

    def _focus_check(self) -> None:
        if self._top is None:
            return
        focus = self._top.focus_get()
        if focus is None or (focus is not self._list and focus is not self._anchor):
            self.hide()

    def move(self, delta: int) -> None:
        if self._list is None:
            return
        sel = self._list.curselection()
        idx = (sel[0] if sel else -1) + delta
        idx = max(0, min(idx, len(self._items) - 1))
        self._list.selection_clear(0, "end")
        self._list.selection_set(idx)
        self._list.activate(idx)
        self._list.see(idx)

    def current(self) -> str | None:
        if self._list is None:
            return None
        sel = self._list.curselection()
        return self._items[sel[0]] if sel else None

    def choose(self) -> None:
        value = self.current()
        self.hide(refocus=True)
        if value is not None:
            self._on_select(value)

    def hide(self, refocus: bool = False) -> None:
        if self._top is not None:
            self._top.destroy()
            self._top = None
            self._list = None
            if refocus:
                self._anchor.focus_set()


class Dropdown(_Focusable):
    def __init__(
        self,
        master: tk.Misc,
        *,
        values: list[str],
        variable: tk.StringVar,
        command: abc.Callable[[str], Any] | None = None,
        width: int | None = None,
        **kwargs: Any,
    ):
        super().__init__(master, cursor="hand2", **kwargs)
        self._values = values
        self._var = variable
        self._command = command
        self._popup = PopupList(self, self._select)
        font = theme.font("body")
        if width is None:
            width = max((font.measure(v) for v in values), default=100) + theme.px(56)
        r = self.ring
        self.configure(
            width=max(width, theme.px(160)) + 2 * r,
            height=font.metrics("linespace") + theme.px(18) + 2 * r,
        )
        self.bind("<KeyPress-Down>", lambda e: self.invoke(), add=True)
        variable.trace_add("write", lambda *args: self.redraw())

    def invoke(self) -> None:
        if self._popup.visible:
            self._popup.hide()
        else:
            self._popup.show(self._values, selected=self._var.get(), focus=True)

    def _select(self, value: str) -> None:
        self._var.set(value)
        if self._command is not None:
            self._command(value)

    def redraw(self) -> None:
        self.delete("all")
        if not match_parent_bg(self):
            return
        r = self.ring
        w, h = self.winfo_width(), self.winfo_height()
        radius = theme.px(8)
        draw_rounded(
            self, r, r, w - r, h - r, radius=radius,
            fill=theme.color("hover" if self._hover else "field"),
            border=theme.color("focus" if self._hover else "border_strong"),
            border_width=theme.px(1),
        )
        self.draw_ring(radius)
        self.create_text(
            r + theme.px(14), h / 2, text=self._var.get(), anchor="w",
            font=theme.font("body"), fill=theme.color("text"),
        )
        self.create_text(
            w - r - theme.px(14), h / 2, text=icon("chevron_down"), anchor="e",
            font=theme.font("icon_sm"), fill=theme.color("muted"),
        )


class TextField(Surface):
    """
    Single-line entry with a rounded frame, optional leading icon, placeholder,
    clear button and suggestion popup.
    """
    def __init__(
        self,
        master: tk.Misc,
        *,
        placeholder: str = '',
        leading_icon: str | None = None,
        clearable: bool = False,
        suggestions: abc.Callable[[str], list[str]] | None = None,
        on_change: abc.Callable[[str], Any] | None = None,
        on_submit: abc.Callable[[str], Any] | None = None,
        textvariable: tk.StringVar | None = None,
        width: int = 24,
        font: str = "body",
    ):
        super().__init__(master, fill="field", border="border_strong", radius=8)
        self.var = textvariable or tk.StringVar(self)
        self._on_change = on_change
        self._on_submit = on_submit
        self._suggestions = suggestions
        pad = theme.px(12)
        col = 0
        if leading_icon:
            IconLabel(self, leading_icon, size="icon_sm", fg="muted").grid(
                column=col, row=0, padx=(pad, 0)
            )
            col += 1
        self.entry = tk.Entry(
            self,
            textvariable=self.var,
            borderwidth=0,
            highlightthickness=0,
            relief="flat",
            width=width,
            font=theme.font(font),
        )
        theme.bind(
            self.entry,
            background="field",
            foreground="text",
            insertbackground="text",
            selectbackground="accent_soft",
            selectforeground="text",
            disabledbackground="field",
        )
        self.entry.grid(column=col, row=0, sticky="ew", padx=pad, pady=theme.px(9))
        self.columnconfigure(col, weight=1)
        self._placeholder = Label(self, placeholder, fg="dim", font=font, bg="field")
        self._placeholder.bind("<Button-1>", lambda e: self.entry.focus_set())
        self._entry_col = col
        self._clear: Button | None = None
        if clearable:
            self._clear = Button(
                self, icon="close", variant="icon_ghost", command=self.clear,
                padding=(6, 2), radius=6, takefocus=False,
            )
            self._clear.grid(column=col + 1, row=0, padx=(0, theme.px(6)))
        self.var.trace_add("write", lambda *args: self._changed())
        self.entry.bind("<FocusIn>", lambda e: self._focus(True), add=True)
        self.entry.bind("<FocusOut>", lambda e: self._focus(False), add=True)
        self.entry.bind("<Return>", self._submit, add=True)
        self.entry.bind("<KP_Enter>", self._submit, add=True)
        self._popup: PopupList | None = None
        if suggestions is not None:
            self._popup = PopupList(self, self._pick)
            self.entry.bind("<Down>", lambda e: self._popup_move(1), add=True)
            self.entry.bind("<Up>", lambda e: self._popup_move(-1), add=True)
            self.entry.bind("<Escape>", self._escape, add=True)
        self._changed(initial=True)

    def _focus(self, focused: bool) -> None:
        self.set_style(border="focus" if focused else "border_strong")
        if focused:
            self._show_suggestions()
        elif self._popup is not None:
            self.after(150, self._popup._focus_check)

    def _changed(self, initial: bool = False) -> None:
        text = self.var.get()
        if text:
            self._placeholder.place_forget()
        else:
            self._placeholder.place(
                in_=self.entry, relx=0, rely=0.5, anchor="w", x=theme.px(1)
            )
        if self._clear is not None:
            if text:
                self._clear.grid()
            else:
                self._clear.grid_remove()
        if initial:
            return
        if self._on_change is not None:
            self._on_change(text)
        if self.focus_get() is self.entry:
            self._show_suggestions()

    def _show_suggestions(self) -> None:
        if self._popup is None or self._suggestions is None:
            return
        self._popup.show(self._suggestions(self.var.get()), selected=None)
        if self._popup._list is not None:
            self._popup._list.selection_clear(0, "end")

    def _popup_move(self, delta: int) -> str:
        if self._popup is not None:
            if not self._popup.visible:
                self._show_suggestions()
            else:
                self._popup.move(delta)
        return "break"

    def _escape(self, event: tk.Event[Any]) -> str | None:
        if self._popup is not None and self._popup.visible:
            self._popup.hide()
            return "break"
        return None

    def _pick(self, value: str) -> None:
        self.var.set(value)
        self.entry.icursor("end")
        if self._popup is not None:
            self._popup.hide()
        self.entry.focus_set()
        if self._on_submit is not None:
            self._on_submit(value)

    def _submit(self, event: tk.Event[Any]) -> str:
        if self._popup is not None and self._popup.visible and self._popup.current():
            self._popup.choose()
            return "break"
        if self._popup is not None:
            self._popup.hide()
        if self._on_submit is not None:
            self._on_submit(self.var.get())
        return "break"

    def get(self) -> str:
        return self.var.get()

    def set(self, text: str) -> None:
        self.var.set(text)

    def clear(self) -> None:
        self.var.set('')
        if self._popup is not None:
            self._popup.hide()

    def set_error(self, error: bool) -> None:
        self.set_style(border="danger" if error else "border_strong")


class Tooltip:
    def __init__(self, widget: tk.Misc, text: str | abc.Callable[[], str]):
        self._widget = widget
        self._text = text
        self._top: tk.Toplevel | None = None
        self._job: str | None = None
        widget.bind("<Enter>", self._schedule, add=True)
        widget.bind("<Leave>", self._hide, add=True)
        widget.bind("<ButtonPress>", self._hide, add=True)

    def set_text(self, text: str | abc.Callable[[], str]) -> None:
        self._text = text

    def _schedule(self, event: tk.Event[Any]) -> None:
        self._cancel()
        self._job = self._widget.after(450, self._show)

    def _cancel(self) -> None:
        if self._job is not None:
            self._widget.after_cancel(self._job)
            self._job = None

    def _show(self) -> None:
        self._job = None
        text = self._text() if callable(self._text) else self._text
        if not text or not _alive(self._widget):
            return
        top = self._top = tk.Toplevel(self._widget)
        top.overrideredirect(True)
        top.attributes("-topmost", True)
        frame = tk.Frame(top, highlightthickness=1, borderwidth=0)
        theme.bind(frame, background="panel_alt", highlightbackground="border_strong")
        frame.pack()
        label = tk.Label(
            frame,
            text=text,
            justify="left",
            font=theme.font("small"),
            padx=theme.px(10),
            pady=theme.px(6),
            wraplength=theme.px(360),
        )
        theme.bind(label, background="panel_alt", foreground="text")
        label.pack()
        x = self._widget.winfo_pointerx() + theme.px(12)
        y = self._widget.winfo_pointery() + theme.px(16)
        top.geometry(f"+{x}+{y}")

    def _hide(self, event: tk.Event[Any] | None = None) -> None:
        self._cancel()
        if self._top is not None:
            self._top.destroy()
            self._top = None


def bind_click(widgets: abc.Iterable[tk.Misc], callback: abc.Callable[[], Any]) -> None:
    for widget in widgets:
        widget.bind("<Button-1>", lambda e: callback(), add=True)
        widget.configure(cursor="hand2")  # type: ignore[call-arg]


def pointer_inside(widget: tk.Misc) -> bool:
    try:
        under = widget.winfo_containing(*widget.winfo_pointerxy())
    except (tk.TclError, KeyError):
        return False
    while under is not None:
        if under is widget:
            return True
        under = under.master
    return False


def descendants(widget: tk.Misc) -> abc.Iterator[tk.Misc]:
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


def set_window_colors(root: tk.Tk, *, dark: bool) -> None:
    """
    Color the native Windows title bar to blend in with the window background.
    """
    if sys.platform != "win32":
        return

    def colorref(token: str) -> int:
        value = theme.color(token).lstrip('#')
        r, g, b = int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)
        return (b << 16) | (g << 8) | r

    try:
        hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
        dwm = ctypes.windll.dwmapi
        for attribute, value in (
            (20, int(dark)),  # DWMWA_USE_IMMERSIVE_DARK_MODE
            (35, colorref("bg")),  # DWMWA_CAPTION_COLOR
            (36, colorref("text")),  # DWMWA_TEXT_COLOR
        ):
            c_value = ctypes.c_int(value)
            dwm.DwmSetWindowAttribute(
                hwnd, attribute, ctypes.byref(c_value), ctypes.sizeof(c_value)
            )
    except Exception:
        pass

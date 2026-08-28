from __future__ import annotations

import ctypes
import os
from ctypes import wintypes
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Protocol

import pygame

from trace2task.windows_control import (
    WindowInfo,
    WindowsBackend,
    WindowSelector,
    WindowSession,
    configure_physical_dpi_api,
    physical_dpi_context,
)

SRCCOPY = 0x00CC0020
CAPTUREBLT = 0x40000000
DIB_RGB_COLORS = 0
BI_RGB = 0
PW_CLIENTONLY = 0x00000001
PW_RENDERFULLCONTENT = 0x00000002


class _BitmapInfoHeader(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class _RgbQuad(ctypes.Structure):
    _fields_ = [
        ("rgbBlue", wintypes.BYTE),
        ("rgbGreen", wintypes.BYTE),
        ("rgbRed", wintypes.BYTE),
        ("rgbReserved", wintypes.BYTE),
    ]


class _BitmapInfo(ctypes.Structure):
    _fields_ = [("bmiHeader", _BitmapInfoHeader), ("bmiColors", _RgbQuad * 1)]


class WindowFrameCapture(Protocol):
    def capture(self, window: WindowInfo) -> pygame.Surface: ...


class GdiWindowCapture:
    """Capture pixels rendered by one selected window's client area."""

    def __init__(self) -> None:
        if os.name != "nt":
            raise RuntimeError("The Windows capture adapter is available only on Windows")
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
        self.user32.GetDC.argtypes = [wintypes.HWND]
        self.user32.GetDC.restype = wintypes.HDC
        self.user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
        self.user32.ReleaseDC.restype = ctypes.c_int
        self.user32.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
        self.user32.PrintWindow.restype = wintypes.BOOL
        self.user32.GetForegroundWindow.restype = wintypes.HWND
        configure_physical_dpi_api(self.user32)
        self.gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
        self.gdi32.CreateCompatibleDC.restype = wintypes.HDC
        self.gdi32.DeleteDC.argtypes = [wintypes.HDC]
        self.gdi32.DeleteDC.restype = wintypes.BOOL
        self.gdi32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
        self.gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
        self.gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
        self.gdi32.SelectObject.restype = wintypes.HGDIOBJ
        self.gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
        self.gdi32.DeleteObject.restype = wintypes.BOOL
        self.gdi32.BitBlt.argtypes = [
            wintypes.HDC,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.HDC,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.DWORD,
        ]
        self.gdi32.BitBlt.restype = wintypes.BOOL
        self.gdi32.GetDIBits.argtypes = [
            wintypes.HDC,
            wintypes.HBITMAP,
            wintypes.UINT,
            wintypes.UINT,
            wintypes.LPVOID,
            ctypes.POINTER(_BitmapInfo),
            wintypes.UINT,
        ]
        self.gdi32.GetDIBits.restype = ctypes.c_int

    def capture(self, window: WindowInfo) -> pygame.Surface:
        with physical_dpi_context(self.user32):
            return self._capture_physical(window)

    def _capture_physical(self, window: WindowInfo) -> pygame.Surface:
        width = window.client_width
        height = window.client_height
        if width <= 0 or height <= 0:
            raise ValueError("Cannot capture a window with an empty client area")

        screen_dc = self.user32.GetDC(None)
        if not screen_dc:
            raise ctypes.WinError(ctypes.get_last_error())
        memory_dc = self.gdi32.CreateCompatibleDC(screen_dc)
        if not memory_dc:
            self.user32.ReleaseDC(None, screen_dc)
            raise ctypes.WinError(ctypes.get_last_error())
        bitmap = self.gdi32.CreateCompatibleBitmap(screen_dc, width, height)
        if not bitmap:
            self.gdi32.DeleteDC(memory_dc)
            self.user32.ReleaseDC(None, screen_dc)
            raise ctypes.WinError(ctypes.get_last_error())

        previous_object = self.gdi32.SelectObject(memory_dc, bitmap)
        try:
            copied = self.user32.PrintWindow(
                wintypes.HWND(window.handle),
                memory_dc,
                PW_CLIENTONLY | PW_RENDERFULLCONTENT,
            )
            if not copied:
                foreground = int(self.user32.GetForegroundWindow() or 0)
                if foreground != window.handle:
                    raise RuntimeError(
                        "The target cannot render off-screen capture and is not foreground; "
                        "no screen pixels were captured"
                    )
                copied = self.gdi32.BitBlt(
                    memory_dc,
                    0,
                    0,
                    width,
                    height,
                    screen_dc,
                    window.client_left,
                    window.client_top,
                    SRCCOPY | CAPTUREBLT,
                )
                if not copied:
                    raise ctypes.WinError(ctypes.get_last_error())
            self.gdi32.SelectObject(memory_dc, previous_object)
            previous_object = None

            bitmap_info = _BitmapInfo()
            bitmap_info.bmiHeader = _BitmapInfoHeader(
                biSize=ctypes.sizeof(_BitmapInfoHeader),
                biWidth=width,
                biHeight=-height,
                biPlanes=1,
                biBitCount=32,
                biCompression=BI_RGB,
                biSizeImage=width * height * 4,
            )
            pixels = ctypes.create_string_buffer(width * height * 4)
            scanlines = self.gdi32.GetDIBits(
                screen_dc,
                bitmap,
                0,
                height,
                pixels,
                ctypes.byref(bitmap_info),
                DIB_RGB_COLORS,
            )
            if scanlines != height:
                raise ctypes.WinError(ctypes.get_last_error())
            return pygame.image.frombuffer(pixels.raw, (width, height), "BGRA").copy()
        finally:
            if previous_object:
                self.gdi32.SelectObject(memory_dc, previous_object)
            self.gdi32.DeleteObject(bitmap)
            self.gdi32.DeleteDC(memory_dc)
            self.user32.ReleaseDC(None, screen_dc)


@dataclass(frozen=True)
class CaptureResult:
    output_path: str
    window: dict[str, object]
    size: tuple[int, int]


def capture_window_once(
    selector: WindowSelector,
    output_path: Path,
    *,
    backend: WindowsBackend,
    capture: WindowFrameCapture,
    focus: bool = False,
) -> CaptureResult:
    session = WindowSession(selector, backend)
    window = session.focus(timeout_seconds=10) if focus else session.resolve()
    if not window.is_visible or window.is_minimized:
        raise RuntimeError("The target window must be visible and unminimized for capture")
    surface = capture.capture(window)
    destination = output_path.expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    pygame.image.save(surface, destination)
    return CaptureResult(
        output_path=str(destination),
        window=asdict(window),
        size=surface.get_size(),
    )

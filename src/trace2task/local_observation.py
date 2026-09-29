"""Observation adapters for the local GUI-agent loop.

The runner owns target authorization; an observation adapter only returns the
fresh screenshot and trusted target identity for the next execution turn.
"""

from __future__ import annotations

from pathlib import Path

import pygame


class Win32DesktopObservation:
    def __init__(self, session, capture, root):
        self.session = session
        self.capture = capture
        self.root = Path(root)

    def observe(self, _target, index):
        window = self.session.observe()
        frame = self.capture.capture(window)
        path = self.root / f"{index:04d}.png"
        pygame.image.save(frame, path)
        identity = {
            "foreground_handle": window.handle,
            "screen_size": (window.client_width, window.client_height),
        }
        return identity, dict(identity), path

    @staticmethod
    def context(_target, state, backend):
        # Driver capabilities are enforced below the model, not injected into its prompt.
        return None


class CuaWindowObservation:
    def __init__(self, scope, driver):
        self.scope = scope
        self.driver = driver

    def observe(self, target, index):
        target = self.scope.switch(target)
        state, path = self.driver.observe(target, index)
        return target, state, path

    def context(self, target, state, backend):
        # IDs are task data for models that can select an authorized target.
        # Delivery mode and backend action routes stay in the execution adapter.
        return self.scope.context(target)

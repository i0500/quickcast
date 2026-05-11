"""Services — backend handle bag passed to UI sections.

Sections used to be built from `_mock_state` so the design preview could
run without a controller. The production AppWindow injects this Services
bundle instead, and sections write directly to the real `settings`,
calling `save_now()` (which debounces) so changes persist between runs.

Section factories accept Services as their single positional argument.
For preview-mode (mocks) we still ship a `Services.mock()` constructor.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from PySide6.QtCore import QObject, QTimer, Signal

from quickcast.config import Settings


@dataclass
class Services:
    settings: Settings
    controller: Optional[object] = None    # MacroController (avoids cyclic import)
    arduino: Optional[object] = None       # ArduinoBackend
    telegram: Optional[object] = None      # TelegramNotifier
    alarms: Optional[object] = None        # AlarmScheduler
    bus: Optional["_AppSignalBus"] = None

    def save_now(self) -> None:
        """Persist settings — call from any control's change handler."""
        try:
            self.settings.save()
        except Exception:
            # Save failures shouldn't break the UI; logger picks it up.
            from quickcast.utils.logger import logger
            logger.exception("settings.save() failed")

    @classmethod
    def mock(cls) -> "Services":
        """Preview-mode services — fresh defaults, no backend wiring."""
        from quickcast.ui.sections._mock_state import mock_settings
        return cls(settings=mock_settings, bus=_AppSignalBus())


class _AppSignalBus(QObject):
    """App-level signal bus carried inside Services.

    Distinct from `quickcast.ui.design.signals.bus` (theme + live_scores
    are global concerns). This bus carries section-to-section domain
    events: slot list mutated, alarm list mutated, capture target
    changed, etc.
    """
    slot_list_changed = Signal()
    alarm_list_changed = Signal()
    capture_target_changed = Signal()
    settings_imported = Signal()


__all__ = ["Services", "_AppSignalBus"]

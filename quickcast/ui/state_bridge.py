"""Bridge between in-memory mock state and persisted Settings.

The new design preview was built on three singletons that lived in
`_mock_state`:

  • `mock_settings` (Settings)
  • `slot_state`    (per-slot on/off + label + key, with toggled signal)
  • `alarm_state`   (per-alarm on/off, with toggled signal)

Sections all read/write these. To make the production app persist user
edits without a per-section refactor, we:

  1. Mutate `mock_settings` IN PLACE so it carries the real saved values
     (other modules already imported its reference at module load time;
     re-binding the attribute would not propagate).
  2. Re-seed `slot_state` / `alarm_state` from those settings.
  3. Subscribe to the singletons' `*_toggled` signals and write back to
     settings + persist via `services.save_now()`.

That gives us round-trip persistence for the most visible interactions
without touching `combat_section.py`, `slots_section.py`, etc.
"""
from __future__ import annotations

from PySide6.QtCore import QObject, QTimer

from quickcast.config import Settings
from quickcast.ui.sections import _mock_state
from quickcast.ui.sections._mock_state import alarm_state, slot_state
from quickcast.utils.logger import logger


_DEV_TITLE_HINTS = (
    "lineage-w-macro", "vscode", "visual studio code", "cursor",
    "powershell", "terminal", "claude", "github",
)


def _looks_like_real_game(title: str) -> bool:
    """Heuristic: is this title a plausible Lineage W game window?

    The actual game writes "리니지W | <캐릭명>" (Korean) or "Lineage W |
    <name>" (English). Generic strings like "Lineage" alone or random
    short titles are almost always something else (Steam tile, browser
    tab, …) and should not be auto-attached.
    """
    t = (title or "").lower()
    if not t:
        return False
    # Must contain a strong game indicator AND have at least 6 chars.
    if len(t) < 6:
        return False
    return ("리니지" in title) or ("lineage w" in t) or ("퍼플" in title) or ("purple" in t)


def install(real_settings: Settings) -> None:
    """Apply a real Settings instance to the shared mock_settings.

    This must be called BEFORE any section factory runs, so the in-place
    mutation propagates to the references those sections capture.
    """
    mock = _mock_state.mock_settings
    # Pydantic v2 allows attribute assignment by default. Copy every
    # top-level field — that's enough because `Settings` is the root
    # model and submodels are referenced via `mock.pk`, `mock.potion`,
    # `mock.slots`, `mock.alarms`, etc. Replacing those references is
    # the goal: sliders / forms then read/write the live submodels.
    for field_name in type(real_settings).model_fields:
        setattr(mock, field_name, getattr(real_settings, field_name))

    # Sanitise stored capture window: clear if it looks like a dev
    # artefact (IDE/terminal) OR isn't plausibly a real game window
    # (e.g. bare "Lineage" from a Steam tile). Auto-detect on next boot
    # will pick "리니지W | …" if the actual game is running.
    raw = mock.capture_window_title or ""
    lowered = raw.lower()
    is_dev = any(h in lowered for h in _DEV_TITLE_HINTS)
    is_real = _looks_like_real_game(raw)
    if raw and (is_dev or not is_real):
        logger.warning(
            f"state_bridge: cleared non-game window title '{raw}'"
            f" (dev={is_dev}, real_game={is_real})"
        )
        mock.capture_window_title = ""
    logger.info(
        f"state_bridge: settings → mock applied "
        f"(slots={len(mock.slots)}, alarms={len(mock.alarms)}, "
        f"theme='{mock.theme}', pk.thr={mock.pk.threshold}, po.thr={mock.potion.threshold})"
    )

    _seed_slot_state_from_settings(mock)
    _seed_alarm_state_from_settings(mock)


def wire_persistence(save_now) -> None:
    """Connect singleton change signals → save callback.

    `save_now` is a no-arg callable (typically `services.save_now`).
    Connection is one-shot per process; idempotency is the caller's
    responsibility (do not call wire_persistence twice).
    """
    # Slot on/off — settings.slots is a dict keyed by slot id.
    def _on_slot_toggled(sid: str, on: bool) -> None:
        slot = _mock_state.mock_settings.slots.get(sid)
        if slot is not None:
            slot.use = on
            save_now()

    slot_state.slot_toggled.connect(_on_slot_toggled)

    # Alarm on/off — Alarm.enabled (not .use), matched by label.
    def _on_alarm_toggled(name: str, on: bool) -> None:
        for al in _mock_state.mock_settings.alarms:
            if al.label == name:
                al.enabled = on
                break
        save_now()

    alarm_state.alarm_toggled.connect(_on_alarm_toggled)


# ───────── seeding helpers ─────────
def _seed_slot_state_from_settings(settings: Settings) -> None:
    """Replace slot_state's internal maps with values derived from settings.

    We don't recreate the singleton — too many widgets are subscribed.
    We just mutate its private dicts. The singleton emits no signal for
    bulk seeding; widgets re-read on theme_changed / restyle anyway.
    """
    on_map: dict[str, bool] = {}
    label_map: dict[str, str] = {}
    key_map: dict[str, str] = {}
    for sid, slot in settings.slots.items():
        on_map[sid] = slot.use
        label_map[sid] = slot.label or f"SLOT-{sid}"
        key_map[sid] = slot.key
    if not on_map:
        # empty config — leave the demo seed intact
        return
    slot_state._on = on_map
    slot_state._label = label_map
    slot_state._key = key_map


def _seed_alarm_state_from_settings(settings: Settings) -> None:
    """Seed alarm_state from the persisted alarm list. Always runs the
    full replacement so the dashboard sidebar matches the Alerts tab —
    if the user has no alarms saved, the demo seed (혈던/격전/etc.) is
    cleared too. Previously we kept the demo when settings.alarms was
    empty, which made the dashboard show fake alarms while the Alerts
    tab was correctly empty.
    """
    on_map = {al.label: bool(al.enabled) for al in settings.alarms}
    alarm_state._on = on_map


__all__ = ["install", "wire_persistence"]

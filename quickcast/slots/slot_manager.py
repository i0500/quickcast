"""Slot evaluation engine.

Owns runtime cooldown state and decides which slots fire on each frame.
The original JS lived inside `controlLoop`; pulling it out lets us unit
test the decision logic without spinning up capture/serial.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Iterable

from quickcast.config import PkSlot, PotionSlot, Settings, Slot
from quickcast.core.recognition import FrameAnalysis
from quickcast.utils.logger import logger
from quickcast.utils.timer import Cooldown


@dataclass
class FireEvent:
    """Emitted when the manager decides a slot should trigger."""
    slot_id: str          # e.g. "1", "0", "11", "pk", "potion"
    label: str
    key: str
    count: int
    delay: float
    tele_use: bool        # send a Telegram on fire
    snapshot: bool = False  # if True, attach a screenshot to telegram


class SlotManager:
    """Evaluates all slots against the latest analysis and emits FireEvents."""

    PK_ID = "pk"
    POTION_ID = "potion"

    def __init__(self) -> None:
        self.cooldown = Cooldown()
        # Per-slot last-diag-log timestamp for throttling.
        self._last_diag: dict[str, float] = {}

    def evaluate(
        self,
        settings: Settings,
        analysis: FrameAnalysis,
    ) -> list[FireEvent]:
        events: list[FireEvent] = []

        # ───── ordinary slots (sorted: 1..9, 0, then 11+) ─────
        for sid in self._slot_iteration_order(settings.slots):
            slot = settings.slots[sid]
            # Slot skip reasons go to DEBUG so the dashboard isn't
            # flooded; visible to advanced users via the log file.
            if not slot.use:
                continue
            if not (slot.hp.min <= analysis.hp <= slot.hp.max):
                continue
            if not (slot.mp.min <= analysis.mp <= slot.mp.max):
                continue
            if not self.cooldown.is_ready(sid):
                continue

            events.append(self._make_event(sid, slot))
            self.cooldown.trigger(sid, slot.cooltime)
            if not slot.repeat:
                slot.use = False
            logger.info(
                f"🎯 {slot.label}  키:{slot.key} ×{slot.count}  "
                f"(HP {analysis.hp}%, MP {analysis.mp}%)"
            )

        # ───── PK slot ─────
        pk = settings.pk
        if pk.use and analysis.pk_detected and self.cooldown.is_ready(self.PK_ID):
            if pk.hp.min <= analysis.hp <= pk.hp.max:
                events.append(FireEvent(
                    slot_id=self.PK_ID, label="PK 대응",
                    key=pk.key, count=pk.count, delay=pk.delay,
                    tele_use=True, snapshot=True,
                ))
                self.cooldown.trigger(self.PK_ID, pk.cooltime)
                if not pk.repeat:
                    pk.use = False
                logger.info(
                    f"⚔️ PK 대응  키:{pk.key} ×{pk.count}  (HP {analysis.hp}%)"
                )

        # ───── Potion-empty slot (one-shot regardless of repeat) ─────
        potion = settings.potion
        if potion.use and analysis.potion_empty:
            if potion.hp.min <= analysis.hp <= potion.hp.max:
                events.append(FireEvent(
                    slot_id=self.POTION_ID, label="물약 부족 귀환",
                    key=potion.key, count=potion.count, delay=potion.delay,
                    tele_use=True, snapshot=True,
                ))
                potion.use = False
                logger.info(
                    f"🧪 물약 부족 → 귀환 키:{potion.key} ×{potion.count}  (HP {analysis.hp}%)"
                )

        return events

    @staticmethod
    def _slot_iteration_order(slots: dict[str, Slot]) -> Iterable[str]:
        """Match the original ordering: 1..9, 0, then sorted dynamic slots."""
        base = ["1", "2", "3", "4", "5", "6", "7", "8", "9", "0"]
        present = [s for s in base if s in slots]
        dynamic = sorted(
            [s for s in slots if s not in base],
            key=lambda s: int(s) if s.isdigit() else 999_999,
        )
        return present + dynamic

    @staticmethod
    def _make_event(sid: str, slot: Slot) -> FireEvent:
        return FireEvent(
            slot_id=sid, label=slot.label,
            key=slot.key, count=slot.count, delay=slot.delay,
            tele_use=slot.tele_use,
        )

    def reset(self) -> None:
        self.cooldown.reset()


__all__ = ["SlotManager", "FireEvent"]

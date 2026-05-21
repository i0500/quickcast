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
        # 발동 조건이 처음 만족된 시각 — sustain_seconds 동안 연속으로
        # 유지될 때만 발동시키기 위한 타이머. 조건이 깨지면 해당 키를
        # 제거하고, 발동에 성공하면 다음 사이클을 위해 다시 제거한다.
        # 키: 슬롯 id ("1".."9","0","11"+, "pk", "potion").
        self._cond_first_seen: dict[str, float] = {}

    def evaluate(
        self,
        settings: Settings,
        analysis: FrameAnalysis,
    ) -> list[FireEvent]:
        events: list[FireEvent] = []
        now = time.monotonic()

        # ───── ordinary slots (sorted: 1..9, 0, then 11+) ─────
        active_ids: set[str] = set()
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
            # 조건 만족 — sustain 타이머 갱신/체크. 쿨타임/sustain 미충족
            # 시에도 first_seen은 유지해야 연속 유지 시간이 누적된다.
            active_ids.add(sid)
            first_seen = self._cond_first_seen.get(sid)
            if first_seen is None:
                self._cond_first_seen[sid] = now
                first_seen = now
            if not self.cooldown.is_ready(sid):
                continue
            sustain = (
                max(0.0, float(getattr(slot, "sustain_seconds", 0.0) or 0.0))
                if bool(getattr(slot, "sustain_enabled", False))
                else 0.0
            )
            held = now - first_seen
            if sustain > 0.0 and held < sustain:
                continue

            events.append(self._make_event(sid, slot))
            self.cooldown.trigger(sid, slot.cooltime)
            # 발동 후 sustain 타이머 리셋 — 다음 사이클도 동일하게
            # sustain 만큼 유지돼야 다시 발동한다.
            self._cond_first_seen.pop(sid, None)
            if not slot.repeat:
                slot.use = False
            if sustain > 0.0:
                logger.info(
                    f"🎯 {slot.label}  키:{slot.key} ×{slot.count}  "
                    f"(HP {analysis.hp}%, MP {analysis.mp}%, 유지 {held:.1f}s)"
                )
            else:
                logger.info(
                    f"🎯 {slot.label}  키:{slot.key} ×{slot.count}  "
                    f"(HP {analysis.hp}%, MP {analysis.mp}%)"
                )

        # ───── PK slot ─────
        pk = settings.pk
        pk_active = (
            pk.use and analysis.pk_detected
            and (pk.hp.min <= analysis.hp <= pk.hp.max)
        )
        if pk_active:
            active_ids.add(self.PK_ID)
            first_seen = self._cond_first_seen.get(self.PK_ID)
            if first_seen is None:
                self._cond_first_seen[self.PK_ID] = now
                first_seen = now
            if self.cooldown.is_ready(self.PK_ID):
                sustain = max(0.0, float(getattr(pk, "sustain_seconds", 0.0) or 0.0))
                held = now - first_seen
                if sustain == 0.0 or held >= sustain:
                    events.append(FireEvent(
                        slot_id=self.PK_ID, label="PK 대응",
                        key=pk.key, count=pk.count, delay=pk.delay,
                        tele_use=True, snapshot=True,
                    ))
                    self.cooldown.trigger(self.PK_ID, pk.cooltime)
                    self._cond_first_seen.pop(self.PK_ID, None)
                    if not pk.repeat:
                        pk.use = False
                    if sustain > 0.0:
                        logger.info(
                            f"⚔️ PK 대응  키:{pk.key} ×{pk.count}  "
                            f"(HP {analysis.hp}%, 유지 {held:.1f}s)"
                        )
                    else:
                        logger.info(
                            f"⚔️ PK 대응  키:{pk.key} ×{pk.count}  (HP {analysis.hp}%)"
                        )

        # ───── Potion-empty slot (one-shot regardless of repeat) ─────
        potion = settings.potion
        potion_active = (
            potion.use and analysis.potion_empty
            and (potion.hp.min <= analysis.hp <= potion.hp.max)
        )
        if potion_active:
            active_ids.add(self.POTION_ID)
            first_seen = self._cond_first_seen.get(self.POTION_ID)
            if first_seen is None:
                self._cond_first_seen[self.POTION_ID] = now
                first_seen = now
            sustain = max(0.0, float(getattr(potion, "sustain_seconds", 0.0) or 0.0))
            held = now - first_seen
            if sustain == 0.0 or held >= sustain:
                events.append(FireEvent(
                    slot_id=self.POTION_ID, label="물약 부족 귀환",
                    key=potion.key, count=potion.count, delay=potion.delay,
                    tele_use=True, snapshot=True,
                ))
                potion.use = False
                self._cond_first_seen.pop(self.POTION_ID, None)
                if sustain > 0.0:
                    logger.info(
                        f"🧪 물약 부족 → 귀환 키:{potion.key} ×{potion.count}  "
                        f"(HP {analysis.hp}%, 유지 {held:.1f}s)"
                    )
                else:
                    logger.info(
                        f"🧪 물약 부족 → 귀환 키:{potion.key} ×{potion.count}  "
                        f"(HP {analysis.hp}%)"
                    )

        # 조건이 더 이상 만족되지 않는 id의 sustain 타이머는 즉시 제거 —
        # 다음 활성화 때 첫 감지 시각이 새로 잡혀야 한다.
        for sid in list(self._cond_first_seen.keys()):
            if sid not in active_ids:
                self._cond_first_seen.pop(sid, None)

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
        self._cond_first_seen.clear()

    def reset_sustain(self) -> None:
        """sustain 누적 타이머만 리셋. 사냥터 복귀 시퀀스 직후처럼 게임
        상태가 점프적으로 바뀌어 "이전에 N초 유지됐다"가 의미를 잃는
        시점에 호출한다. 쿨타임은 그대로 둔다."""
        self._cond_first_seen.clear()


__all__ = ["SlotManager", "FireEvent"]

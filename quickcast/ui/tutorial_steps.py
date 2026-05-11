"""Definition of the first-run tutorial steps.

Kept separate from the overlay widget so the copy + target-widget
selectors can be reviewed/edited without touching the rendering code.
Each step's `target_finder` receives the AppWindow and returns the
widget to spotlight (or None for a centered intro card).
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtWidgets import QWidget

from quickcast.ui.components.tutorial import TutorialStep


def _find(app, attr_path: str) -> Optional[QWidget]:
    """Walk a dotted attribute path on the app, supporting both
    `obj.attr` and `obj[key]` lookups + tuple/list index `obj.0`.
    Returning None lets the overlay fall back to a centered bubble."""
    cur = app
    for part in attr_path.split("."):
        if cur is None:
            return None
        # Try dict / mapping access first
        if isinstance(cur, dict):
            cur = cur.get(part)
            continue
        # Try int index for tuple/list
        if part.lstrip("-").isdigit():
            try:
                cur = cur[int(part)]
                continue
            except Exception:
                return None
        cur = getattr(cur, part, None)
    return cur if isinstance(cur, QWidget) else None


def build_steps() -> list[TutorialStep]:
    return [
        # ── 1. 캡처 영역 (대시보드 ROI 박스) ──
        TutorialStep(
            title="캡처 영역 — HP / MP / PK / 물약 박스",
            body_html=(
                "게임창 위에 4개의 ROI 박스가 있습니다.<br>"
                "<b>빨강</b> HP &nbsp;·&nbsp; <b>파랑</b> MP &nbsp;·&nbsp; "
                "<b>주황</b> PK &nbsp;·&nbsp; <b>노랑</b> 물약<br><br>"
                "게임 해상도/UI 위치에 따라 박스를 끌어서 정확한 위치로 옮겨주세요. "
                "PK·물약 박스는 크기 고정, 위치만 이동 가능합니다."
            ),
            section_id="dashboard",
            target_finder=lambda a: _find(a, "_sections.dashboard.1._dashboard_preview"),
            arrow="below",
        ),
        # ── 2. 슬롯 키 (슬롯 탭) ──
        TutorialStep(
            title="슬롯 — 사용할 스킬 등록",
            body_html=(
                "<b>+ 슬롯 추가</b>로 사용할 스킬을 등록합니다.<br>"
                "각 슬롯에 다음을 설정:<br>"
                "&nbsp;·&nbsp; <b>입력 키</b>: 게임에서 그 스킬을 발동하는 키<br>"
                "&nbsp;·&nbsp; <b>HP / MP 범위</b>: 이 범위 안일 때만 발사<br>"
                "&nbsp;·&nbsp; <b>연사 횟수 / 간격 / 쿨타임</b><br>"
                "&nbsp;·&nbsp; <b>반복</b>: 끄면 1회만 발사 후 자동 OFF"
            ),
            section_id="slots",
            target_finder=lambda a: _find(a, "_sections.slots.0"),
            arrow="right",
        ),
        # ── 3. 전투 대응 (PK / 물약) ──
        TutorialStep(
            title="전투 대응 — PK · 물약 자동 응답",
            body_html=(
                "<b>PK 대응</b>: PK 감지 시 귀환 키 자동 입력<br>"
                "<b>물약 부족 대응</b>: ! 표시 감지 시 1회 귀환 (이후 토글 자동 OFF)<br><br>"
                "<b>감지 민감도</b> 슬라이더로 임계값 조정. "
                "박스를 옮겼을 때 점수가 변화는지 대시보드에서 확인하세요."
            ),
            section_id="combat",
            target_finder=lambda a: _find(a, "_sections.combat.1"),
            arrow="center",
        ),
        # ── 4. 알람 ──
        TutorialStep(
            title="알람 — 운영 점검 / 이벤트 시간 알림",
            body_html=(
                "지정 시간에 Windows 알림 + 사운드로 알려줍니다.<br>"
                "&nbsp;·&nbsp; <b>요일</b>: 비우면 매일<br>"
                "&nbsp;·&nbsp; <b>모드</b>: 반복 / 1회<br>"
                "&nbsp;·&nbsp; <b>재알림</b>: N분 간격으로 다시 알림 (0이면 1회)<br>"
                "&nbsp;·&nbsp; <b>반복 종료</b>: 자동 종료까지 시간 (전역 설정)"
            ),
            section_id="alerts",
            target_finder=lambda a: _find(a, "_sections.alerts.1"),
            arrow="center",
        ),
        # ── 5. 사냥터 복귀 ──
        TutorialStep(
            title="사냥터 복귀 — 마을 귀환 후 자동 복귀",
            body_html=(
                "<b>오만의 탑 주문서 사용</b> 권장 흐름:<br>"
                "&nbsp;1. 슬롯에 <b>주문서 사용 키</b> 등록<br>"
                "&nbsp;2. 사냥터 복귀 단계:<br>"
                "&nbsp;&nbsp;&nbsp;① 주문서 키 입력 → "
                "② 이동 → ③ 어시스트 키<br><br>"
                "트리거: 물약 부족·PK·HP 0%·특정 슬롯 발사. "
                "각 트리거 1회씩 발동 (조건 해제 후 재발동 가능)."
            ),
            section_id="combat",
            target_finder=lambda a: _find(a, "_sections.combat.1"),
            arrow="center",
        ),
    ]


__all__ = ["build_steps"]

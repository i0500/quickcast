"""
QuickCast 네이버 포스트용 DOCX — Step 3
섹션 05 슬롯 — 상황별 스킬 자동 사용
섹션 06 PK · 물약 부족 대응
섹션 07 펫 호루라기 자동 닫기 NEW (v1.0.3)
섹션 08 사냥터 자동 복귀
"""
from __future__ import annotations
from pathlib import Path

from docx import Document
from docx.shared import Pt, Cm, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH

# Step 1·2 의 헬퍼·디자인을 그대로 재사용
from make_naverpost_step1 import (
    FONT_KR, FONT_EN, INK, INK_SOFT, ACCENT, RULE,
    add_paragraph, add_page_break, style_run,
    make_cover, make_intro, setup_page,
)
from make_naverpost_step2 import (
    section_header, lead, body, h3, bullet, capture_placeholder,
    info_box, compare_table, link_bullet, add_hyperlink,
    section_01_compare, section_02_install, section_03_power, section_04_hpmp,
)


# ───────── 섹션 본문 ─────────
def section_05_slots(doc):
    section_header(
        doc, "05", "슬롯 — 상황별 스킬 자동 사용",
        "HP·MP 구간에 맞춰 정해둔 키를 자동으로 입력합니다.",
    )
    lead(
        doc,
        "슬롯은 QuickCast의 핵심 기능입니다. 하나의 슬롯에 ‘어떤 키를’, ‘HP·MP가 어느 구간일 때’, "
        "‘몇 번 / 어떤 간격으로 / 얼마나 자주’ 누를지 정의해두면, 매크로가 켜져 있는 동안 "
        "그 조건이 만족될 때마다 자동으로 키 입력을 보냅니다."
    )

    capture_placeholder(doc, 6,
                         "슬롯 탭 — 좌측 슬롯 목록과 우측 편집 화면",
                         image_name="04_slots.png")

    h3(doc, "슬롯 하나에 들어가는 항목")
    bullet(doc, "라벨", "‘공격 1’, ‘힐’, ‘버프 갱신’ 같은 식별용 이름. 자유롭게 입력.")
    bullet(doc, "입력 키", "게임에서 해당 스킬에 할당된 단축키. 단일 키 또는 조합키 지원.")
    bullet(doc, "HP / MP 범위",
            "두 범위가 모두 충족될 때만 동작합니다. 예) HP 0~50%, MP 30~100%.")
    bullet(doc, "연사 횟수·간격",
            "한 번 발동에 몇 회, 몇 ms 간격으로 누를지. 광역기·연타기에 활용.")
    bullet(doc, "쿨타임", "같은 슬롯이 다시 발동되기까지 최소 대기 시간(초).")
    bullet(doc, "반복", "끄면 ‘조건 충족 → 1회 발동 후 슬롯 자동 OFF’. 한 번만 쓸 스킬에 유용.")
    bullet(doc, "발동 시 텔레그램 알림",
            "이 슬롯이 발동될 때마다 텔레그램 메시지를 받아볼 수 있습니다(설정 연결 필요).")

    h3(doc, "활용 예시")
    info_box(doc, "SAMPLE", [
        "공격 광역기 — 키 ‘1’, HP 30~100% & MP 20~100%, 연사 4회·간격 200ms, 쿨타임 5초.",
        "비상 회복기 — 키 ‘F1’, HP 0~30%, 1회만, 쿨타임 8초, 반복 끔(상황 정리되면 직접 다시 ON).",
        "버프 갱신 — 키 ‘F5’, HP 80~100% & MP 50~100%, 쿨타임 120초.",
    ])

    body(
        doc,
        "여러 슬롯을 동시에 활성화해도 됩니다. 각 슬롯은 자신의 조건과 쿨타임만 보고 독립적으로 "
        "발동되므로, 비상 회복은 위급할 때만, 광역기는 평소에 자동으로 — 같은 식으로 자연스럽게 "
        "역할 분담이 됩니다."
    )


def section_06_pk_potion(doc):
    section_header(
        doc, "06", "PK · 물약 부족 대응",
        "위급 상황을 화면에서 감지해 정해둔 키로 대응합니다.",
    )
    lead(
        doc,
        "사냥 중 가장 빠른 반응이 필요한 두 가지 — PK 감지와 물약 부족 — 를 "
        "별도 카드로 분리해 두었습니다. 두 기능 모두 화면 상의 표시(아이콘 / 텍스트)를 "
        "템플릿 매칭으로 인식해 동작합니다."
    )

    capture_placeholder(doc, 7,
                         "전투 대응 탭 — PK 대응 / 물약 부족 대응 / 사냥터 복귀 매크로",
                         image_name="03_combat.png")

    h3(doc, "PK 대응")
    body(
        doc,
        "캐릭터 머리 위에 뜨는 PK 표시(검·X 아이콘)를 인식해 자동 대응합니다. "
        "보통 ‘귀환 스크롤 키’를 지정해 두지만, 자리에 따라 즉시 빠지는 단축키나 보호 스킬 키를 "
        "쓰는 경우도 있습니다."
    )
    bullet(doc, "입력 키 · 연사 · 쿨타임", "다른 슬롯과 같은 방식으로 동작 정의.")
    bullet(doc, "HP 범위",
            "지정한 HP 구간에서만 반응. 풀피일 때 무리해서 귀환하는 일을 막을 수 있습니다.")
    bullet(doc, "감지 민감도 / 임계값",
            "박스 안 점수가 임계값을 넘으면 ‘감지됨’. 낮을수록 느슨, 높을수록 엄격.")

    h3(doc, "물약 부족 대응")
    body(
        doc,
        "체력 바 옆에 떠 있는 ‘!’ 표시를 감지합니다. 자판기 형 물약을 다 쓴 시점에 떠 있는 그 "
        "느낌표예요. 감지되면 지정한 키를 입력해 사냥터 복귀 시퀀스를 트리거할 수 있습니다."
    )

    h3(doc, "박스를 크게 — 검색 영역 모드")
    body(
        doc,
        "박스(ROI)를 템플릿보다 크게 잡으면, 그 안에서 자동으로 가장 잘 맞는 위치를 찾아 매칭합니다. "
        "캐릭터 머리 위 PK 표시처럼 위치가 살짝씩 흔들리는 요소도 안정적으로 잡힙니다. "
        "매칭된 위치는 라이브 프리뷰에 흰색 박스로 표시되어, 어디서 잡혔는지 한눈에 확인할 수 있습니다."
    )

    info_box(doc, "TIP — 임계값 정하기", [
        "1) 실제 PK 또는 물약 부족 상황을 만들고 라이브 점수를 확인합니다.",
        "2) 평소 화면(감지가 없을 때)의 점수와 비교합니다.",
        "3) 평소 점수 + 약 100,000 정도의 여유를 두고 임계값으로 설정합니다.",
        "4) 오탐이 잦으면 임계값을 한 단계 더 올리거나, ROI 박스를 더 좁게 잡습니다.",
    ])


def section_07_pet_whistle(doc):
    section_header(
        doc, "07", "펫 호루라기 자동 닫기  ⭐ NEW",
        "v1.0.3 — 3초 유지 트리거로 오탐을 차단합니다.",
    )
    lead(
        doc,
        "펫 교감이 성공하면 화면 중앙에 발바닥 모양의 팝업이 잠시 뜨면서 슬롯 입력을 가로막습니다. "
        "QuickCast는 이 발바닥 팝업을 인식해 자동으로 ESC를 보내, 슬롯 동작이 끊기지 않도록 합니다."
    )

    capture_placeholder(doc, 8,
                         "캡처 탭 — 오버레이 자동 닫기 카드 (펫 호루라기 + 감지 민감도 + ROI)",
                         image_name="02_capture.png")

    h3(doc, "v1.0.3에서 달라진 점")
    bullet(doc, "3초 유지 트리거",
            "발바닥이 ‘3초 이상 연속 감지’될 때만 ESC를 보냅니다. 비슷한 색상의 짧은 화면 효과로 "
            "ESC가 잘못 발사되는 일이 거의 사라집니다.")
    bullet(doc, "템플릿 이미지 교체",
            "발바닥 인식용 기준 이미지가 새로 교체되어 매칭 점수가 더 안정적입니다.")
    bullet(doc, "‘아이템 획득’ 카드 숨김",
            "보상 상자 팝업도 같은 ESC로 함께 닫히는 경우가 많아 별도 UI에서 가렸습니다(설정은 유지).")

    h3(doc, "설정 방법")
    bullet(doc, "1단계", "캡처 탭 → ‘오버레이 자동 닫기’ 카드.")
    bullet(doc, "2단계", "‘펫 호루라기 (교감 성공 발바닥)’ 토글 ON.")
    bullet(doc, "3단계",
            "ROI 박스(분홍)를 발바닥 팝업이 뜨는 화면 상단 중앙 위치에 맞춰 둡니다. 기본값이 맞다면 "
            "그대로 두어도 됩니다.")
    bullet(doc, "4단계",
            "감지 민감도 슬라이더로 임계값 조정. 60~70% 정도가 안정적입니다.")

    info_box(doc, "NOTE", [
        "‘테스트 ESC’ 버튼으로 키 경로(입력 방식 → 게임 창)가 정상인지 미리 확인할 수 있습니다.",
        "민감도를 너무 높이면 발바닥은 더 잘 잡지만 진짜 발바닥일 때만 잡히는 게 아니라 비슷한 "
        "이미지에도 반응하지 않게 될 수 있으니, 60% 부근에서 시작하시기를 권합니다.",
    ])


def section_08_recovery(doc):
    section_header(
        doc, "08", "사냥터 자동 복귀",
        "마을로 돌아왔을 때 사냥터로 다시 자동 복귀하는 클릭·키 시퀀스.",
    )
    lead(
        doc,
        "물약을 다 쓰거나 PK로 마을에 강제 귀환됐을 때, 사냥터로 다시 들어가는 과정은 "
        "보통 ‘주문서 사용 → 마법진 클릭 → 어시스트 키’ 같은 정해진 동작의 반복입니다. "
        "그 흐름을 단계별로 등록해 두면 트리거 발생 시 자동으로 재현해 줍니다."
    )

    h3(doc, "트리거 — 언제 시작될까")
    bullet(doc, "물약 부족", "‘!’ 표시 감지 시 시작.")
    bullet(doc, "PK 감지", "PK 표시 감지 시 시작.")
    bullet(doc, "HP 0%", "캐릭터 사망 시 시작.")
    bullet(doc, "특정 슬롯 사용",
            "사용자가 ‘귀환 스크롤’ 슬롯에 묶어두면 그 슬롯이 발동될 때 함께 시작.")
    body(
        doc,
        "여러 트리거를 동시에 켜둘 수 있습니다. 한 번 시작되면 시퀀스가 끝날 때까지는 "
        "동일 트리거로 다시 시작되지 않아 중복 실행 걱정이 없습니다."
    )

    h3(doc, "단계 등록")
    bullet(doc, "단계 추가", "‘+ 단계 추가’ 버튼으로 새 단계를 만듭니다.")
    bullet(doc, "좌표 클릭 모드",
            "대시보드 라이브 프리뷰에서 직접 위치를 클릭해 좌표를 지정.")
    bullet(doc, "키 입력 모드",
            "좌표 대신 키 한 번 입력(예: F1)으로 동작.")
    bullet(doc, "단계 대기",
            "각 단계 사이 ms 단위 대기 시간. 화면 전환·이펙트 시간 만큼 여유를 줍니다.")

    h3(doc, "시작 지연 (Start Delay)")
    body(
        doc,
        "트리거 발생 후 시퀀스가 곧바로 시작되면 마을 도착 / 안전지대 버프 / 로딩 화면이 "
        "아직 끝나지 않아 클릭이 빈 곳에 떨어질 수 있습니다. 기본값 120초는 그 시간을 기다리기 "
        "위한 안전 마진입니다. 환경에 맞춰 60~180초 사이로 조정하시면 됩니다."
    )

    info_box(doc, "EXAMPLE — 오만의 탑 주문서", [
        "단계 1. 키 입력  ‘주문서 사용 키’ (대기 3000ms)",
        "단계 2. 클릭  ‘마법진 좌표’ (대기 5000ms)",
        "단계 3. 키 입력  ‘어시스트 키’ (대기 2000ms)",
        "단계 4. 키 입력  ‘공격 시작 키’",
    ])


# ───────── 빌드 ─────────
def main():
    doc = Document()
    setup_page(doc)

    # Step 1 — 표지 + 도입부
    make_cover(doc)
    make_intro(doc)

    # Step 2 — 섹션 01 ~ 04
    section_01_compare(doc)
    section_02_install(doc)
    section_03_power(doc)
    section_04_hpmp(doc)

    # Step 3 — 섹션 05 ~ 08
    section_05_slots(doc)
    section_06_pk_potion(doc)
    section_07_pet_whistle(doc)
    section_08_recovery(doc)

    out = Path(r"F:/린w/dist/QuickCast_v1.0.3_네이버포스트.docx")
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        doc.save(str(out))
        print(f"saved: {out}  size={out.stat().st_size}")
    except PermissionError:
        alt = out.with_name(out.stem + "_new.docx")
        doc.save(str(alt))
        print(f"원본이 Word에 열려있어 임시 저장: {alt}")


if __name__ == "__main__":
    main()

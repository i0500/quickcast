"""HP/MP 게이지 자동 탐지 — 리니지W (좌측 최상단 HUD).

화면 프레임에서 빨강(HP)/파랑(MP) **가로 막대**를 색으로 찾아 ROI 사각형을
자동 산출한다. 리니지W는 내 HP/MP 바가 화면 **좌측 최상단(11시 방향, 맨왼쪽
위)** 에 HP(위)·MP(아래) 로 세로로 붙어 있고, 둘 다 같은 x 에서 시작하는
가늘고 긴 가로 막대다(폭 ~150px, 높이 ~9px @ 1280×720). 바로 아래에는
**버프 아이콘 줄(파랑·초록 등 컬러풀)** 이 있다.

핵심 난점 & 대응:
  - MP가 깎여 있으면(예: 32%) 파란 fill 이 짧아(~50px) 그 자체로는 '막대'로
    안 잡히고, 대신 **바로 아래 버프 아이콘들이 가로로 병합돼 더 넓은 파랑**
    으로 잡혀 MP ROI 가 버프 영역으로 매핑되는 오탐이 난다.
  - 그래서 **HP(빨강) 를 먼저** 찾는다(보통 더 길고 안정적). 그 다음 MP 는
    **HP 바로 아래(같은 x, 좁은 세로 밴드)** 에서만 찾는다 → 버프 줄은 위치로
    제외된다.
  - HP/MP 는 같은 길이로 쌓인 막대라, 한쪽이 깎여 짧게 잡혀도 **폭은 둘 중
    더 긴 쪽을 공유**한다(부분 충전 보정). 따라서 MP 가 32% 여도 ROI 는 바
    전체 길이로 셋팅된다.

리니지 클래식(csupporter) 버전과 다른 점:
  - 탐색 영역: 화면 하단이 아니라 **좌측 최상단** 만 스캔.
  - 배치: HP가 MP **왼쪽**이 아니라 MP **위** 에 있다(같은 x, 세로 스택).
  - 색 임계값: core.recognition 의 HP(빨강 채널 ≈210+) · MP(HSV hue 85~135,
    sat·val>70) 판정과 같은 기준을 써서 '자동찾기가 잡는 막대 = 인식이 읽는
    막대' 가 되게 한다.

좌표계: 입력 프레임이 곧 ROI 좌표계다(정규화 1280×720 BGRA 프레임). 반환
``(x, y, w, h)`` 는 그 프레임 픽셀 기준이라 ``settings.hp_cap`` 등에 그대로
넣으면 된다.

정확도 팁: HP/MP가 **가득 찼을 때** 자동 찾기를 하면 막대 전체 길이가 잡혀
가장 정확하다.
"""
from __future__ import annotations

from typing import Optional

import numpy as np

# (x, y, w, h) 또는 None
Rect = Optional[tuple]

# 탐색 영역 — 좌측 최상단(맨왼쪽 위 HUD). 프레임의 좌측 W%·상단 H% 만 스캔.
# HP/MP(y≈24~46)와 그 아래 버프 줄(y≈71~)이 들어오지만, 아래의 HP 우선 +
# MP 밴드 제한 로직이 버프를 위치로 걸러낸다.
_REGION_W_FRAC = 0.45
_REGION_H_FRAC = 0.20


def _color_masks(bgr: np.ndarray):
    """BGR 프레임에서 (빨강=HP, 파랑/시안=MP) 이진 마스크를 만든다.

    recognition._hp_ratio(빨강 채널 210+) / _mp_ratio_via_hsv(hue 85~135,
    sat·val>70) 의 판정 기준과 맞춰, 자동찾기가 잡는 막대가 실제 인식이 읽는
    막대와 일치하도록 한다.
    """
    import cv2
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    hh = hsv[:, :, 0]
    ss = hsv[:, :, 1]
    vv = hsv[:, :, 2]
    # 빨강(HP): hue 0근처/180근처 + 높은 채도·명도. 흰색·노랑·살구색 배제.
    red = ((hh <= 12) | (hh >= 168)) & (ss > 120) & (vv > 120)
    # 파랑/시안(MP): recognition 의 HSV MP 판정과 동일(hue 85~135, sat·val>70).
    blue = (hh >= 85) & (hh <= 135) & (ss > 70) & (vv > 70)
    return (red.astype(np.uint8) * 255), (blue.astype(np.uint8) * 255)


def _bars(mask: np.ndarray, frame_w: int, frame_h: int, *,
          close_w: int = 10, min_w_frac: float = 0.05) -> list:
    """마스크에서 '가로 막대' 후보들을 ``[(x, y, w, h), ...]`` (폭 큰 순)로 반환.

    막대 특징: 가로로 길고(width 큼) 세로로 얇다(height 작음). 막대 위 숫자·
    광택으로 끊긴 픽셀을 가로 커널 close 로 잇는다. ``min_w_frac`` 을 낮추면
    깎여서 짧아진 fill(예: MP 32%)도 후보로 잡힌다 — 대신 위치 제한과 함께
    써서 오탐을 막는다.
    """
    import cv2
    m = mask
    if close_w > 1:
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (close_w, 1))
        m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, kernel)
    n, _labels, stats, _cent = cv2.connectedComponentsWithStats(m, connectivity=8)
    min_w = frame_w * min_w_frac
    out = []
    for i in range(1, n):
        x, y, w, h, _area = stats[i]
        if w < min_w:                     # 너무 짧으면 막대 아님
            continue
        if h < 2 or h > frame_h * 0.08:   # 너무 두껍거나 1px 노이즈면 막대 아님
            continue
        if w / max(1, h) < 3.0:           # 가로:세로 비율 — 막대는 길쭉해야 함
            continue
        out.append((int(x), int(y), int(w), int(h)))
    out.sort(key=lambda r: r[2], reverse=True)   # 폭 큰 순
    return out


def _topmost(bars: list) -> Rect:
    """가장 위쪽(같은 8px 밴드면 더 넓은) 막대 1개. 없으면 None."""
    if not bars:
        return None
    return sorted(bars, key=lambda r: (r[1] // 8, -r[2]))[0]


def _x_overlap(a: tuple, b: tuple) -> int:
    """두 사각형의 x축 겹침 픽셀 수."""
    ax0, ax1 = a[0], a[0] + a[2]
    bx0, bx1 = b[0], b[0] + b[2]
    return max(0, min(ax1, bx1) - max(ax0, bx0))


def locate_hp_mp_bars(bgr: np.ndarray, *,
                      region_w_frac: float = _REGION_W_FRAC,
                      region_h_frac: float = _REGION_H_FRAC) -> dict:
    """프레임 **좌측 최상단**에서 HP(빨강)/MP(파랑) 막대를 찾는다.

    HP(빨강)를 먼저 찾고, MP(파랑)는 HP 바로 아래 좁은 밴드에서만 찾아 그 아래
    버프 아이콘 줄을 위치로 제외한다. 둘은 같은 길이의 세로 스택이라 폭은 더
    긴 쪽을 공유해 부분 충전(짧은 fill)을 보정한다.

    Returns: ``{"hp": (x,y,w,h)|None, "mp": (x,y,w,h)|None}`` — 프레임 픽셀 좌표.
    """
    if bgr is None or getattr(bgr, "size", 0) == 0:
        return {"hp": None, "mp": None}
    if bgr.ndim == 3 and bgr.shape[2] == 4:
        bgr = bgr[:, :, :3]
    fh, fw = bgr.shape[:2]
    rw = max(1, int(fw * region_w_frac))
    rh = max(1, int(fh * region_h_frac))
    # 좌측 최상단 크롭 — 원점이 프레임 원점과 같으므로 좌표 변환이 필요 없다.
    region = np.ascontiguousarray(bgr[:rh, :rw])
    red, blue = _color_masks(region)

    # HP: 빨강은 보통 더 길고 안정적 → 영역 내 가장 위쪽 빨강 막대.
    reds = _bars(red, fw, fh, min_w_frac=0.05)
    hp: Rect = _topmost(reds)

    # MP: 깎였을 수 있어 최소폭을 낮춰 잡되, HP 바로 아래(같은 x)만 인정 →
    # 더 아래의 버프 아이콘 병합 블롭은 위치로 제외된다.
    blues = _bars(blue, fw, fh, min_w_frac=0.025)
    mp: Rect = None
    if hp is not None:
        hx, hy, hw, hh = hp
        gap_max = max(10, 3 * hh)
        cands = [b for b in blues
                 if _x_overlap(b, hp) >= 0.4 * hw and hy < b[1] <= hy + hh + gap_max]
        if cands:
            cands.sort(key=lambda b: (b[1], -b[2]))   # HP에 가장 가까운 아래, 넓은 순
            mp = cands[0]
    if mp is None:
        # 폴백: HP를 못 찾았거나 밴드에 MP가 없을 때 영역 내 가장 위쪽 파랑.
        mp = _topmost(blues)
    if hp is None and reds:
        hp = _topmost(reds)

    # 같은 길이로 쌓인 막대 → x/폭 공유(부분 충전 보정: 더 긴 쪽이 진짜 길이).
    if hp is not None and mp is not None:
        x0 = min(hp[0], mp[0])
        width = max(hp[2], mp[2])
        hp = (x0, hp[1], width, hp[3])
        mp = (x0, mp[1], width, mp[3])

    return {"hp": hp, "mp": mp}


__all__ = ["locate_hp_mp_bars"]


if __name__ == "__main__":   # 스크린샷으로 빠른 검증: python -m quickcast.core.bar_locator <img>
    import sys
    import cv2
    if len(sys.argv) < 2:
        print("usage: python -m quickcast.core.bar_locator <image-path>")
        raise SystemExit(2)
    img = cv2.imread(sys.argv[1])
    if img is None:
        print("이미지 로드 실패:", sys.argv[1])
        raise SystemExit(1)
    res = locate_hp_mp_bars(img)
    print("프레임 크기:", img.shape[1], "x", img.shape[0])
    print("HP 막대:", res["hp"])
    print("MP 막대:", res["mp"])
    for k, rect in res.items():
        if rect:
            x, y, w, h = rect
            color = (0, 0, 255) if k == "hp" else (255, 0, 0)
            cv2.rectangle(img, (x, y), (x + w, y + h), color, 2)
    out = sys.argv[1].rsplit(".", 1)[0] + "_detected.png"
    cv2.imwrite(out, img)
    print("표시 저장:", out)

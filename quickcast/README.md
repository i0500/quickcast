# QuickCast (Python Native Edition)

기존 브라우저 매크로(`linW_js_230128.html`)를 Python 네이티브로 이식.
Chrome 의존성 제거, DXGI 캡처, OpenCV 네이티브 매칭으로 메모리/CPU 사용량을 1/5~1/10로 절감.

## 빠른 시작

```cmd
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python -m quickcast
```

## 모듈 구조

| 모듈 | 책임 |
|------|------|
| `core.capture` | mss DXGI 화면 캡처, ROI 추출 |
| `core.recognition` | HP/MP 비율 계산, PK/물약 매칭 (OpenCV) |
| `core.controller` | 메인 제어 루프, 쓰레드 오케스트레이션 |
| `core.state` | 런타임 상태 (쿨타임, 마스터스위치) |
| `slots` | 일반 슬롯 + PK/물약 특수 슬롯 |
| `input_io.arduino` | pyserial Arduino HID 통신 |
| `notify` | Telegram 알림, 알람 시스템 |
| `ui` | PySide6 메인창, 플로팅 스위치 |
| `config` | Pydantic 설정 모델, JSON 영속화 |

## 빌드

```cmd
build.bat
```

`dist/quickcast.exe` 단일 실행파일 생성.

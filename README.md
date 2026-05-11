# QuickCast

브라우저 기반 스킬 매크로를 Python 네이티브로 재작성한 데스크탑 앱. 원작자 **아누이두** ([mandloh.tistory.com/136](https://mandloh.tistory.com/136))의 브라우저 매크로(`index_20221104.html`)를 기반으로 다음을 개선:

- Chrome 의존성 제거 → 단독 실행
- DXGI 화면 캡처로 CPU/메모리 1/5~1/10 절감
- OpenCV 네이티브 매칭
- PySide6 데스크탑 UI (다크/라이트 테마, 다국어 가능 구조)
- Telegram 알림, 알람 스케줄러, 플로팅 스위치, 사냥터 복귀 매크로 등 기능 확장
- PyInstaller 단일 exe 빌드

## 설치

### 옵션 A — 실행파일만
[Releases](../../releases) 에서 `quickcast.exe` 다운로드 후 실행. Windows 10/11 64bit 전용.

### 옵션 B — 소스에서 실행
```cmd
git clone https://github.com/<your-user>/quickcast.git
cd quickcast
python -m venv .venv
.venv\Scripts\activate
pip install -r quickcast\requirements.txt
python -m quickcast
```

Python 3.11+ 권장.

## 빌드

```cmd
quickcast\build.bat
```

`dist\quickcast.exe` 단일 파일 생성. PyInstaller `--onefile`.

## 사용

1. 게임 창 캡처 등록 (대시보드 → 창 선택)
2. 입력 방식 선택 (Arduino HID 또는 PostMessage)
3. 슬롯/물약/PK 설정
4. 플로팅 스위치 또는 단축키로 ON/OFF

상세 설정은 앱 내 튜토리얼 참고.

## 원작/저작권

- **원작자**: 아누이두 — [mandloh.tistory.com/136](https://mandloh.tistory.com/136)
- 원본은 브라우저 단일 HTML 파일 (`index_20221104.html`, 2022.11.04 버전)
- 본 프로젝트는 원작 동작을 참고하여 Python 네이티브로 재구현 및 기능 확장한 파생작
- 별도 라이선스 미지정 — 사용/배포 전 원작자 의사 확인 권장

## 후원

이 프로젝트가 도움 되셨다면 ☕ [Buy Me a Coffee — snjdevs](https://buymeacoffee.com/snjdevs)

## 면책

- 사용자는 게임사 EULA·이용약관·관련 법령을 준수할 책임이 있습니다
- 본 프로그램 사용으로 인한 계정 제재·법적 분쟁 등은 사용자 본인 책임
- 개발자는 어떠한 손해에 대해서도 책임지지 않습니다

# 고급 정보

README에 담지 않은 설정 파일, 동작 원리, 보안, 개발 정보입니다. 전체 설계는 [SPEC.md](SPEC.md)에 있습니다.

## 설정 파일

`%APPDATA%\ClaudeUsageTray\config.json`에 있습니다. 위젯 오른쪽 클릭 → **설정 파일 열기**로도 열 수 있습니다.

| 키 | 기본값 | 설명 |
|---|---|---|
| `poll_minutes` | `5` | 조회 간격(분), 1–60 |
| `alert_thresholds` | `[80, 90, 95]` | 주간 % 알림 기준, 1–100 |
| `relogin_notice_days` | `3` | 로그인 만료 며칠 전부터 알릴지, 1–14 |
| `popup_seconds` | `8` | 알림 창이 떠 있는 시간(초), 2–60 |
| `start_with_windows` | `true` | Windows 시작 시 실행 |
| `widget_mode` | `"compact"` | 평소 크기: `"compact"`(작은 막대) 또는 `"mini"`(아주 작은 아이콘) |
| `widget_pos` | `null` | 끌어서 옮긴 위젯의 오른쪽 아래 모서리 좌표. `null`이면 기본 위치 |

- **앱을 종료한 뒤 고치고 다시 실행하세요.** 실행 중에 크기, 위치, 시작 시 실행을 바꾸면 파일이 다시 저장되어 그 사이에 고친 내용이 덮어써질 수 있습니다.
- 숫자가 범위를 벗어나면 끝값으로 맞추고, 형식이 틀리면 기본값을 씁니다. `alert_thresholds`에서 1–100 밖의 값은 빠집니다.
- 파일이 깨져서 읽을 수 없으면 기본값으로 동작하고, 그 파일은 덮어쓰지 않습니다.
- 환경변수는 `CLAUDE_CONFIG_DIR`(Claude Code 설정 폴더를 옮긴 경우)만 읽습니다. 넣어야 할 비밀값이 없어서 `.env` 파일은 쓰지 않습니다.

## 동작 원리

1. 5분마다 `GET https://api.anthropic.com/api/oauth/usage`로 사용률을 받아옵니다. Claude Code의 `/usage`와 같은 값이고, 소수점 없는 정수 퍼센트입니다.
   - 인증에는 Claude Code가 저장해 둔 로그인 토큰(`%USERPROFILE%\.claude\.credentials.json`, 또는 `CLAUDE_CONFIG_DIR` 폴더)을 **읽기만** 합니다.
2. 토큰은 약 8시간마다 만료됩니다. 만료된 토큰으로 401 응답을 받으면, Claude Code에 아주 짧은 요청을 한 번 보내 Claude Code가 직접 토큰을 갱신하게 합니다.
   ```
   claude -p "." --model haiku --system-prompt "Reply with one character." --tools "" --strict-mcp-config --setting-sources "" --disable-slash-commands --no-session-persistence --output-format json
   ```
   - 한 번에 Haiku 기준 입력 약 580토큰, 출력 3토큰입니다. 정상일 때는 하루 2~3번입니다.
   - 안전장치:
     - 토큰이 실제로 만료됐을 때만 시도합니다. 유효한 토큰에 대한 401, 403, 로그인 만료 때는 시도하지 않습니다.
     - `ANTHROPIC_API_KEY` 같은 인증 환경변수를 빼고 실행해서 API 키로 과금되지 않습니다.
     - 토큰이 실제로 바뀌었을 때만 성공으로 칩니다.
     - 시도 간격은 최소 30분이고, 실패가 이어지면 최대 8시간까지 늘립니다. 문제가 계속돼도 하루 최대 7~8번입니다.
3. 로그인 자체는 약 4주마다 만료됩니다. 갱신으로는 연장되지 않아서 `claude auth login`으로 다시 로그인해야 합니다. 앱이 3일 전부터 알려줍니다.
4. 소진 예측(그래프 점선, 판정, 색, 알림)은 이번 주 평균 속도, 즉 이번 주 누적 사용률 ÷ 주 시작 후 지난 시간으로 계산합니다.
   - 최근 24시간 속도(선형 회귀)는 참고용으로 판정 아래 줄에만 표시합니다.
   - 리셋 직후 12시간은 데이터가 적어 "예측 준비 중"으로 표시합니다.
   - 데이터가 오래됐으면 마지막으로 측정한 시각을 기준으로 계산합니다.
5. (선택) API를 읽지 못했을 때 `usage-snapshot.json`이 있으면 그 값을 씁니다. 직접 만든 Claude Code 상태줄 스크립트가 이 파일을 남기는 경우에만 해당합니다.
6. (선택) API 크레딧 잔액. `%USERPROFILE%\.claude\claude-mode\usage.jsonl`이 있으면 큰 화면에 잔액 줄이 생깁니다. 없으면 아무것도 표시하지 않습니다.
   - `usage.jsonl`: 한 줄에 `{"at": 초, "cost": 달러, "model": ...}` — 작업마다 쓴 비용
   - 잔액 = 월 지급액($100) − 마지막 충전일(매달 27일) 0시 이후 비용 합. 남은 크레딧은 이월되지 않습니다.
   - (보정용) `credit.json`: `{"monthly": 100, "grant_day": 27, "balance": 98.2, "as_of": 초}` — 콘솔에서 직접 본 잔액과 그 시각. 있으면 다음 충전일 전까지 `balance` − `as_of` 이후 비용 합으로 계산하고, 지급액·충전일도 이 파일 값을 씁니다.
   - 콘솔 잔액을 API로 읽을 방법이 없어서 추정치입니다. 같은 API 키를 기록 밖에서 쓰면 그 비용은 빠지니, 그때만 `credit.json`으로 맞추세요.

## 보안과 개인정보

- 로그인 토큰은 `api.anthropic.com`에만 보냅니다. 리다이렉트는 따라가지 않습니다. 토큰을 화면, 로그, 파일 어디에도 남기지 않고, 이것을 테스트로 검증합니다.
- `claude` 명령은 PATH에서만 찾고 현재 폴더는 보지 않습니다. 받은 폴더에 누군가 `claude.cmd`를 심어 둬도 실행되지 않습니다.
- Claude Code의 로그인 파일은 수정하지 않습니다.
- 앱이 쓰는 곳은 두 군데뿐입니다.
  - `%APPDATA%\ClaudeUsageTray\`: `config.json`, `history.jsonl`(이번 주와 지난주 사용률), `state.json`(알림 표시 여부), `app.log`(토큰 없음, 최대 약 400KB)
  - 레지스트리 `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`의 `ClaudeUsageTray` 값: 시작 시 실행을 켰을 때만
- 텔레메트리는 없습니다.

## 자원 사용량 (Windows 11, v1.0.0, 대기 상태)

| 항목 | 값 |
|---|---|
| CPU | 약 0.2% |
| 메모리 | 개인 메모리 약 27MB (작업 집합 약 64MB, 단일 `.exe` 압축 해제용 보조 프로세스 포함) |
| 실행 파일 | 약 18MB |
| 네트워크 | 5분마다 요청 1회 (수 KB) |

## 소스에서 실행·빌드

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements-dev.txt
.venv\Scripts\python -m pytest            # 테스트 (실제 창이 화면 구석에 잠깐 뜹니다)
.venv\Scripts\python main.py              # 실행
.venv\Scripts\python tools\build.py       # dist\ClaudeUsageTray.exe와 .sha256 생성
```

- 소스로 실행할 때는 시작 프로그램에 자동 등록하지 않습니다.
- Microsoft Store판 Python에서는 데이터가 `%LOCALAPPDATA%\Packages\PythonSoftwareFoundation.Python...\LocalCache\Roaming\ClaudeUsageTray`에 저장됩니다. Store 앱의 폴더 가상화 때문이고, 빌드한 `.exe`에는 해당하지 않습니다.
- 폰트를 다시 줄이려면 `tools/subset_fonts.py <galmuri dist 폴더>`를 실행합니다.

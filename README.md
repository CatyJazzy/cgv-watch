# cgv-watch

CGV 용산아이파크몰 **스파이더맨-브랜드 뉴 데이** 예매 오픈 / 빈좌석 감시기.
GitHub Actions가 **60초 간격**으로 조회하고, 변화가 있을 때만 Slack으로 알린다.

**조회 전용이다.** 예매·결제·좌석 선점은 하지 않는다.

## 알림 조건

1. 🎬 **예매 오픈** — 대상 날짜에 스파이더맨 회차가 처음 생기는 순간
2. 💺 **빈좌석 발생** — 이미 열린 회차가 기준 좌석 수 미만이었다가 넘어서는 순간 (취소표)

상태를 Actions 캐시에 저장해 **변화가 있을 때만** 알린다. 같은 내용으로 반복 알림이 오지 않는다.

## 설정

`Settings → Secrets and variables → Actions` 에 두 개를 등록한다.

| Secret | 값 |
| --- | --- |
| `SLACK_BOT_TOKEN` | Slack 앱의 Bot User OAuth Token (`xoxb-…`) |
| `SLACK_CHANNEL` | 알림을 받을 채널 ID (`C…`) |

Slack 앱에는 봇 토큰 스코프 `chat:write` 가 필요하고, 봇이 해당 채널에 초대돼 있어야 한다
(`/invite @봇이름`).

## 감시 대상 바꾸기

`.github/workflows/watch.yml` 의 실행 인자를 고친다.

```yaml
--theater 0013            # CGV theaterCode (0013 = 용산아이파크몰)
--dates 20260809 20260816 # 감시할 날짜들
--movie 스파이더맨          # 영화명 부분일치
--min-seats 2             # 이 좌석 수 이상이면 알림
--after 14:00             # (선택) 이 시각 이후 회차만
--before 20:00            # (선택) 이 시각 이전 회차만
```

## 로컬에서 쓰기

```bash
python3 cgv_spiderman_watch.py --once      # 현재 상태만 출력
python3 cgv_spiderman_watch.py --single    # 1회 감시 (변화 시 알림)
python3 cgv_spiderman_watch.py             # 상주 감시 (기본 5분 간격)
```

## 주의

- Actions cron은 **5~15분 지연될 수 있다.** 정시 오픈런에는 부적합하다.
- 60일간 리포지토리에 활동이 없으면 GitHub가 스케줄을 자동 비활성화한다.
- 데이터 출처는 `daiso-mcp` 의 공개 REST (`mcp.aka.page`). 인증 키가 필요 없고,
  이 리포지토리는 upstream 코드를 포함하지 않는다.

## 폴링 강도 자동 조절

2026-07-27 실측 기준 CGV는 **약 8일 앞까지** 예매를 연다. 즉 오픈은 `대상일 - 8일`
무렵에 일어난다. 워크플로가 매 실행마다 대상일까지 남은 일수를 계산해 모드를 정한다.

| 대상일까지 | 모드 | 폴링 |
| --- | --- | --- |
| 5~12일 | 🔥 HOT | 60초 간격 (오픈 순간 조준) |
| 그 외 | COLD | 5분 간격 (취소표만 감시) |

`Actions → CGV 스파이더맨 감시 → Run workflow` 에서 `force_hot` 을 켜면
쿨다운을 무시하고 강제로 60초 간격으로 돌릴 수 있다.

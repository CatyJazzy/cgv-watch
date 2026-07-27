#!/usr/bin/env python3
"""CGV 용산아이파크몰 스파이더맨 예매 오픈 / 빈좌석 감시기 (조회 전용).

- 예매/결제/좌석선점은 하지 않는다. 변화가 생기면 알림만 보낸다.
- 데이터 출처: daiso-mcp 공개 REST (https://mcp.aka.page/api/cgv/timetable)

사용 예:
    # 현재 상태만 한 번 출력
    python3 cgv_spiderman_watch.py --once

    # 8/9, 8/16 감시 (기본값). 5분 간격, 2석 이상이면 알림
    python3 cgv_spiderman_watch.py

    # Slack 알림까지
    SLACK_WEBHOOK_URL='https://hooks.slack.com/services/...' python3 cgv_spiderman_watch.py
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

API = "https://mcp.aka.page/api/cgv/timetable"
KST = timezone(timedelta(hours=9))

DEFAULT_THEATER = "0013"  # CGV 용산아이파크몰
DEFAULT_DATES = ["20260809", "20260816"]
DEFAULT_MOVIE = "스파이더맨"
WEEKDAYS = "월화수목금토일"


# --------------------------------------------------------------------------- fetch


def fetch_timetable(theater: str, play_date: str, timeout: int = 25) -> list[dict]:
    """해당 날짜의 전체 시간표를 반환. 미오픈이면 빈 리스트."""
    qs = urllib.parse.urlencode(
        {"theaterCode": theater, "playDate": play_date, "limit": 300}
    )
    req = urllib.request.Request(
        f"{API}?{qs}",
        headers={"User-Agent": "cgv-seat-watch/1.0", "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        payload = json.load(resp)
    if not payload.get("success"):
        raise RuntimeError(f"API success=false: {str(payload)[:200]}")
    return payload.get("data", {}).get("timetable") or []


def select(rows: list[dict], movie: str, after: str | None, before: str | None) -> list[dict]:
    """영화명 부분일치 + 상영 시작시각 범위로 회차를 추린다."""
    out = []
    for r in rows:
        if movie not in r.get("movieName", ""):
            continue
        start = r.get("startTime", "")
        if after and start < after:
            continue
        if before and start > before:
            continue
        out.append(r)
    return sorted(out, key=lambda r: r.get("startTime", ""))


def key_of(row: dict) -> str:
    """회차 식별자. scheduleId는 상영관 단위라 회차마다 고유하지 않아 직접 조합한다."""
    return "|".join(
        [
            row.get("playDate", ""),
            row.get("startTime", ""),
            row.get("movieName", ""),
            str(row.get("totalSeats", "")),
        ]
    )


def label(row: dict) -> str:
    d = row.get("playDate", "")
    try:
        dow = WEEKDAYS[datetime.strptime(d, "%Y%m%d").weekday()]
        d = f"{d[4:6]}/{d[6:8]}({dow})"
    except ValueError:
        pass
    return (
        f"{d} {row.get('startTime','')}~{row.get('endTime','')} "
        f"{row.get('movieName','')} — 잔여 {row.get('remainingSeats')}/{row.get('totalSeats')}석"
    )


# --------------------------------------------------------------------------- notify


def notify_macos(title: str, body: str) -> None:
    if sys.platform != "darwin":  # CI(리눅스)에서는 조용히 건너뛴다
        return
    script = (
        f'display notification {json.dumps(body)} '
        f'with title {json.dumps(title)} sound name "Glass"'
    )
    try:
        subprocess.run(
            ["osascript", "-e", script], check=False, capture_output=True, timeout=10
        )
    except Exception as exc:  # noqa: BLE001 - 알림 실패가 감시를 죽이면 안 된다
        print(f"[warn] macOS 알림 실패: {exc}", file=sys.stderr)


def slack_text(title: str, body: str, url: str) -> str:
    return f"*{title}*\n{body}\n<{url}|CGV에서 예매하기>"


def notify_slack_webhook(webhook: str, title: str, body: str, url: str) -> None:
    """Incoming Webhook 방식 — 채널이 URL에 고정돼 있다."""
    payload = {"text": slack_text(title, body, url), "unfurl_links": False}
    req = urllib.request.Request(
        webhook,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            resp.read()
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] Slack webhook 알림 실패: {exc}", file=sys.stderr)


def notify_slack_bot(token: str, channel: str, title: str, body: str, url: str) -> None:
    """봇 토큰(xoxb-) + chat.postMessage 방식. 봇 토큰 스코프 chat:write 필요."""
    # 채널 ID(C…/G…/D…)면 그대로, 채널명이면 # 를 붙여준다.
    target = channel if channel[:1] in "CGD#" else f"#{channel}"
    payload = {
        "channel": target,
        "text": slack_text(title, body, url),
        "unfurl_links": False,
    }
    req = urllib.request.Request(
        "https://slack.com/api/chat.postMessage",
        data=json.dumps(payload).encode(),
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "Authorization": f"Bearer {token}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.load(resp)
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] Slack 전송 실패: {exc}", file=sys.stderr)
        return
    if not result.get("ok"):
        err = result.get("error", "unknown")
        hint = {
            "not_in_channel": f"봇을 채널에 초대하세요 — 슬랙에서 /invite @앱이름 ({channel})",
            "channel_not_found": f"채널 '{channel}' 을 찾을 수 없습니다. #없이 이름만 쓰거나 채널 ID(C…)를 쓰세요.",
            "invalid_auth": "토큰이 잘못됐습니다. xoxb- 로 시작하는 Bot User OAuth Token 인지 확인하세요.",
            "missing_scope": "봇 토큰 스코프에 chat:write 를 추가하고 앱을 재설치하세요.",
        }.get(err, "")
        print(f"[warn] Slack 거부: {err} {hint}", file=sys.stderr)


def alert(cfg, title: str, lines: list[str]) -> None:
    body = "\n".join(lines)
    stamp = datetime.now(KST).strftime("%H:%M:%S")
    print(f"\n🔔 [{stamp}] {title}\n{body}\n", flush=True)
    if cfg.macos:
        notify_macos(title, body if len(body) < 400 else body[:400] + "…")
    if cfg.slack_token:
        notify_slack_bot(cfg.slack_token, cfg.slack_channel, title, body, cfg.booking_url)
    if cfg.slack_webhook:
        notify_slack_webhook(cfg.slack_webhook, title, body, cfg.booking_url)


# --------------------------------------------------------------------------- state


def load_state(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(path: str, state: dict) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False)
    os.replace(tmp, path)


# --------------------------------------------------------------------------- core


def check_once(cfg, state: dict, first_run: bool) -> None:
    """모든 대상 날짜를 한 번 조회하고, 상태 변화가 있으면 알림."""
    for play_date in cfg.dates:
        try:
            rows = fetch_timetable(cfg.theater, play_date)
        except (urllib.error.URLError, urllib.error.HTTPError, RuntimeError, TimeoutError) as exc:
            print(f"[{play_date}] 조회 실패 (다음 주기에 재시도): {exc}", file=sys.stderr)
            continue

        hits = select(rows, cfg.movie, cfg.after, cfg.before)
        prev = state.get(play_date, {})
        prev_open = bool(prev.get("open"))
        prev_seats: dict = prev.get("seats", {})

        stamp = datetime.now(KST).strftime("%m-%d %H:%M:%S")
        available = [r for r in hits if (r.get("remainingSeats") or 0) >= cfg.min_seats]
        print(
            f"[{stamp}] {play_date}: 전체 {len(rows)}회차 / "
            f"{cfg.movie} {len(hits)}회차 / {cfg.min_seats}석↑ {len(available)}회차",
            flush=True,
        )

        # 1) 예매 오픈 감지 — 스파이더맨 회차가 0개였다가 생겼을 때
        if hits and not prev_open and not first_run:
            alert(
                cfg,
                f"🎬 예매 오픈! {play_date} {cfg.movie}",
                [f"{len(hits)}개 회차가 열렸습니다."] + [label(r) for r in hits[:8]],
            )

        # 2) 빈좌석 감지 — 직전에 기준 미달이던 회차가 기준을 넘겼을 때
        elif not first_run:
            newly = [
                r
                for r in available
                if prev_seats.get(key_of(r), -1) < cfg.min_seats
            ]
            if newly:
                alert(
                    cfg,
                    f"💺 빈좌석 발생! {play_date} {cfg.movie}",
                    [label(r) for r in newly[:8]],
                )

        state[play_date] = {
            "open": bool(hits),
            "seats": {key_of(r): (r.get("remainingSeats") or 0) for r in hits},
            "checked_at": datetime.now(KST).isoformat(timespec="seconds"),
        }

    save_state(cfg.state_file, state)


def print_status(cfg) -> None:
    for play_date in cfg.dates:
        try:
            rows = fetch_timetable(cfg.theater, play_date)
        except Exception as exc:  # noqa: BLE001
            print(f"{play_date}: 조회 실패 — {exc}")
            continue
        hits = select(rows, cfg.movie, cfg.after, cfg.before)
        try:
            dow = WEEKDAYS[datetime.strptime(play_date, "%Y%m%d").weekday()]
        except ValueError:
            dow = "?"
        print(f"\n=== {play_date}({dow}) — 전체 {len(rows)}회차 ===")
        if not rows:
            print("  아직 시간표 자체가 안 열림 (예매 미오픈)")
            continue
        if not hits:
            print(f"  '{cfg.movie}' 회차 없음 — 이 날짜는 아직 미편성/미오픈")
            continue
        for r in hits:
            mark = "✅" if (r.get("remainingSeats") or 0) >= cfg.min_seats else "  "
            print(f"  {mark} {label(r)}")


def main() -> int:
    p = argparse.ArgumentParser(description="CGV 스파이더맨 예매오픈/빈좌석 감시 (조회 전용)")
    p.add_argument("--theater", default=DEFAULT_THEATER, help="CGV theaterCode (기본 0013=용산아이파크몰)")
    p.add_argument("--dates", nargs="+", default=DEFAULT_DATES, help="YYYYMMDD 목록")
    p.add_argument("--movie", default=DEFAULT_MOVIE, help="영화명 부분일치 키워드")
    p.add_argument("--min-seats", type=int, default=2, help="이 좌석 수 이상이면 알림 (기본 2)")
    p.add_argument("--after", default=None, help="이 시각 이후 회차만 (HH:MM)")
    p.add_argument("--before", default=None, help="이 시각 이전 회차만 (HH:MM)")
    p.add_argument("--interval", type=int, default=300, help="폴링 간격(초), 최소 60 (기본 300)")
    p.add_argument("--once", action="store_true", help="현재 상태만 출력하고 종료")
    p.add_argument(
        "--single",
        action="store_true",
        help="감시 1회차만 수행하고 종료 (cron/CI 용). 변화가 있으면 알림을 보낸다.",
    )
    p.add_argument("--no-macos", dest="macos", action="store_false", help="macOS 알림 끄기")
    p.add_argument(
        "--slack-token",
        default=os.environ.get("SLACK_BOT_TOKEN"),
        help="Slack Bot User OAuth Token (xoxb-…). env SLACK_BOT_TOKEN 로도 가능",
    )
    p.add_argument(
        "--slack-channel",
        default=os.environ.get("SLACK_CHANNEL", "daily-notice"),
        help="봇 토큰 방식일 때 보낼 채널명 또는 채널 ID (기본 daily-notice)",
    )
    p.add_argument(
        "--slack-webhook",
        default=os.environ.get("SLACK_WEBHOOK_URL"),
        help="Incoming Webhook URL 방식 (봇 토큰 대신 쓸 때). env SLACK_WEBHOOK_URL",
    )
    p.add_argument("--test-notify", action="store_true", help="알림 채널만 즉시 테스트하고 종료")
    p.add_argument(
        "--state-file",
        default=os.path.expanduser("~/.cgv_seat_watch_state.json"),
        help="이전 조회 결과 저장 위치",
    )
    cfg = p.parse_args()

    cfg.interval = max(60, cfg.interval)  # 서버 예의: 1분 미만 폴링 금지
    cfg.booking_url = "https://www.cgv.co.kr/ticket/"

    if cfg.slack_token and not cfg.slack_token.startswith("xoxb-"):
        print(
            "[warn] --slack-token 이 xoxb- 로 시작하지 않습니다. "
            "Slack 앱 > OAuth & Permissions > 'Bot User OAuth Token' 을 쓰세요.",
            file=sys.stderr,
        )

    if cfg.test_notify:
        alert(cfg, "🔔 CGV 감시기 알림 테스트", ["이 메시지가 보이면 알림 설정 완료입니다."])
        return 0

    if cfg.once:
        print_status(cfg)
        return 0

    if cfg.single:
        state = load_state(cfg.state_file)
        # 상태 파일이 아예 없는 최초 실행에서만 알림을 건너뛴다 (기준선 저장).
        check_once(cfg, state, first_run=not state)
        return 0

    if cfg.slack_token:
        ch = cfg.slack_channel
        slack_state = "봇토큰 → " + (ch if ch[:1] in "CGD" else "#" + ch.lstrip("#"))
    elif cfg.slack_webhook:
        slack_state = "webhook"
    else:
        slack_state = "off"

    print(
        f"감시 시작 — {cfg.theater} / '{cfg.movie}' / {', '.join(cfg.dates)} / "
        f"{cfg.min_seats}석↑ / {cfg.interval}초 간격",
        flush=True,
    )
    print(
        f"알림: macOS={'on' if cfg.macos else 'off'}, Slack={slack_state}"
        "  (Ctrl+C 로 중지)\n",
        flush=True,
    )

    state = load_state(cfg.state_file)
    first_run = not state  # 첫 실행에서 과거 상태 없이 알림 폭탄 내지 않기
    try:
        while True:
            check_once(cfg, state, first_run)
            first_run = False
            time.sleep(cfg.interval)
    except KeyboardInterrupt:
        print("\n감시 중지.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

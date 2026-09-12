"""Watch ingresso.com for sessions of a given movie at a given theater.

Walks forward day by day from today until the API returns 204 (no sessions
published for that date). If the movie shows up on any of those days, sends
one e-mail listing every session with its checkout URL.
"""

import http.client
import json
import os
import smtplib
import ssl
from datetime import date, datetime, timedelta
from email.message import EmailMessage
from zoneinfo import ZoneInfo

API_HOST = "api-content.ingresso.com"
CITY_ID = "1"
THEATER_ID = "996"
MOVIE_ID = "31537"
MOVIE_LABEL = "Duna - Parte 3"

TZ = ZoneInfo("America/Sao_Paulo")
MAX_DAYS = 12 * 30     # hard stop, so a broken API cannot loop forever
STOP_AFTER_EMPTY = 1   # consecutive 204s before giving up


def env_flag(name, default):
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


def env_date(name):
    raw = os.environ.get(name, "").strip()
    if not raw:
        return None
    return date.fromisoformat(raw)  # formato YYYY-MM-DD


# Repository variables no GitHub, nao secrets.
ENABLED = env_flag("ENABLED", True)
START_DATE = env_date("START_DATE")  # vazio = hoje em TZ

HEADERS = {
    "accept": "application/json, text/plain, */*",
    "accept-language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
    "dnt": "1",
    "origin": "https://www.ingresso.com",
    "priority": "u=1, i",
    "sec-ch-ua": '"Chromium";v="152", "Not?A_Brand";v="24", "Google Chrome";v="152"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-site",
    "user-agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36"
    ),
}


def fetch_day(conn, day):
    """Return the parsed payload for one date, or None when the API sends 204."""
    path = (
        f"/v0/sessions/city/{CITY_ID}/theater/{THEATER_ID}"
        f"/partnership/home/groupBy/sessionType?date={day.isoformat()}"
    )
    conn.request("GET", path, headers=HEADERS)
    res = conn.getresponse()
    body = res.read()  # always drain, so the connection stays reusable

    if res.status == 204:
        return None
    if res.status != 200:
        raise RuntimeError(f"{day.isoformat()}: HTTP {res.status}")
    if not body.strip():
        return None
    return json.loads(body.decode("utf-8"))


def extract_sessions(payload):
    """Pull (label, [(time, url), ...]) for our movie out of one day's payload."""
    result = []
    for day in payload or []:
        found = {}
        for movie in day.get("movies") or []:
            if movie.get("id") != MOVIE_ID:
                continue
            for group in movie.get("sessionTypes") or []:
                for session in group.get("sessions") or []:
                    url = session.get("siteURL")
                    if not url:
                        continue
                    # keyed by session id, so a session listed under more
                    # than one type group is not duplicated
                    found[session.get("id")] = (session.get("time") or "", url)
        if found:
            label = day.get("dateFormatted") or day.get("date") or "?"
            result.append((label, sorted(found.values())))
    return result


def collect():
    conn = http.client.HTTPSConnection(API_HOST, timeout=20)
    days = []
    empty_streak = 0
    current = START_DATE or datetime.now(TZ).date()

    try:
        for _ in range(MAX_DAYS):
            payload = fetch_day(conn, current)
            if payload is None:
                empty_streak += 1
                if empty_streak >= STOP_AFTER_EMPTY:
                    break
            else:
                empty_streak = 0
                days.extend(extract_sessions(payload))
            current += timedelta(days=1)
    finally:
        conn.close()

    return days


def build_body(days):
    blocks = []
    for label, sessions in days:
        lines = [f"Dia {label}"]
        lines.extend(f"{time} - {url}" for time, url in sessions)
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def send_email(body):
    msg = EmailMessage()
    msg["Subject"] = f"{MOVIE_LABEL}: sessoes disponiveis"
    msg["From"] = os.environ["SMTP_USER"]
    msg["To"] = os.environ["MAIL_TO"]
    msg.set_content(body)

    with smtplib.SMTP("smtp.gmail.com", 587, timeout=30) as smtp:
        smtp.starttls(context=ssl.create_default_context())
        smtp.login(os.environ["SMTP_USER"], os.environ["SMTP_PASS"])
        smtp.send_message(msg)


def main():
    if not ENABLED:
        print("ENABLED=false, nada a fazer.")
        return

    days = collect()
    if not days:
        print("Nenhuma sessao encontrada.")
        return

    body = build_body(days)
    print(body)
    send_email(body)
    print("E-mail enviado.")


if __name__ == "__main__":
    main()

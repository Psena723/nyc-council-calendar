"""Builds hearings.ics from the NYC Council Legistar calendar page."""
import re
import sys
import hashlib
import datetime as dt
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

URL = "https://legistar.council.nyc.gov/Calendar.aspx"
# Leave this list empty to track every committee and meeting.
# To track only some, list exact names, e.g. "Committee on Land Use",
COMMITTEES = []
DEFAULT_HOURS = 1  # the site lists no end time, so this is a placeholder
NY = ZoneInfo("America/New_York")

DATE_RE = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b")
TIME_RE = re.compile(r"\b(\d{1,2}):(\d{2})\s*([AP]M)\b", re.I)


def fetch_rows():
    resp = requests.get(
        URL, timeout=60, headers={"User-Agent": "Mozilla/5.0 (calendar-sync)"}
    )
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    rows = []
    for tr in soup.find_all("tr"):
        link = tr.find("a", href=re.compile(r"MeetingDetail\.aspx", re.I))
        if not link:
            continue
        cells = [c.get_text(" ", strip=True) for c in tr.find_all("td")]
        rows.append((tr, link, cells))
    return rows


def parse(rows):
    wanted = {c.lower() for c in COMMITTEES}
    events = []
    for tr, link, cells in rows:
        text = " ".join(cells)
        name = cells[0].strip() if cells else ""
        if not name or (wanted and name.lower() not in wanted):
            continue
        d = DATE_RE.search(text)
        t = TIME_RE.search(text)
        if not d or not t:
            continue
        month, day, year = (int(x) for x in d.groups())
        hour, minute = int(t.group(1)), int(t.group(2))
        ampm = t.group(3).upper()
        if ampm == "PM" and hour != 12:
            hour += 12
        if ampm == "AM" and hour == 12:
            hour = 0
        start = dt.datetime(year, month, day, hour, minute, tzinfo=NY)

        location = ""
        for i, c in enumerate(cells):
            if TIME_RE.fullmatch(c.strip()) and i + 1 < len(cells):
                location = cells[i + 1].strip()
                break

        status = ""
        low = text.lower()
        if "cancel" in low:
            status = "CANCELLED"
        elif "defer" in low:
            status = "DEFERRED"

        detail_url = urljoin(URL, link["href"])
        m = re.search(r"[?&]ID=(\d+)", detail_url)
        uid_core = m.group(1) if m else hashlib.md5(
            f"{name}{start.isoformat()}".encode()
        ).hexdigest()

        events.append(
            {
                "uid": f"{uid_core}@nyc-council-calendar",
                "title": f"NYC Council - {name}" + (f" [{status}]" if status else ""),
                "start": start,
                "end": start + dt.timedelta(hours=DEFAULT_HOURS),
                "location": location,
                "url": detail_url,
            }
        )
    return events


def esc(s):
    return (
        s.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\n", "\\n")
    )


def fold(line):
    out, line = [], line
    while len(line.encode("utf-8")) > 74:
        cut = 74
        while len(line[:cut].encode("utf-8")) > 74:
            cut -= 1
        out.append(line[:cut])
        line = " " + line[cut:]
    out.append(line)
    return "\r\n".join(out)


def utc(d):
    return d.astimezone(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def build_ics(events):
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//nyc-council-calendar//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:NYC Council Hearings",
        "X-WR-TIMEZONE:America/New_York",
        "REFRESH-INTERVAL;VALUE=DURATION:PT4H",
        "X-PUBLISHED-TTL:PT4H",
    ]
    for e in sorted(events, key=lambda x: x["start"]):
        desc = f"Details and agenda: {e['url']}\nEnd time is a placeholder."
        lines += [
            "BEGIN:VEVENT",
            f"UID:{e['uid']}",
            f"DTSTAMP:{utc(e['start'])}",  # stable, so the file only changes when hearings change
            f"DTSTART:{utc(e['start'])}",
            f"DTEND:{utc(e['end'])}",
            f"SUMMARY:{esc(e['title'])}",
            f"LOCATION:{esc(e['location'])}",
            f"DESCRIPTION:{esc(desc)}",
            f"URL:{e['url']}",
            "BEGIN:VALARM",
            "ACTION:DISPLAY",
            "DESCRIPTION:Hearing tomorrow",
            "TRIGGER:-P1D",
            "END:VALARM",
            "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")
    return "\r\n".join(fold(l) for l in lines) + "\r\n"


def main():
    rows = fetch_rows()
    if not rows:
        # The page layout probably changed. Fail loudly and leave the old file alone.
        print("ERROR: no meeting rows found on the page.", file=sys.stderr)
        sys.exit(1)
    events = parse(rows)
    print(f"Found {len(rows)} meetings, {len(events)} matching.")
    with open("hearings.ics", "w", newline="") as f:
        f.write(build_ics(events))


if __name__ == "__main__":
    main()

"""Forex Factory high-impact USD event reminders from its public weekly export."""
import datetime as dt
import hashlib
import html
import json
import urllib.request

from bot import UTC, timestamp

CALENDAR_URL = 'https://nfs.faireconomy.media/ff_calendar_thisweek.json'


def download_calendar():
    request = urllib.request.Request(CALENDAR_URL, headers={'User-Agent': 'PersonalGoldNewsMonitor/1.0'})
    with urllib.request.urlopen(request, timeout=15) as response:
        if response.url.split('?')[0] != CALENDAR_URL:
            raise ValueError('Unexpected calendar redirect')
        data = response.read(2_000_001)
    if len(data) > 2_000_000:
        raise ValueError('Calendar too large')
    rows = json.loads(data)
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError('Unexpected calendar format')
    return rows


def cached_calendar(db, now, downloader=download_calendar):
    db.execute('CREATE TABLE IF NOT EXISTS calendar_cache (key INTEGER PRIMARY KEY, attempted REAL, fetched REAL, payload TEXT)')
    cached = db.execute('SELECT attempted, fetched, payload FROM calendar_cache WHERE key=1').fetchone()
    current = now.timestamp()
    # Fetch at most once an hour, including after failure; preserve cache between jobs.
    if cached and current - cached[0] < 3600:
        if cached[1] and current - cached[1] < 86400:
            return json.loads(cached[2])
        raise ValueError('Calendar unavailable during cooldown')
    db.execute('INSERT OR REPLACE INTO calendar_cache VALUES (1, ?, ?, ?)',
               (current, cached[1] if cached else 0, cached[2] if cached else '[]'))
    db.commit()
    try:
        rows = downloader()
        db.execute('UPDATE calendar_cache SET fetched=?, payload=? WHERE key=1',
                   (current, json.dumps(rows, ensure_ascii=False)))
        db.commit()
        return rows
    except (OSError, ValueError):
        if cached and cached[1] and current - cached[1] < 86400:
            return json.loads(cached[2])
        raise


def format_event(row, event_time, now):
    minutes = max(1, int((event_time - now).total_seconds() / 60))
    forecast = html.escape(str(row.get('forecast') or 'غير متاح'))
    previous = html.escape(str(row.get('previous') or 'غير متاح'))
    return ('📅 <b>تذكير بموعد خبر أمريكي عالي التأثير</b>\n\n'
            f"الحدث بلغة المصدر: {html.escape(str(row['title'])[:400])}\n"
            'العملة: USD — التصنيف في تقويم Forex Factory: تأثير مرتفع\n'
            f"الموعد: {event_time.strftime('%Y-%m-%d %H:%M UTC')}\n"
            f'باقٍ نحو {minutes} دقيقة عند الرصد\n'
            f'التوقع: {forecast}\nالسابق: {previous}\n\n'
            'قد يؤثر الحدث في توقعات الفائدة والدولار والذهب. هذا تذكير بالموعد؛ '
            'لا يتضمن النتيجة الفعلية ولا يحدد اتجاه حركة السعر.\n'
            '<a href="https://www.forexfactory.com/calendar">التحقق من الموعد في التقويم</a>')


def check_calendar(db, send, now=None, downloader=download_calendar):
    now = now or dt.datetime.now(UTC)
    rows = cached_calendar(db, now, downloader)
    db.execute('CREATE TABLE IF NOT EXISTS calendar_sent (id TEXT PRIMARY KEY)')
    sent = 0
    for row in rows:
        if str(row.get('country', '')).upper() != 'USD' or str(row.get('impact', '')).lower() != 'high':
            continue
        date = timestamp(row.get('date', ''))
        if not date or not row.get('title') or not 0 < (date - now).total_seconds() <= 1800:
            continue
        key = hashlib.sha256(json.dumps([row['title'], 'USD', date.isoformat()]).encode()).hexdigest()
        if db.execute('SELECT 1 FROM calendar_sent WHERE id=?', (key,)).fetchone():
            continue
        send(format_event(row, date, now))
        db.execute('INSERT INTO calendar_sent VALUES (?)', (key,))
        db.commit()
        sent += 1
    return sent

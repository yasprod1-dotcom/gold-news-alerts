"""Forex Factory high-impact USD event reminders from its public weekly export."""
import datetime as dt
import hashlib
import html
import json
import re
import urllib.request

from bot import UTC, timestamp
from news_policy import format_time

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


def event_name(title):
    title = re.sub(r'^us\s+', '', str(title).strip(), flags=re.I)
    patterns = [
        (r'^(?:core )?cpi(?: (?:m/m|y/y))?$', 'التضخم CPI'),
        (r'^(?:core )?pce price index(?: (?:m/m|y/y))?$', 'التضخم المفضل للفيدرالي PCE'),
        (r'^(?:core )?ppi(?: (?:m/m|y/y))?$', 'أسعار المنتجين PPI'),
        (r'^non[- ]?farm (?:employment change|payrolls)$', 'الوظائف غير الزراعية NFP'),
        (r'^unemployment rate$', 'معدل البطالة'),
        (r'^average hourly earnings(?: (?:m/m|y/y))?$', 'متوسط الأجور'),
        (r'^(?:federal funds rate|fomc statement|fomc press conference)$', 'قرار أو مؤتمر الفيدرالي'),
    ]
    for pattern, label in patterns:
        if re.search(pattern, title, re.I):
            return label
    return None


def format_event(rows, event_time, now):
    if isinstance(rows, dict):
        rows = [rows]
    minutes = max(1, int((event_time - now).total_seconds() / 60))
    details = []
    for row in rows[:6]:
        forecast = html.escape(str(row.get('forecast') or 'غير متاح'))
        previous = html.escape(str(row.get('previous') or 'غير متاح'))
        details.append(f"• {html.escape(event_name(row['title']) or row['title'])} ({html.escape(row['title'])})\n"
                       f'المتوقع: {forecast} | السابق: {previous}')
    return ('📅 <b>XAU/USD | موعد اقتصادي حساس</b>\n'
            f"الموعد: {format_time(event_time)} — بعد نحو {minutes} دقيقة\n"
            'USD | تأثير مرتفع حسب Forex Factory\n\n' + '\n'.join(details)
            + '\nالتوقعات من تصدير مخزّن؛ قد يتأخر تحديثها.\n\nما يهم عند الصدور: الفعلي مقابل المتوقع، وتوافق تفاصيل الإصدار. '
            'لا تكفي قراءة رقم واحد إن تعارضت معه الأجور أو البطالة أو التضخم الأساسي.\n'
            'تأكيد السوق: حركة الدولار والعوائد والذهب والسبريد في منصتك؛ البوت لا يقيسها.\n'
            'هذا تذكير؛ لا يتضمن النتيجة الفعلية ولا إشارة دخول.\n'
            '<a href="https://www.forexfactory.com/calendar">فتح تقويم المصدر</a>')


def check_calendar(db, send, now=None, downloader=download_calendar):
    now = now or dt.datetime.now(UTC)
    rows = cached_calendar(db, now, downloader)
    db.execute('CREATE TABLE IF NOT EXISTS calendar_sent (id TEXT PRIMARY KEY)')
    sent = 0
    grouped = {}
    for row in rows:
        if str(row.get('country', '')).upper() != 'USD' or str(row.get('impact', '')).lower() != 'high':
            continue
        date = timestamp(row.get('date', ''))
        if not date or not event_name(row.get('title', '')) or not 0 < (date - now).total_seconds() <= 900:
            continue
        grouped.setdefault(date, []).append(row)
    for date, rows_at_time in sorted(grouped.items()):
        # One reminder for simultaneous CPI / core CPI, or payroll / wages / unemployment.
        key = 'strict-v2-' + hashlib.sha256(('USD:' + date.isoformat()).encode()).hexdigest()
        if db.execute('SELECT 1 FROM calendar_sent WHERE id=?', (key,)).fetchone():
            continue
        send(format_event(rows_at_time, date, now))
        db.execute('INSERT INTO calendar_sent VALUES (?)', (key,))
        db.commit()
        sent += 1
    return sent

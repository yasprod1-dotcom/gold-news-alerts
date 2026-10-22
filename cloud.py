"""One scheduled check for a GitHub Actions runner; no interactive input."""
import os
import html
import json
from pathlib import Path
import sqlite3
import sys
import xml.etree.ElementTree as ET

from bot import FEEDS, Telegram, TelegramError, database, fetch, process_feed
from event_alerts import check_calendar


def configuration(env):
    token = env.get('TELEGRAM_BOT_TOKEN', '').strip()
    raw_id = env.get('TELEGRAM_CHAT_ID', '').strip()
    if not token or not raw_id:
        raise ValueError('أضف TELEGRAM_BOT_TOKEN وTELEGRAM_CHAT_ID إلى أسرار GitHub أولًا.')
    try:
        chat_id = int(raw_id)
        if chat_id <= 0:
            raise ValueError()
    except ValueError:
        raise ValueError('TELEGRAM_CHAT_ID يجب أن يكون معرّف محادثتك الشخصية كما ظهر في رسالة البوت.') from None
    return token, chat_id


def run_once(tg, chat_id, db, fetcher=fetch, calendar_checker=check_calendar):
    """Cache contains public news/calendar state, never owner IDs or tokens."""
    chat = tg.call('getChat', chat_id=chat_id)
    if chat.get('type') != 'private' or chat.get('id') != chat_id:
        raise TelegramError('استخدم معرف محادثة شخصية مع البوت.')
    db.execute('CREATE TABLE IF NOT EXISTS cloud_meta (key TEXT PRIMARY KEY)')
    db.execute('CREATE TABLE IF NOT EXISTS cloud_health (key INTEGER PRIMARY KEY, state TEXT)')
    errors = 0
    total = 0
    failed = []
    healthy_news = 0
    for feed in FEEDS:
        try:
            seeded, count = process_feed(db, feed, fetcher(feed), lambda message: tg.send(chat_id, message))
            total += count
            healthy_news += 1
            print(f'{feed.key}: ' + ('initialized' if seeded else f'{count} alerts'))
        except (OSError, ValueError, ET.ParseError, TelegramError) as error:
            errors += 1
            failed.append((feed.key, feed.source + ' — ' + feed.category))
            # Only a source ID is logged; exception strings may contain credentials.
            code = getattr(error, 'code', None)
            detail = type(error).__name__ + (f' HTTP {code}' if code is not None else '')
            print(f'{feed.key}: check or delivery failed ({detail}); next run will retry')
    try:
        reminders = calendar_checker(db, lambda message: tg.send(chat_id, message))
        total += reminders
        print(f'forexfactory-calendar: {reminders} reminders')
    except (OSError, ValueError, TelegramError):
        errors += 1
        failed.append(('forexfactory-calendar', 'تقويم Forex Factory'))
        print('forexfactory-calendar: check or delivery failed; next run will retry')
    signature = json.dumps([key for key, _ in failed])
    health = db.execute('SELECT state FROM cloud_health WHERE key=1').fetchone()
    failures_text = ('\n\n⚠️ تعذرت قراءة أو إرسال أخبار هذه المصادر، وستعاد المحاولة:\n'
                     + '\n'.join(html.escape(label) for _, label in failed)) if failed else '\nجميع المصادر متاحة في هذا الفحص.'
    if healthy_news and not db.execute("SELECT 1 FROM cloud_meta WHERE key='started'").fetchone():
        tg.send(chat_id, '✅ تم تفعيل فحص أخبار الذهب عبر GitHub.\n'
                'تصل التنبيهات إلى تلغرام على هاتفك والكمبيوتر مغلق.\n'
                'الفحص مجدول كل خمس دقائق تقريبًا، وقد يتأخر تشغيل GitHub أو نشر الخلاصات.\n'
                'المصادر: الفيدرالي وBLS وBloomberg وForex Factory وFXStreet ومختارات LiveSquawk العامة.\n'
                'تذكيرات التقويم تخص أخبار USD عالية التأثير قبل موعدها بنحو نصف ساعة.\n'
                'سُجلت الأخبار الحالية دون إرسالها؛ ستصلك الإصدارات الجديدة.\n'
                'إذا أُعيد تهيئة سجل الأخبار، قد تصلك رسالة التفعيل هذه مرة أخرى.'
                + failures_text)
        db.execute("INSERT INTO cloud_meta VALUES ('started')")
        db.execute('INSERT OR REPLACE INTO cloud_health VALUES (1, ?)', (signature,))
        db.commit()
        print('Cloud startup confirmation delivered to Telegram.')
    elif db.execute("SELECT 1 FROM cloud_meta WHERE key='started'").fetchone() and (not health or health[0] != signature):
        tg.send(chat_id, ('⚠️ تحديث حالة مصادر أخبار الذهب.' if failed else '✅ عادت جميع مصادر أخبار الذهب للعمل.')
                + failures_text)
        db.execute('INSERT OR REPLACE INTO cloud_health VALUES (1, ?)', (signature,))
        db.commit()
    return total, errors


def main():
    try:
        token, chat_id = configuration(os.environ)
        folder = Path(os.environ.get('GOLD_STATE_DIR', '.cloud-state'))
        folder.mkdir(parents=True, exist_ok=True)
        with database(folder / 'state.sqlite3') as db:
            _, errors = run_once(Telegram(token), chat_id, db)
        return 1 if errors else 0
    except (ValueError, TelegramError) as error:
        print(str(error))
        return 1
    except (OSError, sqlite3.Error):
        print('تعذر فتح سجل الأخبار أو الاتصال. راجع حالة تشغيل GitHub.')
        return 1
    except Exception:
        print('تعذر إتمام الفحص. لم تُعرض تفاصيل الطلب لحماية مفتاح البوت.')
        return 1


if __name__ == '__main__':
    sys.exit(main())

"""One scheduled check for a GitHub Actions runner; no interactive input."""
import os
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
    errors = 0
    total = 0
    for feed in FEEDS:
        try:
            seeded, count = process_feed(db, feed, fetcher(feed), lambda message: tg.send(chat_id, message))
            total += count
            print(f'{feed.key}: ' + ('initialized' if seeded else f'{count} alerts'))
        except (OSError, ValueError, ET.ParseError, TelegramError):
            errors += 1
            # Only a source ID is logged; exception strings may contain credentials.
            print(f'{feed.key}: check or delivery failed; next run will retry')
    try:
        reminders = calendar_checker(db, lambda message: tg.send(chat_id, message))
        total += reminders
        print(f'forexfactory-calendar: {reminders} reminders')
    except (OSError, ValueError, TelegramError):
        errors += 1
        print('forexfactory-calendar: check or delivery failed; next run will retry')
    if errors == 0 and not db.execute("SELECT 1 FROM cloud_meta WHERE key='started'").fetchone():
        tg.send(chat_id, '✅ تم تفعيل فحص أخبار الذهب عبر GitHub.\n'
                'تصل التنبيهات إلى تلغرام على هاتفك والكمبيوتر مغلق.\n'
                'الفحص مجدول كل خمس دقائق تقريبًا، وقد يتأخر تشغيل GitHub أو نشر الخلاصات.\n'
                'المصادر: الفيدرالي وBLS وBloomberg وForex Factory وFXStreet ومختارات LiveSquawk العامة.\n'
                'تذكيرات التقويم تخص أخبار USD عالية التأثير قبل موعدها بنحو نصف ساعة.\n'
                'سُجلت الأخبار الحالية دون إرسالها؛ ستصلك الإصدارات الجديدة.\n'
                'إذا أُعيد تهيئة سجل الأخبار، قد تصلك رسالة التفعيل هذه مرة أخرى.')
        db.execute("INSERT INTO cloud_meta VALUES ('started')")
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

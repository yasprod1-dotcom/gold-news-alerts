"""One-time phone pairing; only a SHA-256 verifier is given to the public job."""
import hashlib
import hmac
import os
import re
import sys
import time

from bot import Telegram, TelegramError


def find_match(updates, expected_hash):
    matches = []
    for update in updates:
        message = update.get('message', {})
        chat = message.get('chat', {})
        sender = message.get('from', {})
        text = message.get('text', '').strip()
        if (chat.get('type') != 'private' or sender.get('is_bot')
                or sender.get('id') != chat.get('id') or not text.startswith('/start ')):
            continue
        actual = hashlib.sha256(text.encode('utf-8')).hexdigest()
        if hmac.compare_digest(actual, expected_hash):
            matches.append((chat['id'], update['update_id']))
    if len({chat for chat, _ in matches}) > 1:
        raise TelegramError('ظهرت رسائل ربط متعارضة. استخدم رمزًا جديدًا.')
    return matches[-1] if matches else None


def link(tg, expected_hash, clock=time.monotonic, max_wait=90):
    if not re.fullmatch(r'[0-9a-f]{64}', expected_hash):
        raise ValueError('أدخل بصمة الربط الصحيحة في إعداد التشغيل اليدوي.')
    tg.call('getMe')
    if tg.call('getWebhookInfo').get('url'):
        raise TelegramError('البوت مرتبط بخدمة أخرى. لن يغيّر هذا البرنامج ربطه الحالي.')
    deadline = clock() + max_wait
    offset = 0
    while clock() < deadline:
        updates = tg.call('getUpdates', offset=offset, timeout=15, limit=100, allowed_updates=['message'])
        match = find_match(updates, expected_hash)
        if match:
            chat_id, update_id = match
            tg.send(chat_id, '✅ وصلت رسالة الربط بنجاح.\n\n'
                    f'معرف محادثتك للإعداد السحابي:\n<code>{chat_id}</code>\n\n'
                    'انسخ الرقم أعلاه وضعه في خانة Secret على GitHub تحت الاسم TELEGRAM_CHAT_ID، '
                    'ثم اضغط Add secret.\n'
                    'هذه رسالة إعداد؛ تنبيهات الأخبار تبدأ بعد حفظ الرقم ونجاح الفحص الأول.')
            # Acknowledge only after successful delivery so failed sends remain retryable.
            tg.call('getUpdates', offset=update_id + 1, timeout=0, limit=1, allowed_updates=['message'])
            print('Pairing confirmation delivered privately in Telegram. No chat ID is logged.')
            return
        if updates:
            offset = max(row['update_id'] for row in updates) + 1
    raise TelegramError('لم تصل رسالة الربط المطابقة. أرسل الرسالة الخاصة إلى بوتك ثم أعد تشغيل الربط.')


def main():
    token = os.environ.get('TELEGRAM_BOT_TOKEN', '').strip()
    verifier = os.environ.get('TELEGRAM_LINK_HASH', '').strip().lower()
    if not token:
        print('أضف TELEGRAM_BOT_TOKEN إلى أسرار GitHub أولًا.')
        return 1
    try:
        link(Telegram(token), verifier)
        return 0
    except (TelegramError, ValueError) as error:
        print(str(error))
        return 1
    except Exception:
        print('تعذر إتمام الربط. لم تُعرض تفاصيل الاتصال لحماية التوكن.')
        return 1


if __name__ == '__main__':
    sys.exit(main())

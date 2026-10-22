"""Personal Arabic Telegram alerts from official US economic RSS feeds. Stdlib only."""
from __future__ import annotations

import argparse
import datetime as dt
import email.utils
import getpass
import hashlib
import html
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass

ROOT = Path(__file__).resolve().parent
UTC = dt.timezone.utc
INTERVAL = 60
OFFICIAL_HOSTS = ("www.federalreserve.gov", "federalreserve.gov", "www.bls.gov", "bls.gov")


@dataclass(frozen=True)
class Feed:
    key: str
    url: str
    source: str
    category: str
    reason: str
    keywords: tuple[str, ...] = ()
    kind: str = "official"
    hosts: tuple[str, ...] = OFFICIAL_HOSTS


FEEDS = [
    Feed("fed-policy", "https://www.federalreserve.gov/feeds/press_monetary.xml",
         "الاحتياطي الفيدرالي الأمريكي", "السياسة النقدية الأمريكية",
         "قد يغير هذا الإعلان توقعات أسعار الفائدة والدولار، وهما عاملان مؤثران في الذهب."),
    Feed("fed-speeches", "https://www.federalreserve.gov/feeds/speeches.xml",
         "الاحتياطي الفيدرالي الأمريكي", "خطاب متعلق بالاقتصاد أو الفائدة",
         "قد يوضح الخطاب توجه السياسة النقدية. يلزم قراءة محتواه لتقييم أي تغيير في التوقعات.",
         ("monetary", "inflation", "economic outlook", "economic conditions", "interest rate",
          "labor market", "labour market", "employment", "fomc", "balance sheet")),
    Feed("cpi", "https://www.bls.gov/feed/cpi.rss",
         "مكتب إحصاءات العمل الأمريكي", "تحديث مؤشر أسعار المستهلكين CPI",
         "قد يغير تقرير التضخم توقعات الفائدة. يعتمد تأثيره على الذهب على النتائج مقارنة بتوقعات السوق."),
    Feed("jobs", "https://www.bls.gov/feed/empsit.rss",
         "مكتب إحصاءات العمل الأمريكي", "تحديث تقرير الوظائف الأمريكي",
         "قد تغير بيانات الوظائف والبطالة والأجور توقعات الفائدة والدولار، مما قد يؤثر في الذهب."),
    Feed("ppi", "https://www.bls.gov/feed/ppi.rss",
         "مكتب إحصاءات العمل الأمريكي", "تحديث مؤشر أسعار المنتجين PPI",
         "قد يقدم التقرير معلومات عن ضغوط الأسعار وتوقعات التضخم والسياسة النقدية."),
    Feed("bloomberg-markets", "https://www.bloomberg.com/feeds/markets/news.rss",
         "Bloomberg Markets", "عنوان إخباري متعلق بالذهب والأسواق", "",
         kind="news", hosts=("www.bloomberg.com", "bloomberg.com", "feeds.bloomberg.com")),
    Feed("bloomberg-economics", "https://www.bloomberg.com/feeds/economics/news.rss",
         "Bloomberg Economics", "عنوان إخباري متعلق بالاقتصاد والذهب", "",
         kind="news", hosts=("www.bloomberg.com", "bloomberg.com", "feeds.bloomberg.com")),
    Feed("forexfactory-news", "https://www.forexfactory.com/news",
         "Forex Factory", "عنوان إخباري متعلق بالذهب والأسواق", "",
         kind="ff-news", hosts=("www.forexfactory.com", "forexfactory.com")),
    Feed("fxstreet-news", "https://www.fxstreet.com/rss/news",
         "FXStreet News", "عنوان إخباري متعلق بالذهب والأسواق", "",
         kind="news", hosts=("www.fxstreet.com", "fxstreet.com")),
    Feed("livesquawk-highlights", "https://www.livesquawk.com/latest-news",
         "LiveSquawk — المختارات العامة فقط", "مختار إخباري متعلق بالذهب والأسواق", "",
         kind="ls-news", hosts=("www.livesquawk.com", "livesquawk.com")),
]


def plain(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]*>", " ", value or ""))).strip()


def timestamp(value: str) -> dt.datetime | None:
    try:
        result = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        try:
            result = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            return None
    return result.replace(tzinfo=result.tzinfo or UTC).astimezone(UTC)


def official_link(value: str) -> bool:
    return trusted_link(value, OFFICIAL_HOSTS)


def trusted_link(value: str, hosts) -> bool:
    parsed = urllib.parse.urlsplit(value)
    return parsed.scheme == "https" and parsed.hostname in hosts and not parsed.username and not parsed.password


def parse_feed(raw: bytes, hosts=OFFICIAL_HOSTS, news=False) -> list[dict]:
    root = ET.fromstring(raw)
    ns = "{http://www.w3.org/2005/Atom}"
    if root.tag == f"{ns}feed":
        nodes = root.findall(f"{ns}entry")
    elif root.tag == "rss":
        nodes = root.findall("./channel/item")
    else:
        raise ValueError("Unsupported feed")
    result = []
    for node in nodes:
        def value(*names):
            for name in names:
                child = node.find(name)
                if child is not None:
                    return "".join(child.itertext()).strip()
            return ""
        title = plain(value("title", f"{ns}title"))
        link = value("link")
        if not link:
            for child in node.findall(f"{ns}link"):
                if child.get("rel", "alternate") == "alternate":
                    link = child.get("href", "")
                    break
        summary = plain(value("description", f"{ns}summary", f"{ns}content"))
        published = value("pubDate", f"{ns}published", f"{ns}updated")
        if not title or not trusted_link(link, hosts):
            continue
        # Publication timestamps often change without a new story; exclude them.
        identity = json.dumps([title, link] if news else [title, link, summary], ensure_ascii=False)
        result.append(dict(title=title, link=link, summary=summary, published=published,
                           id=hashlib.sha256(identity.encode()).hexdigest()))
    if not result:
        raise ValueError("No usable official items")
    return result


class ForexFactoryNewsParser(HTMLParser):
    """Read public Latest Stories data; ignore comments, scripts and other blocks."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.items = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'news-block-component' and attrs.get('data-instance-id') == '1000':
            self.items = json.loads(attrs.get('data-items', '[]'))


def parse_forexfactory_news(body: str) -> list[dict]:
    parser = ForexFactoryNewsParser()
    parser.feed(body)
    if not isinstance(parser.items, list) or not parser.items:
        raise ValueError('Forex Factory public news format unavailable')
    result = []
    for row in parser.items:
        link = urllib.parse.urljoin('https://www.forexfactory.com', row.get('url', ''))
        if not trusted_link(link, ('www.forexfactory.com',)) or not row.get('title'):
            continue
        try:
            date = dt.datetime.fromtimestamp(int(row['dateline']), UTC).isoformat()
        except (KeyError, ValueError, TypeError, OverflowError):
            continue
        result.append(dict(id='ff-news-' + str(row['id']), title=plain(row['title']),
                           summary=plain(row.get('preview', '')), published=date,
                           link=link, attribution=plain(row.get('source', '')),
                           publisher_impact=row.get('impact', '')))
    if not result:
        raise ValueError('Forex Factory returned no usable news')
    return result


class LiveSquawkHighlightsParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.depth = 0
        self.capture = False
        self.title = []
        self.date = None
        self.items = []

    def handle_starttag(self, tag, attrs):
        if tag != 'div':
            return
        attrs = dict(attrs)
        classes = attrs.get('class', '').split()
        if not self.depth and 'latest__news__each' in classes:
            self.depth, self.title, self.date = 1, [], None
        elif self.depth:
            self.depth += 1
            if 'latest_news_each_title' in classes:
                self.capture = True
            if 'latest_news_each_time' in classes:
                self.date = attrs.get('data-item-time')

    def handle_data(self, data):
        if self.capture:
            self.title.append(data)

    def handle_endtag(self, tag):
        if tag != 'div' or not self.depth:
            return
        self.capture = False
        self.depth -= 1
        if self.depth == 0:
            title = plain(''.join(self.title))
            try:
                published = dt.datetime.fromtimestamp(int(self.date), UTC).isoformat()
            except (ValueError, TypeError, OverflowError):
                return
            if title:
                identity = hashlib.sha256((title + published).encode()).hexdigest()
                self.items.append(dict(id='ls-' + identity, title=title, summary='',
                                       published=published, link='https://www.livesquawk.com/latest-news'))


def parse_livesquawk_highlights(body):
    parser = LiveSquawkHighlightsParser()
    parser.feed(body)
    if not parser.items:
        raise ValueError('LiveSquawk public highlights unavailable')
    return parser.items


def fetch(feed: Feed) -> list[dict]:
    request = urllib.request.Request(feed.url, headers={
        "User-Agent": "PersonalGoldNewsMonitor/1.0", "Accept": "application/rss+xml, application/xml, text/xml"
    })
    with urllib.request.urlopen(request, timeout=15) as response:
        if not trusted_link(response.url, feed.hosts):
            raise ValueError("Unexpected redirect")
        raw = response.read(2_000_001)
        charset = response.headers.get_content_charset() or 'utf-8'
    if len(raw) > 2_000_000:
        raise ValueError("Feed too large")
    if feed.kind == 'ff-news':
        return parse_forexfactory_news(raw.decode(charset, errors='replace'))
    if feed.kind == 'ls-news':
        return parse_livesquawk_highlights(raw.decode(charset, errors='replace'))
    return parse_feed(raw, feed.hosts, news=feed.kind == 'news')


def news_topic(item: dict) -> tuple[str, str] | None:
    body = (item['title'] + ' ' + item['summary']).casefold()
    if re.search(r'\b(trading guide|technical analysis|price forecast|how to trade)\b', body):
        return None
    if re.search(r'\b(gold|bullion|xauusd|xau/usd)\b', body):
        return ('خبر متعلق بالذهب', 'يتناول العنوان الذهب مباشرة. اقرأ المصدر لمعرفة التفاصيل والسياق.')
    us = re.search(r'\b(fed|fomc|federal reserve|u\.?s\.?|american|treasury|treasuries|dollar|powell)\b', body)
    macro = re.search(r'\b(inflation|cpi|pce|payrolls|jobs|unemployment|interest rates?|rate cuts?|rate hikes?|yields?|monetary|tariffs?)\b', body)
    if us and macro:
        return ('خبر عن الفائدة أو الاقتصاد الأمريكي', 'قد يؤثر الخبر في توقعات الفائدة أو الدولار، ومن ثم في الذهب. لم تُحدد جهة حركة السعر.')
    geopolitical = re.search(r'\b(geopolitical|ceasefire|military strikes?|missile|strait of hormuz|nuclear threats?|war escalat\w*|sanctions?)\b', body)
    regional_crisis = re.search(r'\b(strikes?|evacuat\w*|war)\b', body) and re.search(r'\b(iran|israel|middle east|hormuz|russia|ukraine)\b', body)
    if geopolitical or regional_crisis:
        return ('تطور جيوسياسي محتمل الأهمية', 'قد يغير التطور الطلب على الملاذات الآمنة أو توقعات التضخم. أثره على الذهب يعتمد على السياق.')
    return None


def relevant(feed: Feed, item: dict) -> bool:
    if feed.kind in ('news', 'ff-news', 'ls-news'):
        return news_topic(item) is not None
    body = (item["title"] + " " + item["summary"]).casefold()
    return not feed.keywords or any(word in body for word in feed.keywords)


def format_alert(feed: Feed, item: dict, now: dt.datetime | None = None) -> str:
    now = now or dt.datetime.now(UTC)
    published = timestamp(item["published"])
    when = published.strftime("%Y-%m-%d %H:%M UTC") if published else "غير متاح في المصدر"
    is_news = feed.kind in ('news', 'ff-news', 'ls-news')
    topic = news_topic(item) if is_news else None
    category, reason = topic or (feed.category, feed.reason)
    source = feed.source
    if item.get('attribution'):
        source += ' — ينقل عن: ' + item['attribution']
    header = '🔔 <b>عنوان إخباري قد يؤثر في الذهب</b>\n\n' if is_news else '🔔 <b>خبر رسمي قد يؤثر في الذهب</b>\n\n'
    return (
        header
        + f"<b>{html.escape(category)}</b>\n"
        f"المصدر: {html.escape(source)}\n"
        f"وقت النشر: {when}\n"
        f"وقت الرصد: {now.strftime('%Y-%m-%d %H:%M UTC')}\n\n"
        f"<b>سبب المتابعة:</b> {html.escape(reason)}\n\n"
        f"<b>العنوان الأصلي بلغة المصدر:</b>\n{html.escape(item['title'][:700])}\n\n"
        f"<a href=\"{html.escape(item['link'], quote=True)}\">قراءة الخبر من المصدر</a>\n\n"
        "هذا تنبيه إلى نشر أو تحديث خبر؛ ليس إشارة شراء أو بيع. "
        "لم تُقارن الأرقام بتوقعات السوق، ولم يُحدد اتجاه حركة الذهب."
    )


class TelegramError(Exception):
    pass


class Telegram:
    def __init__(self, token: str):
        if not re.fullmatch(r"\d+:[A-Za-z0-9_-]+", token):
            raise TelegramError("صيغة مفتاح البوت غير صحيحة.")
        self.token = token

    def call(self, method: str, **data):
        request = urllib.request.Request(
            f"https://api.telegram.org/bot{self.token}/{method}",
            data=json.dumps(data).encode(), headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=35) as response:
                payload = json.load(response)
            if not payload.get("ok"):
                raise TelegramError("لم يقبل تلغرام الطلب.")
            return payload["result"]
        except urllib.error.HTTPError as error:
            # Never display a request URL: it contains the token.
            if error.code == 401:
                raise TelegramError("رفض تلغرام المفتاح؛ تحقق من BotFather.") from None
            if error.code == 409:
                raise TelegramError("البوت مستخدم في خدمة أخرى؛ استخدم بوتًا جديدًا مخصصًا لهذا البرنامج.") from None
            raise TelegramError(f"تعذر الاتصال بتلغرام (HTTP {error.code}).") from None
        except (OSError, ValueError):
            raise TelegramError("تعذر الاتصال بتلغرام. تحقق من الإنترنت وحاول مجددًا.") from None

    def send(self, chat_id: int, text: str):
        return self.call("sendMessage", chat_id=chat_id, text=text, parse_mode="HTML",
                         link_preview_options={"is_disabled": True})


def database(path=ROOT / "state.sqlite3"):
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE IF NOT EXISTS seen (id TEXT PRIMARY KEY)")
    db.execute("CREATE TABLE IF NOT EXISTS seeded (feed TEXT PRIMARY KEY)")
    return db


def process_feed(db, feed, items, send, now=None):
    now = now or dt.datetime.now(UTC)
    seed = db.execute("SELECT 1 FROM seeded WHERE feed=?", (feed.key,)).fetchone() is None
    sent = 0
    for item in sorted(items, key=lambda row: timestamp(row["published"]) or dt.datetime.min.replace(tzinfo=UTC)):
        if db.execute("SELECT 1 FROM seen WHERE id=?", (item["id"],)).fetchone():
            continue
        published = timestamp(item["published"])
        fresh = published is not None and -300 <= (now - published).total_seconds() <= 21600
        if not seed and fresh and relevant(feed, item):
            send(format_alert(feed, item, now))
            sent += 1
        # Persist after delivery. Failed deliveries remain eligible for retry.
        db.execute("INSERT OR IGNORE INTO seen VALUES (?)", (item["id"],))
        db.commit()
    if seed:
        db.execute("INSERT INTO seeded VALUES (?)", (feed.key,))
        db.commit()
    return seed, sent


def pair(tg: Telegram, bot: dict, db, on_pair=None, stop=None):
    db.execute("CREATE TABLE IF NOT EXISTS owner (bot INTEGER PRIMARY KEY, chat INTEGER)")
    existing = db.execute("SELECT chat FROM owner WHERE bot=?", (bot["id"],)).fetchone()
    if existing:
        return existing[0]
    code = secrets.token_hex(8)
    if on_pair:
        on_pair(bot['username'], f"/start {code}")
    else:
        print(f"\nافتح البوت: https://t.me/{bot['username']}")
        print(f"أرسل إليه هذه الرسالة بالضبط لربط حسابك: /start {code}")
        print("بانتظار رسالة الربط لمدة خمس دقائق...")
    offset = 0
    until = time.monotonic() + 300
    while time.monotonic() < until:
        if stop is not None and stop.is_set():
            raise TelegramError("تم إيقاف الربط.")
        for update in tg.call("getUpdates", offset=offset, timeout=20, allowed_updates=["message"]):
            offset = update["update_id"] + 1
            message = update.get("message", {})
            chat = message.get("chat", {})
            if (chat.get("type") == "private" and message.get("text", "").strip() == f"/start {code}"
                    and message.get("from", {}).get("id") == chat.get("id")):
                db.execute("INSERT INTO owner VALUES (?, ?)", (bot["id"], chat["id"]))
                db.commit()
                return chat["id"]
    raise TelegramError("انتهت مهلة الربط. أعد تشغيل البرنامج للمحاولة مجددًا.")


def monitor(tg, chat_id, db, stop=None, status=None):
    status = status or print
    tg.send(chat_id, "✅ بدأ تشغيل مراقب أخبار الذهب.\nالمصادر: الفيدرالي وBLS وBloomberg وForex Factory وFXStreet ومختارات LiveSquawk العامة.\n"
            f"معرف محادثتك للإعداد السحابي: <code>{chat_id}</code>\n"
            "يجري فحص المصادر كل دقيقة؛ قد يتأخر نشر الأخبار في خلاصاتها. "
            "سيتم تجاهل الأخبار الموجودة عند أول اتصال ناجح بكل مصدر.\n"
            "يعمل البوت ما دام هذا البرنامج مفتوحًا والجهاز متصلًا بالإنترنت ومستيقظًا.")
    failures = {feed.key: 0 for feed in FEEDS}
    notified = set()
    status("✅ البوت متصل بحسابك. بدأت متابعة مصادر الأخبار.")
    while True:
        if stop is not None and stop.is_set():
            return
        for feed in FEEDS:
            if stop is not None and stop.is_set():
                return
            try:
                items = fetch(feed)
                seed, count = process_feed(db, feed, items, lambda text: tg.send(chat_id, text))
                if feed.key in notified:
                    tg.send(chat_id, f"✅ عاد الاتصال بالمصدر: {feed.source} — {feed.category}")
                    notified.remove(feed.key)
                failures[feed.key] = 0
                status(f"{feed.category}: " + ("تم تسجيل الأخبار السابقة دون إرسالها" if seed else f"تنبيهات جديدة: {count}"))
            except (OSError, ValueError, ET.ParseError, TelegramError):
                failures[feed.key] += 1
                status(f"{feed.category}: تعذرت دورة الفحص أو الإرسال، ستتم إعادة المحاولة.")
                if failures[feed.key] >= 3 and feed.key not in notified:
                    try:
                        tg.send(chat_id, f"⚠️ تعذرت متابعة أو إرسال أخبار: {feed.category}. "
                                "قد تتأخر التنبيهات؛ تستمر إعادة المحاولة.")
                        notified.add(feed.key)
                    except TelegramError:
                        pass
        try:
            from event_alerts import check_calendar
            count = check_calendar(db, lambda message: tg.send(chat_id, message))
            status(f"تقويم Forex Factory: تذكيرات جديدة: {count}")
        except (OSError, ValueError, TelegramError):
            status("تقويم Forex Factory: تعذرت المتابعة مؤقتًا؛ ستتم إعادة المحاولة.")
        if stop is not None:
            if stop.wait(INTERVAL):
                return
        else:
            time.sleep(INTERVAL)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--demo", action="store_true")
    parser.add_argument("--check-feeds", action="store_true")
    args = parser.parse_args()
    if args.demo:
        print(format_alert(FEEDS[2], dict(title="Consumer Price Index — EXAMPLE ONLY",
                                        link="https://www.bls.gov/cpi/", published="")))
        return
    if args.check_feeds:
        from concurrent.futures import ThreadPoolExecutor
        def check(feed):
            try:
                return feed.key, len(fetch(feed))
            except Exception as error:
                return feed.key, type(error).__name__
        with ThreadPoolExecutor(max_workers=5) as pool:
            results = list(pool.map(check, FEEDS))
        for key, result in results:
            print(key, result)
        if any(not isinstance(result, int) for _, result in results):
            raise SystemExit(1)
        return
    print("مراقب أخبار الذهب — أغلق النافذة أو اضغط Ctrl+C للإيقاف.")
    token = os.environ.get("TELEGRAM_BOT_TOKEN") or getpass.getpass("ألصق مفتاح البوت هنا (لن يظهر ولن يُحفظ): ")
    try:
        tg = Telegram(token.strip())
        bot = tg.call("getMe")
        webhook = tg.call("getWebhookInfo")
        if webhook.get("url"):
            raise TelegramError("هذا البوت مرتبط بخدمة أخرى. أنشئ بوتًا جديدًا مخصصًا لهذا البرنامج.")
        with database() as db:
            chat_id = pair(tg, bot, db)
            monitor(tg, chat_id, db)
    except TelegramError as error:
        print(str(error))
        raise SystemExit(1)
    except sqlite3.Error:
        print("تعذر فتح سجل البوت. استخرج الملفات في مجلد قابل للكتابة وأغلق أي نسخة أخرى منه.")
        raise SystemExit(1)
    except KeyboardInterrupt:
        print("\nتم إيقاف المراقبة.")


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    main()

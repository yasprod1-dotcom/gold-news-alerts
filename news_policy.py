"""Conservative gold event selection and source-grounded briefing fields."""
import datetime as dt
import html
import re
from decimal import Decimal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

MAX_NEWS_AGE_SECONDS = 600
try:
    DISPLAY_ZONE = ZoneInfo('Africa/Casablanca')
    DISPLAY_ZONE_LABEL = 'المغرب'
except ZoneInfoNotFoundError:
    DISPLAY_ZONE = dt.timezone.utc
    DISPLAY_ZONE_LABEL = 'UTC'

NOISE = re.compile(r'\b(preview|outlook|forecast to|expected to|set to|ahead of|awaits?|awaiting|may|might|could|would|weekly|daily|recap|roundup|digest|analysis|technical|price forecast|prediction|how to|what to watch|what it means|raises pressure|bets|positioning|eyes?|traders weigh)\b', re.I)
US = re.compile(r'\b(u\.?s\.?|united states|american|fed|fomc|federal reserve)\b', re.I)
SURPRISE = re.compile(r'\b(surpris\w*|unexpected\w*|hotter|cooler|beats?|miss(?:es|ed)?|above (?:forecasts?|expectations?|estimates?)|below (?:forecasts?|expectations?|estimates?)|(?:higher|lower|stronger|weaker) than expected)\b', re.I)
RELEASE = re.compile(r'\b(rose|rises?|fell|falls?|jump\w*|drop\w*|increas\w*|decreas\w*|unchanged|accelerat\w*|slow\w*)\b', re.I)
NUMBER = r'[-+]?\d[\d,]*(?:\.\d+)?\s*(?:%|bps?|[KMB])?'
LABEL = r'(?:est(?:imate[ds]?)?\.?|forecast|expected|consensus)'


def clean(text):
    return re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]+>', ' ', text or ''))).strip()


def comparison(item):
    """Only accept a locally adjacent, explicitly labelled actual/forecast pair."""
    text = clean(item.get('title', '') + ' ' + item.get('summary', ''))
    patterns = [
        rf'(?P<actual>{NUMBER})\s*\(\s*{LABEL}\s*[:=]?\s*(?P<forecast>{NUMBER})',
        rf'(?P<actual>{NUMBER})\s+(?:vs\.?|versus)\s+(?P<forecast>{NUMBER})\s+(?:expected|forecast|estimated|consensus)\b',
        rf'\bactual\s*[:=]\s*(?P<actual>{NUMBER})\s*[,;|]\s*{LABEL}\s*[:=]\s*(?P<forecast>{NUMBER})',
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if not match:
            continue
        context = text[max(0, match.start() - 80):match.start()]
        # A named metric is required; never associate unrelated figures.
        metrics = list(re.finditer(r'\b(core cpi|cpi|core pce|pce|non[- ]?farm (?:payrolls|employment change)|payrolls|unemployment rate|ppi|retail sales|gdp)\b', context, re.I))
        if not metrics:
            continue
        metric = metrics[-1].group(0)
        between = context[metrics[-1].end():]
        if len(between) > 40 or re.search(r'[.;!?]|\d|\b(company|earnings|eps|revenue)\b', between, re.I):
            continue
        actual, forecast = match.group('actual').strip(), match.group('forecast').strip()
        def numeric(value):
            parsed = re.fullmatch(r'([-+]?\d[\d,]*(?:\.\d+)?)\s*(%|bps?|[KMB])?', value, re.I)
            return (Decimal(parsed.group(1).replace(',', '')), (parsed.group(2) or '').upper()) if parsed else None
        a, f = numeric(actual), numeric(forecast)
        delta = None
        if a and f and a[1] == f[1]:
            delta = a[0] - f[0]
        previous_match = re.match(r'\s*[,;]\s*(?:prev(?:ious)?\.?)\s*[:=]?\s*(' + NUMBER + ')', text[match.end():], re.I)
        return dict(metric=metric, actual=actual, forecast=forecast, delta=delta,
                    previous=previous_match.group(1).strip() if previous_match else None)
    return None


def profile(kind, title, reason, mechanism):
    return dict(kind=kind, title=title, reason=reason, mechanism=mechanism)


def classify(feed_key, item):
    title = clean(item.get('title', ''))
    lower = title.casefold()
    if not title:
        return None
    # Official releases are events; a generic speech, calendar or implementation note is not.
    if feed_key in ('cpi', 'jobs', 'ppi'):
        if re.search(r'\b(correction|notice|schedule|methodology|revision policy)\b', lower):
            return None
        return release_profile({'cpi': 'cpi', 'jobs': 'jobs', 'ppi': 'ppi'}[feed_key])
    if feed_key == 'fed-policy':
        if re.search(r'\b(minutes|fomc statement)\b', lower) and not re.search(r'\b(schedule|calendar|implementation)\b', lower):
            return policy_profile('minutes' if 'minutes' in lower else 'decision')
        if re.search(r'\b(emergency|unscheduled)\b', lower) and re.search(r'\b(rate|liquidity|facility|federal reserve)\b', lower):
            return policy_profile('emergency')
        return None
    if feed_key == 'fed-speeches':
        return None
    if NOISE.search(title):
        return None
    comp = comparison(item)
    is_release = bool(SURPRISE.search(title) or comp or (RELEASE.search(title) and re.search(r'\d', title)))
    if comp and comp['delta'] == 0 and not SURPRISE.search(title):
        return None
    if US.search(title) and is_release and not re.search(r'\b(inflation expectations|breakeven|survey of economists)\b', lower):
        for pattern, kind in [(r'\b(cpi|consumer prices?|inflation)\b', 'cpi'),
                              (r'\b(pce|personal consumption expenditures)\b', 'pce'),
                              (r'\b(non[- ]?farm|payrolls|jobs report|employment report|unemployment rate)\b', 'jobs'),
                              (r'\b(ppi|producer prices?)\b', 'ppi'),
                              (r'\b(retail sales|gdp)\b', 'growth')]:
            if re.search(pattern, lower):
                # GDP / retail sales qualify only as a reported surprise, not an ordinary release recap.
                if kind == 'growth' and not (SURPRISE.search(title) or comp):
                    return None
                return release_profile(kind)
    if US.search(title) and re.search(r'\b(cuts?|raises?|hikes?|holds?|keeps?)\b', lower) and re.search(r'\b(rates?|target range)\b', lower):
        if re.search(r'\b(expects?|predicts?|sees|likely|economists?|analysts?|bank of america|bofa|goldman|jpmorgan|morgan stanley|official|governor|goolsbee|bostic|waller|daly)\b', lower):
            return None
        return policy_profile('decision')
    if re.search(r'\b(emergency|unscheduled)\b', lower) and US.search(title) and re.search(r'\b(liquidity|facility|meeting|rates?)\b', lower):
        return policy_profile('emergency')
    if re.search(r'\b(us|u\.s\.|united states|trump|white house)\b', lower) and re.search(r'\b(announces?|imposes?|suspends?|removes?|raises?)\b', lower) and re.search(r'\b(tariffs?)\b', lower):
        percentages = [float(value) for value in re.findall(r'(\d+(?:\.\d+)?)\s*%', lower)]
        if any(value >= 10 for value in percentages) or re.search(r'\b(global|all imports|china|european union)\b', lower):
            return profile('trade-policy', 'تغيير جوهري في السياسة التجارية الأمريكية',
                           'ورد إعلان محدد عن الرسوم، لا مجرد توقع أو تهديد.',
                           'قد يتغير تسعير التضخم والنمو والمخاطر معًا؛ اتجاه الذهب غير محسوم من العنوان وحده.')
    if US.search(title) and re.search(r'\b(downgrades?|downgraded)\b', lower) and re.search(r'\b(sovereign|credit rating|creditworthiness)\b', lower):
        return profile('sovereign-risk', 'تغيير في التصنيف الائتماني الأمريكي',
                       'تغيير محدد في مخاطر الائتمان السيادي.',
                       'قد يعيد تسعير الدولار والسندات وطلب الملاذات الآمنة؛ لا يحدد وحده اتجاه الذهب اللحظي.')
    # Regional fear, threats, commentary and prospective plans are excluded.
    if re.search(r'\b(plans?|considers?|talks|warns?|threatens?|concerns?|fears?|risks?)\b', lower):
        return None
    region = re.search(r'\b(iran|israel|gaza|hamas|lebanon|hezbollah|middle east|hormuz|russia|ukraine|nato|china|taiwan|united states|u\.?s\.?)\b', lower)
    material = re.search(r'\b(launch\w*|attack\w*|strikes?|bomb\w*|invad\w*|invasion|evacuat\w*|closes?|closure|blocks?|reopens?|halt\w*|signs?|signed|agrees?|agreed|reached|collaps\w*|broken|ends?)\b', lower)
    if region and material and re.search(r'\b(missiles?|military|strikes?|troops|embass\w*|diplomats?|ceasefire|hormuz|invasion|nuclear)\b', lower):
        if re.search(r'\b(satellite|exercise|drill|procurement|purchase)\b', lower):
            return None
        deescalation = bool(re.search(r'\b(ceasefire|reopens?)\b', lower) and re.search(r'\b(agrees?|agreed|signs?|signed|reached|reopens?)\b', lower))
        return profile('geopolitics', 'تطور جيوسياسي جوهري',
                       'يتضمن العنوان تطورًا محددًا، لا مجرد حديث عام عن التوترات.',
                       'التهدئة قد تخفف طلب الملاذ الآمن، لكن أثر النفط والدولار قد يعاكسها.' if deescalation else
                       'التصعيد قد يرفع طلب الملاذ الآمن؛ أثره على النفط والدولار والعوائد قد يغير اتجاه الذهب.')
    return None


def release_profile(kind):
    title = {'cpi': 'صدور بيانات التضخم الأمريكية CPI', 'pce': 'صدور بيانات التضخم الأمريكية PCE',
             'ppi': 'صدور بيانات أسعار المنتجين الأمريكية PPI', 'jobs': 'صدور بيانات سوق العمل الأمريكي',
             'growth': 'مفاجأة في بيانات النمو أو الاستهلاك الأمريكي'}[kind]
    return profile(kind, title, 'إصدار اقتصادي قد يعيد تسعير توقعات الفائدة الأمريكية.',
                   'تضخم أعلى من المتوقع قد يدعم الدولار والعوائد ويضغط الذهب؛ الأدنى قد يعكس ذلك.'
                   if kind in ('cpi', 'pce', 'ppi') else
                   'قوة البيانات قد تدعم الدولار والعوائد وتضغط الذهب؛ ضعفها قد يعكس ذلك. تفاصيل البطالة والأجور قد تتعارض.')


def policy_profile(kind):
    title = {'decision': 'قرار أو بيان للسياسة النقدية الأمريكية', 'minutes': 'نشر محضر اجتماع الفيدرالي',
             'emergency': 'إجراء أو اجتماع طارئ للفيدرالي'}[kind]
    return profile('policy-' + kind, title,
                   'المهم هو ما تغير مقارنة بتسعير السوق، وليس كلمة رفع أو خفض وحدها.',
                   'تشدد يفوق التوقعات قد يضغط الذهب عبر الدولار والعوائد؛ تيسير يفوقها قد يدعمه. لا تتوفر هنا مقارنة بتسعير السوق.')


def format_time(value):
    return value.astimezone(DISPLAY_ZONE).strftime('%H:%M:%S') + ' ' + DISPLAY_ZONE_LABEL


def mechanism(info, comp):
    if not comp or comp['delta'] is None:
        return info['mechanism']
    if comp['delta'] == 0:
        return 'الرقم مطابق للتوقع المذكور، فلا يُعد وحده مفاجأة. قد تأتي الحركة من تفاصيل أخرى في الإصدار.'
    metric = comp['metric'].casefold()
    stronger = comp['delta'] > 0
    if 'unemployment' in metric:
        stronger = not stronger
    if re.search(r'cpi|pce|ppi|payroll|employment change|unemployment|retail sales|gdp', metric):
        return ('قراءة مشروطة: ضغط محتمل على الذهب إذا دعم الرقم ارتفاع الدولار والعوائد. هذه الحركة لم تُقَس هنا.'
                if stronger else
                'قراءة مشروطة: دعم محتمل للذهب إذا دفع الرقم الدولار والعوائد إلى الانخفاض. هذه الحركة لم تُقَس هنا.')
    return info['mechanism']


def render_brief(feed, item, published, now):
    info = classify(feed.key, item)
    if not info:
        raise ValueError('Item does not meet strict event policy')
    comp = comparison(item)
    age = max(0, int((now - published).total_seconds()))
    delay = f'{age // 60}د {age % 60}ث'
    reliability = 'إصدار رسمي' if feed.kind == 'official' else 'تقرير منقول عن المصدر؛ لم يُتحقق منه مستقلًا'
    facts = 'الفعلي مقابل المتوقع: غير متاح بوضوح في الخلاصة؛ لم يُستنتج من العنوان.'
    if comp:
        facts = (f"المقياس: {html.escape(comp['metric'])}\n"
                 f"الفعلي: {html.escape(comp['actual'])} | المتوقع: {html.escape(comp['forecast'])}")
        if comp['previous']:
            facts += f" | السابق: {html.escape(comp['previous'])}"
        if comp['delta'] is not None:
            direction = 'أعلى' if comp['delta'] > 0 else 'أقل' if comp['delta'] < 0 else 'مطابق'
            facts += f'\nالمقارنة: {direction} من المتوقع المذكور.' if direction != 'مطابق' else '\nالمقارنة: مطابق للتوقع المذكور.'
    excerpt = clean(item.get('summary', ''))
    # Show a short attributed excerpt only when it adds explicit data to an official generic title.
    evidence = ''
    if not comp and feed.kind == 'official' and re.search(r'\d', excerpt):
        evidence = '\nمقتطف المصدر: ' + html.escape(excerpt[:220]) + ('…' if len(excerpt) > 220 else '')
    attribution = (' — ' + html.escape(item['attribution'])) if item.get('attribution') else ''
    status = '⚠️ وصول متأخر عن النشر؛ قد تكون الحركة الأولى حدثت بالفعل.' if age > 120 else 'رُصد حديثًا؛ وقت النشر لا يضمن بدء الحركة الآن.'
    return (f"🚨 <b>XAU/USD | {html.escape(info['title'])}</b>\n"
            f"نُشر {format_time(published)} | تأخر الرصد: {delay}\n{status}\n\n"
            f"<b>الخبر من المصدر:</b>\n{html.escape(item['title'][:450])}\n{facts}{evidence}\n\n"
            f"<b>ما الذي قد يتغير؟</b>\n{html.escape(mechanism(info, comp))}\n\n"
            'تأكيد رد الفعل: قارن حركة الذهب بالدولار والعوائد والسبريد في منصتك؛ هذه البيانات غير متاحة للبوت.\n'
            f"المصدر: {html.escape(feed.source)}{attribution} | {reliability}\n"
            f"<a href=\"{html.escape(item['link'], quote=True)}\">فتح الخبر الأصلي</a>\n"
            'تنبيه سياقي متأخر محتمل، وليس إشارة دخول أو قياسًا لتأثير تحقق في السوق.')


STOPWORDS = {'the', 'a', 'an', 'to', 'of', 'and', 'in', 'on', 'as', 'for', 'with', 'from', 'after', 'says', 'said', 'breaking', 'update', 'news'}


def title_signature(item):
    text = clean(item['title']).casefold()
    tokens = set(re.findall(r'[a-z]+|\d+(?:\.\d+)?%?', text)) - STOPWORDS
    numbers = set(re.findall(r'[-+]?\d+(?:\.\d+)?%?', text))
    return tokens, numbers


def is_duplicate(db, item, now):
    import json
    tokens, numbers = title_signature(item)
    for encoded_tokens, encoded_numbers in db.execute('SELECT tokens, numbers FROM event_alert_history WHERE sent > ?', (now.timestamp() - 1800,)):
        old = set(json.loads(encoded_tokens))
        if numbers != set(json.loads(encoded_numbers)):
            continue
        if tokens == old or (len(tokens & old) >= 4 and len(tokens & old) / max(1, len(tokens | old)) >= .72):
            return True
    return False


def remember_alert(db, item, now):
    import json
    tokens, numbers = title_signature(item)
    db.execute('INSERT INTO event_alert_history VALUES (?, ?, ?)',
               (now.timestamp(), json.dumps(sorted(tokens)), json.dumps(sorted(numbers))))
    db.execute('DELETE FROM event_alert_history WHERE sent < ?', (now.timestamp() - 86400,))

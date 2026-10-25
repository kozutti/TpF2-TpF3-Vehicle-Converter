"""Folder names for converted MODs, ready to drop into the game's `mods` folder.

A Workshop MOD folder is usually just its id (`1234567890`). The output folder
then becomes `1234567890_<series name>` (`1234567890_800系新幹線`, `..._西鉄9000系`), the short
Japanese series name read out of the MOD's title (suggest_vehicle_name); a folder that already
carries a name keeps it. The game-internal modId stays ASCII and
independent of this name (see planner.build_plan).
"""
from __future__ import annotations

import re
from pathlib import Path

ID_ONLY = re.compile(r'\d+')
LEADING_ID = re.compile(r'(\d{6,})(?:_|$)')
FULLWIDTH = {'/': '／', '\\': '＼', ':': '：', '*': '＊', '?': '？', '"': '”', '<': '＜', '>': '＞', '|': '｜'}
MAX_TITLE = 60
# trailing decoration that is not part of the vehicle's name: " Ver: 4.0.2", " v1.2", " - made by …"
NOISE = [re.compile(r'\s*[-–—]{1,3}\s*made\s+by\b.*$', re.I), re.compile(r'\s+[-–—]{3,}.*$'),
         re.compile(r'\s*\bver(?:sion)?\.?\s*:?\s*v?\d[\w.\-]*\s*$', re.I), re.compile(r'\s+v\d+(?:\.\d+)+\s*$', re.I)]


def safe_name(text):
    """A Windows-safe folder name part: forbidden characters become their full-width forms."""
    text = ''.join(FULLWIDTH.get(c, c) for c in str(text) if c >= ' ')
    return re.sub(r'\s+', ' ', text).strip(' .')


def clean_title(title):
    text = str(title or '')
    for pattern in NOISE:
        text = pattern.sub('', text)
    text = safe_name(text)
    return text[:MAX_TITLE].rstrip(' .')


# Operators written out in full -> the short form a folder name uses. JR / 国鉄 / JNR are left out of names altogether.
COMPANIES = [
    (r'京王帝都電鉄|京王電鉄|Keio', '京王'), (r'小田急電鉄|Odakyu', '小田急'), (r'東京急行電鉄|東急電鉄|Tokyu', '東急'),
    (r'西武鉄道|Seibu', '西武'), (r'東武鉄道|Tobu', '東武'), (r'京成電鉄|Keisei', '京成'), (r'近畿日本鉄道|Kintetsu', '近鉄'),
    (r'阪急電鉄|Hankyu', '阪急'), (r'阪神電気鉄道|Hanshin', '阪神'), (r'相模鉄道|Sotetsu', '相鉄'),
    (r'京浜急行電鉄|KEIKYU|Keikyu', '京急'), (r'東京地下鉄|東京メトロ|Tokyo ?Metro', '東京メトロ'), (r'東京都交通局|Toei', '都営'),
    (r'西日本鉄道|Nishitetsu', '西鉄'), (r'名古屋鉄道|Meitetsu', '名鉄'), (r'南海電気鉄道|Nankai', '南海'),
    (r'京阪電気鉄道|Keihan', '京阪'), (r'日本貨物鉄道', 'JR貨物')]
# trailing words that only say what kind of vehicle it is
KIND = re.compile(r'(電車|気動車|電気機関車|ディーゼル機関車|機関車|客車|貨車|特急車|電気式気動車)')
# a series: optional prefix letters / kana, digits, an optional 系/形 and an optional 番台
SERIES = re.compile(r'(?<![A-Za-z0-9.])((?:(?:[A-Za-z]{1,3}|キハ|キロ|クモハ|モハ|クハ|サハ)[ -]?\d{1,5}|\d{2,5})(?:-\d{1,4})?)(?!\d)\s*(系|形)?(?:\s*(\d{1,4}番台))?')
ENGLISH = [(re.compile(r'\bShinkansen\s+(?=[A-Z]?\d)', re.I), '新幹線'), (re.compile(r'\bSeries\s*(\d+)', re.I), r'\1系'),
           (re.compile(r'(\d+)\s*Series\b', re.I), r'\1系'), (re.compile(r'\bnew(?=\d)', re.I), '新'),
           (re.compile(r'\b(?:JR|JNR)\s*(?:East|Central|West|Kyushu|Hokkaido|Shikoku|Freight)?\b', re.I), '')]
MAX_SERIES = 3
JAPANESE = re.compile(r'[぀-ヿ一-鿿]')


def suggest_vehicle_name(title):
    """A short Japanese series name (`E233系3000番台`, `西鉄9000形`, `E7系新幹線`) from a MOD title; '' when none is found."""
    text = str(title or '')
    for pattern in NOISE:
        text = pattern.sub('', text)
    text = re.sub(r'[～〜~].*?[～〜~]', ' ', text)
    text = re.sub(r'[（(][^）)]*[）)]|[\[【][^\]】]*[\]】]|リクエスト用|\d+次車', ' ', text)
    if not (JAPANESE.search(text) or re.search(r'Series|Shinkansen|\bJNR\b|\bJR\b|KEIKYU|Keikyu', text, re.I)
            or any(re.search(pattern, text, re.I) for pattern, _ in COMPANIES)):
        return ''  # not a Japanese vehicle (`British Rail Class 47`): leave the title alone instead of guessing a series
    shinkansen = bool(re.search(r'新幹線|Shinkansen', text, re.I))
    company = ''
    for pattern, short in COMPANIES:
        if re.search(pattern, text, re.I):
            company = short
            text = re.sub(pattern, ' ', text, flags=re.I)
            break
    for pattern, replacement in ENGLISH:
        text = pattern.sub(replacement, text)
    text = re.sub(r'JR\s*(?:東日本|東海|西日本|九州|北海道|四国|貨物)?|国鉄|JNR|日本国有鉄道|東日本旅客鉄道', ' ', text)
    text = KIND.sub(' ', text).replace('新幹線', ' ')
    found, seen = [], set()
    for match in SERIES.finditer(text):
        body, suffix, platform = match.groups()
        body = body.replace(' ', '')
        if body not in seen:
            seen.add(body)
            found.append((body, suffix or '', platform or ''))
    if not found:  # a nickname only (アーバンライナー): the operator and what is left of the title
        rest = re.sub(r'[\s／/・_\-]+', ' ', text).strip()
        rest = re.sub(r'(?<=[^ -~]) | (?=[^ -~])', '', rest)  # no spaces next to Japanese text
        return (company + rest) if rest else ''
    found = found[:MAX_SERIES]
    suffixes = {suffix for _, suffix, _ in found}
    if len(suffixes) == 1 and (next(iter(suffixes)) or len(found) > 1 or shinkansen):
        suffix = next(iter(suffixes)) or '系'
        name = '／'.join(body for body, _, _ in found) + suffix + (found[-1][2] if len(found) == 1 else '')
    else:
        name = '／'.join(body + (suffix or '') + platform for body, suffix, platform in found)
    if company and not shinkansen:
        name = company + name
    return name + ('新幹線' if shinkansen else '')


def source_name(source):
    source = Path(source)
    return source.stem if source.is_file() else source.name


def output_folder_name(source, title):
    """`<id>_<vehicle name>` for an id-only MOD folder; any other folder name is kept (made Windows-safe)."""
    name = source_name(source)
    if ID_ONLY.fullmatch(name):
        vehicle = suggest_vehicle_name(title) or clean_title(title)
        if vehicle and vehicle != name:
            return f'{name}_{vehicle}'
        return name
    return safe_name(name) or 'vehicle'


def mod_id_core(source):
    """ASCII body of the modId: the Workshop id when the folder starts with one, otherwise the sanitised folder name."""
    name = source_name(source)
    found = LEADING_ID.match(name)
    if found:
        return found.group(1)
    return re.sub(r'[^a-z0-9_]+', '_', name.lower()).strip('_') or 'vehicle'


def unique_child(parent, name):
    """`name` under `parent`, or `name_2`, `name_3`… when something already has it."""
    parent = Path(parent)
    candidate, index = parent / name, 2
    while candidate.exists():
        candidate = parent / f'{name}_{index}'
        index += 1
    return candidate

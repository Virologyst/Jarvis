"""Time and date anywhere in the world, worded to be spoken aloud.

Uses the system clock and the IANA timezone database (zoneinfo), so daylight saving is always right.
Place names are matched against a table of common cities, countries and regions, then against the
city part of the IANA zone names ("new york" -> America/New_York). Places that could mean more than
one timezone (a country with several, or two cities with the same name) are reported as ambiguous so
Jarvis can ask which one was meant. Read-only: nothing here changes anything.
"""

from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta, timezone
from functools import lru_cache, wraps
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones

HOME_ZONE = "Australia/Brisbane"
HOME_NAME = "Brisbane"

_HERE = {"", "here", "local", "home", "the office", "office", "this office", "my time", "our time",
         "local time", "brisbane", "brissie", "brisvegas", "queensland", "qld", "gold coast",
         "sunshine coast", "ipswich", "logan", "toowoomba", "cairns", "townsville", "aest"}

# name | zone, or name | Label=Zone; Label=Zone for names that could mean several timezones.
_PLACES = """
utc|UTC
gmt|UTC
coordinated universal time|UTC
greenwich mean time|UTC
zulu|UTC
sydney|Australia/Sydney
new south wales|Australia/Sydney
nsw|Australia/Sydney
canberra|Australia/Sydney
act|Australia/Sydney
newcastle|Newcastle, Australia=Australia/Sydney; Newcastle, England=Europe/London
wollongong|Australia/Sydney
melbourne|Australia/Melbourne
victoria|Victoria, Australia=Australia/Melbourne; Victoria, Canada=America/Vancouver
geelong|Australia/Melbourne
hobart|Australia/Hobart
tasmania|Australia/Hobart
adelaide|Australia/Adelaide
south australia|Australia/Adelaide
darwin|Australia/Darwin
northern territory|Australia/Darwin
alice springs|Australia/Darwin
perth|Perth, Australia=Australia/Perth; Perth, Scotland=Europe/London
western australia|Australia/Perth
broken hill|Australia/Broken_Hill
lord howe island|Australia/Lord_Howe
norfolk island|Pacific/Norfolk
australia|Brisbane=Australia/Brisbane; Sydney, Melbourne, Canberra and Hobart=Australia/Sydney; Adelaide=Australia/Adelaide; Darwin=Australia/Darwin; Perth=Australia/Perth
new zealand|Pacific/Auckland
nz|Pacific/Auckland
auckland|Pacific/Auckland
wellington|Pacific/Auckland
christchurch|Pacific/Auckland
queenstown|Pacific/Auckland
chatham islands|Pacific/Chatham
fiji|Pacific/Fiji
papua new guinea|Pacific/Port_Moresby
png|Pacific/Port_Moresby
port moresby|Pacific/Port_Moresby
samoa|Pacific/Apia
tonga|Pacific/Tongatapu
vanuatu|Pacific/Efate
solomon islands|Pacific/Guadalcanal
new caledonia|Pacific/Noumea
noumea|Pacific/Noumea
guam|Pacific/Guam
tahiti|Pacific/Tahiti
hawaii|Pacific/Honolulu
honolulu|Pacific/Honolulu
japan|Asia/Tokyo
tokyo|Asia/Tokyo
osaka|Asia/Tokyo
kyoto|Asia/Tokyo
south korea|Asia/Seoul
korea|Asia/Seoul
seoul|Asia/Seoul
busan|Asia/Seoul
north korea|Asia/Pyongyang
pyongyang|Asia/Pyongyang
china|Asia/Shanghai
beijing|Asia/Shanghai
shanghai|Asia/Shanghai
shenzhen|Asia/Shanghai
guangzhou|Asia/Shanghai
chengdu|Asia/Shanghai
hong kong|Asia/Hong_Kong
macau|Asia/Macau
macao|Asia/Macau
taiwan|Asia/Taipei
taipei|Asia/Taipei
philippines|Asia/Manila
manila|Asia/Manila
singapore|Asia/Singapore
malaysia|Asia/Kuala_Lumpur
kuala lumpur|Asia/Kuala_Lumpur
kl|Asia/Kuala_Lumpur
thailand|Asia/Bangkok
bangkok|Asia/Bangkok
phuket|Asia/Bangkok
vietnam|Asia/Ho_Chi_Minh
hanoi|Asia/Ho_Chi_Minh
ho chi minh city|Asia/Ho_Chi_Minh
saigon|Asia/Ho_Chi_Minh
cambodia|Asia/Phnom_Penh
phnom penh|Asia/Phnom_Penh
laos|Asia/Vientiane
myanmar|Asia/Yangon
burma|Asia/Yangon
indonesia|Jakarta and western Indonesia=Asia/Jakarta; Bali and central Indonesia=Asia/Makassar; Papua and eastern Indonesia=Asia/Jayapura
jakarta|Asia/Jakarta
bali|Asia/Makassar
denpasar|Asia/Makassar
east timor|Asia/Dili
timor leste|Asia/Dili
dili|Asia/Dili
brunei|Asia/Brunei
india|Asia/Kolkata
delhi|Asia/Kolkata
new delhi|Asia/Kolkata
mumbai|Asia/Kolkata
bombay|Asia/Kolkata
bangalore|Asia/Kolkata
bengaluru|Asia/Kolkata
chennai|Asia/Kolkata
hyderabad|Hyderabad, India=Asia/Kolkata; Hyderabad, Pakistan=Asia/Karachi
kolkata|Asia/Kolkata
calcutta|Asia/Kolkata
pune|Asia/Kolkata
sri lanka|Asia/Colombo
colombo|Asia/Colombo
nepal|Asia/Kathmandu
kathmandu|Asia/Kathmandu
bangladesh|Asia/Dhaka
dhaka|Asia/Dhaka
bhutan|Asia/Thimphu
maldives|Indian/Maldives
pakistan|Asia/Karachi
karachi|Asia/Karachi
lahore|Asia/Karachi
islamabad|Asia/Karachi
afghanistan|Asia/Kabul
kabul|Asia/Kabul
iran|Asia/Tehran
tehran|Asia/Tehran
iraq|Asia/Baghdad
baghdad|Asia/Baghdad
kuwait|Asia/Kuwait
saudi arabia|Asia/Riyadh
saudi|Asia/Riyadh
riyadh|Asia/Riyadh
jeddah|Asia/Riyadh
mecca|Asia/Riyadh
qatar|Asia/Qatar
doha|Asia/Qatar
bahrain|Asia/Bahrain
united arab emirates|Asia/Dubai
uae|Asia/Dubai
dubai|Asia/Dubai
abu dhabi|Asia/Dubai
oman|Asia/Muscat
muscat|Asia/Muscat
yemen|Asia/Aden
jordan|Asia/Amman
amman|Asia/Amman
syria|Asia/Damascus
damascus|Asia/Damascus
lebanon|Asia/Beirut
beirut|Asia/Beirut
israel|Asia/Jerusalem
jerusalem|Asia/Jerusalem
tel aviv|Asia/Jerusalem
palestine|Asia/Gaza
gaza|Asia/Gaza
turkey|Europe/Istanbul
turkiye|Europe/Istanbul
istanbul|Europe/Istanbul
ankara|Europe/Istanbul
cyprus|Asia/Nicosia
georgia|Georgia, the country=Asia/Tbilisi; Georgia, the US state=America/New_York
tbilisi|Asia/Tbilisi
armenia|Asia/Yerevan
azerbaijan|Asia/Baku
baku|Asia/Baku
kazakhstan|Asia/Almaty
almaty|Asia/Almaty
astana|Asia/Almaty
uzbekistan|Asia/Tashkent
tashkent|Asia/Tashkent
kyrgyzstan|Asia/Bishkek
tajikistan|Asia/Dushanbe
turkmenistan|Asia/Ashgabat
mongolia|Asia/Ulaanbaatar
ulaanbaatar|Asia/Ulaanbaatar
russia|Moscow=Europe/Moscow; Kaliningrad=Europe/Kaliningrad; Yekaterinburg=Asia/Yekaterinburg; Novosibirsk=Asia/Novosibirsk; Vladivostok=Asia/Vladivostok
moscow|Europe/Moscow
saint petersburg|Europe/Moscow
st petersburg|St Petersburg, Russia=Europe/Moscow; St Petersburg, Florida=America/New_York
vladivostok|Asia/Vladivostok
united kingdom|Europe/London
uk|Europe/London
britain|Europe/London
great britain|Europe/London
england|Europe/London
scotland|Europe/London
wales|Europe/London
northern ireland|Europe/London
london|Europe/London
manchester|Europe/London
liverpool|Europe/London
edinburgh|Europe/London
glasgow|Europe/London
cardiff|Europe/London
belfast|Europe/London
leeds|Europe/London
bristol|Europe/London
oxford|Europe/London
cambridge|Cambridge, England=Europe/London; Cambridge, Massachusetts=America/New_York
birmingham|Birmingham, England=Europe/London; Birmingham, Alabama=America/Chicago
ireland|Europe/Dublin
dublin|Europe/Dublin
cork|Europe/Dublin
portugal|Europe/Lisbon
lisbon|Europe/Lisbon
porto|Europe/Lisbon
azores|Atlantic/Azores
madeira|Atlantic/Madeira
spain|Europe/Madrid
madrid|Europe/Madrid
barcelona|Europe/Madrid
seville|Europe/Madrid
valencia|Europe/Madrid
canary islands|Atlantic/Canary
tenerife|Atlantic/Canary
france|Europe/Paris
paris|Europe/Paris
lyon|Europe/Paris
marseille|Europe/Paris
nice|Europe/Paris
belgium|Europe/Brussels
brussels|Europe/Brussels
netherlands|Europe/Amsterdam
holland|Europe/Amsterdam
amsterdam|Europe/Amsterdam
rotterdam|Europe/Amsterdam
the hague|Europe/Amsterdam
luxembourg|Europe/Luxembourg
germany|Europe/Berlin
berlin|Europe/Berlin
munich|Europe/Berlin
frankfurt|Europe/Berlin
hamburg|Europe/Berlin
cologne|Europe/Berlin
switzerland|Europe/Zurich
zurich|Europe/Zurich
geneva|Europe/Zurich
bern|Europe/Zurich
austria|Europe/Vienna
vienna|Europe/Vienna
italy|Europe/Rome
rome|Europe/Rome
milan|Europe/Rome
venice|Europe/Rome
florence|Europe/Rome
naples|Europe/Rome
malta|Europe/Malta
denmark|Europe/Copenhagen
copenhagen|Europe/Copenhagen
norway|Europe/Oslo
oslo|Europe/Oslo
sweden|Europe/Stockholm
stockholm|Europe/Stockholm
finland|Europe/Helsinki
helsinki|Europe/Helsinki
iceland|Atlantic/Reykjavik
reykjavik|Atlantic/Reykjavik
estonia|Europe/Tallinn
latvia|Europe/Riga
lithuania|Europe/Vilnius
poland|Europe/Warsaw
warsaw|Europe/Warsaw
krakow|Europe/Warsaw
czech republic|Europe/Prague
czechia|Europe/Prague
prague|Europe/Prague
slovakia|Europe/Bratislava
hungary|Europe/Budapest
budapest|Europe/Budapest
slovenia|Europe/Ljubljana
croatia|Europe/Zagreb
zagreb|Europe/Zagreb
serbia|Europe/Belgrade
belgrade|Europe/Belgrade
bosnia|Europe/Sarajevo
montenegro|Europe/Podgorica
north macedonia|Europe/Skopje
albania|Europe/Tirane
greece|Europe/Athens
athens|Europe/Athens
bulgaria|Europe/Sofia
sofia|Europe/Sofia
romania|Europe/Bucharest
bucharest|Europe/Bucharest
moldova|Europe/Chisinau
ukraine|Europe/Kyiv
kyiv|Europe/Kyiv
kiev|Europe/Kyiv
belarus|Europe/Minsk
minsk|Europe/Minsk
egypt|Africa/Cairo
cairo|Africa/Cairo
libya|Africa/Tripoli
tunisia|Africa/Tunis
algeria|Africa/Algiers
morocco|Africa/Casablanca
casablanca|Africa/Casablanca
marrakech|Africa/Casablanca
nigeria|Africa/Lagos
lagos|Africa/Lagos
abuja|Africa/Lagos
ghana|Africa/Accra
accra|Africa/Accra
senegal|Africa/Dakar
dakar|Africa/Dakar
ivory coast|Africa/Abidjan
cameroon|Africa/Douala
ethiopia|Africa/Addis_Ababa
addis ababa|Africa/Addis_Ababa
kenya|Africa/Nairobi
nairobi|Africa/Nairobi
uganda|Africa/Kampala
tanzania|Africa/Dar_es_Salaam
rwanda|Africa/Kigali
somalia|Africa/Mogadishu
sudan|Africa/Khartoum
south africa|Africa/Johannesburg
johannesburg|Africa/Johannesburg
joburg|Africa/Johannesburg
cape town|Africa/Johannesburg
durban|Africa/Johannesburg
pretoria|Africa/Johannesburg
zimbabwe|Africa/Harare
zambia|Africa/Lusaka
botswana|Africa/Gaborone
namibia|Africa/Windhoek
mozambique|Africa/Maputo
angola|Africa/Luanda
madagascar|Indian/Antananarivo
mauritius|Indian/Mauritius
seychelles|Indian/Mahe
democratic republic of the congo|Kinshasa=Africa/Kinshasa; Lubumbashi=Africa/Lubumbashi
united states|Eastern time, like New York=America/New_York; Central time, like Chicago=America/Chicago; Mountain time, like Denver=America/Denver; Arizona=America/Phoenix; Pacific time, like Los Angeles=America/Los_Angeles; Alaska=America/Anchorage; Hawaii=Pacific/Honolulu
usa|Eastern time, like New York=America/New_York; Central time, like Chicago=America/Chicago; Mountain time, like Denver=America/Denver; Arizona=America/Phoenix; Pacific time, like Los Angeles=America/Los_Angeles; Alaska=America/Anchorage; Hawaii=Pacific/Honolulu
us|Eastern time, like New York=America/New_York; Central time, like Chicago=America/Chicago; Mountain time, like Denver=America/Denver; Arizona=America/Phoenix; Pacific time, like Los Angeles=America/Los_Angeles; Alaska=America/Anchorage; Hawaii=Pacific/Honolulu
america|Eastern time, like New York=America/New_York; Central time, like Chicago=America/Chicago; Mountain time, like Denver=America/Denver; Arizona=America/Phoenix; Pacific time, like Los Angeles=America/Los_Angeles; Alaska=America/Anchorage; Hawaii=Pacific/Honolulu
the states|Eastern time, like New York=America/New_York; Central time, like Chicago=America/Chicago; Mountain time, like Denver=America/Denver; Arizona=America/Phoenix; Pacific time, like Los Angeles=America/Los_Angeles; Alaska=America/Anchorage; Hawaii=Pacific/Honolulu
eastern time|America/New_York
us eastern|America/New_York
east coast|America/New_York
central time|America/Chicago
us central|America/Chicago
mountain time|America/Denver
us mountain|America/Denver
pacific time|America/Los_Angeles
us pacific|America/Los_Angeles
west coast|America/Los_Angeles
new york|America/New_York
new york city|America/New_York
nyc|America/New_York
manhattan|America/New_York
brooklyn|America/New_York
boston|America/New_York
massachusetts|America/New_York
washington|Washington DC=America/New_York; Washington state=America/Los_Angeles
washington dc|America/New_York
dc|America/New_York
washington state|America/Los_Angeles
philadelphia|America/New_York
pennsylvania|America/New_York
pittsburgh|America/New_York
baltimore|America/New_York
maryland|America/New_York
virginia|America/New_York
new jersey|America/New_York
connecticut|America/New_York
miami|America/New_York
orlando|America/New_York
florida|America/New_York
tampa|America/New_York
atlanta|America/New_York
charlotte|America/New_York
north carolina|America/New_York
south carolina|America/New_York
ohio|America/New_York
cleveland|America/New_York
columbus|America/New_York
detroit|America/Detroit
michigan|America/Detroit
indianapolis|America/Indiana/Indianapolis
chicago|America/Chicago
illinois|America/Chicago
houston|America/Chicago
dallas|America/Chicago
austin|America/Chicago
san antonio|America/Chicago
texas|America/Chicago
new orleans|America/Chicago
louisiana|America/Chicago
nashville|America/Chicago
tennessee|America/Chicago
memphis|America/Chicago
minneapolis|America/Chicago
minnesota|America/Chicago
wisconsin|America/Chicago
milwaukee|America/Chicago
st louis|America/Chicago
missouri|America/Chicago
kansas city|America/Chicago
oklahoma|America/Chicago
alabama|America/Chicago
iowa|America/Chicago
denver|America/Denver
colorado|America/Denver
salt lake city|America/Denver
utah|America/Denver
albuquerque|America/Denver
new mexico|America/Denver
boise|America/Boise
montana|America/Denver
wyoming|America/Denver
phoenix|America/Phoenix
arizona|America/Phoenix
tucson|America/Phoenix
los angeles|America/Los_Angeles
la|America/Los_Angeles
hollywood|America/Los_Angeles
san francisco|America/Los_Angeles
sf|America/Los_Angeles
silicon valley|America/Los_Angeles
san jose|San Jose, California=America/Los_Angeles; San José, Costa Rica=America/Costa_Rica
san diego|America/Los_Angeles
sacramento|America/Los_Angeles
california|America/Los_Angeles
seattle|America/Los_Angeles
portland|Portland, Oregon=America/Los_Angeles; Portland, Maine=America/New_York
oregon|America/Los_Angeles
las vegas|America/Los_Angeles
vegas|America/Los_Angeles
nevada|America/Los_Angeles
alaska|America/Anchorage
anchorage|America/Anchorage
canada|Toronto and Ottawa=America/Toronto; Vancouver=America/Vancouver; Calgary=America/Edmonton; Winnipeg=America/Winnipeg; Halifax=America/Halifax; St John's=America/St_Johns
toronto|America/Toronto
ottawa|America/Toronto
ontario|America/Toronto
montreal|America/Toronto
quebec|America/Toronto
vancouver|America/Vancouver
british columbia|America/Vancouver
calgary|America/Edmonton
edmonton|America/Edmonton
alberta|America/Edmonton
winnipeg|America/Winnipeg
manitoba|America/Winnipeg
saskatchewan|America/Regina
regina|America/Regina
halifax|America/Halifax
nova scotia|America/Halifax
newfoundland|America/St_Johns
st johns|America/St_Johns
mexico|America/Mexico_City
mexico city|America/Mexico_City
cancun|America/Cancun
tijuana|America/Tijuana
guadalajara|America/Mexico_City
monterrey|America/Monterrey
guatemala|America/Guatemala
belize|America/Belize
el salvador|America/El_Salvador
honduras|America/Tegucigalpa
nicaragua|America/Managua
costa rica|America/Costa_Rica
panama|America/Panama
cuba|America/Havana
havana|America/Havana
jamaica|America/Jamaica
bahamas|America/Nassau
dominican republic|America/Santo_Domingo
puerto rico|America/Puerto_Rico
barbados|America/Barbados
trinidad|America/Port_of_Spain
colombia|America/Bogota
bogota|America/Bogota
venezuela|America/Caracas
caracas|America/Caracas
ecuador|America/Guayaquil
quito|America/Guayaquil
galapagos|Pacific/Galapagos
peru|America/Lima
lima|America/Lima
bolivia|America/La_Paz
la paz|America/La_Paz
chile|America/Santiago
santiago|America/Santiago
easter island|Pacific/Easter
argentina|America/Argentina/Buenos_Aires
buenos aires|America/Argentina/Buenos_Aires
uruguay|America/Montevideo
montevideo|America/Montevideo
paraguay|America/Asuncion
brazil|Sao Paulo, Rio and Brasilia=America/Sao_Paulo; Manaus=America/Manaus; Fernando de Noronha=America/Noronha
sao paulo|America/Sao_Paulo
rio|America/Sao_Paulo
rio de janeiro|America/Sao_Paulo
brasilia|America/Sao_Paulo
manaus|America/Manaus
greenland|America/Nuuk
antarctica|McMurdo=Antarctica/McMurdo; Casey=Antarctica/Casey; Davis=Antarctica/Davis; Mawson=Antarctica/Mawson
"""


class PlaceError(ValueError):
    """A place name that can't be turned into one timezone; the message says why."""


class AmbiguousPlace(PlaceError):
    def __init__(self, place: str, options: list[tuple[str, str]]):
        self.place, self.options = place, options
        super().__init__(f"{place} is ambiguous")


class UnknownPlace(PlaceError):
    def __init__(self, place: str):
        self.place = place
        super().__init__(f"unknown place {place}")


def _normalise(text: str) -> str:
    text = text.lower().replace("&", " and ")
    text = re.sub(r"[.'’]", "", text)
    text = re.sub(r"[^a-z0-9/+\-_ ]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"^(the )?(time )?(in |at )", "", text)
    if text in _PLACE_TABLE():          # "eastern time" is a name in its own right
        return text
    return re.sub(r" (time|time zone|timezone)$", "", text)


@lru_cache(maxsize=1)
def _PLACE_TABLE() -> dict[str, list[tuple[str, str]]]:   # noqa: N802 - behaves like a constant
    table = {}
    for line in _PLACES.strip().splitlines():
        name, _, value = line.partition("|")
        if "=" in value:
            options = [tuple(part.strip().rsplit("=", 1)) for part in value.split(";")]
        else:
            options = [(name.title(), value.strip())]
        table[name.strip()] = options
    return table


@lru_cache(maxsize=1)
def _zone_index() -> tuple[dict[str, str], dict[str, str]]:
    """IANA names by lower-case full name, and by their last part ("new york" -> zone)."""
    full, by_city = {}, {}
    for zone in sorted(available_timezones()):
        full[zone.lower()] = zone
        if "/" in zone and not zone.startswith(("Etc/", "SystemV/", "US/")):
            city = zone.rsplit("/", 1)[1].replace("_", " ").lower()
            # Old aliases like America/Louisville sit beside America/Kentucky/Louisville;
            # the longer name is the current one.
            if city not in by_city or zone.count("/") > by_city[city].count("/"):
                by_city[city] = zone
    return full, by_city


@lru_cache(maxsize=1)
def _option_labels() -> dict[str, str]:
    """The labels of ambiguous names, so "Portland, Maine" picks that option directly."""
    return {_normalise(label): zone for options in _PLACE_TABLE().values() if len(options) > 1
            for label, zone in options}


def _lookup(key: str) -> list[tuple[str, str]] | None:
    """(label, zone) options for a normalised name, or None if it isn't known."""
    if key in _HERE:
        return [(HOME_NAME, HOME_ZONE)]
    if key in _PLACE_TABLE():
        return _PLACE_TABLE()[key]
    if key in _option_labels():
        return [(key.title(), _option_labels()[key])]
    full, by_city = _zone_index()
    if key.replace(" ", "_") in full:
        zone = full[key.replace(" ", "_")]
        return [(zone.rsplit("/", 1)[-1].replace("_", " "), zone)]
    if key in by_city:
        return [(key.title(), by_city[key])]
    return None


def resolve(place: str | None) -> tuple[str, ZoneInfo]:
    """(spoken name, zone) for a place; no place means here in Brisbane."""
    name, key = resolve_key(place)
    return name, ZoneInfo(key)


def resolve_key(place: str | None) -> tuple[str, str]:
    """(spoken name, IANA zone name) for a place; no place means here in Brisbane.

    "City, region" narrows an ambiguous city by its region, and falls back to the region alone for
    towns we don't know. Raises AmbiguousPlace when the name could mean different timezones,
    UnknownPlace when it's not a place we know."""
    raw = (place or "").strip()
    options = _lookup(_normalise(raw))
    if options is None and "," in raw:
        head, tail = (part.strip() for part in raw.split(",", 1))
        head_options = _lookup(_normalise(head))
        tail_options = _lookup(_normalise(tail))
        tail_zones = {zone for _, zone in tail_options or []}
        if head_options:
            narrowed = [o for o in head_options
                        if _normalise(tail) in _normalise(o[0]) or o[1] in tail_zones]
            options = narrowed or head_options
        else:
            options = tail_options
    if options is None:
        raise UnknownPlace(raw)
    if len({zone for _, zone in options}) > 1:
        raise AmbiguousPlace(raw, options)
    if _normalise(raw) in _HERE:
        return HOME_NAME, HOME_ZONE
    return (raw if any(c.isupper() for c in raw) else raw.title()), options[0][1]


# --- Saying times and dates -------------------------------------------------------------------

_ONES = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
         "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen",
         "eighteen", "nineteen"]
_TENS = {20: "twenty", 30: "thirty", 40: "forty", 50: "fifty"}
_ORDINAL_ONES = ["", "first", "second", "third", "fourth", "fifth", "sixth", "seventh", "eighth",
                 "ninth", "tenth", "eleventh", "twelfth", "thirteenth", "fourteenth", "fifteenth",
                 "sixteenth", "seventeenth", "eighteenth", "nineteenth"]
_ORDINAL_TENS = {20: "twentieth", 30: "thirtieth"}


def number_words(n: int) -> str:
    """0-59 in words."""
    if n < 20:
        return _ONES[n]
    tens, ones = divmod(n, 10)
    return _TENS[tens * 10] + (f"-{_ONES[ones]}" if ones else "")


def ordinal_words(n: int) -> str:
    """1-31 as an ordinal: 4 -> fourth, 22 -> twenty-second."""
    if n < 20:
        return _ORDINAL_ONES[n]
    tens, ones = divmod(n, 10)
    return _ORDINAL_TENS[tens * 10] if not ones else f"{_TENS[tens * 10]}-{_ORDINAL_ONES[ones]}"


def _hour_words(hour24: int) -> str:
    if hour24 == 0:
        return "midnight"
    if hour24 == 12:
        return "midday"
    return number_words(hour24 % 12)


def _period(hour24: int) -> str:
    if hour24 < 12:
        return "in the morning"
    if hour24 < 18:
        return "in the afternoon"
    if hour24 < 21:
        return "in the evening"
    return "at night"


def say_time(t: time | datetime) -> str:
    """15:15 -> "quarter past three in the afternoon", 23:45 -> "quarter to midnight"."""
    hour, minute = t.hour, t.minute
    if minute == 0:
        if hour in (0, 12):
            return _hour_words(hour)
        return f"{_hour_words(hour)} o'clock {_period(hour)}"
    if minute <= 30:
        if minute == 15:
            words = "quarter past"
        elif minute == 30:
            words = "half past"
        else:
            unit = "" if minute % 5 == 0 else (" minute" if minute == 1 else " minutes")
            words = f"{number_words(minute)}{unit} past"
        about = hour
    else:
        left = 60 - minute
        if left == 15:
            words = "quarter to"
        else:
            unit = "" if left % 5 == 0 else (" minute" if left == 1 else " minutes")
            words = f"{number_words(left)}{unit} to"
        about = (hour + 1) % 24
    spoken = f"{words} {_hour_words(about)}"
    return spoken if about in (0, 12) else f"{spoken} {_period(hour)}"


def say_date(d: date | datetime) -> str:
    """2026-06-04 -> "Thursday the fourth of June"."""
    return f"{d.strftime('%A')} the {ordinal_words(d.day)} of {d.strftime('%B')}"


def _say_hours(delta: timedelta) -> str:
    hours, rest = divmod(abs(int(delta.total_seconds() // 60)), 60)
    if rest == 30:
        return "half an hour" if hours == 0 else f"{number_words(hours)} and a half hours"
    if rest:
        return f"{number_words(hours)} hours and {number_words(rest)} minutes"
    return "one hour" if hours == 1 else f"{number_words(hours)} hours"


def _difference(name: str, there: datetime, here: datetime) -> str:
    """How far `name` is ahead of or behind Brisbane at this moment."""
    delta = there.utcoffset() - here.utcoffset()
    if not delta:
        return f"{name} is on the same time as {HOME_NAME}."
    side = "ahead of" if delta > timedelta(0) else "behind"
    return f"{name} is {_say_hours(delta)} {side} {HOME_NAME}."


def _zone_note(dt: datetime) -> str:
    offset = dt.utcoffset() or timedelta(0)
    sign = "+" if offset >= timedelta(0) else "-"
    hours, minutes = divmod(abs(int(offset.total_seconds() // 60)), 60)
    utc = f"UTC{sign}{hours}" + (f":{minutes:02d}" if minutes else "")
    dst = ", daylight saving" if dt.dst() else ""
    return f"{dt.tzinfo.key}, {dt.tzname()}, {utc}{dst}"


def _clarify(err: PlaceError, now: datetime) -> str:
    if isinstance(err, AmbiguousPlace):
        lines = [f"'{err.place}' could mean more than one time zone. Ask which one they mean. "
                 "Current times, if they want them all:"]
        for label, zone in err.options:
            local = now.astimezone(ZoneInfo(zone))
            lines.append(f"- {label}: {say_time(local)}, {say_date(local)} "
                         f"[{local:%H:%M %Y-%m-%d}, {zone}]")
        return "\n".join(lines)
    return (f"I don't know a place or time zone called '{err.place}'. Ask them to name a nearby "
            "major city or the country.")


def _now(now: datetime | None) -> datetime:
    return now if now is not None else datetime.now(timezone.utc)


def _needs_tz_database(fn):
    """Say so plainly, rather than fail obscurely, if the timezone database isn't installed."""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except ZoneInfoNotFoundError as err:
            return (f"I can't tell the time right now: the timezone database is missing ({err}). "
                    "It comes from the tzdata Python package, which needs installing on the brain.")
    return wrapper


# --- What the tools call ----------------------------------------------------------------------

@_needs_tz_database
def current_time(place: str | None = None, now: datetime | None = None) -> str:
    """The time and date now, here or in `place`, ready to be said aloud."""
    now = _now(now)
    try:
        name, zone = resolve(place)
    except PlaceError as err:
        return _clarify(err, now)
    here = now.astimezone(ZoneInfo(HOME_ZONE))
    there = now.astimezone(zone)
    text = f"In {name} it's {say_time(there)}, {say_date(there)}."
    if zone.key != HOME_ZONE:
        text += " " + _difference(name, there, here)
        if there.date() != here.date():
            text += f" In {HOME_NAME} it's {say_date(here)}."
    return f"{text} [{there:%H:%M %A %Y-%m-%d}; {_zone_note(there)}]"


_TIME_RE = re.compile(r"^\s*(\d{1,2})(?:[:.h ]?(\d{2}))?\s*(am|pm|a\.m\.|p\.m\.)?\s*$", re.I)


def parse_time(text: str) -> time:
    """'15:00', '3pm', '3:30 pm', '0930', 'noon', 'midnight' -> a time."""
    words = text.strip().lower()
    if words in ("noon", "midday"):
        return time(12, 0)
    if words == "midnight":
        return time(0, 0)
    m = _TIME_RE.match(words)
    if not m:
        raise ValueError(f"can't read the time '{text}'; give it as HH:MM, 24-hour")
    hour, minute = int(m.group(1)), int(m.group(2) or 0)
    suffix = (m.group(3) or "").replace(".", "")
    if suffix:
        if not 1 <= hour <= 12:
            raise ValueError(f"'{text}' isn't a valid time")
        hour = hour % 12 + (12 if suffix == "pm" else 0)
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError(f"'{text}' isn't a valid time")
    return time(hour, minute)


@_needs_tz_database
def convert_time(at: str, from_place: str | None = None, to_place: str | None = None,
                 on: str | None = None, now: datetime | None = None) -> str:
    """What time it is in `to_place` when it's `at` in `from_place` (both default to Brisbane).

    `on` is the date in `from_place` (YYYY-MM-DD); without it, the next time it's `at` there
    (today, or tomorrow if that time has passed), so daylight saving matches that day."""
    now = _now(now)
    try:
        src_name, src = resolve(from_place)
        dst_name, dst = resolve(to_place)
    except PlaceError as err:
        return _clarify(err, now)
    wanted = parse_time(at)
    if on:
        day = date.fromisoformat(on.strip())
    else:
        local_now = now.astimezone(src)
        day = local_now.date()
        if wanted < local_now.time().replace(second=0, microsecond=0):
            day += timedelta(days=1)
    start = datetime.combine(day, wanted, tzinfo=src)
    note = ""
    if start.astimezone(timezone.utc).astimezone(src).replace(tzinfo=None) != start.replace(tzinfo=None):
        note = (f" Note: {say_time(wanted)} doesn't exist in {src_name} that day because the "
                "clocks go forward.")
    end = start.astimezone(dst)
    days = (end.date() - start.date()).days
    when = {1: ", the next day", -1: ", the day before"}.get(days, "")
    text = (f"When it's {say_time(start)} on {say_date(start)} in {src_name}, it's "
            f"{say_time(end)} on {say_date(end)} in {dst_name}{when}.")
    return (f"{text}{note} [{start:%H:%M %Y-%m-%d} {src.key} = {end:%H:%M %Y-%m-%d} {dst.key}]")

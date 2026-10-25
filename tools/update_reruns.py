#!/usr/bin/env python3
"""
원신 출첵 앱의 '복각표' 데이터(checkin/data/reruns.json)를 만든다. GitHub Actions가 하루 한 번 돌린다.

출처: Genshin Impact Wiki(Fandom)의 'Wish/History' 문서. 버전별 기원 목록에서 5성·4성 캐릭터가
언제 픽업됐는지(사실 정보)만 뽑는다. 그림·표 디자인은 가져오지 않는다.
캐릭터의 한국어 이름과 원소는 각 캐릭터 문서에서 한 번만 읽고 checkin/data/characters.json에 기억한다.

앱은 이 파일만 가볍게 받아 '마지막 픽업 뒤 몇 버전 지났는지'를 직접 계산한다.
"""
import html
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

API = "https://genshin-impact.fandom.com/api.php"
UA = "SumiLab-Checkin-Reruns/1.0 (+https://apps.sumi.kr/checkin/)"
ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "checkin/data/reruns.json"
CHARS = ROOT / "checkin/data/characters.json"
# 복각표 얼굴 그림 주소(한국어 이름 → 호요랩 위키의 공식 그림 주소). 그림은 받지 않고 주소만 둔다.
# 앱·웹이 공식 서버에서 바로 받아 보여 주며, config.json의 lobbyOfficialArt가 false면 둘 다 쓰지 않는다.
FACES = ROOT / "checkin/data/faces.json"
HOYOWIKI_LIST = "https://sg-wiki-api.hoyolab.com/hoyowiki/genshin/wapi/get_entry_page_list"
# 상시 기원에서도 나오는 5성. 위키에서 자동으로 가르기 어려워 여기서 관리한다(새로 상시가 되면 추가).
STANDARD = {"Diluc", "Jean", "Keqing", "Mona", "Qiqi", "Tighnari", "Dehya", "Yumemizuki Mizuki"}
# 픽업에 나오지 않는 이름(여행자 등)이나 무기 이름이 섞여도 캐릭터 문서가 아니면 걸러진다.
ELEMENTS = {"Anemo", "Geo", "Electro", "Dendro", "Hydro", "Pyro", "Cryo"}
# 캐릭터 문서에 한국어 이름 칸이 없는 경우.
KO_OVERRIDES = {"Xiao": "소", "Durin": "두린", "Fischl": "피슬"}
# 5.8 다음 버전들은 위키에서 "Luna I" 같은 이름으로 불린다. 순서를 위해 6.0부터 번호를 붙인다.
ROMAN = {"I": 1, "II": 2, "III": 3, "IV": 4, "V": 5, "VI": 6, "VII": 7, "VIII": 8, "IX": 9, "X": 10}


def api(params):
    url = API + "?" + urllib.parse.urlencode({**params, "format": "json", "formatversion": "2"})
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


class Tables(HTMLParser):
    """버전 제목(h3)과 그 아래 표의 행을 차례로 모은다. 칸마다 글자와 링크 제목을 남긴다."""

    def __init__(self):
        super().__init__()
        self.items = []  # ("version", "7.1") 또는 ("row", [cells])
        self.heading = None
        self.depth = 0
        self.row = None
        self.cell = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in ("h2", "h3"):
            self.heading = ""
        if tag == "table":
            self.depth += 1
        if self.depth == 1 and tag == "tr":
            self.row = []
        if self.depth == 1 and tag in ("td", "th") and self.row is not None:
            self.cell = {"text": "", "titles": []}
            self.row.append(self.cell)
        if tag == "a" and self.cell is not None and a.get("title"):
            self.cell["titles"].append(a["title"])

    def handle_endtag(self, tag):
        if tag in ("h2", "h3") and self.heading is not None:
            m = re.search(r"Version (\d+\.\d+)", self.heading)
            luna = re.search(r'Version "?Luna ([IVX]+)"?', self.heading)
            if m:
                self.items.append(("version", m.group(1)))
            elif luna and luna.group(1) in ROMAN:
                self.items.append(("version", f"6.{ROMAN[luna.group(1)] - 1}"))
            self.heading = None
        if self.depth == 1 and tag in ("td", "th"):
            self.cell = None
        if self.depth == 1 and tag == "tr" and self.row is not None:
            self.items.append(("row", self.row))
            self.row = None
        if tag == "table":
            self.depth -= 1

    def handle_data(self, data):
        if self.heading is not None:
            self.heading += data
        if self.cell is not None:
            self.cell["text"] += data


def version_label(v):
    """화면에 보일 이름. 6.x는 위키·게임 이름대로 'Luna I'처럼."""
    major, minor = v.split(".")
    if major == "6":
        roman = {n: r for r, n in ROMAN.items()}[int(minor) + 1]
        return f"Luna {roman}"
    return v


def version_key(v):
    major, minor = v.split(".")
    return int(major), int(minor)


MONTHS = {m: i for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"], 1)}


def end_date(text):
    """'September 23, 2026 — October 13, 2026'에서 종료일(2026-10-13). 없거나 '진행 중'이면 None."""
    found = re.findall(r"(January|February|March|April|May|June|July|August|September|October|November|December) (\d{1,2}), (\d{4})", text)
    if len(found) < 2:
        return None
    month, day, year = found[-1]
    return f"{year}-{MONTHS[month]:02d}-{int(day):02d}"


def load_history():
    text = api({"action": "parse", "page": "Wish/History", "prop": "text"})["parse"]["text"]
    parser = Tables()
    parser.feed(text)
    versions = {}  # 버전 -> 가장 이른 기원 시작일
    rows = []  # (버전, 시작일, 링크 제목들, 종료일 또는 None)
    current = None
    for kind, value in parser.items:
        if kind == "version":
            current = value
            continue
        if current is None:
            continue
        cells = value
        date = next((m.group(0) for c in cells for m in [re.search(r"\d{4}-\d{2}-\d{2}", c["text"])] if m), None)
        if not date:
            continue
        titles = [t for c in cells for t in c["titles"]]
        rows.append((current, date, titles, end_date(" ".join(c["text"] for c in cells))))
        versions[current] = min(versions.get(current, date), date)
    return versions, rows


def featured_stars():
    """픽업에 나온 적 있는 캐릭터 이름: 'Wish/Featured' 문서의 첫 표(5성) 머리글 + 상시 5성, 둘째 표(4성) 머리글."""
    text = api({"action": "parse", "page": "Wish/Featured", "prop": "text"})["parse"]["text"]
    tables = []
    at = 0
    for _ in range(2):
        start = text.index("<table", at)
        table = text[start:text.index("</table>", start)]
        head = table[:table.index("</tr>")]
        tables.append({html.unescape(t) for t in re.findall(r'<a [^>]*title="([^"]+)"', head)})
        at = start + len(table)
    return tables[0] | STANDARD, tables[1] - tables[0] - STANDARD


def fill_info(names, cache):
    """아직 모르는 캐릭터의 한국어 이름·원소를 50명씩 한 번에 읽어 cache에 채운다."""
    missing = sorted(n for n in names if n not in cache)
    for i in range(0, len(missing), 50):
        batch = missing[i:i + 50]
        pages = api({
            "action": "query", "prop": "revisions", "rvprop": "content", "rvslots": "main",
            "titles": "|".join(batch), "redirects": "1",
        })["query"]["pages"]
        for page in pages:
            revs = page.get("revisions")
            if not revs:
                continue
            wikitext = revs[0]["slots"]["main"]["content"]
            element = re.search(r"^\|\s*element\s*=\s*(\w+)", wikitext, re.M)
            ko = re.search(r"^\|\s*ko\s*=\s*(?:\{\{tt\|)?([^|}\n]+)", wikitext, re.M)
            if element and element.group(1) in ELEMENTS:
                title = page["title"]
                # 위키 표기용 기호(//종려// 등)는 뗀다.
                name = KO_OVERRIDES.get(title) or (re.sub(r"[/'\[\]]", "", ko.group(1)).strip() if ko else title)
                cache[title] = {"ko": name, "element": element.group(1)}
        time.sleep(1)  # 위키에 부담을 주지 않게


def hoyowiki_faces(names):
    """호요랩 위키 캐릭터 목록에서 이름이 같은 캐릭터의 얼굴 그림 주소. 실패하면 None(지난 파일을 그대로 둔다)."""
    out = {}
    try:
        for page in range(1, 8):
            body = json.dumps({"filters": [], "menu_id": "2", "page_num": page, "page_size": 50, "use_es": True}).encode()
            req = urllib.request.Request(HOYOWIKI_LIST, data=body, headers={
                "User-Agent": UA, "Content-Type": "application/json", "x-rpc-language": "ko-kr", "Referer": "https://wiki.hoyolab.com/"})
            with urllib.request.urlopen(req, timeout=60) as r:
                items = json.load(r).get("data", {}).get("list", [])
            for it in items:
                if it.get("name") in names and it.get("icon_url", "").startswith("https://"):
                    out[it["name"]] = it["icon_url"]
            if len(items) < 50:
                break
    except Exception as e:
        print("호요랩 위키 얼굴 그림 실패:", e, file=sys.stderr)
        return None
    return out


def write_faces(cache):
    names = {c["ko"] for c in cache.values()}
    faces = hoyowiki_faces(names)
    if faces is None:
        return
    missing = sorted(names - set(faces))
    if missing:
        print("얼굴 그림 없음(원소 아이콘으로 보임):", ", ".join(missing))
    text = json.dumps(dict(sorted(faces.items())), ensure_ascii=False, separators=(",", ":")) + "\n"
    if not FACES.exists() or FACES.read_text() != text:
        FACES.write_text(text)
        print(f"얼굴 그림 {len(faces)}명")


def main():
    versions, rows = load_history()
    if len(versions) < 10:
        sys.exit("버전 목록을 읽지 못했어요(문서 구조가 바뀌었을 수 있음).")
    stars, four_stars = featured_stars()
    if len(four_stars) < 20:
        sys.exit("4성 목록을 읽지 못했어요(문서 구조가 바뀌었을 수 있음).")
    cache = json.loads(CHARS.read_text()) if CHARS.exists() else {}
    fill_info(stars | four_stars, cache)
    last = {}  # 이름 -> (버전, 시작일, 같은 기원의 5성 수)
    first = {}  # 이름 -> 처음 픽업된 버전
    runs = {}  # 이름 -> 혼자 픽업된(집록이 아닌) 기원의 시작일들
    banners = {}  # (시작일, 5성들) -> 픽업 정보. 같은 날 같은 캐릭터 기원(집록 등)이 여러 줄이면 하나로.
    for version, date, titles, end in rows:
        names = [t for t in dict.fromkeys(titles) if t in stars and t in cache]
        # 같은 기원의 픽업 4성(앱의 4성 복각표·알림용, 고를 때만 보인다). 5성 집계와는 따로 센다.
        fours = [t for t in dict.fromkeys(titles) if t in four_stars and t in cache]
        if names and end:
            key = (date, tuple(sorted(names)))
            banners[key] = {"version": version, "start": date, "end": end, "chars": sorted(names), "chronicled": len(names) > 1}
            if fours:
                banners[key]["four"] = sorted(fours)
        for n in fours:
            prev = last.get(n)
            if prev is None or (version_key(version), date) >= (version_key(prev[0]), prev[1]):
                last[n] = (version, date, 0)
            if n not in first or version_key(version) < version_key(first[n]):
                first[n] = version
            runs.setdefault(n, set()).add(date)
        for n in names:
            prev = last.get(n)
            if prev is None or (version_key(version), date) >= (version_key(prev[0]), prev[1]):
                last[n] = (version, date, len(names))
            if n not in first or version_key(version) < version_key(first[n]):
                first[n] = version
            if len(names) == 1:
                runs.setdefault(n, set()).add(date)
    CHARS.write_text(json.dumps(cache, ensure_ascii=False, indent=1, sort_keys=True) + "\n")
    write_faces(cache)

    data = {
        "source": "Genshin Impact Wiki (genshin-impact.fandom.com), CC BY-SA",
        "versions": [{"v": v, "label": version_label(v), "start": versions[v]} for v in sorted(versions, key=version_key)],
        # 최근 픽업과 앞으로의 픽업(시작·종료일). 앱이 '지금 픽업 중'과 '기다리는 캐릭터 알림'에 쓴다. 작게 유지하려고 최근 것만.
        "banners": sorted(banners.values(), key=lambda b: (b["start"], b["chars"]))[-16:],
        "characters": sorted(
            (
                {
                    "name": n,
                    "ko": cache[n]["ko"],
                    "element": cache[n]["element"],
                    "last": v,
                    "lastStart": d,
                    # 한 기원에 5성이 여럿이면 집록(지난 캐릭터 모음) 기원이다.
                    "chronicled": count > 1,
                    "standard": n in STANDARD,
                    # 처음 나온 버전(지금 버전이면 '신규'), 단독 픽업 횟수(1이면 아직 복각 없음).
                    "first": first[n],
                    "runs": len(runs.get(n, ())),
                }
                for n, (v, d, count) in last.items() if n not in four_stars
            ),
            key=lambda c: (version_key(c["last"]), c["name"]),
        ),
        # 픽업 4성. 예전 앱·웹은 이 칸을 모르니 5성 목록(characters)과 따로 둔다. runs는 픽업에 나온 기원 수.
        "fours": sorted(
            (
                {"name": n, "ko": cache[n]["ko"], "element": cache[n]["element"], "last": v, "lastStart": d,
                 "first": first[n], "runs": len(runs.get(n, ()))}
                for n, (v, d, _) in last.items() if n in four_stars
            ),
            key=lambda c: (version_key(c["last"]), c["name"]),
        ),
    }
    # 앱이 받는 파일이라 공백 없이 작게(약 8KB, GitHub Pages가 gzip으로 더 줄인다).
    text = json.dumps(data, ensure_ascii=False, separators=(",", ":")) + "\n"
    if OUT.exists() and OUT.read_text() == text:
        print("변경 없음")
        return
    OUT.write_text(text)
    print(f"갱신: 버전 {len(data['versions'])}개, 캐릭터 {len(data['characters'])}명")


if __name__ == "__main__":
    main()

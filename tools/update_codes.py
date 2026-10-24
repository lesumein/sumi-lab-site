#!/usr/bin/env python3
"""
원신 리딤 코드·이벤트 목록(checkin/data/codes.json, events.json)을 만든다. GitHub Actions가 1시간마다
(금요일 밤 특별 방송 시간대에는 15분마다) 돌린다. 웹앱(apps.sumi.kr/genshin-codes/)이 이 파일을 읽는다.

출처
- 원신 Fandom 위키 'Promotional Code' 문서: 코드, 쓸 수 있는 서버, 보상, 올라온 날, 만료일
- HoYoLAB 공식 코드 목록(공개): 특별 방송 코드. 위키보다 먼저 올라온다.
- HoYoLAB 원신 이벤트 목록(공개)

새 코드가 생기면 웹앱에서 알림을 켠 기기에 웹 푸시를 한 번 보낸다(OneSignal, 키는 ONESIGNAL_API_KEY).
로그인 정보는 어디에도 쓰지 않는다.
"""
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

WIKI = "https://genshin-impact.fandom.com/api.php"
BBS = "https://bbs-api-os.hoyolab.com"
UA = "SumiLab-Checkin-Codes/1.0 (+https://apps.sumi.kr/genshin-codes/)"
BBS_HEADERS = {"x-rpc-client_type": "4", "x-rpc-app_version": "2.50.0", "x-rpc-language": "ko-kr"}
ROOT = Path(__file__).resolve().parent.parent
CODES = ROOT / "checkin/data/codes.json"
EVENTS = ROOT / "checkin/data/events.json"
NAMES = ROOT / "checkin/data/item_names.json"
# 위키에는 아직 유효로 남아 있지만 써 보니 만료된 코드(제보로 하나씩 더한다). 한 줄에 하나, '#' 뒤는 메모.
EXPIRED = ROOT / "checkin/data/expired-codes.txt"
# 만료일을 모르는 코드가 이만큼(일) 지나면 '오래된 코드'로 따로 보여 준다.
OLD_DAYS = 45
WEB = "https://apps.sumi.kr/genshin-codes/"
ONESIGNAL_APP_ID = "b45e38a4-bb0d-44c2-8d58-33869fde35c8"
SERVER_NAMES = {"SEA": "아시아", "NA": "미국", "EU": "유럽", "SAR": "TW·HK·MO"}
# 앱(Redeem.kt)과 같은 기본 이름. 나머지는 위키 아이템 문서의 한국어 표기를 배워 item_names.json에 둔다.
BASE_NAMES = {
    "Primogem": "원석", "Mora": "모라",
    "Hero's Wit": "영웅의 경험", "Adventurer's Experience": "모험가의 경험", "Wanderer's Advice": "방랑자의 경험",
    "Mystic Enhancement Ore": "정제용 마법 광석", "Fine Enhancement Ore": "정제용 양질 광석", "Enhancement Ore": "정제용 광석",
    "Fragile Resin": "취약한 레진",
}
# 공식 목록은 보상을 그림으로만 준다. 자주 나오는 그림만 이름을 붙인다(파일 이름 앞부분).
OFFICIAL_ICONS = {"150a941de99e": "원석", "503abf5f2f2c": "모라", "d3eb1267f27b": "정제용 마법 광석", "46de1e881b5d": "영웅의 경험"}
KST = timezone(timedelta(hours=9))
UTC8 = timezone(timedelta(hours=8))


def get(url, headers=None):
    req = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def wiki(params):
    return get(WIKI + "?" + urllib.parse.urlencode({**params, "format": "json", "formatversion": "2"}))


# ---------- 위키 코드 표 ----------

def code_rows(text):
    """{{Code Row|...}} 틀마다 인자 목록. 틀 안의 {{ }}, [[ ]]는 한 덩어리로 둔다."""
    rows = []
    start = text.find("{{Code Row")
    while start >= 0:
        head = start + len("{{Code Row")
        if text.startswith("/", head):
            start = text.find("{{Code Row", head)
            continue
        params, cur, depth, i = [], [], 0, head
        while i < len(text):
            two = text[i:i + 2]
            if two in ("{{", "[["):
                depth += 1; cur.append(two); i += 2; continue
            if two == "}}" and depth == 0:
                break
            if two in ("}}", "]]"):
                depth -= 1; cur.append(two); i += 2; continue
            if text[i] == "|" and depth == 0:
                params.append("".join(cur).strip()); cur = []
            else:
                cur.append(text[i])
            i += 1
        params.append("".join(cur).strip())
        rows.append(params[1:])
        start = text.find("{{Code Row", i)
    return rows


def clean(text):
    for mark in ("[[", "]]", "{{", "}}"):
        text = text.replace(mark, "")
    return text.strip()


def wiki_codes():
    text = wiki({"action": "parse", "page": "Promotional_Code", "prop": "wikitext"})["parse"]["wikitext"]
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    out = []
    for params in code_rows(text):
        if any(p.replace(" ", "").lower() == "notacode=yes" for p in params):
            continue
        pos = [p for p in params if not re.match(r"^[A-Za-z_]+\s*=", p)]
        codes = [c.strip().upper() for c in (pos[0] if pos else "").split(";")]
        codes = [c for c in codes if re.fullmatch(r"[A-Z0-9]{6,20}", c)]
        if not codes:
            continue
        servers = [s.upper() for s in re.split(r"[,/\s]+", pos[1] if len(pos) > 1 else "") if s]
        reward = pos[2].strip() if len(pos) > 2 and "*" in pos[2] else None
        found = re.search(r"\d{4}-\d{2}-\d{2}", pos[3]) if len(pos) > 3 else None
        expiry = re.search(r"\d{4}-\d{2}-\d{2}", pos[4]) if len(pos) > 4 else None
        out.append({
            "codes": codes, "servers": servers, "reward": reward,
            "discovered": found.group(0) if found else None, "expiry": expiry.group(0) if expiry else None,
        })
    return out


def server_label(servers):
    if not servers or "G" in servers or "A" in servers:
        return "전 서버"
    names = [SERVER_NAMES[s] for s in servers if s in SERVER_NAMES]
    return "·".join(names) or None  # 중국 서버 전용이면 None(빼기)


# ---------- 아이템 한국어 이름 ----------

def ko_name(content):
    for line in content.splitlines():
        line = line.strip()
        if line.startswith("|ko") and line.split("=", 1)[0].strip() == "|ko":
            value = line.split("=", 1)[1].strip()
            # {{tt|절운|絕雲}}처럼 풀이가 붙은 틀은 앞 글자만
            value = re.sub(r"\{\{[^|{}]*\|([^|{}]*)(\|[^{}]*)?\}\}", r"\1", value)
            return clean(value) or None
    return None


def learn_names(items, names):
    todo = [n for n in items if n not in BASE_NAMES and n not in names]
    for i in range(0, len(todo), 40):
        batch = todo[i:i + 40]
        try:
            q = wiki({"action": "query", "prop": "revisions", "rvprop": "content", "rvslots": "main",
                      "redirects": "1", "titles": "|".join(batch)})["query"]
        except Exception as e:
            print("이름 찾기 실패:", e, file=sys.stderr)
            return
        back = {}
        for key in ("normalized", "redirects"):
            for m in q.get(key, []):
                back[m["to"]] = back.get(m["from"], m["from"])
        for page in q.get("pages", []):
            rev = (page.get("revisions") or [{}])[0]
            ko = ko_name(rev.get("slots", {}).get("main", {}).get("content", ""))
            if ko:
                names[back.get(page["title"], page["title"])] = ko


def reward_parts(raw):
    for part in raw.split(";"):
        name = clean(part.split("*")[0])
        count = part.split("*", 1)[1].strip().replace(",", "") if "*" in part else ""
        if name and count.isdigit():
            yield name, int(count)


def reward_text(raw, names):
    items = [(BASE_NAMES.get(n) or names.get(n) or n, c) for n, c in reward_parts(raw)]
    shown = " · ".join(f"{n} ×{c:,}" for n, c in items[:3])
    return shown + (f" 외 {len(items) - 3}종" if len(items) > 3 else "") if items else None


# ---------- 공식 목록, 이벤트 ----------

def official_codes():
    data = get(f"{BBS}/community/painter/wapi/circle/channel/guide/material?game_id=2", BBS_HEADERS)
    out = []
    for module in data.get("data", {}).get("modules", []):
        for b in (module.get("exchange_group") or {}).get("bonuses", []):
            code = (b.get("exchange_code") or "").upper()
            if b.get("code_status") != "ON" or not code:
                continue
            items = []
            for icon in b.get("icon_bonuses", []):
                key = icon.get("icon_url", "").rsplit("/", 1)[-1][:12]
                if key in OFFICIAL_ICONS:
                    items.append(f"{OFFICIAL_ICONS[key]} ×{icon.get('bonus_num', 0):,}")
            offline = b.get("offline_at")
            expiry = datetime.fromtimestamp(int(offline), UTC8).date().isoformat() if offline else None
            out.append({"code": code, "reward": " · ".join(items) or None, "expiry": expiry})
    return out


def events():
    data = get(f"{BBS}/community/community_contribution/wapi/event/list?gids=2&size=20", BBS_HEADERS)
    now = int(time.time())
    out = []
    for e in data.get("data", {}).get("list", []):
        end = int(e.get("end") or 0)
        if e.get("status") != 3 and not (int(e.get("start") or 0) <= now and (end == 0 or now < end)):
            continue
        if end and end <= now:
            continue
        path = e.get("web_path") or ""
        url = path if path.startswith("http") else "https://www.hoyolab.com" + path
        out.append({"id": str(e.get("id")), "name": (e.get("name") or "").strip(), "end": end,
                    "banner": e.get("banner_url") or "", "url": url})
    out.sort(key=lambda e: e["end"] or 1 << 62)
    return out


# ---------- 알림 ----------

def notify(new):
    """새 코드를 웹 푸시로 한 번 알린다(OneSignal). 누르면 웹앱이 열린다. 키는 저장소 Secret(ONESIGNAL_API_KEY)에만 있다."""
    key = os.environ.get("ONESIGNAL_API_KEY")
    if not key or not new:
        return
    lines = [f"{c['code']} · {c['server']}" + (f" · {c['reward']}" if c.get("reward") else "") for c in new]
    title = f"새 리딤 코드 {len(new)}개" + (" (방송)" if any(c.get("live") for c in new) else "")
    message = "\n".join(lines)
    body = {
        "app_id": ONESIGNAL_APP_ID,
        "target_channel": "push",
        "included_segments": ["Total Subscriptions"],
        # 영어(en) 내용은 필수라 같은 한국어 문장을 넣는다.
        "headings": {"en": title, "ko": title},
        "contents": {"en": message, "ko": message},
        "url": WEB,
        "chrome_web_icon": WEB + "icon-192.png",
    }
    req = urllib.request.Request(
        "https://api.onesignal.com/notifications?c=push", data=json.dumps(body, ensure_ascii=False).encode(), method="POST",
        headers={"Content-Type": "application/json", "Authorization": f"Key {key}", "User-Agent": UA},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            result = json.load(r)
        print("알림 보냄:", title, "· 받는 기기 수 확인은 OneSignal 대시보드에서", "· 오류" if result.get("errors") else "")
    except Exception as e:
        print("알림 실패:", e, file=sys.stderr)


def expired_reported():
    """써 보니 만료된 코드(대문자). 파일이 없으면 빈 집합."""
    if not EXPIRED.exists():
        return set()
    return {line.split("#", 1)[0].strip().upper() for line in EXPIRED.read_text().splitlines() if line.split("#", 1)[0].strip()}


def main():
    today = datetime.now(UTC8).date()
    names = json.loads(NAMES.read_text()) if NAMES.exists() else {}
    previous = json.loads(CODES.read_text()) if CODES.exists() else None

    try:
        rows = wiki_codes()
    except Exception as e:
        print("위키 실패:", e, file=sys.stderr)
        rows = None
    try:
        official = official_codes()
    except Exception as e:
        print("공식 목록 실패:", e, file=sys.stderr)
        official = None
    if rows is None and official is None:
        sys.exit("코드 목록을 하나도 받지 못했어요")
    # 한쪽이 실패하면 지난 결과를 그대로 둔다(코드가 사라졌다 다시 생겨 알림이 또 가지 않게).
    if rows is None or official is None:
        print("한쪽 출처가 실패해 이번에는 코드 목록을 바꾸지 않아요")
        codes = previous["codes"] if previous else []
    else:
        rows = [r for r in rows if not r["expiry"] or date.fromisoformat(r["expiry"]) >= today]
        learn_names({n for r in rows if r["reward"] for n, _ in reward_parts(r["reward"])}, names)
        known = {c for r in rows for c in r["codes"]}
        live = {o["code"] for o in official}
        codes = [{"code": o["code"], "server": "전 서버", "reward": o["reward"], "discovered": None,
                  "expiry": o["expiry"], "live": True} for o in official if o["code"] not in known
                 and (not o["expiry"] or date.fromisoformat(o["expiry"]) >= today)]
        for r in rows:
            label = server_label(r["servers"])
            if label is None:
                continue
            codes.append({"code": r["codes"][0], "server": label, "reward": reward_text(r["reward"], names) if r["reward"] else None,
                          "discovered": r["discovered"], "expiry": r["expiry"], "live": any(c in live for c in r["codes"])})
        seen = set()
        codes = [c for c in codes if not (c["code"] in seen or seen.add(c["code"]))]
        reported = expired_reported()
        codes = [c for c in codes if c["code"] not in reported]
        for c in codes:
            c["old"] = (not c["expiry"] and not c["live"] and c["discovered"] is not None
                        and (today - date.fromisoformat(c["discovered"])).days > OLD_DAYS)

    try:
        event_list = events()
    except Exception as e:
        print("이벤트 실패:", e, file=sys.stderr)
        event_list = json.loads(EVENTS.read_text()) if EVENTS.exists() else []

    old = {c["code"] for c in previous["codes"]} if previous else None
    new = [c for c in codes if old is not None and c["code"] not in old]
    if old is None:
        print("처음 만드는 목록이라 알림은 보내지 않아요")

    NAMES.write_text(json.dumps(dict(sorted(names.items())), ensure_ascii=False, indent=0) + "\n")
    # 바뀐 게 없으면 파일도 그대로 두어 커밋이 생기지 않게 한다(시각을 넣지 않는다).
    CODES.write_text(json.dumps({"codes": codes}, ensure_ascii=False, separators=(",", ":")) + "\n")
    EVENTS.write_text(json.dumps(event_list, ensure_ascii=False, separators=(",", ":")) + "\n")
    print(f"코드 {len(codes)}개(새 코드 {len(new)}개), 이벤트 {len(event_list)}개")
    notify(new)


if __name__ == "__main__":
    main()

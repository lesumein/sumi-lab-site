#!/usr/bin/env python3
"""
확인용 계정으로 리딤 응답이 코드 상태에 따라 갈리는지 시험한다(수동 실행 전용).
쿠키는 저장소 Secret(HOYO_TEST_COOKIE_TOKEN, HOYO_TEST_ACCOUNT_MID)에서만 읽고 출력하지 않는다.
UID도 앞뒤만 남기고 가린다.
"""
import json
import os
import sys
import time
import urllib.parse
import urllib.request

UA = "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Mobile Safari/537.36"


def cookie():
    token = os.environ.get("HOYO_TEST_COOKIE_TOKEN", "").strip()
    mid = os.environ.get("HOYO_TEST_ACCOUNT_MID", "").strip()
    if not token or not mid:
        sys.exit("HOYO_TEST_COOKIE_TOKEN / HOYO_TEST_ACCOUNT_MID 시크릿이 없어요")
    return f"cookie_token_v2={token}; account_mid_v2={mid}"


def get(url, ck):
    req = urllib.request.Request(url, headers={"Cookie": ck, "User-Agent": UA, "Referer": "https://genshin.hoyoverse.com/"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def mask(uid):
    uid = str(uid)
    return uid[:2] + "*" * max(0, len(uid) - 4) + uid[-2:]


def main():
    ck = cookie()
    roles = get("https://api-account-os.hoyoverse.com/account/binding/api/getUserGameRolesByCookieToken?game_biz=hk4e_global&lang=ko", ck)
    print("캐릭터 목록 응답:", roles.get("retcode"), roles.get("message"))
    found = (roles.get("data") or {}).get("list") or []
    if not found:
        sys.exit("이 계정에 원신 캐릭터가 없거나 쿠키가 맞지 않아요")
    role = found[0]
    print(f"캐릭터: {role.get('region')} · UID {mask(role.get('game_uid'))} · 모험 등급 {role.get('level')}")
    codes = sys.argv[1:] or ["SUMILABNOTREAL1", "BALLETCOLLAB"]
    for i, code in enumerate(codes):
        if i:
            time.sleep(6)
        q = urllib.parse.urlencode({"uid": role["game_uid"], "region": role["region"], "lang": "ko", "cdkey": code,
                                    "game_biz": "hk4e_global", "sLangKey": "ko-kr"})
        r = get("https://sg-hk4e-api.hoyoverse.com/common/apicdkey/api/webExchangeCdkey?" + q, ck)
        print(f"{code}: retcode {r.get('retcode')} · {r.get('message')}")


if __name__ == "__main__":
    main()

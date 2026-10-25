#!/usr/bin/env python3
"""
원신 출첵 앱 알림의 '큰 캐릭터 그림'용 얼굴 위치(checkin/data/portraits.json)를 만든다. GitHub Actions가 하루 한 번 돌린다.

호요랩 위키의 캐릭터 세로 그림(갤러리)에서 얼굴이 있는 곳을 찾아 좌표만 적는다. 그림은 저장하지 않는다.
앱은 공식 서버에서 그림을 받아 이 좌표로 잘라 쓰고, 좌표가 없는 캐릭터는 지금처럼 작은 얼굴 동그라미를 쓴다.
config.json의 lobbyOfficialArt가 false면 앱이 그림 자체를 쓰지 않는다.

얼굴 찾기: deepghs/anime_face_detection(MIT)의 face_detect_v1.4_s. 그림 속 얼굴 위치만 찾는 인식 모델이다.
얼굴이 가면 등으로 가려 못 찾으면(사이노 등) 같은 곳의 anime_head_detection(MIT) head_detect_v2.0_s로 머리를 찾아
얼굴 자리로 환산한다. 앱은 어느 쪽이든 같은 크기로 자른다.
이미 찾은 캐릭터는 그림 주소가 바뀌지 않는 한 다시 찾지 않는다(새 캐릭터·바뀐 그림만 돈다).

필요한 것: pip install onnxruntime numpy pillow
"""
import hashlib
import io
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

import numpy as np
import onnxruntime as ort
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "checkin/data/portraits.json"
UA = "SumiLab-Checkin-Portraits/1.0 (+https://apps.sumi.kr/checkin/)"
LIST = "https://sg-wiki-api.hoyolab.com/hoyowiki/genshin/wapi/get_entry_page_list"
PAGE = "https://sg-wiki-api-static.hoyolab.com/hoyowiki/genshin/wapi/entry_page?entry_page_id="
WIKI_HEADERS = {"User-Agent": UA, "x-rpc-language": "ko-kr", "Referer": "https://wiki.hoyolab.com/"}
# 받은 모델이 시험한 그것과 같은지 확인한다(버전 고정 + 해시 확인).
HF = "https://huggingface.co/deepghs/"
FACE_MODEL = (HF + "anime_face_detection/resolve/784dc4c0bb692351ddcdbe6131a050b17d3025d5/face_detect_v1.4_s/model.onnx",
              "403b5bc93b6ff789b7d183418df4a1364049bac00c24acd927604a7ff6891483",
              Path(os.environ.get("FACE_MODEL", ROOT / ".cache/face_detect_v1.4_s.onnx")))
HEAD_MODEL = (HF + "anime_head_detection/resolve/06604feee81983792a57c21081e539c0ae229833/head_detect_v2.0_s/model.onnx",
              "6679f9b71192298bbf174d82e9e5581c3237b0c3dc67deace7cdbf686b070a00",
              Path(os.environ.get("HEAD_MODEL", ROOT / ".cache/head_detect_v2.0_s.onnx")))
# 이보다 확신이 낮거나, 얼굴이 그림 아래쪽에서 잡히면 좌표를 적지 않는다(앱은 작은 얼굴 동그라미를 쓴다).
# 모델 권장 기준은 0.31이고, 시험한 122명은 모두 0.77 이상이었다.
MIN_SCORE = 0.5
MAX_FACE_Y = 0.6


def fetch(url, data=None, headers=None, timeout=60):
    url = "".join(c if ord(c) < 128 else urllib.parse.quote(c) for c in url)  # 주소에 한자 이름이 섞여 있다
    req = urllib.request.Request(url, data=data, headers=headers or WIKI_HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def characters():
    """호요랩 위키 캐릭터 목록: 한국어 이름 → 문서 번호."""
    out = {}
    for page in range(1, 10):
        body = json.dumps({"filters": [], "menu_id": "2", "page_num": page, "page_size": 50, "use_es": True}).encode()
        items = json.loads(fetch(LIST, body, {**WIKI_HEADERS, "Content-Type": "application/json"})).get("data", {}).get("list", [])
        for it in items:
            if it.get("name") and it.get("entry_page_id"):
                out[it["name"]] = it["entry_page_id"]
        if len(items) < 50:
            break
    return out


def gallery_pic(entry_id):
    """캐릭터 문서의 갤러리 세로 그림 주소(없으면 None)."""
    page = json.loads(fetch(PAGE + entry_id)).get("data", {}).get("page", {})
    for module in page.get("modules", []):
        for comp in module.get("components", []):
            if comp.get("component_id") == "gallery_character":
                pic = json.loads(comp.get("data") or "{}").get("pic", "")
                return pic if pic.startswith("https://") else None
    return None


def load_model(spec):
    url, sha, path = spec
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(fetch(url, headers={"User-Agent": UA}, timeout=300))
    if hashlib.sha256(path.read_bytes()).hexdigest() != sha:
        path.unlink()
        sys.exit(f"인식 모델 파일이 예상과 달라요: {path.name}")
    return ort.InferenceSession(str(path), providers=["CPUExecutionProvider"])


def head_to_face(head):
    """머리 상자를 얼굴 상자로 환산한다(얼굴과 머리가 다 잡히는 캐릭터들에서 잰 비율: 폭 0.62배, 위에서 70% 높이가 얼굴 중심)."""
    left, top, right, bottom = head
    cx, cy = (left + right) / 2, top + (bottom - top) * 0.7
    half_w, half_h = (right - left) * 0.31, (bottom - top) * 0.28
    return [round(min(1.0, max(0.0, v)), 4) for v in (cx - half_w, cy - half_h, cx + half_w, cy + half_h)]


def find_face(session, image):
    """가장 확실한 얼굴(머리 모델이면 머리) 하나: (점수, [왼, 위, 오른, 아래]) — 그림 크기에 대한 비율."""
    w, h = image.size
    scale = 640 / max(w, h)
    nw, nh = max(32, int(w * scale) // 32 * 32), max(32, int(h * scale) // 32 * 32)
    x = np.asarray(image.convert("RGB").resize((nw, nh)), dtype=np.float32).transpose(2, 0, 1)[None] / 255
    out = session.run(None, {session.get_inputs()[0].name: x})[0][0]
    if out.shape[0] < out.shape[1]:
        out = out.T  # (후보, [cx, cy, w, h, 점수])
    best = out[int(np.argmax(out[:, 4:].max(axis=1)))]
    cx, cy, bw, bh = (float(v) for v in best[:4])
    box = [(cx - bw / 2) / nw, (cy - bh / 2) / nh, (cx + bw / 2) / nw, (cy + bh / 2) / nh]
    return float(best[4:].max()), [round(min(1.0, max(0.0, v)), 4) for v in box]


def confident(score, face):
    return score >= MIN_SCORE and (face[1] + face[3]) / 2 <= MAX_FACE_Y


def main():
    old = json.loads(OUT.read_text()) if OUT.exists() else {}
    try:
        chars = characters()
    except Exception as e:
        sys.exit(f"호요랩 위키 목록 실패(지난 파일을 그대로 둠): {e}")
    if len(chars) < 50:
        sys.exit("캐릭터 목록이 너무 짧아요(지난 파일을 그대로 둠).")

    faces = heads = None
    result, skipped = {}, []
    for name, entry_id in sorted(chars.items()):
        try:
            pic = gallery_pic(entry_id)
        except Exception as e:
            if name in old:
                result[name] = old[name]  # 이번에 못 읽었으면 지난 값을 둔다
            print(f"{name}: 문서 읽기 실패 {e}", file=sys.stderr)
            continue
        if not pic:
            skipped.append(name)
            continue
        if old.get(name, {}).get("pic") == pic:
            result[name] = old[name]
            continue
        faces = faces or load_model(FACE_MODEL)
        try:
            image = Image.open(io.BytesIO(fetch(pic, timeout=120)))
            score, face = find_face(faces, image)
            if not confident(score, face):
                heads = heads or load_model(HEAD_MODEL)
                head_score, head = find_face(heads, image)
                print(f"{name}: 얼굴이 가려져 있어 머리로 찾음({score:.2f} → {head_score:.2f})")
                score, face = head_score, head_to_face(head)
        except Exception as e:
            print(f"{name}: 그림 처리 실패 {e}", file=sys.stderr)
            continue
        if not confident(score, face):
            print(f"{name}: 얼굴을 확실히 못 찾음({score:.2f}) — 작은 얼굴 그림을 씀")
            continue
        result[name] = {"pic": pic, "face": face}
        print(f"{name}: 얼굴 찾음({score:.2f})")
        time.sleep(0.5)  # 위키에 부담을 주지 않게

    if skipped:
        print("세로 그림 없음:", ", ".join(skipped))
    text = json.dumps(dict(sorted(result.items())), ensure_ascii=False, separators=(",", ":")) + "\n"
    if not OUT.exists() or OUT.read_text() != text:
        OUT.write_text(text)
        print(f"얼굴 위치 {len(result)}명 저장")
    else:
        print("변경 없음")


if __name__ == "__main__":
    main()

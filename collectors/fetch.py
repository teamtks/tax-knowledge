#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""必要になった資料を、その場で保管庫に取り込む。

なぜこれがあるのか
------------------
定期収集だけでは、**使う場面になって初めて足りないと分かる資料**が必ず出る。
そのときに「保管庫に未収録」と書いて止まるのでは、根拠のない回答と変わらない。
**足りないと分かったら、その場で取りに行き、取り込んでから引用する。**

取り込んだものは `config/wanted.json` にも記録する。
定期収集（GitHub Actions）がこのリストを毎回読み、改正があれば更新する。
一度取りに行ったものは、以後ずっと最新に保たれる。

使い方
------
    # 国税庁・審判所のページ（個別通達・質疑応答事例・タックスアンサーなど）
    python collectors/fetch.py page <URL> [--種別 個別通達] [--名称 "..."]

    # 法令を丸ごと（e-Gov）。法令名で指定すれば law_id を調べて登録する
    python collectors/fetch.py law 民法

    # 税務訴訟資料（判決）の全文。順号で指定する
    python collectors/fetch.py sosho 13918

    # wanted.json に載っているものをすべて取り直す（定期収集から呼ぶ）
    python collectors/fetch.py wanted
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import subprocess
import sys
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _web as W                                            # noqa: E402

ROOT = W.ROOT
WANTED = os.path.join(ROOT, "config", "wanted.json")

# 取り込み先。URL の形から決める。
_振り分け = [
    (r"/law/tsutatsu/kobetsu/([a-z\-]+)/", "kobetsu", "個別通達"),
    (r"/law/shitsugi/([a-z]+)/", "qa", "質疑応答事例"),
    (r"/law/bunshokaito/", "bunsho", "文書回答事例"),
    (r"/taxes/shiraberu/taxanswer/([a-z]+)/", "taxanswer", "タックスアンサー"),
    (r"/law/jimu-unei/", "jimu", "事務運営指針"),
    (r"kfs\.go\.jp/service/JP/", "saiketsu/zenbun", "公表裁決事例"),
]


def _wanted() -> dict:
    if os.path.exists(WANTED):
        with open(WANTED, encoding="utf-8") as f:
            return json.load(f)
    return {"_説明": "必要になって取り込んだ資料。定期収集が毎回取り直し、改正を反映する。",
            "page": [], "law": [], "sosho": []}


def _記録(kind: str, key: str, 備考: str = ""):
    w = _wanted()
    lst = w.setdefault(kind, [])
    if not any(x["key"] == key for x in lst):
        lst.append({"key": key, "追加日": datetime.date.today().isoformat(), "備考": 備考})
        W.write_json_if_changed(WANTED, w)


# ------------------------------------------------------------------ page

def 振り分け(url: str) -> tuple[str, str, str]:
    for pat, d, 種 in _振り分け:
        m = re.search(pat, url)
        if m:
            sub = m.group(1) if m.groups() else ""
            return d, sub, 種
    return "nta_pages", "", "その他"


def _id(url: str) -> str:
    """URL から重複しない名前を作る。`.../sozoku/850605/01.htm` → `sozoku-850605-01`"""
    p = urllib.parse.urlparse(url).path
    p = re.sub(r"^/(law/tsutatsu/kobetsu|law/shitsugi|taxes/shiraberu/taxanswer|law)/", "", p)
    p = re.sub(r"\.html?$", "", p).strip("/")
    return W.safe_name(p.replace("/", "-"))


def page(url: str, 種別: str | None = None, 名称: str | None = None,
         同じ階層も: bool = True, 記録: bool = True) -> list[str]:
    """1ページ（と、同じ階層の続きのページ）を取り込む。書いたファイルのパスを返す。"""
    d, sub, 種 = 振り分け(url)
    種 = 種別 or 種
    h, final = W.get(url)
    題 = 名称 or W.title(h)
    書いた = []

    pages = [(final, h)]
    if 同じ階層も:
        # 個別通達は 01.htm, 02.htm … と1つの通達が複数ページに分かれている。
        # 同じディレクトリにある連番のページを全部取る。
        base = final.rsplit("/", 1)[0] + "/"
        seen = {final}
        for u, _t in W.links(h, final):
            if u.startswith(base) and re.search(r"/\d{2}\.html?$", u) and u not in seen:
                seen.add(u)
                h2, f2 = W.get(u)
                pages.append((f2, h2))

    for u, hh in pages:
        本文 = W.strip_tags(W.nta_body(hh))
        本文 = re.sub(r"\n{3,}", "\n\n", 本文)
        小題 = W.title(hh)
        md = "\n".join([
            f"# {小題 or 題}",
            "",
            f"> 種別: {種}" + (f"／{sub}" if sub else ""),
            f"> 元の資料: {題}" if 小題 and 小題 != 題 else "> ",
            W.出典(u, "国税不服審判所" if "kfs.go.jp" in u else "国税庁"),
            "",
            本文,
            "",
        ])
        path = os.path.join(ROOT, d, sub, _id(u) + ".md") if sub else os.path.join(ROOT, d, _id(u) + ".md")
        st = W.write_if_changed(path, md)
        書いた.append(os.path.relpath(path, ROOT).replace("\\", "/"))
        print(f"  {st}  {書いた[-1]}  ({小題[:40]})")
    if 記録:
        _記録("page", final, f"{種}: {題}")
    return 書いた


# ------------------------------------------------------------------ law

def law(名前: str) -> str:
    """法令を config/laws.json に登録し、収集する。"""
    cfg_p = os.path.join(ROOT, "config", "laws.json")
    with open(cfg_p, encoding="utf-8") as f:
        cfg = json.load(f)
    hit = next((x for x in cfg["laws"] if x["法令名"] == 名前), None)
    if hit is None:
        url = "https://laws.e-gov.go.jp/api/2/laws?law_title=" + urllib.parse.quote(名前)
        raw, _ = W.fetch(url)
        js = json.loads(raw.decode("utf-8"))
        cand = [x for x in js.get("laws", [])
                if x.get("revision_info", {}).get("law_title") == 名前]
        if not cand:
            raise SystemExit(f"e-Gov に「{名前}」が完全一致で見つかりません。")
        lid = cand[0]["law_info"]["law_id"]
        hit = {"法令名": 名前, "law_id": lid, "略称": 名前, "有効": True,
               "_備考": f"{datetime.date.today()} 必要になって追加（collectors/fetch.py）"}
        cfg["laws"].append(hit)
    elif not hit.get("有効"):
        hit["有効"] = True
        hit["_備考"] = (hit.get("_備考", "") + f"／{datetime.date.today()} 必要になって有効化").strip("／")
    W.write_json_if_changed(cfg_p, cfg)
    subprocess.run([sys.executable, os.path.join(ROOT, "collectors", "egov_law.py"),
                    "--law", hit["law_id"]], check=True)
    _記録("law", 名前)
    return hit["law_id"]


# ------------------------------------------------------------------ sosho

def sosho(順号: str) -> str:
    """税務訴訟資料（課税関係判決）の全文を取り込む。目次（hanrei/index.json）から PDF を引く。"""
    idx_p = os.path.join(ROOT, "hanrei", "index.json")
    if not os.path.exists(idx_p):
        raise SystemExit("hanrei/index.json がありません。先に collectors/nta_sosho.py で目次を作ってください。")
    with open(idx_p, encoding="utf-8") as f:
        idx = json.load(f)
    e = next((x for x in idx["判決"] if x["順号"] == str(順号)), None)
    if e is None:
        raise SystemExit(f"順号 {順号} は目次にありません。")
    raw, _ = W.fetch(e["pdf"])
    text = _pdf_text(raw)
    md = "\n".join([
        f"# 税務訴訟資料 第{e.get('号', '')}号 順号{e['順号']}　{e.get('事件', '')}",
        "",
        f"- 裁判所: {e.get('裁判所', '')}",
        f"- 判決日: {e.get('判決日', '')}",
        f"- 税目: {e.get('税目', '')}",
        f"- 原典: {e['pdf']}",
        "",
        W.出典(e["pdf"], "国税庁 税務大学校"),
        "",
        "---",
        "",
        text,
        "",
    ])
    path = os.path.join(ROOT, "hanrei", "sosho", e["年"], f"{e['順号']}.md")
    st = W.write_if_changed(path, md)
    print(f"  {st}  {os.path.relpath(path, ROOT)}")
    _記録("sosho", str(順号), e.get("事件", ""))
    return path


def _pdf_text(raw: bytes) -> str:
    try:
        import fitz                                        # PyMuPDF（手元）
        doc = fitz.open(stream=raw, filetype="pdf")
        return "\n".join(p.get_text() for p in doc)
    except ImportError:
        import io
        from pypdf import PdfReader                        # GitHub Actions
        return "\n".join((p.extract_text() or "") for p in PdfReader(io.BytesIO(raw)).pages)


# ------------------------------------------------------------------ wanted

def wanted():
    w = _wanted()
    for x in w.get("page", []):
        try:
            page(x["key"], 記録=False)
        except Exception as e:                              # noqa: BLE001
            print(f"  !! {x['key']}: {e}")
    for x in w.get("sosho", []):
        try:
            sosho(x["key"])
        except SystemExit as e:
            print(f"  !! 順号{x['key']}: {e}")
    # 法令は laws.json に登録済みなので、法令の定期収集が更新する


def main() -> int:
    ap = argparse.ArgumentParser(description="必要になった資料をその場で取り込む")
    sp = ap.add_subparsers(dest="cmd", required=True)
    a = sp.add_parser("page"); a.add_argument("url"); a.add_argument("--種別"); a.add_argument("--名称")
    a.add_argument("--単独", action="store_true", help="同じ階層の続きのページを取らない")
    b = sp.add_parser("law"); b.add_argument("名前")
    c = sp.add_parser("sosho"); c.add_argument("順号")
    sp.add_parser("wanted")
    args = ap.parse_args()
    if args.cmd == "page":
        page(args.url, args.種別, args.名称, 同じ階層も=not args.単独)
    elif args.cmd == "law":
        law(args.名前)
    elif args.cmd == "sosho":
        sosho(args.順号)
    else:
        wanted()
    return 0


if __name__ == "__main__":
    sys.exit(main())

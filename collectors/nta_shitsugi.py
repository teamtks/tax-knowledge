#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""国税庁「質疑応答事例」を収集する。

質疑応答事例は、通達では書き切れない**具体的な事実関係への当てはめ**を国税庁が示したものである。
通達が「原則」なら、質疑応答事例は「この場合はどうなるか」の答えになる。

取り込むもの
------------
    qa/<税目>/<分類>-<番号>.md    1事例1ファイル
    qa/<税目>/README.md           税目ごとの目次（分類別）
    qa/index.json                 全事例の一覧（題・分類・基準日・パス・URL）

各事例には **基準日**（「令和◯年◯月◯日現在の法令・通達等に基づいて作成」）を必ず残す。
**質疑応答事例は改正に追随して書き換えられないことがある。**基準日より後に改正があれば、
回答は現行法に合わないかもしれない。引用するときは基準日と現行法を見比べること。

使い方
------
    python collectors/nta_shitsugi.py            # config/qa.json の全税目
    python collectors/nta_shitsugi.py hojin shohi
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _web as W                                            # noqa: E402

ROOT = W.ROOT
OUT = os.path.join(ROOT, "qa")
CONFIG = os.path.join(ROOT, "config", "qa.json")
HOST = "https://www.nta.go.jp"

_基準日 = re.compile(r"((?:令和|平成)\s*\d+年\s*\d+月\s*\d+日)現在の法令")
_節 = ("【照会要旨】", "【回答要旨】", "【関係法令通達】", "注記")


def 目次を読む(code: str, index_url: str) -> list[dict]:
    """税目の目次から、事例のURLと分類を取る。分類は直前の見出しで決める。"""
    h, final = W.get(index_url)
    body = W.nta_body(h)
    out, 分類 = [], ""
    pat = re.compile(r"/law/shitsugi/%s/(\d+)/(\d+)\.htm" % re.escape(code))
    # 分類は、ページ冒頭の目次（<a href="#a-01">収益の計上</a>）と、
    # 本文中の錨（<a id="a-01">）の対応で決まる。見出しタグは使われていない。
    目次 = {k: W.strip_tags(v) for k, v in re.findall(r'<a href="#(a-[\w-]+)"[^>]*>(.*?)</a>', body, re.S)}
    for m in re.finditer(r"<a[^>]+(?:id|name)=\"(a-[\w-]+)\"[^>]*>|<a[^>]+href=\"([^\"#]+)\"[^>]*>(.*?)</a>",
                         body, re.S | re.I):
        if m.group(1) is not None:
            分類 = 目次.get(m.group(1), 分類)
            continue
        url = W.urllib.parse.urljoin(final, m.group(2))
        mm = pat.search(url)
        if mm and not any(x["url"] == url for x in out):
            out.append({"url": url, "分類番号": mm.group(1), "番号": mm.group(2),
                        "題": W.strip_tags(m.group(3)), "分類": 分類})
    return out


def 事例を書く(code: str, 名称: str, e: dict) -> dict:
    h, final = W.get(e["url"])
    body = W.nta_body(h)
    text = re.sub(r"\n{3,}", "\n\n", W.strip_tags(body))
    題 = W.title(h) or e["題"]
    m = _基準日.search(text)
    基準日 = re.sub(r"\s", "", m.group(1)) if m else ""

    # 見出しを Markdown の見出しにする
    for s in _節:
        text = text.replace(s, f"\n## {s.strip('【】')}\n")
    text = text.replace(題 + "\n", "", 1).strip()
    関係 = ""
    mm = re.search(r"## 関係法令通達\n(.*?)(?:\n## |\Z)", text, re.S)
    if mm:
        関係 = re.sub(r"\s+", " ", mm.group(1)).strip()

    id_ = f"{e['分類番号']}-{e['番号']}"
    md = "\n".join([
        f"# 質疑応答事例（{名称}）{題}",
        "",
        f"- 分類: {e['分類']}",
        f"- 基準日: **{基準日 or '記載なし'}** 現在の法令・通達等に基づく",
        f"- 原典: {final}",
        "",
        "> [!warning] 基準日より後の改正は反映されていない",
        "> 引用する前に、基準日以後に関係法令が改正されていないか確かめること。",
        "",
        W.出典(final),
        "",
        text,
        "",
    ])
    path = os.path.join(OUT, code, f"{id_}.md")
    st = W.write_if_changed(path, md)
    return {"id": f"{code}/{id_}", "税目": 名称, "分類": e["分類"], "題": 題,
            "基準日": 基準日, "関係法令通達": 関係[:300],
            "path": os.path.relpath(path, ROOT).replace("\\", "/"), "url": final, "_状態": st}


def 税目を集める(t: dict, verbose=True) -> list[dict]:
    code, 名称 = t["code"], t["名称"]
    目次 = 目次を読む(code, HOST + t["index"])
    if verbose:
        print(f"{名称} ({code}): {len(目次)}事例")
    rows, 数 = [], {"作成": 0, "更新": 0, "不変": 0, "失敗": 0}
    for e in 目次:
        try:
            r = 事例を書く(code, 名称, e)
            数[r.pop("_状態")] += 1
            rows.append(r)
        except Exception as ex:                             # noqa: BLE001
            数["失敗"] += 1
            print(f"  !! {e['url']}: {ex}")
    # 目次から消えた事例は削除する（国税庁が廃止したもの）
    keep = {os.path.basename(r["path"]) for r in rows}
    d = os.path.join(OUT, code)
    for f in os.listdir(d) if os.path.isdir(d) else []:
        if f.endswith(".md") and f != "README.md" and f not in keep and 数["失敗"] == 0:
            os.remove(os.path.join(d, f))
            print(f"  削除 qa/{code}/{f}（目次から消えた）")
    # 目次
    L = [f"# 質疑応答事例（{名称}）", "", W.出典(HOST + t["index"]), "",
         f"{len(rows)}事例。分類ごとに並べる。", ""]
    cur = None
    for r in rows:
        if r["分類"] != cur:
            cur = r["分類"]
            L += ["", f"## {cur or '（分類なし）'}", ""]
        L.append(f"- [{r['題']}]({os.path.basename(r['path'])})　基準日 {r['基準日'] or '—'}")
    W.write_if_changed(os.path.join(d, "README.md"), "\n".join(L) + "\n")
    if verbose:
        print(f"  作成{数['作成']} 更新{数['更新']} 不変{数['不変']} 失敗{数['失敗']}")
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("codes", nargs="*")
    ap.add_argument("--summary-file")
    a = ap.parse_args()
    with open(CONFIG, encoding="utf-8") as f:
        cfg = json.load(f)
    targets = [t for t in cfg["税目"] if t.get("有効", True) and (not a.codes or t["code"] in a.codes)]
    idx_p = os.path.join(OUT, "index.json")
    idx = {}
    if os.path.exists(idx_p):
        with open(idx_p, encoding="utf-8") as f:
            idx = {r["id"]: r for r in json.load(f).get("事例", [])}
    changed = []
    for t in targets:
        rows = 税目を集める(t)
        for k in [k for k in idx if k.startswith(t["code"] + "/")]:
            del idx[k]
        for r in rows:
            idx[r["id"]] = r
        changed.append(t["名称"])
    W.write_json_if_changed(idx_p, {
        "_説明": "国税庁 質疑応答事例の一覧。基準日は各事例が前提とする法令の時点。",
        "_出典": "https://www.nta.go.jp/law/shitsugi/01.htm",
        "事例": sorted(idx.values(), key=lambda r: r["id"])})
    if a.summary_file:
        with open(a.summary_file, "w", encoding="utf-8") as f:
            f.write("、".join(changed))
    return 0


if __name__ == "__main__":
    sys.exit(main())

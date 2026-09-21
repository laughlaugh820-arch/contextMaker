#!/usr/bin/env python3
"""青空文庫のテキストを取得・整形してリポジトリに保存するツール。

取得元は青空文庫本家ではなく、テキスト版ミラー
https://github.com/aozorahack/aozorabunko_text を raw.githubusercontent.com 経由で参照する。
ミラーは本家の zip からテキストのみを取り出したもので、1日1回更新される。

  fetch   作品ID を指定して本文を取得・保存する
  search  カタログからタイトル・著者名で作品を探す
  info    カタログの1作品を表示する
  build-catalog  ミラーのローカルクローンから index/catalog.tsv を作り直す
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import unicodedata
import urllib.error
import urllib.request
from dataclasses import dataclass, asdict
from datetime import date
from pathlib import Path

RAW_BASE = "https://raw.githubusercontent.com/aozorahack/aozorabunko_text/HEAD"
CARD_URL = "https://www.aozora.gr.jp/cards/{person_id}/card{work_id}.html"

REPO_ROOT = Path(__file__).resolve().parent.parent
CATALOG_PATH = REPO_ROOT / "index" / "catalog.tsv"
DEFAULT_OUT = REPO_ROOT / "library"

# 本文の前後にある定型ブロックの目印
DELIM_RE = re.compile(r"^-{20,}$")
COLOPHON_RE = re.compile(r"^底本[：:]")

RUBY_RE = re.compile(r"《[^》]*》")
RUBY_MARK = "｜"
# ［＃…］ 形式の入力者注。直前の ※（外字注記の目印）ごと落とす。
NOTE_RE = re.compile(r"※?［＃[^］]*］")


@dataclass
class Work:
    work_id: str
    person_id: str
    title: str
    subtitle: str
    author: str
    path: str

    @property
    def raw_url(self) -> str:
        return f"{RAW_BASE}/{self.path}"

    @property
    def card_url(self) -> str:
        return CARD_URL.format(person_id=self.person_id, work_id=self.work_id)


# --------------------------------------------------------------------------- 取得

def fetch_bytes(url: str, timeout: int = 60) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "contextMaker-aozora/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def decode(data: bytes) -> str:
    """青空文庫のテキストは Shift_JIS。実体は cp932 相当なのでそちらを優先する。"""
    for encoding in ("cp932", "shift_jis", "utf-8"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("cp932", errors="replace")


# --------------------------------------------------------------------------- 整形

def strip_ruby(text: str, keep_ruby: bool = False) -> str:
    if keep_ruby:
        text = RUBY_RE.sub(lambda m: "(" + m.group(0)[1:-1] + ")", text)
    else:
        text = RUBY_RE.sub("", text)
    return text.replace(RUBY_MARK, "")


def split_sections(lines: list[str]) -> tuple[list[str], list[str], list[str]]:
    """テキストを (ヘッダ, 本文, 奥付) に分ける。

    ヘッダは先頭の空行まで（タイトル・副題・著者名）。
    そのあとに続く ---- で囲まれた「テキスト中に現れる記号について」は本文に含めない。
    奥付は「底本：」以降すべて。
    """
    header: list[str] = []
    i = 0
    while i < len(lines) and lines[i].strip():
        header.append(lines[i])
        i += 1

    # 記号説明ブロックを読み飛ばす
    j = i
    while j < len(lines) and not lines[j].strip():
        j += 1
    if j < len(lines) and DELIM_RE.match(lines[j].strip()):
        j += 1
        while j < len(lines) and not DELIM_RE.match(lines[j].strip()):
            j += 1
        j += 1  # 閉じの区切り線
        i = j

    body: list[str] = []
    colophon: list[str] = []
    for k in range(i, len(lines)):
        if not colophon and COLOPHON_RE.match(lines[k].strip()):
            colophon = lines[k:]
            break
        body.append(lines[k])

    return header, body, colophon


def parse_header(header: list[str]) -> tuple[str, str, str]:
    """ヘッダ行から (タイトル, 副題, 著者名) を取り出す。

    青空文庫のテキストはヘッダ行数が作品ごとに違う（副題・原題の有無）。
    1行目をタイトル、最終行を著者名、あいだを副題として扱う。
    """
    cleaned = [strip_ruby(line).strip() for line in header if line.strip()]
    if not cleaned:
        return "", "", ""
    if len(cleaned) == 1:
        return cleaned[0], "", ""
    title = cleaned[0]
    author = cleaned[-1]
    subtitle = " ".join(cleaned[1:-1])
    return title, subtitle, author


def to_plain_text(text: str, keep_ruby: bool = False) -> tuple[str, dict]:
    """青空文庫形式のテキストを、注記類を落とした本文に変換する。"""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")
    header, body, colophon = split_sections(lines)
    title, subtitle, author = parse_header(header)

    body_text = "\n".join(body)
    body_text = strip_ruby(body_text, keep_ruby=keep_ruby)
    body_text = NOTE_RE.sub("", body_text)
    # 行頭の全角字下げは本文の一部なので、前後の空行だけを落とす
    body_text = re.sub(r"\n{3,}", "\n\n", body_text).strip("\n") + "\n"

    meta = {
        "title": title,
        "subtitle": subtitle,
        "author": author,
        "colophon": strip_ruby("\n".join(colophon)).strip(),
    }
    return body_text, meta


# --------------------------------------------------------------------------- カタログ

def load_catalog(path: Path = CATALOG_PATH) -> list[Work]:
    if not path.exists():
        sys.exit(f"カタログが見つからない: {path}\n"
                 "build-catalog サブコマンドで作成するか、リポジトリの index/catalog.tsv を確認すること。")
    works = []
    with path.open(encoding="utf-8") as f:
        header = f.readline()  # 見出し行
        del header
        for line in f:
            fields = line.rstrip("\n").split("\t")
            if len(fields) == 6:
                works.append(Work(*fields))
    return works


def find_work(work_id: str, catalog: list[Work]) -> Work | None:
    key = work_id.lstrip("0") or "0"
    for work in catalog:
        if work.work_id.lstrip("0") == key:
            return work
    return None


def normalize(text: str) -> str:
    return unicodedata.normalize("NFKC", text).lower()


# --------------------------------------------------------------------------- 保存

def slugify(title: str, limit: int = 40) -> str:
    slug = re.sub(r"[\\/:*?\"<>|\s]+", "_", title).strip("_")
    return slug[:limit] or "untitled"


def save_work(work: Work, out_dir: Path, keep_ruby: bool = False) -> Path:
    raw_text = decode(fetch_bytes(work.raw_url))
    plain, meta = to_plain_text(raw_text, keep_ruby=keep_ruby)

    title = meta["title"] or work.title
    dest = out_dir / work.person_id / f"{work.work_id}_{slugify(title)}"
    dest.mkdir(parents=True, exist_ok=True)

    (dest / "original.txt").write_text(
        raw_text.replace("\r\n", "\n"), encoding="utf-8")
    (dest / "plain.txt").write_text(plain, encoding="utf-8")

    record = {
        **asdict(work),
        "title": title,
        "subtitle": meta["subtitle"],
        "author": meta["author"] or work.author,
        "source": work.raw_url,
        "card": work.card_url,
        "mirror": "https://github.com/aozorahack/aozorabunko_text",
        "encoding_original": "Shift_JIS (cp932)",
        "ruby": "括弧に変換" if keep_ruby else "除去",
        "colophon": meta["colophon"],
        "characters": len(plain),
        "fetched_on": date.today().isoformat(),
    }
    (dest / "meta.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return dest


# --------------------------------------------------------------------------- サブコマンド

def cmd_fetch(args: argparse.Namespace) -> int:
    catalog = load_catalog()
    out_dir = Path(args.out).resolve()
    failed = 0
    for work_id in args.work_ids:
        work = find_work(work_id, catalog)
        if work is None:
            print(f"作品ID {work_id}: カタログに無い", file=sys.stderr)
            failed += 1
            continue
        try:
            dest = save_work(work, out_dir, keep_ruby=args.keep_ruby)
        except urllib.error.URLError as exc:
            print(f"作品ID {work_id}: 取得失敗 ({exc})", file=sys.stderr)
            failed += 1
            continue
        rel = dest.relative_to(REPO_ROOT) if dest.is_relative_to(REPO_ROOT) else dest
        print(f"{work.author}『{work.title}』-> {rel}")
    return 1 if failed else 0


def cmd_search(args: argparse.Namespace) -> int:
    catalog = load_catalog()
    query = normalize(args.query)
    author = normalize(args.author) if args.author else None
    hits = 0
    for work in catalog:
        if query and query not in normalize(work.title):
            continue
        if author and author not in normalize(work.author):
            continue
        label = f"{work.title} {work.subtitle}".strip()
        print(f"{work.work_id}\t{work.author}\t{label}")
        hits += 1
        if hits >= args.limit:
            print(f"... (上限 {args.limit} 件で打ち切り)")
            break
    if hits == 0:
        print("該当なし")
    return 0


def cmd_info(args: argparse.Namespace) -> int:
    work = find_work(args.work_id, load_catalog())
    if work is None:
        print(f"作品ID {args.work_id}: カタログに無い", file=sys.stderr)
        return 1
    print(json.dumps(asdict(work) | {"raw_url": work.raw_url, "card_url": work.card_url},
                     ensure_ascii=False, indent=2))
    return 0


def cmd_build_catalog(args: argparse.Namespace) -> int:
    """ミラーのローカルクローンを走査して catalog.tsv を作る。

    git clone --depth 1 https://github.com/aozorahack/aozorabunko_text.git
    したディレクトリを --mirror に渡す。
    """
    mirror = Path(args.mirror).resolve()
    cards = mirror / "cards"
    if not cards.is_dir():
        sys.exit(f"ミラーの cards ディレクトリが見つからない: {cards}")

    rows = []
    for txt in sorted(cards.rglob("*.txt")):
        rel = txt.relative_to(mirror).as_posix()
        parts = rel.split("/")
        if len(parts) != 5 or parts[2] != "files":
            continue
        person_id = parts[1]
        work_id = parts[3].split("_")[0]
        head = decode(txt.read_bytes()[:4096]).replace("\r\n", "\n").split("\n")
        header, _, _ = split_sections(head)
        title, subtitle, author = parse_header(header)
        rows.append((work_id, person_id, title, subtitle, author, rel))

    # 作品IDは原則数値だが、ごく一部に非数値のディレクトリ名がある
    rows.sort(key=lambda r: (not r[0].isdigit(),
                             int(r[0]) if r[0].isdigit() else 0, r[0], r[1]))
    out = Path(args.output).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        f.write("work_id\tperson_id\ttitle\tsubtitle\tauthor\tpath\n")
        for row in rows:
            f.write("\t".join(field.replace("\t", " ") for field in row) + "\n")
    print(f"{len(rows)} 件を {out} に書き出した")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    p_fetch = sub.add_parser("fetch", help="作品を取得して保存する")
    p_fetch.add_argument("work_ids", nargs="+", help="青空文庫の作品ID")
    p_fetch.add_argument("--out", default=str(DEFAULT_OUT), help="保存先 (既定: library/)")
    p_fetch.add_argument("--keep-ruby", action="store_true",
                         help="ルビを削除せず 漢字(かんじ) の形で残す")
    p_fetch.set_defaults(func=cmd_fetch)

    p_search = sub.add_parser("search", help="カタログを検索する")
    p_search.add_argument("query", nargs="?", default="", help="タイトルの一部")
    p_search.add_argument("--author", default="", help="著者名の一部")
    p_search.add_argument("--limit", type=int, default=50, help="表示件数の上限")
    p_search.set_defaults(func=cmd_search)

    p_info = sub.add_parser("info", help="作品のカタログ情報を表示する")
    p_info.add_argument("work_id")
    p_info.set_defaults(func=cmd_info)

    p_build = sub.add_parser("build-catalog", help="ミラーのクローンからカタログを作り直す")
    p_build.add_argument("--mirror", required=True, help="aozorabunko_text のクローン先")
    p_build.add_argument("--output", default=str(CATALOG_PATH))
    p_build.set_defaults(func=cmd_build_catalog)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())

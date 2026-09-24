#!/usr/bin/env python3
"""長編を章ごとに区切り、章単位の構成を測る。

短編用の analyze.py は本文全体を10等分して緊張曲線を出すが、25万字の長編では粗すぎる。
こちらは青空文庫の原本（original.txt）にある見出し注記
「［＃「一」は中見出し］」で章を区切り、章ごとに長さ・会話率・文長・結び方などを測る。

  chapters              library/ のうち5万字以上の作品をすべて解析する
  chapters --work 773   作品IDを指定する

出力は analysis/chapters/<作品ID>.json。数値と見出しの文字列だけを残し、本文は引用しない。
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import analyze as A  # noqa: E402
import aozora as Z  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
LIBRARY = REPO_ROOT / "library"
OUTPUT = REPO_ROOT / "analysis" / "chapters"
MIN_CHARS = 50000

HEADING = re.compile(r"［＃「([^」]+)」は([大中小])見出し］")
# 見出しの文字列を注記で挟む書き方（『平の将門』『季節のない街』）
HEADING_BLOCK = re.compile(r"［＃([大中小])見出し］(.+?)［＃\1見出し終わり］")
# 「一の一」「二の三」のように、章と節を一つの見出しに書く形
CHAPTER_SECTION = re.compile(r"^(.+?)の([一二三四五六七八九十百〇０-９0-9]+)$")


# 見出し注記が無い作品の見出し行。どちらも句点を含まない20字以内の行に限る
INDENT_HEADING = re.compile(r"^［＃\d+字下げ］([^「『（［].{0,19})$")


def heading_of(line: str, mode: str) -> tuple[str, str] | None:
    """見出し行なら (見出しの文字列, 段) を返す。mode は区切り方。"""
    stripped = line.strip()
    if mode == "annotation":
        m = HEADING.search(line)
        if m:
            return m.group(1), m.group(2)
        m = HEADING_BLOCK.search(line)
        if m:
            label = re.sub(r"［＃[^］]*］", "", m.group(2)).strip()
            return (label, m.group(1)) if label else None
        return None
    if "。" in stripped:
        return None
    if mode == "indent":
        m = INDENT_HEADING.match(stripped)
        if m:
            label = re.sub(r"［＃[^］]*］", "", m.group(1)).strip()
            return (label, "中") if label else None
        return None
    if mode == "bare" and len(stripped) <= 20 and any(p.match(stripped) for p in A.HEADING_PATTERNS):
        return (stripped, "中")
    return None


def detect_mode(body: list[str]) -> str | None:
    """見出しの書き方を決める。注記 → 字下げの章題 → 漢数字だけの行、の順に試す。

    青空文庫では見出し注記（［＃「一」は中見出し］）が付いている作品が多いが、
    付いていない作品もある（『平の将門』は字下げの章題、『二つの庭』は漢数字だけの行）。
    """
    for mode in ("annotation", "indent", "bare"):
        if sum(1 for line in body if heading_of(line, mode)) >= 3:
            return mode
    return None


def split_units(original: str) -> tuple[list[dict], str]:
    """原本を見出しで区切る。返り値は (単位の一覧, 区切りに使った見出しの段)。

    単位は {level, label, part, chapter, text}。text は注記とルビを落とした本文。
    """
    lines = original.replace("\r\n", "\n").split("\n")
    header, body, _ = Z.split_sections(lines)
    mode = detect_mode(body)
    units: list[dict] = []
    current = {"level": None, "label": "（冒頭）", "lines": []}
    part = None
    for line in body:
        h = heading_of(line, mode) if mode else None
        if h:
            label, level = h
            if level == "大":
                part = label
            units.append(current)
            current = {"level": level, "label": label, "part": part, "lines": []}
            continue
        current["lines"].append(line)
    units.append(current)

    for u in units:
        text = "\n".join(u.pop("lines"))
        text = Z.strip_ruby(text)
        text = Z.NOTE_RE.sub("", text)
        u["text"] = re.sub(r"\n{3,}", "\n\n", text).strip("\n")
        u.setdefault("part", None)

    # 冒頭の空の単位や、大見出し直後の空の単位は落とす
    units = [u for u in units if len(u["text"].strip()) >= 30]
    levels = {u["level"] for u in units if u["level"]}
    # 章として数える段: 中見出しがあれば中、なければ小、それも無ければ大
    unit_level = "中" if "中" in levels else ("小" if "小" in levels else ("大" if "大" in levels else None))
    return units, unit_level


def group_chapters(units: list[dict], unit_level: str | None) -> list[dict]:
    """見出しの段に合わせて章をまとめる。「一の一」形式は「一」の章にまとめる。"""
    if unit_level is None:
        return [{"label": "（全体）", "part": None, "sections": 1, "text": "\n".join(u["text"] for u in units)}]
    chapters: list[dict] = []
    for u in units:
        if u["level"] is None:
            # 最初の見出しより前の本文は、最初の章に含める
            if chapters:
                chapters[-1]["text"] += "\n" + u["text"]
            else:
                chapters.append({"label": "（序）", "part": u["part"], "sections": 1, "text": u["text"]})
            continue
        if u["level"] != unit_level:
            continue
        m = CHAPTER_SECTION.match(u["label"])
        key = m.group(1) if m else u["label"]
        if chapters and m and chapters[-1]["label"] == key and chapters[-1]["part"] == u["part"]:
            chapters[-1]["text"] += "\n" + u["text"]
            chapters[-1]["sections"] += 1
        else:
            chapters.append({"label": key, "part": u["part"], "sections": 1, "text": u["text"]})
    return chapters


def ending_type(sentences: list[str], mean_len: float) -> str:
    """章の最後の文の種類。引きの作り方を大づかみに分類する。"""
    if not sentences:
        return "なし"
    last = sentences[-1].strip()
    if last.startswith(("「", "『")) or last.endswith(("」", "』")):
        return "会話"
    if last.rstrip("」』").endswith(("？", "?")):
        return "問い"
    if len(last) < mean_len * 0.5:
        return "短文"
    return "地の文"


def measure_chapter(text: str) -> dict:
    paragraphs = A.split_paragraphs(text)
    sentences = A.sentences_of(text)
    stats = A.basic_stats(text, paragraphs, sentences)
    mean_len = stats["sentence_length_mean"]
    first = paragraphs[0] if paragraphs else ""
    return {
        "characters": len(text),
        "sentences": len(sentences),
        "sentence_length_mean": mean_len,
        "dialogue_ratio": stats["dialogue_ratio"],
        "exclaim_question_per_1000": round(stats["exclaim_per_1000"] + stats["question_per_1000"], 2),
        "simile_per_1000": round(len(A.extract_similes(sentences, limit=10**6)) / len(text) * 1000, 2) if text else 0.0,
        "opens_with_dialogue": bool(A.DIALOGUE_HEAD.match(first)),
        "ending": ending_type(sentences, mean_len),
    }


def spearman(xs: list[float], ys: list[float]) -> float | None:
    """順位相関。章の長さが後半に向かって伸びるか縮むかを見る。"""
    n = len(xs)
    if n < 4:
        return None
    rank = lambda v: {x: i for i, x in enumerate(sorted(range(n), key=lambda k: v[k]))}
    rx, ry = rank(xs), rank(ys)
    d2 = sum((rx[i] - ry[i]) ** 2 for i in range(n))
    return round(1 - 6 * d2 / (n * (n * n - 1)), 3)


def analyze_novel(work_dir: Path) -> dict | None:
    meta = json.loads((work_dir / "meta.json").read_text(encoding="utf-8"))
    original = (work_dir / "original.txt").read_text(encoding="utf-8")
    units, unit_level = split_units(original)
    chapters = group_chapters(units, unit_level)
    total = sum(len(c["text"]) for c in chapters)
    if total < MIN_CHARS:
        return None

    rows, offset = [], 0
    for i, c in enumerate(chapters):
        m = measure_chapter(c["text"])
        rows.append({"index": i + 1, "label": c["label"], "part": c["part"], "sections": c["sections"],
                     "position": round(offset / total * 100, 1), **m})
        offset += len(c["text"])

    lengths = [r["characters"] for r in rows]
    n = len(rows)
    endings = [r["ending"] for r in rows]
    parts = [p for p in dict.fromkeys(r["part"] for r in rows) if p]
    summary = {
        "chapters": n,
        "parts": len(parts),
        "heading_level_used": unit_level,
        "chapter_length_median": round(statistics.median(lengths)) if lengths else 0,
        "chapter_length_cv": round(statistics.pstdev(lengths) / statistics.fmean(lengths), 2) if n > 1 else 0.0,
        "length_trend": spearman(list(range(n)), lengths),
        "first_chapter_ratio": round(lengths[0] / statistics.median(lengths), 2) if n > 1 else None,
        "last_chapter_ratio": round(lengths[-1] / statistics.median(lengths), 2) if n > 1 else None,
        "longest_chapter_position": rows[max(range(n), key=lambda i: lengths[i])]["position"] if n else None,
        "dialogue_peak_position": rows[max(range(n), key=lambda i: rows[i]["dialogue_ratio"])]["position"] if n else None,
        "ending_share": {k: round(endings.count(k) / n, 2) for k in ("会話", "問い", "短文", "地の文")} if n else {},
        "opens_with_dialogue_share": round(sum(r["opens_with_dialogue"] for r in rows) / n, 2) if n else 0.0,
    }
    return {"work_id": meta["work_id"], "title": meta["title"], "author": meta["author"],
            "characters": total, "summary": summary, "chapters": rows}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--work", help="作品IDを指定する")
    parser.add_argument("--out", default=str(OUTPUT))
    args = parser.parse_args(argv)
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)

    count = 0
    for meta_path in sorted(LIBRARY.glob("*/*/meta.json")):
        work_dir = meta_path.parent
        if args.work and work_dir.name.split("_")[0] != args.work:
            continue
        result = analyze_novel(work_dir)
        if result is None:
            continue
        (out / f"{result['work_id']}.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        s = result["summary"]
        print(f"{result['author']}『{result['title']}』 {result['characters']:>7,}字  "
              f"章{s['chapters']:>3}（部{s['parts']}、{s['heading_level_used']}見出し）  "
              f"章長中央{s['chapter_length_median']:>6,}  ばらつき{s['chapter_length_cv']:>5}  "
              f"長さの傾向{s['length_trend']}")
        count += 1
    if not count:
        print("対象の長編が無い（5万字以上で original.txt のあるもの）", file=sys.stderr)
        return 1
    print(f"\n{count} 作品を {out.relative_to(REPO_ROOT)} に書き出した")
    return 0


if __name__ == "__main__":
    sys.exit(main())

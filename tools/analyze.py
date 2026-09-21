#!/usr/bin/env python3
"""library/ に保存した作品から、構成・比喩・文体の特徴を抽出する。

形態素解析器は使わず、文字種と記号の並びだけで判定する。環境に依存させないため。
そのぶん語彙の抽出は精度が低いので、結果は「傾向」として読むこと。

  analyze                 library/ 以下すべてを解析して analysis/ に書き出す
  analyze --work 623      作品IDを指定して1作品だけ解析する
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIBRARY = REPO_ROOT / "library"
ANALYSIS = REPO_ROOT / "analysis"

SEGMENTS = 10  # 緊張曲線の分割数

# --------------------------------------------------------------------------- 文字種

KANJI = re.compile(r"[一-鿿㐀-䶿]")
HIRAGANA = re.compile(r"[ぁ-ゟ]")
KATAKANA = re.compile(r"[ァ-ヿ]")
KANA = r"ぁ-ゟァ-ヿー"

# 会話段落の目印。行頭の字下げを挟む場合がある。
DIALOGUE_HEAD = re.compile(r"^[　\s]*[「『]")
# 地の文に埋め込まれた会話も数えるため、鉤括弧の中身そのものを拾う。
QUOTE_SPAN = re.compile(r"[「『][^「」『』]*[」』]")

# 旧字体の代表例。新字新仮名か旧字旧仮名かの判定に使う。
OLD_KANJI = set("學國實體發變聲會來氣當樂兒寫廣擧曉晝萬與舊黨點觀壹濟藝總歸戰傳醫櫻")

# 章見出し。「一」「二、」「第三章」「上」など、短く句点を含まない行に限る。
HEADING_PATTERNS = [
    re.compile(r"^[一二三四五六七八九十百千]+$"),
    re.compile(r"^[一二三四五六七八九十百千]+[、．.　 ]"),
    re.compile(r"^[０-９0-9]+$"),
    re.compile(r"^[０-９0-9]+[、．.　 ]"),
    # 「第一夜」「第三信」など、単位語は作品ごとに違うので数字のあとは緩く取る
    re.compile(r"^第[一二三四五六七八九十百千０-９0-9]+[^。、]{0,8}$"),
    re.compile(r"^[上中下]([　\s].{0,18})?$"),
    re.compile(r"^(序|跋|序章|終章|序詞|序文|前編|中編|後編|前篇|中篇|後篇|"
               r"はしがき|まえがき|あとがき|附記|付記|結語|結び)([　\s].{0,18})?$"),
    re.compile(r"^その[一二三四五六七八九十百０-９0-9]+"),
]

# 直喩の目印。キーは分類名、値はその形を拾う正規表現。
SIMILE_MARKERS = {
    # 「Xのように」の形に限る。「ように思われる」「ようになった」「ように言った」など、
    # 推量・様態・目的の用法は直喩ではないので除く。ただし文末の「のようだ」は
    # 「顔を見ると金時のようだ」のように直喩なので残す。
    "ようだ": re.compile(
        r"[のただ]よう(?!に(思|見え|見受|感じ|なっ|なり|なる|し|せ|言|云|申|願|祈|命|頼|聞|覚え|考え)"
        r"|な(気|感じ|口調|顔|声|様子|風|ふう|具合)|です|であ|でご)"
        r"[なにだ]"),
    "みたい": re.compile(r"みたい[なにだでな]"),
    "ごとし": re.compile(r"(のごとく|のごとき|ごとし|如く|如き)"),
    "まるで": re.compile(r"まるで"),
    "あたかも": re.compile(r"(あたかも|恰も)"),
    "さながら": re.compile(r"さながら"),
    "似る": re.compile(r"(に似た|に似て|を思わせる|を思わす)"),
    "見紛う": re.compile(r"(と見紛う|と見まがう|かと疑う)"),
}

# 畳語のうち、擬音語・擬態語というより副詞として使われるもの。集計から外す。
REDUPLICATION_STOPWORDS = {
    "なかなか", "いよいよ", "ますます", "だんだん", "たびたび", "ときどき",
    "もともと", "それぞれ", "われわれ", "いろいろ", "しばしば", "まちまち",
    "ほとほと", "とにかく", "かえすがえす", "みすみす", "つれづれ",
    "とうとう", "わざわざ", "そうそう", "いていて",
}

# 本文の末尾に付く「（大正四年九月）」のような発表年月の注記。結びの抽出から外す。
TRAILING_NOTE = re.compile(r"^[（(].{0,60}[）)]$")

SENTENCE_END = "。！？!?"
CLOSERS = "」』）)〉》】］\"'、"


# --------------------------------------------------------------------------- 分割

def split_paragraphs(text: str) -> list[str]:
    return [line for line in text.split("\n") if line.strip()]


def sentences_of(text: str) -> list[str]:
    """段落ごとに文へ分割し、重複を避けつつ平坦なリストにする。"""
    result = []
    for para in split_paragraphs(text):
        result.extend(_split_one_paragraph(para))
    return result


def _split_one_paragraph(para: str) -> list[str]:
    parts, buf = [], ""
    i = 0
    while i < len(para):
        buf += para[i]
        if para[i] in SENTENCE_END:
            j = i + 1
            while j < len(para) and para[j] in CLOSERS:
                buf += para[j]
                j += 1
            parts.append(buf.strip())
            buf = ""
            i = j
            continue
        i += 1
    if buf.strip():
        parts.append(buf.strip())
    return [p for p in parts if p]


# --------------------------------------------------------------------------- 統計

# これより長い鉤括弧は、やりとりではなく「括弧に入った語り」とみなす（枠物語の語り手など）。
DIALOGUE_SPAN_LIMIT = 200


def quote_ratios(text: str) -> tuple[float, float, float]:
    """鉤括弧の占有率を (会話, 括弧付きの語り, 1箇所あたり平均長) で返す。

    泉鏡花『高野聖』のように語り全体が鉤括弧に入る作品があるため、
    短い括弧（やりとり）と長い括弧（語り）を分けないと会話の多さを見誤る。
    """
    if not text:
        return 0.0, 0.0, 0.0
    spans = [len(m.group(0)) for m in QUOTE_SPAN.finditer(text)]
    if not spans:
        return 0.0, 0.0, 0.0
    short = sum(n for n in spans if n <= DIALOGUE_SPAN_LIMIT)
    long_ = sum(n for n in spans if n > DIALOGUE_SPAN_LIMIT)
    return (round(short / len(text), 3), round(long_ / len(text), 3),
            round(sum(spans) / len(spans), 1))


def detect_orthography(text: str) -> str:
    """新字新仮名か旧字旧仮名かを、旧字体と歴史的仮名遣いの出現率から判定する。"""
    sample = text[:20000]
    if not sample:
        return "不明"
    old = sum(1 for ch in sample if ch in OLD_KANJI)
    kana_old = sample.count("ゐ") + sample.count("ゑ") + sample.count("ヰ") + sample.count("ヱ")
    score = (old + kana_old) / len(sample) * 1000
    if score >= 1.5:
        return "旧字旧仮名"
    if score >= 0.3:
        return "混在"
    return "新字新仮名"


def ratio(pattern: re.Pattern, text: str) -> float:
    return round(len(pattern.findall(text)) / len(text), 4) if text else 0.0


def basic_stats(text: str, paragraphs: list[str], sentences: list[str]) -> dict:
    lengths = [len(s) for s in sentences] or [0]
    dialogue_paragraphs = sum(1 for p in paragraphs if DIALOGUE_HEAD.match(p))
    dialogue_ratio, narration_ratio, span_mean = quote_ratios(text)
    return {
        "characters": len(text),
        "paragraphs": len(paragraphs),
        "sentences": len(sentences),
        "sentence_length_mean": round(statistics.fmean(lengths), 1),
        "sentence_length_median": round(statistics.median(lengths), 1),
        "sentence_length_stdev": round(statistics.pstdev(lengths), 1),
        "sentence_length_max": max(lengths),
        "dialogue_ratio": dialogue_ratio,
        "quoted_narration_ratio": narration_ratio,
        "quote_span_mean": span_mean,
        "dialogue_paragraph_ratio": round(dialogue_paragraphs / len(paragraphs), 3) if paragraphs else 0.0,
        "orthography": detect_orthography(text),
        "kanji_ratio": ratio(KANJI, text),
        "hiragana_ratio": ratio(HIRAGANA, text),
        "katakana_ratio": ratio(KATAKANA, text),
        "comma_per_sentence": round(text.count("、") / len(sentences), 2) if sentences else 0.0,
        "dash_per_1000": round(text.count("――") / len(text) * 1000, 2) if text else 0.0,
        "ellipsis_per_1000": round(text.count("……") / len(text) * 1000, 2) if text else 0.0,
        "exclaim_per_1000": round(
            (text.count("！") + text.count("!")) / len(text) * 1000, 2) if text else 0.0,
        "question_per_1000": round(
            (text.count("？") + text.count("?")) / len(text) * 1000, 2) if text else 0.0,
    }


def detect_headings(paragraphs: list[str]) -> list[dict]:
    """章見出しらしい行と、その章の文字数を返す。"""
    headings = []
    for index, para in enumerate(paragraphs):
        line = para.strip()
        if len(line) > 20 or "。" in line:
            continue
        if any(p.match(line) for p in HEADING_PATTERNS):
            headings.append({"index": index, "label": line})
    for i, h in enumerate(headings):
        start = h["index"] + 1
        end = headings[i + 1]["index"] if i + 1 < len(headings) else len(paragraphs)
        h["characters"] = sum(len(p) for p in paragraphs[start:end])
    return headings


def tension_curve(sentences: list[str]) -> list[dict]:
    """本文を10等分し、区間ごとの文体指標を出す。構成の型を比べるために使う。"""
    if not sentences:
        return []
    total = sum(len(s) for s in sentences)
    if total == 0:
        return []
    target = total / SEGMENTS
    buckets: list[list[str]] = [[] for _ in range(SEGMENTS)]
    acc = 0
    for sentence in sentences:
        index = min(int(acc / target), SEGMENTS - 1)
        buckets[index].append(sentence)
        acc += len(sentence)

    curve = []
    for i, bucket in enumerate(buckets):
        joined = "".join(bucket)
        lengths = [len(s) for s in bucket] or [0]
        dialogue = quote_ratios(joined)[0]
        curve.append({
            "segment": i + 1,
            "sentences": len(bucket),
            "sentence_length_mean": round(statistics.fmean(lengths), 1),
            "dialogue_ratio": dialogue,
            "exclaim_question_per_1000": round(
                sum(joined.count(c) for c in "！？!?") / len(joined) * 1000, 2) if joined else 0.0,
            "dash_ellipsis_per_1000": round(
                (joined.count("――") + joined.count("……")) / len(joined) * 1000, 2) if joined else 0.0,
        })
    return curve


# --------------------------------------------------------------------------- 技法

def peak_segment(curve: list[dict], key: str) -> int | None:
    """指標が最大になる区間番号。全区間ゼロなら None を返す。

    max() に任せると同順位の先頭が返り、感嘆符を一切使わない作品が
    「冒頭がピーク」に数えられてしまう。
    """
    if not curve or max(c[key] for c in curve) <= 0:
        return None
    return max(curve, key=lambda c: c[key])["segment"]


def extract_similes(sentences: list[str], limit: int = 60) -> list[dict]:
    """直喩の目印を含む文を抜き出す。位置は作品全体を 0〜1 に正規化した値。"""
    total = sum(len(s) for s in sentences) or 1
    found, acc = [], 0
    for sentence in sentences:
        kinds = [name for name, pattern in SIMILE_MARKERS.items() if pattern.search(sentence)]
        if kinds and 10 <= len(sentence) <= 160:
            found.append({
                "markers": kinds,
                "position": round(acc / total, 3),
                "text": sentence,
            })
        acc += len(sentence)
    # 長すぎず短すぎない、比喩らしい文を優先して残す
    found.sort(key=lambda f: abs(len(f["text"]) - 55))
    return found[:limit]


def extract_reduplications(text: str) -> dict:
    """ABAB 型・AABB 型の畳語を集計する。擬音語・擬態語の近似。"""
    counter = Counter()
    for size in (2, 3):
        for match in re.finditer(rf"(?<![{KANA}])([{KANA}]{{{size}}})\1", text):
            word = match.group(0)
            if word not in REDUPLICATION_STOPWORDS:
                counter[word] += 1
    return dict(counter.most_common(40))


def extract_openings_closings(sentences: list[str], count: int = 3) -> dict:
    # 「（大正四年九月）」のような発表年月の注記は本文の結びではないので落とす
    body = list(sentences)
    while body and TRAILING_NOTE.match(body[-1]):
        body.pop()
    return {
        "opening": body[:count],
        "closing": body[-count:] if len(body) >= count else body,
    }


def extract_repetitions(sentences: list[str], limit: int = 15) -> list[dict]:
    """同じ書き出しが連続する箇所を拾う。畳みかけの技法。"""
    found = []
    run_head, run = None, []
    for sentence in sentences + [""]:
        head = sentence[:4]
        if run_head is not None and head == run_head and len(head) >= 3:
            run.append(sentence)
            continue
        if len(run) >= 2:
            found.append({"head": run_head, "count": len(run) + 1, "sample": run[:3]})
        run_head, run = head, []
    found.sort(key=lambda f: -f["count"])
    return found[:limit]


def extract_vocabulary(text: str, limit: int = 40) -> dict:
    """漢字・カタカナの連なりを語の近似として数える。助詞に埋もれないための割り切り。"""
    counter = Counter()
    for word in re.findall(r"[一-鿿]{2,4}|[ァ-ヿー]{3,8}", text):
        counter[word] += 1
    return {w: c for w, c in counter.most_common(limit) if c >= 3}


# --------------------------------------------------------------------------- 実行

def analyze_work(work_dir: Path) -> dict:
    meta = json.loads((work_dir / "meta.json").read_text(encoding="utf-8"))
    raw_text = (work_dir / "plain.txt").read_text(encoding="utf-8")
    all_paragraphs = split_paragraphs(raw_text)
    headings = detect_headings(all_paragraphs)

    # 章見出しは1字の「文」として統計を歪めるので、本文の集計からは外す。
    # 『こころ』のように見出しが110個ある作品では、書き出しの抽出まで狂う。
    heading_rows = {h["index"] for h in headings}
    paragraphs = [p for i, p in enumerate(all_paragraphs) if i not in heading_rows]
    text = "\n".join(paragraphs)
    sentences = sentences_of(text)

    similes_all = extract_similes(sentences, limit=10**6)
    stats = basic_stats(text, paragraphs, sentences)
    # 例は60件までしか保存しないので、密度は全件から別に出しておく
    stats["simile_count"] = len(similes_all)
    stats["simile_per_1000"] = round(len(similes_all) / len(text) * 1000, 2) if text else 0.0

    return {
        "work_id": meta["work_id"],
        "person_id": meta["person_id"],
        "title": meta["title"],
        "author": meta["author"],
        "card": meta.get("card", ""),
        "path": str(work_dir.relative_to(REPO_ROOT)),
        "stats": stats,
        "headings": headings,
        "tension_curve": tension_curve(sentences),
        "exclaim_peak_segment": peak_segment(tension_curve(sentences), "exclaim_question_per_1000"),
        "dialogue_peak_segment": peak_segment(tension_curve(sentences), "dialogue_ratio"),
        "similes": extract_similes(sentences),
        "reduplications": extract_reduplications(text),
        **extract_openings_closings(sentences),
        "repetitions": extract_repetitions(sentences),
        "vocabulary": extract_vocabulary(text),
    }


def iter_work_dirs(work_id: str | None = None):
    for meta_path in sorted(LIBRARY.glob("*/*/meta.json")):
        work_dir = meta_path.parent
        if work_id and work_dir.name.split("_")[0] != work_id:
            continue
        yield work_dir


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--work", help="作品IDを指定して1作品だけ解析する")
    parser.add_argument("--out", default=str(ANALYSIS), help="出力先 (既定: analysis/)")
    args = parser.parse_args(argv)

    out_root = Path(args.out).resolve()
    count = 0
    for work_dir in iter_work_dirs(args.work):
        result = analyze_work(work_dir)
        dest = out_root / result["person_id"]
        dest.mkdir(parents=True, exist_ok=True)
        (dest / f"{result['work_id']}.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        stats = result["stats"]
        print(f"{result['author']}『{result['title']}』"
              f" {stats['characters']:>7}字 文{stats['sentences']:>5}"
              f" 平均{stats['sentence_length_mean']:>5}字"
              f" 会話{stats['dialogue_ratio']:.2f}"
              f" 直喩{len(result['similes']):>3}")
        count += 1

    if count == 0:
        print("解析対象が見つからない。先に tools/aozora.py fetch で作品を取得すること。",
              file=sys.stderr)
        return 1
    print(f"\n{count} 作品を {out_root.relative_to(REPO_ROOT)} に書き出した")
    return 0


if __name__ == "__main__":
    sys.exit(main())

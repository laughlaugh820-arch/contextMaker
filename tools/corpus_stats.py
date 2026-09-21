#!/usr/bin/env python3
"""青空文庫ミラーの無作為標本から、文体指標の分布を出す。

context/guide/story_craft.md の数値の出どころ。名作コーパス（library/）は選び方に
偏りがあるので、ガイドの「多数派はこうだ」という主張はこちらで裏づける。

  corpus_stats --mirror /path/to/aozorabunko_text [--n 2000] [--seed 42]

ミラーは git clone --depth 1 https://github.com/aozorahack/aozorabunko_text.git で取る。
結果は analysis/corpus_distribution.json にも書く。
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import analyze as A  # noqa: E402
import aozora as Z  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
OUTPUT = REPO_ROOT / "analysis" / "corpus_distribution.json"

# 標本に入れる条件。韻文・文語・断片を除く
MIN_CHARS, MAX_CHARS = 3000, 300000
MIN_SENTENCES = 40
MAX_MEAN_LENGTH = 120  # 句点で切れない文語体を除く

QUANTILES = [("p10", 0.10), ("p25", 0.25), ("median", 0.50), ("p75", 0.75), ("p90", 0.90)]


def quantile(values: list[float], p: float) -> float:
    return sorted(values)[int(p * (len(values) - 1))]


def measure(text: str) -> dict | None:
    paragraphs_all = A.split_paragraphs(text)
    heading_rows = {h["index"] for h in A.detect_headings(paragraphs_all)}
    paragraphs = [p for i, p in enumerate(paragraphs_all) if i not in heading_rows]
    body = "\n".join(paragraphs)
    if not (MIN_CHARS <= len(body) <= MAX_CHARS):
        return None
    if A.detect_orthography(body) != "新字新仮名":
        return None
    sentences = A.sentences_of(body)
    while sentences and A.TRAILING_NOTE.match(sentences[-1]):
        sentences.pop()
    if len(sentences) < MIN_SENTENCES:
        return None
    lengths = [len(s) for s in sentences]
    mean = statistics.fmean(lengths)
    if mean > MAX_MEAN_LENGTH:
        return None

    stats = A.basic_stats(body, paragraphs, sentences)
    curve = A.tension_curve(sentences)
    similes = A.extract_similes(sentences, limit=10**6)
    prose_paragraphs = [len(p) for p in paragraphs if not A.DIALOGUE_HEAD.match(p)]
    percentile = lambda n: sum(1 for l in lengths if l < n) / len(lengths)

    return {
        "sentence_length_mean": mean,
        "sentence_length_max": max(lengths),
        "sentence_length_cv": stats["sentence_length_stdev"] / mean,
        "comma_per_sentence": stats["comma_per_sentence"],
        "first_sentence_length": lengths[0],
        "first_sentence_percentile": percentile(lengths[0]),
        "last_sentence_length": lengths[-1],
        "last_sentence_percentile": percentile(lengths[-1]),
        "paragraph_length_mean": statistics.fmean(prose_paragraphs) if prose_paragraphs else 0,
        "dialogue_ratio": stats["dialogue_ratio"],
        "dialogue_later_half": (statistics.fmean(c["dialogue_ratio"] for c in curve[5:])
                                > statistics.fmean(c["dialogue_ratio"] for c in curve[:5])),
        "exclaim_question_per_1000": stats["exclaim_per_1000"] + stats["question_per_1000"],
        "exclaim_peak_segment": A.peak_segment(curve, "exclaim_question_per_1000"),
        "dash_per_1000": stats["dash_per_1000"],
        "ellipsis_per_1000": stats["ellipsis_per_1000"],
        "kanji_ratio": stats["kanji_ratio"],
        "simile_per_1000": len(similes) / len(body) * 1000,
        "simile_markers": Counter(m for s in similes for m in s["markers"]),
        "reduplication_per_1000": sum(A.extract_reduplications(body).values()) / len(body) * 1000,
    }


def summarize(rows: list[dict]) -> dict:
    n = len(rows)
    dist = lambda key: {label: round(quantile([r[key] for r in rows], p), 3)
                        for label, p in QUANTILES}
    zero = lambda key: round(sum(1 for r in rows if r[key] == 0) / n, 3)
    with_dialogue = [r for r in rows if r["dialogue_ratio"] >= 0.05]
    peaks = [r["exclaim_peak_segment"] for r in rows if r["exclaim_peak_segment"]]
    peak_counts = Counter(peaks)
    markers: Counter = Counter()
    for r in rows:
        markers.update(r["simile_markers"])
    total_markers = sum(markers.values()) or 1

    dash_heavy = sum(1 for r in rows if r["dash_per_1000"] >= 1) / n
    ellipsis_heavy = sum(1 for r in rows if r["ellipsis_per_1000"] >= 1) / n
    both_heavy = sum(1 for r in rows if r["dash_per_1000"] >= 1 and r["ellipsis_per_1000"] >= 1) / n

    return {
        "n": n,
        "sentence_length_mean": dist("sentence_length_mean"),
        "sentence_length_max": dist("sentence_length_max"),
        "sentence_length_cv": dist("sentence_length_cv"),
        "comma_per_sentence": dist("comma_per_sentence"),
        "first_sentence_length": dist("first_sentence_length"),
        "first_sentence_percentile_mean": round(statistics.fmean(r["first_sentence_percentile"] for r in rows), 3),
        "last_sentence_length": dist("last_sentence_length"),
        "last_sentence_percentile_mean": round(statistics.fmean(r["last_sentence_percentile"] for r in rows), 3),
        "paragraph_length_mean": dist("paragraph_length_mean"),
        "dialogue_ratio": dist("dialogue_ratio"),
        "dialogue_later_half_share_when_present": round(
            sum(r["dialogue_later_half"] for r in with_dialogue) / len(with_dialogue), 3) if with_dialogue else None,
        "exclaim_question_per_1000": dist("exclaim_question_per_1000"),
        "exclaim_question_zero_share": zero("exclaim_question_per_1000"),
        "exclaim_peak_segment_share": {seg: round(peak_counts[seg] / len(peaks), 3) for seg in range(1, 11)} if peaks else {},
        "dash_per_1000": dist("dash_per_1000"),
        "dash_zero_share": zero("dash_per_1000"),
        "ellipsis_per_1000": dist("ellipsis_per_1000"),
        "ellipsis_zero_share": zero("ellipsis_per_1000"),
        "dash_and_ellipsis_heavy_share": round(both_heavy, 3),
        "dash_and_ellipsis_heavy_expected_if_independent": round(dash_heavy * ellipsis_heavy, 3),
        "kanji_ratio": dist("kanji_ratio"),
        "simile_per_1000": dist("simile_per_1000"),
        "simile_marker_share": {k: round(v / total_markers, 3) for k, v in markers.most_common()},
        "reduplication_per_1000": dist("reduplication_per_1000"),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--mirror", required=True, help="aozorabunko_text のクローン先")
    parser.add_argument("--n", type=int, default=2000, help="標本の作品数（既定 2000）")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--output", default=str(OUTPUT))
    args = parser.parse_args(argv)

    cards = Path(args.mirror).resolve() / "cards"
    files = sorted(cards.glob("*/files/*/*.txt"))
    if not files:
        sys.exit(f"ミラーの cards ディレクトリにテキストが無い: {cards}")
    random.Random(args.seed).shuffle(files)

    rows, scanned = [], 0
    for path in files:
        if len(rows) >= args.n:
            break
        scanned += 1
        try:
            plain, _ = Z.to_plain_text(Z.decode(path.read_bytes()))
        except Exception:
            continue
        row = measure(plain)
        if row:
            rows.append(row)

    summary = summarize(rows)
    summary["scanned"] = scanned
    summary["filters"] = {"min_chars": MIN_CHARS, "max_chars": MAX_CHARS, "min_sentences": MIN_SENTENCES,
                          "max_mean_length": MAX_MEAN_LENGTH, "orthography": "新字新仮名"}
    out = Path(args.output).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"{scanned} 作品を走査し {len(rows)} 作品を標本にした -> {out.relative_to(REPO_ROOT)}")
    for key in ("sentence_length_mean", "sentence_length_cv", "dialogue_ratio", "simile_per_1000"):
        d = summary[key]
        print(f"  {key:<24} 10%={d['p10']}  中央={d['median']}  90%={d['p90']}")
    print(f"  1文目の百分位平均 {summary['first_sentence_percentile_mean']} / 最終文 {summary['last_sentence_percentile_mean']}")
    print(f"  直喩の目印 {summary['simile_marker_share']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

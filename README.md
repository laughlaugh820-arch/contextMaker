# contextMaker

青空文庫のテキストを取得し、注記類を落とした素のテキストとして保存しておくためのリポジトリ。

## 取得経路

青空文庫本家（`www.aozora.gr.jp`）を直接クロールせず、テキスト版ミラー
[aozorahack/aozorabunko_text](https://github.com/aozorahack/aozorabunko_text) を
`raw.githubusercontent.com` 経由で参照する。

ミラーは本家の zip からテキストファイルのみを取り出して 1 日 1 回更新しているもので、
zip を展開する必要がない。本家はボランティア運営でサーバも潤沢ではないため、
まとまった数の作品を取るときに本家を叩かないこの経路を既定にしている。

## 使い方

```console
# タイトル・著者名で探す
$ python3 tools/aozora.py search 銀河鉄道
456	宮沢賢治	銀河鉄道の夜

$ python3 tools/aozora.py search --author 太宰 --limit 5

# 作品IDを指定して取得・保存する（複数可）
$ python3 tools/aozora.py fetch 456 127
宮沢賢治『銀河鉄道の夜』-> library/000081/456_銀河鉄道の夜
芥川龍之介『羅生門』-> library/000879/127_羅生門

# ルビを消さずに 漢字(かんじ) の形で残す
$ python3 tools/aozora.py fetch 456 --keep-ruby

# カタログ上の1作品の情報（取得元URL、図書カードURL）を見る
$ python3 tools/aozora.py info 456
```

Python 3.9 以降。標準ライブラリのみで動く。

## 保存されるもの

作品ごとに `library/<人物ID>/<作品ID>_<タイトル>/` を作り、3 ファイルを置く。

| ファイル | 内容 |
| --- | --- |
| `original.txt` | ミラーから取得した青空文庫形式のテキストを UTF-8 に変換しただけのもの。ルビ・注記はそのまま |
| `plain.txt` | 本文のみ。ルビ `《》`、ルビ開始記号 `｜`、入力者注 `［＃…］`、冒頭の記号説明、奥付を除去 |
| `meta.json` | タイトル・著者・取得元URL・図書カードURL・底本情報・文字数・取得日 |

元のテキストは Shift_JIS（実体は cp932）なので、変換は cp932 を優先して行う。

## カタログ

`index/catalog.tsv` にミラー収録の全 17,436 作品の一覧を持つ。
列は `work_id`、`person_id`、`title`、`subtitle`、`author`、`path`。
タイトルと著者名は各テキストのヘッダ行から取り出しているため、
副題や原題の扱いは作品によって揺れがある。

更新するときはミラーをクローンして作り直す。

```console
$ git clone --depth 1 https://github.com/aozorahack/aozorabunko_text.git /tmp/azmirror
$ python3 tools/aozora.py build-catalog --mirror /tmp/azmirror
```

## 権利について

`library/` 以下のテキストは
[「青空文庫収録ファイルの取り扱い規準」](https://www.aozora.gr.jp/guide/kijyunn.html)
の下で利用すること。大半は著作権保護期間が終了した作品だが、
保護期間中でクリエイティブ・コモンズ・ライセンス等により再配布されているファイルも含まれる。
作品ごとの条件は `meta.json` の `card`（図書カード）に記載の「利用に関する注記」で確認できる。

# XAccountExporter (`xarchive`)

指定したXアカウント(自分以外)の投稿を`gallery-dl`で全件保存し、後からオフラインで
閲覧できる静的HTMLビューアを生成するツール。CLIとTkinter GUIの両方を提供する。

## セットアップ

```bash
uv venv .venv
uv pip install -e .
source .venv/bin/activate
```

対象アカウントを閲覧できる**捨て垢**のCookieをNetscape形式で用意しておく
(本垢のCookieは使わないこと)。CLIではプロジェクトルートに`cookies.txt`として
配置するのが簡単だが、GUIでは任意のパスのCookieファイルを選択できる。

GUIだけ使いたい場合、Pythonのセットアップは不要。Windows/macOS(Apple Silicon)/Linux向けの
ビルド済み実行ファイルを[Releases](../../releases)からダウンロードして使える。

## GUIの使い方

```bash
xarchive-gui
```

アカウント名・Cookieファイル・保存先ディレクトリを指定し、チェックボックスで
単独リツイート/リプライを含めるかを選んで、「取得(差分)」または「全件取得」→
「ビューア生成」→「ビューアを開く」の順にボタンを押すだけ。入力値は次回起動時に
復元される。Windows/Mac/Linux向けに単一実行ファイルとして配布する場合の手順は
[docs/BUILD.md](docs/BUILD.md)を参照。

## CLIの使い方

```bash
# 新規/差分取得(2回目以降は前回取得分をスキップして新規投稿のみ追記)
xarchive fetch <username>

# 全件再取得(既存データは上書きしない、抜けの補完用)
xarchive fetch <username> --full

# 単独リツイートも含める / リプライを含めない場合
xarchive fetch <username> --include-retweets
xarchive fetch <username> --no-replies

# 取得済みデータからビューア(index.html)を生成
xarchive build <username>

# 生成したビューアを簡易サーバーで閲覧(任意。index.htmlを直接開くだけでも動く)
xarchive serve <username>

# 同一ネットワーク上の他端末(スマホ等)からも閲覧したい場合
xarchive serve <username> --host 0.0.0.0

# 保存済み投稿が削除されていないか確認(任意・追加のネットワークアクセスを伴う)
xarchive check-deleted <username> --limit 100

# data/配下の取得済みアカウント全てを順に差分取得+ビューア再生成(cron向け)
xarchive fetch-all
```

`fetch-all`は多重実行防止のロックを取るので、前回の実行が終わっていなければ何もせず
終了する。cronで定期実行する例(毎日4時に実行、ログは`data/fetch-all.log`に追記):

```cron
0 4 * * * cd /path/to/XAccountExporter && .venv/bin/xarchive fetch-all >> data/fetch-all.log 2>&1
```

生成物は`data/<username>/`以下にまとまる(`media/`・`posts/`・`index.html`)。
`data/`ディレクトリごとバックアップ・移動しても`index.html`はそのまま開ける。

`fetch`実行時、対象アカウントのアイコン画像を`avatar.<拡張子>`として保存し、
`build`でビューアのfavicon(タブアイコン)に設定する(取得に失敗しても処理は継続する)。

## 取得の挙動

- 本文のみの投稿・リプライも含めて保存する。単独リツイート(本文なしの純粋なRT)は含めない
- 引用元ツイートは、取得済みであれば埋め込みカード風に、未取得ならXへのリンクにフォールバックする
- リプライ元が未取得(対象アカウント以外への返信など)の場合も同様にリンクにフォールバックする
- レート制限対策として`--sleep-request`(既定3.0〜6.0秒)・`--sleep`(既定1.0〜3.0秒)を
  やや長めに設定している。必要に応じて`xarchive fetch`のオプションで調整可能
- 差分更新は`gallery-dl`の`--download-archive` + `-A`(N回連続スキップで打ち切り)で実現している。
  **本文のみの投稿はダウンロード扱いにならないため`-A`のスキップ判定に乗らない** —
  そのため差分実行時、本文のみの投稿が連続する区間ではやや余分に遡ることがあるが、
  安全側(取りこぼし防止)に倒した設計として許容している
- 保存済みの投稿JSON(`posts/<tweet_id>.json`)は一度書き出したら上書きしない(追記のみ)
- 削除検知はデフォルトでは行わない(`check-deleted`を明示実行した場合のみ)。検知結果は
  `data/<username>/deleted.json`に追記され、`posts/*.json`自体は変更しない

## 依存

- Python 3.12 / [gallery-dl](https://codeberg.org/mikf/gallery-dl) / Jinja2
- GUIはtkinter(標準ライブラリ、追加インストール不要)
- ビューアのJSはCDN等の外部依存なし(オフライン閲覧のため)
- 単一バイナリ化には`pip install -e ".[build]"`でPyInstallerを追加導入する
  (詳細は[docs/BUILD.md](docs/BUILD.md))

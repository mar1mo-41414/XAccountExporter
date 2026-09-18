# 作業履歴

## 2026-09-17 初期実装

`start-note.md`の仕様書を元に、gallery-dl 1.32.9のtwitter extractorソース
(`~/.local/lib/python3.12/site-packages/gallery_dl/extractor/twitter.py`)を実際に
読み込み、仕様書で「未確定」とされていた実装詳細を先に確定させてから実装した。

### 判明した重要な落とし穴

- `extractor.twitter.text-tweets`が既定`False`。これを`true`にしない限り、本文のみ
  (画像/動画なし)のツイートは**gallery-dlの出力から完全に除外される**。スレッド/
  リプライを漏れなく保存する今回の用途では致命的なため、明示的に有効化した。
- `extractor.twitter.quoted`も既定`False`。引用元ツイートを独立データとして保存するには
  これを`true`にする必要がある。有効化すると引用元は「quote_idと同じtweet_idを持つ
  別アイテム」として個別保存される(埋め込みではない)。
- `--write-metadata`(postprocessor `metadata`、event既定`file`)は**ダウンロードされた
  ファイルに紐づいて発火する**ため、本文のみツイート(ファイル無し)では一切発火しない。
  → 回避策として、event を`post`(`Message.Directory`、ツイート単位で必ず発火)に
  変更し、`directory`/`filename`をカスタム指定して`posts/<tweet_id>.json`に
  1ツイート1ファイルで書き出す方式にした。ただしこの時点(ファイルダウンロード前)では
  個々のメディアファイルの拡張子情報は`tdata`に含まれない。
  → メディアファイル名を`{tweet_id}_{num}.{extension}`で決定的に命名することで解決。
  ビューア生成時は`media/<tweet_id>_*`をglobして実ファイルから種別(画像/動画/GIF)を
  判定する方式にした(追加のメタデータ不要)。
- 差分更新は当初案(最終取得ID/日時をstate.jsonに記録)ではなく、gallery-dl標準の
  `--download-archive`(sqlite) + `-A N`(N回連続スキップで打ち切り)を採用。取得順の
  揺れやページング途中断に強い。ただし本文のみツイートは`-A`のスキップカウントに乗らない
  という制約があり、README/READMEにも明記した。

### JavaScript側のバグ: tweet_idの精度落ち

XのツイートID(snowflake形式)は19桁の整数で、JavaScriptの`Number`が正確に表現できる
範囲(2^53-1)を超える。`JSON.parse`でそのまま数値として読み込むと、`reply_id`/
`quote_id`等をURL文字列に組み立てる際に下位桁が丸められ、生成されるXへのリンクURLが
存在しないツイートIDを指してしまうバグがあった。
→ `tweet_id`/`reply_id`/`quote_id`/`retweet_id`/`conversation_id`/
`external_reply_id`/`external_quote_id`/`author.id`をビューア用JSONに書き出す直前に
すべて文字列化することで解決(`models.py`の`_stringify_ids`)。ツリー構築自体は
Python側で整数のまま行うため、この文字列化による影響はない。

### `<script>`へのデータ埋め込み

投稿本文に`</script>`という文字列が偶然含まれるケースを考慮し、生データのJSON文字列を
そのまま`<script>`に埋め込む方式は採らず、Base64エンコードして
`<script type="application/json">`に格納し、ブラウザ側で`atob` + `TextDecoder`で
デコードする方式にした(全て標準Web APIのみで完結、CDN等の外部依存なし)。

### 動作確認

- 実際に検証用アカウントに対して`xarchive fetch`を実行し、
  本文のみツイート・リプライ・引用ツイートがすべて`posts/*.json`に保存されることを確認
  (131件取得、うちメディア付きは一部)
- 2回目の`fetch`実行で`-A 3`により早期に打ち切られ、既存の`posts/*.json`が
  上書きされない(mtime不変)ことを確認
- ブラウザ拡張(Claude in Chrome)が未接続だったため、実ブラウザでの目視確認の代わりに
  Node.js + jsdomでのヘッドレス実行テストを実施。`TextDecoder`はjsdomのスクリプト実行
  サンドボックスに既定で存在しないため`beforeParse`でNode組み込みの`TextDecoder`を注入して
  テストした(実ブラウザでは標準で利用可能なので本番コードへの変更は不要)。
  JSエラーなし、スレッド/引用カード/メディア表示/検索フィルタが期待通り動作することを確認。

## 2026-09-17 全件取得の完了確認

`--abort-after 2/3`のテスト実行で131件のみ取得した状態で止まっていたため、`--full`で
全件取得を実行。実行後も131件のまま変化がなく、本当に全件なのか(何らかの理由で早期に
打ち切られていないか)を`-v`(verbose)ログで確認した。

ログを見ると、gallery-dlは通常の`UserTweets`タイムラインAPIを既知の最古ツイートまで
遡った後、`SearchTimeline`(`from:<username> max_id:...`)による検索ベースの
フォールバックページネーションに切り替えてさらに過去へ遡っており、これが**3回連続で
「No Tweet results」**になった時点で(`stop_tweets_max`のカウンタが尽きて)自然に
終了していた。バグによる早期打ち切りではなく、これ以上遡れる投稿がAPI上に存在しない
ことを示している。

対象アカウントのプロフィール情報`statuses_count`は193だが、アーカイブ件数131との差
(62件)は、`retweets: false`設定により意図的に除外している単独リツイートである
可能性が高い(未検証・推測)。仕様通りの挙動であり、追加対応は不要と判断した。

## 2026-09-17 引用ツイートの向きが逆になっていたバグ

別の検証用アカウントを新規追加した際、ユーザーから「引用カードがおかしい」と指摘。実データを
確認したところ、時系列的にありえない状態(古いツイートが、自分より後に投稿された
ツイートを"引用元"として表示している)になっていた。

原因はgallery-dl側のフィールド命名の罠だった。当初`quote_id`を「自分が引用している
ツイートのID」だと思って`models.py`で使っていたが、実際には**逆**で、
`quote_id`は「自分を引用したツイートのID」(`quoted_by_id_str`由来)を指す。
「自分が引用しているツイートのID」を表すのは別フィールドの`quoted_id`
(gallery-dl 1.32.9時点のソース調査では見落としていた。venvには1.32.12が入っており、
このバージョンで追加/確認されたフィールドの可能性がある)。

実データで検証:
```
tweet A (2026-07-29): quote_id=B, quoted_id=0   → Aは後からBに引用された(quote_id=引用した側)
tweet B (2026-07-31): quote_id=0, quoted_id=A   → Bのほうが実際にAを引用している側
```
日付の前後関係(引用する側は引用される側より後の投稿)と実際のX上の表示(Bのツイートに
Aが引用カードとして埋め込まれている)の両方と整合するのは`quoted_id`を使う方。

`models.py`の`build_threads`内で参照するフィールドを`quote_id`→`quoted_id`に修正し、
両アカウントのビューアを再生成して修正を確認した。

## 2026-09-17 Tkinter GUI + 単一バイナリ配布対応

将来Windows/Mac/Linux向けに単一実行ファイルとしてGitHub公開したいという要望を受け、
Tkinter製GUI(`xarchive-gui`)を追加した。DiscordChatExporterのようなネイティブウィンドウ
アプリを目指し、追加の外部GUIライブラリは使わずtkinter(標準ライブラリ)のみで実装。

### リファクタリング

`fetch.py`/`models.py`/`render.py`がそれまで暗黙的に「プロジェクトルート配下の`data/`」
という構成に依存していたのを、`data_root`・`cookies_path`を明示的な引数として渡す形に
変更。GUIやパッケージ化バイナリでは「プロジェクトルート」という概念自体が成り立たない
(cookies.txtやpyproject.tomlが存在しない)ため。CLI(`cli.py`)側は従来通り
`find_project_root()`で解決してから渡すことで、既存の使い方に影響が出ないようにした。

`_run_gallery_dl`も`subprocess.run(capture_output=True)`(完了後に一括出力)から
`subprocess.Popen`によるストリーミング実行に変更。GUIのログ欄にリアルタイムで
gallery-dlの出力を流すために必要だったが、CLIの体験も(完了を待たず進捗が見える)
副次的に改善された。

### GUI実装のポイント

- Tkinterはメインスレッド以外からウィジェットを操作できないため、gallery-dl呼び出しは
  `threading.Thread`でバックグラウンド実行し、`queue.Queue`経由でログ行を渡して
  `root.after(100, ...)`のポーリングでメインスレッド側に反映する構成にした
- 設定(Cookieパス・保存先・チェックボックス)は`settings.py`でOS別の標準設定ディレクトリ
  (Windows: `%APPDATA%`、macOS: `~/Library/Application Support`、
  Linux: `$XDG_CONFIG_HOME`)にJSONで永続化し、次回起動時に復元する

### PyInstallerでの単一バイナリ化(Linux上で検証済み)

- `src/xarchive/gui.py`を直接PyInstallerに渡すと、`from . import fetch`のような相対importが
  `ImportError: attempted relative import with no known parent package`で失敗する
  (スクリプトとして実行されると`__main__`扱いになり、パッケージ内の相対importが
  成立しないため)。回避策として`packaging/run_gui.py`という薄いエントリスクリプト
  (`from xarchive.gui import main`という絶対import)を用意し、これをビルド対象にした
- gallery-dlはURLごとに`extractor/`配下のモジュールをimportlibで動的importしているため、
  `--collect-all gallery_dl`を付けないとフリーズ後のバイナリで対応する抽出器が
  見つからずに失敗するリスクがある。実際にLinux上で`--onefile --windowed --collect-all
  gallery_dl`付きでビルドし、生成された単体exeから実際に検証用アカウントの
  ビューア生成(バンドルされたJinja2テンプレート`--add-data`の動作確認)・
  `webbrowser.open`によるブラウザ起動までスクリーンショット付きで動作確認した
  (`xdotool`でGUIを実際に操作)。fetchについても別プロセス(`.venv`経由の通常起動)で
  実際にgallery-dlを叩いて差分取得が成功することを確認済み
- Windows/macOS版はこのLinux環境からはビルドできないため未検証だったが、ユーザーが
  macOS実機で同手順を実行し、ビルド・起動確認に成功(2026-09-17)。`--windowed`指定時、
  PyInstallerはmacOSでは単体バイナリと`.app`バンドルの両方を自動生成することが分かった
  (Linuxでは`.app`は生成されない)。Windows版は依然未検証
  (詳細は`docs/BUILD.md`)

## 2026-09-17 GitHub Actionsリリースビルド追加、macOS zipの不具合修正

タグpush(`v*`)でWindows x64 / Linux x64 / macOS ARM64(`macos-14`)向けに
`xarchive-gui`をビルドし、GitHub Releaseへ添付するワークフロー
(GitHub専用、`.github/workflows/release.yml`。Giteaには置かない)を追加し、
`v0.1.0`で実際に3プラットフォームとも成功することを確認した。

その後、macOS版のzipを解凍すると`.app`ではなく内部の`Contents/`がトップレベルに
展開されてしまう不具合をユーザーから指摘された。原因は`ditto -c -k`が
デフォルトでは指定した`.app`ディレクトリの**内容**をzipのルートに配置する挙動で、
`.app`自体を1つのトップレベルエントリとして含めるには`--keepParent`オプションが
必要だったため。ワークフローに`--keepParent`を追加して修正し、`v0.1.1`として
再タグ・再ビルドした(`v0.1.0`のタグ自体は不変のまま残し、修正版として
バージョンを上げる形にした)。

## 2026-09-17 Windows版で`fetch`が`[WinError 2]`で失敗するバグ

`v0.1.1`をユーザーがWindows実機(別マシン)で実行し、GUIは起動するものの
`取得(全件)`実行時に`[WinError 2] 指定されたファイルが見つかりません。`で失敗する
との報告。原因はビルド検証時の見落としで、Linux/macOSでの単体exe動作確認では
「ビューア生成」ボタンしか実際に試しておらず、`fetch`(gallery-dl呼び出し)自体を
パッケージ化バイナリから実行して確認していなかった。

根本原因: `fetch.py`は`subprocess.Popen(["gallery-dl", ...])`のように外部コマンド名で
gallery-dlを呼んでいたが、これは`gallery-dl`が別途PATH上にインストールされている
前提の呼び方。PyInstallerでフリーズしたバイナリには`--collect-all gallery_dl`で
Pythonライブラリとしては同梱されるが、**独立した`gallery-dl`実行ファイルは存在しない**
ため、PATH解決に失敗して`[WinError 2]`(Linux/macOSなら`FileNotFoundError`相当)に
なっていた。開発環境(venv)では偶然`gallery-dl`コマンドがPATH上にあったため
気づけなかった。

対策として、`gallery-dl`という外部コマンド名で呼ぶのをやめ、以下の2パターンに
分岐する自己解決方式にした(`fetch._gallery_dl_argv()`):
- 通常実行時: `[sys.executable, "-m", "gallery_dl", ...]`(モジュール実行、PATH不要)
- PyInstallerフリーズ時(`sys.frozen`): `[sys.executable, "--xarchive-run-gallery-dl", ...]`
  として**自分自身の実行ファイルを引数付きで再実行**。`packaging/run_gui.py`側で
  この専用フラグを検出した場合はGUIではなく`gallery_dl.main()`を直接呼ぶよう分岐した

Linux上でこの自己再実行方式を実際に検証: `./dist/xarchive-gui --xarchive-run-gallery-dl
--version`で単独動作を確認した上で、GUIから実際に`fetch`ボタンを押してエラーなく
完了することまで確認した(以前の検証で漏れていた「fetchボタンを実際に押す」テストを
今回はきちんと実施した)。`v0.1.2`としてリリース。Windows実機での再確認はユーザー側で
実施予定。

## 2026-09-18 アカウントアイコンをfaviconとして取得・設定

ユーザーから「gallery-dlでアイコンまで取れないか、取れるならfaviconにしたい」との要望。
調査すると、gallery-dl自体にアイコン専用の取得オプションはないが、`_transform_user`が
`author.profile_image`として既にフルサイズのアイコンURLを毎ツイートのメタデータに
含めていることが分かった(`_normal.`サフィックスを除去済みの高解像度版)。そのため
gallery-dl側の設定変更は不要で、既存の保存済みメタデータから該当アカウント本人の
投稿(`author.name`が対象ユーザー名と一致するもの、最新日時のものを優先)を探して
そのURLを直接`urllib.request`でダウンロードする方式にした(`fetch.py`の
`_download_avatar`、`fetch()`成功後に毎回呼び出し・上書き保存)。

保存先は`data/<username>/avatar.<拡張子>`。`render.py`の`build()`で
`avatar.*`をglobして見つかれば`<link rel="icon">`をテンプレートに埋め込み、
無ければ何も出さない(既存データとの後方互換、取得失敗時も処理を止めない)。

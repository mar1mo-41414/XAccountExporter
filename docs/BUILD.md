# 単一バイナリのビルド手順(PyInstaller)

GUI(`xarchive-gui`)をWindows/Mac/Linux向けの単一実行ファイルにパッケージする手順。
PyInstallerは**クロスコンパイル不可**なので、各OS用のバイナリはそのOS上でビルドする必要がある。

## 事前準備

```bash
uv venv .venv
uv pip install -e ".[build]"
```

## ビルドコマンド

```bash
# Linux / macOS
.venv/bin/pyinstaller --onefile --windowed --name xarchive-gui \
  --collect-all gallery_dl \
  --add-data "src/xarchive/templates:xarchive/templates" \
  --paths src \
  packaging/run_gui.py
```

Windows(PowerShell、`--add-data`の区切り文字が`;`になる点に注意):

```powershell
.venv\Scripts\pyinstaller --onefile --windowed --name xarchive-gui `
  --collect-all gallery_dl `
  --add-data "src/xarchive/templates;xarchive/templates" `
  --paths src `
  packaging/run_gui.py
```

生成物は`dist/xarchive-gui`(Windowsは`dist/xarchive-gui.exe`、macOSは`.app`にはならず
単一実行ファイルのまま)。

## なぜこのオプションが必要か

- `--collect-all gallery_dl`: gallery-dlはURLに応じて`extractor/`以下のモジュールを
  文字列ベースでimportlib動的importしている。これを付けないとPyInstallerの静的解析で
  拾いきれず、ビルド後の実行ファイルで対応する抽出器が見つからないエラーになる
  (Linuxでの単体ビルド・実行では実際にこのオプション込みで正常動作することを確認済み)。
- `--add-data ".../templates:xarchive/templates"`: ビューア生成に使う
  `index.html.j2`はPythonコードではなくテンプレートファイルなので、明示的にバンドル
  データとして含めないと実行ファイルに入らない。
- `packaging/run_gui.py`をエントリポイントにしているのは、`src/xarchive/gui.py`を
  そのままスクリプトとして渡すと`from . import fetch`のような相対importが
  `ImportError: attempted relative import with no known parent package`で失敗するため。
  `run_gui.py`は`from xarchive.gui import main`という絶対importにすることでこれを回避している。
- `--windowed`: Windowsでの起動時に黒いコンソールウィンドウが一緒に開かないようにする。

## 動作確認手順

1. `dist/xarchive-gui`(または`.exe`)をビルド元と別のディレクトリにコピーして実行し、
   ソースツリーに依存せず単体で起動することを確認する
2. GUIから任意アカウントの`fetch`(差分取得)を実行し、gallery-dlの動的extractor読み込みが
   正常に動作することを確認する(ここで失敗する場合は`--collect-all gallery_dl`まわりの
   問題である可能性が高い)
3. 「ビューア生成」でエラーなくHTMLが生成されることを確認する
   (テンプレート同梱が正しくできていないとここで失敗する)

## 既知の制約

- Windows版はこのLinux開発環境からはビルド・動作確認できていない。同じ手順を踏んで
  Windows環境で確認する必要がある
- 将来的に複数OS分を自動ビルドしたい場合は、GitHub ActionsのOSマトリックス
  (`windows-latest` / `macos-latest` / `ubuntu-latest`)でこのビルドコマンドを実行し、
  タグpush時にReleaseへ添付する構成が定石(未実装)

## macOSでの動作確認結果

macOS実機上で上記コマンドでビルド・起動確認済み。`--windowed`指定時、PyInstallerは
`dist/xarchive-gui`(単体バイナリ)と`dist/xarchive-gui.app`(`.app`バンドル)の
**両方を自動生成する**(Linuxでは`.app`は生成されず単体バイナリのみ)。配布時は
通常`.app`の方をZip等でまとめて配る想定で問題ない。

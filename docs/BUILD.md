# 単一バイナリのビルド手順(PyInstaller)

GUI(`xarchive-gui`)・CLI(`xarchive`)をWindows/Mac/Linux向けの単一実行ファイルに
パッケージする手順。PyInstallerは**クロスコンパイル不可**なので、各OS用のバイナリは
そのOS上でビルドする必要がある(GitHub Actionsでのマトリックスビルドが定石。後述)。

## 事前準備

```bash
uv venv .venv
uv pip install -e ".[build]"
```

## ビルドコマンド

```bash
# GUI (Linux / macOS)
.venv/bin/pyinstaller --onefile --windowed --name xarchive-gui \
  --collect-all gallery_dl \
  --add-data "src/xarchive/templates:xarchive/templates" \
  --paths src \
  packaging/run_gui.py

# CLI (Linux / macOS)
.venv/bin/pyinstaller --onefile --name xarchive \
  --collect-all gallery_dl \
  --add-data "src/xarchive/templates:xarchive/templates" \
  --paths src \
  packaging/run_cli.py
```

Windows(PowerShell、`--add-data`の区切り文字が`;`になる点に注意):

```powershell
.venv\Scripts\pyinstaller --onefile --windowed --name xarchive-gui `
  --collect-all gallery_dl `
  --add-data "src/xarchive/templates;xarchive/templates" `
  --paths src `
  packaging/run_gui.py

.venv\Scripts\pyinstaller --onefile --name xarchive `
  --collect-all gallery_dl `
  --add-data "src/xarchive/templates;xarchive/templates" `
  --paths src `
  packaging/run_cli.py
```

生成物は`dist/xarchive-gui`・`dist/xarchive`(Windowsは`.exe`が付く、macOSはGUI版のみ
`.app`バンドルも同時生成される。CLI版はどのOSでも単一実行ファイルのまま)。

## なぜこのオプションが必要か

- `--collect-all gallery_dl`: gallery-dlはURLに応じて`extractor/`以下のモジュールを
  文字列ベースでimportlib動的importしている。これを付けないとPyInstallerの静的解析で
  拾いきれず、ビルド後の実行ファイルで対応する抽出器が見つからないエラーになる。
- `--add-data ".../templates:xarchive/templates"`: ビューア生成に使う
  `index.html.j2`はPythonコードではなくテンプレートファイルなので、明示的にバンドル
  データとして含めないと実行ファイルに入らない。
- `packaging/run_gui.py`・`packaging/run_cli.py`をエントリポイントにしているのは、
  `src/xarchive/gui.py`や`cli.py`をそのままスクリプトとして渡すと`from . import fetch`
  のような相対importが`ImportError: attempted relative import with no known parent
  package`で失敗するため。`run_gui.py`/`run_cli.py`は`from xarchive.gui import main`
  という絶対importにすることでこれを回避している。
- `--windowed`(GUI版のみ): Windowsでの起動時に黒いコンソールウィンドウが一緒に
  開かないようにする。CLI版は逆にコンソール出力が必要なので付けない。

### gallery-dlの呼び出し方(自己再実行方式)

パッケージ化されたバイナリの中には、外部コマンドとしての`gallery-dl`は**存在しない**
(`--collect-all gallery_dl`はPythonライブラリとして同梱するだけで、独立した実行ファイルは
作られない)。そのため`xarchive/fetch.py`は素朴に`subprocess.Popen(["gallery-dl", ...])`を
呼ぶのではなく、以下のように分岐している(`fetch._gallery_dl_argv()`):

- 通常のPython実行時(venv経由のCLI/GUI): `[sys.executable, "-m", "gallery_dl", ...]`
  (`python -m gallery_dl`は同じ環境にインストールされたgallery-dlを確実に呼べる)
- PyInstallerでフリーズ済みの場合(`sys.frozen`): `[sys.executable, "--xarchive-run-gallery-dl", ...]`
  として**自分自身の実行ファイルを引数付きで再実行**する(PyInstaller onefileバイナリを
  「自分自身をサブプロセスとして再起動し、別モードで動かす」ための標準的な回避策)。
  `dispatch_gallery_dl_reexec()`(`fetch.py`)がこのフラグを検知した場合、GUI/CLIを
  起動せず`gallery_dl.main()`を直接呼び出す。`run_gui.py`・`run_cli.py`は両方とも
  起動直後にこの関数を呼ぶことでこの仕組みを共有している。

## 動作確認手順

1. `dist/xarchive-gui`・`dist/xarchive`(または`.exe`)をビルド元と別のディレクトリに
   コピーして実行し、ソースツリーに依存せず単体で起動することを確認する
2. `fetch`(差分取得)を実際に実行し、gallery-dlの動的extractor読み込み・自己再実行方式が
   正常に動作することを確認する(ここで失敗する場合は`--collect-all gallery_dl`まわりの
   問題である可能性が高い)。**「ビューア生成」だけの確認では不十分**
   (`[WinError 2]`等、gallery-dl自体の呼び出し失敗はfetch実行時にしか現れないため
   必ずfetchまで実際に実行して確認すること)
3. `build`でエラーなくHTMLが生成されることを確認する
   (テンプレート同梱が正しくできていないとここで失敗する)

## GitHub Actionsでの自動ビルド

`.github/workflows/release.yml`(GitHub専用、Giteaリポジトリには置いていない)で
`v*`タグのpush時にWindows x64 / Linux x64 / macOS ARM64(`macos-14`)向けに
GUI・CLI両方をビルドし、GitHub Releaseへ添付する。

## 既知の制約

- Windows版はこのLinux開発環境からは手元でのビルド・動作確認ができない
  (GitHub Actions上でのビルドは成功している)

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path
from typing import Callable

LOGIN_ERROR_HINTS = (
    "login required",
    "not logged in",
    "could not log in",
    "cookies are invalid",
    "cookies have expired",
)

OnLine = Callable[[str], None]

# PyInstallerでの単一バイナリ化時、外部の`gallery-dl`コマンドはPATH上に存在しない
# (パッケージ化バイナリはgallery_dlをPythonライブラリとしてしか同梱していないため)。
# そのためsubprocessでは常に「今動いているPythonインタプリタ(通常時)」または
# 「自分自身の実行ファイル(PyInstallerフリーズ時)」を再実行し、後者の場合は
# packaging/run_gui.pyがこのフラグを見て`gallery_dl.main()`を直接呼び出す。
GALLERY_DL_REEXEC_FLAG = "--xarchive-run-gallery-dl"


def _gallery_dl_argv() -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable, GALLERY_DL_REEXEC_FLAG]
    return [sys.executable, "-m", "gallery_dl"]


def find_project_root(start: Path | None = None) -> Path:
    """cookies.txt と pyproject.toml が両方あるディレクトリをプロジェクトルートとみなす
    (CLIの既定の保存先/Cookie探索にのみ使う。GUIやパッケージ化バイナリではこの前提が
    成り立たないため、fetch()/check_deleted()はdata_root/cookies_pathを明示的に受け取る)"""
    cur = (start or Path.cwd()).resolve()
    for candidate in (cur, *cur.parents):
        if (candidate / "cookies.txt").exists() and (candidate / "pyproject.toml").exists():
            return candidate
    return cur


def data_dir(data_root: Path, username: str) -> Path:
    return data_root / username


def build_gallery_dl_config(
    cookies_path: Path,
    media_dir: Path,
    posts_dir: Path,
    sleep_request: str,
    sleep: str,
    include_retweets: bool = False,
    include_replies: bool = True,
) -> dict:
    return {
        "extractor": {
            "twitter": {
                "cookies": str(cookies_path),
                "text-tweets": True,
                "retweets": include_retweets,
                "replies": include_replies,
                "quoted": True,
                "videos": True,
                "sleep-request": sleep_request,
                "sleep": sleep,
                "filename": "{tweet_id}_{num}.{extension}",
                "directory": [],
                "base-directory": str(media_dir) + "/",
                "postprocessors": [
                    {
                        "name": "metadata",
                        "event": "post",
                        "directory": str(posts_dir) + "/",
                        "filename": "{tweet_id}.json",
                        "skip": True,
                        "indent": 2,
                    }
                ],
            }
        }
    }


class _Result:
    def __init__(self, returncode: int, output: str):
        self.returncode = returncode
        self.output = output


def _run_gallery_dl(config: dict, args: list[str], on_line: OnLine | None = None) -> _Result:
    with tempfile.NamedTemporaryFile(
        "w", suffix=".json", delete=False, encoding="utf-8"
    ) as fp:
        json.dump(config, fp, ensure_ascii=False)
        conf_path = fp.name
    try:
        cmd = _gallery_dl_argv() + ["--config", conf_path, *args]
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        lines: list[str] = []
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.rstrip("\n")
            lines.append(line)
            if on_line:
                on_line(line)
        proc.wait()
        return _Result(proc.returncode, "\n".join(lines))
    finally:
        Path(conf_path).unlink(missing_ok=True)


def _download_avatar(data_dir: Path, username: str, on_line: OnLine | None) -> None:
    """保存済み投稿から対象アカウント自身のアイコンURLを探し、
    data_dir/avatar.<ext> として保存する(ビューアのfaviconに使う)。
    アイコンはgallery-dlの投稿メタデータに既に含まれているため、
    gallery-dl自体には専用の取得オプションはなく、ここで直接ダウンロードする。"""
    from . import models

    def emit(msg: str) -> None:
        print(msg) if on_line is None else on_line(msg)

    posts = models.load_posts(data_dir)
    if not posts:
        return
    uname_lower = username.lower()
    candidates = [
        p for p in posts.values()
        if (p.get("author") or {}).get("name", "").lower() == uname_lower
        and (p.get("author") or {}).get("profile_image")
    ]
    if not candidates:
        return
    candidates.sort(key=lambda p: p.get("date") or "", reverse=True)
    url = candidates[0]["author"]["profile_image"]

    ext = Path(url.split("?", 1)[0]).suffix or ".jpg"
    for old in data_dir.glob("avatar.*"):
        old.unlink(missing_ok=True)
    dest = data_dir / f"avatar{ext}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            dest.write_bytes(resp.read())
        emit(f"[xarchive] アイコンを保存しました: {dest}")
    except Exception as exc:  # noqa: BLE001
        emit(f"[xarchive] アイコンの取得に失敗しました(スキップ): {exc}")


def fetch(
    username: str,
    data_root: Path,
    cookies_path: Path,
    *,
    full: bool = False,
    sleep_request: str = "3.0-6.0",
    sleep: str = "1.0-3.0",
    abort_after: int = 5,
    include_retweets: bool = False,
    include_replies: bool = True,
    on_line: OnLine | None = None,
) -> int:
    def emit(msg: str) -> None:
        print(msg) if on_line is None else on_line(msg)

    d = data_dir(data_root, username)
    (d / "media").mkdir(parents=True, exist_ok=True)
    (d / "posts").mkdir(parents=True, exist_ok=True)

    config = build_gallery_dl_config(
        cookies_path, d / "media", d / "posts", sleep_request, sleep,
        include_retweets=include_retweets, include_replies=include_replies,
    )

    args: list[str] = []
    archive_path = d / "archive.sqlite3"
    args += ["--download-archive", str(archive_path)]
    if not full:
        args += ["-A", str(abort_after)]
    args.append(f"https://x.com/{username}")

    emit(f"[xarchive] gallery-dl 実行中... (full={full}, sleep-request={sleep_request})")
    result = _run_gallery_dl(config, args, on_line=on_line)

    if result.returncode != 0:
        lowered = result.output.lower()
        if any(hint in lowered for hint in LOGIN_ERROR_HINTS):
            emit(
                "[xarchive] 警告: 捨て垢のcookies.txtが無効(期限切れ/ログアウト済み)の"
                "可能性があります。ブラウザから再度Netscape形式でエクスポートし直してください。"
            )
        else:
            emit(f"[xarchive] gallery-dl が終了コード {result.returncode} で終了しました。")
        return result.returncode

    _download_avatar(d, username, on_line)

    emit(f"[xarchive] 完了: {d}")
    return 0


def check_deleted(
    username: str,
    data_root: Path,
    cookies_path: Path,
    *,
    limit: int = 100,
    sleep_request: str = "3.0-6.0",
    on_line: OnLine | None = None,
) -> int:
    """直近limit件の保存済みツイートが現在も存在するか確認し、
    削除済みと判定したIDを data/<user>/deleted.json に追記する(既存分は上書きしない)。
    ネットワークアクセスを伴う任意機能のためデフォルトでは呼ばれない。"""
    from . import models

    def emit(msg: str) -> None:
        print(msg) if on_line is None else on_line(msg)

    d = data_dir(data_root, username)
    posts = models.load_posts(d)
    if not posts:
        emit("[xarchive] 保存済み投稿がありません。先に fetch を実行してください。")
        return 1

    deleted_path = d / "deleted.json"
    known_deleted = models.load_deleted(d)

    candidates = sorted(posts.values(), key=lambda p: p.get("date") or "", reverse=True)[:limit]
    config = build_gallery_dl_config(cookies_path, d / "media", d / "posts", sleep_request, "0")

    newly_deleted = []
    for post in candidates:
        tweet_id = post["tweet_id"]
        if tweet_id in known_deleted:
            continue
        url = f"https://x.com/i/status/{tweet_id}"
        result = _run_gallery_dl(config, ["--simulate", url])
        lowered = result.output.lower()
        # gallery-dlは削除済み/非公開ツイートでも終了コード0(正常終了)を返し、
        # "[twitter][info] No results for <url>" とだけ出力する(実機検証済み)。
        # 終了コードでは判定できないため、出力テキストのみで判定する。
        not_found = "no results for" in lowered
        if not_found:
            emit(f"[xarchive] 削除を検知: {tweet_id}")
            newly_deleted.append(tweet_id)

    if newly_deleted:
        all_deleted = sorted(known_deleted | set(newly_deleted))
        deleted_path.write_text(
            json.dumps(all_deleted, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        emit(f"[xarchive] {len(newly_deleted)}件の削除を記録しました: {deleted_path}")
    else:
        emit("[xarchive] 削除は検知されませんでした。")
    return 0

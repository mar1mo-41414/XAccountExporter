from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zlib
from datetime import datetime
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
# dispatch_gallery_dl_reexec()がこのフラグを見て`gallery_dl.main()`を直接呼び出す。
# GUI(packaging/run_gui.py)・CLI(packaging/run_cli.py)の両エントリスクリプトが
# 起動直後にdispatch_gallery_dl_reexec()を呼ぶことでこの仕組みを共有している。
GALLERY_DL_REEXEC_FLAG = "--xarchive-run-gallery-dl"


def _gallery_dl_argv() -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable, GALLERY_DL_REEXEC_FLAG]
    return [sys.executable, "-m", "gallery_dl"]


def dispatch_gallery_dl_reexec() -> None:
    """フリーズ実行ファイルがgallery-dl再実行フラグ付きで呼ばれていた場合、
    gallery_dl.main()を実行してプロセスを終了する(戻らない)。
    フラグが無ければ何もせずそのまま処理を返す。"""
    if len(sys.argv) > 1 and sys.argv[1] == GALLERY_DL_REEXEC_FLAG:
        sys.argv = ["gallery-dl", *sys.argv[2:]]
        import gallery_dl

        raise SystemExit(gallery_dl.main())


def find_project_root(start: Path | None = None) -> Path:
    """pyproject.toml があるディレクトリをプロジェクトルートとみなす
    (CLIの既定の保存先/Cookie探索にのみ使う。GUIやパッケージ化バイナリではこの前提が
    成り立たないため、fetch()/check_deleted()はdata_root/cookies_pathを明示的に受け取る)。
    以前はcookies.txtの存在も条件にしていたが、Cookieをディレクトリにまとめて
    --cookiesで指定する運用(resolve_cookies参照)ではプロジェクトルート直下に
    cookies.txtが無いことがあるため、pyproject.tomlのみで判定するようにした。"""
    cur = (start or Path.cwd()).resolve()
    for candidate in (cur, *cur.parents):
        if (candidate / "pyproject.toml").exists():
            return candidate
    return cur


def data_dir(data_root: Path, username: str) -> Path:
    return data_root / username


# 削除(凍結)・非公開(鍵垢)になったアカウントの退避先(data_root直下)。ドット始まりなので
# fetch-all等のアカウント列挙(`.`始まりを除外)の対象にならない。
DELETED_DIR_NAME = ".deleted"

# gallery-dlが出力するエラー行。終了コードは削除/凍結(NotFoundError)=4、鍵垢(AuthorizationError)=16。
# 注意: 鍵垢のメッセージは "<screen_name>'s Tweets are protected"(所有格の's付き)。
# なお削除・改名で出る "NotFoundError: Requested user could not be found" は
# 改名の可能性があり誤判定が怖いため対象外にしている。
_SUSPENDED_RE = re.compile(r"NotFoundError:\s*User is suspended", re.IGNORECASE)
_PROTECTED_RE = re.compile(
    r"AuthorizationError:\s*(?P<name>\w+)(?:'s)?\s+Tweets are protected", re.IGNORECASE
)


def detect_unavailable_account(output: str, username: str) -> str | None:
    """gallery-dlの出力から、対象アカウントが削除・凍結("suspended")/鍵垢化("protected")
    されたことを判定する。どちらでもなければNone。"""
    if _SUSPENDED_RE.search(output):
        return "suspended"
    for m in _PROTECTED_RE.finditer(output):
        if m.group("name").lower() == username.lower():
            return "protected"
    return None


def archive_unavailable_account(data_root: Path, username: str, reason: str) -> Path | None:
    """data_root/<username> を data_root/.deleted/<username> へ移動し、
    data_root/.deleted/accounts.json に記録を追記する。移動先が既に存在する場合は
    タイムスタンプ付きの名前にして上書きを避ける。移動元が無ければNone。"""
    src = data_dir(data_root, username)
    if not src.is_dir():
        return None
    deleted_root = data_root / DELETED_DIR_NAME
    deleted_root.mkdir(parents=True, exist_ok=True)
    now = datetime.now()
    dest = deleted_root / username
    if dest.exists():
        dest = deleted_root / f"{username}_{now:%Y%m%d-%H%M%S}"
    posts_count = sum(1 for _ in (src / "posts").glob("*.json")) if (src / "posts").is_dir() else 0
    shutil.move(str(src), str(dest))

    record_path = deleted_root / "accounts.json"
    records: list = []
    if record_path.is_file():
        try:
            records = json.loads(record_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            records = []
    records.append({
        "username": username,
        "reason": reason,
        "detected_at": now.isoformat(timespec="seconds"),
        "moved_to": dest.name,
        "posts": posts_count,
    })
    record_path.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    return dest


# 表示用。"suspended"はXが返す「User is suspended」だが、削除済み・存在しないIDでも同じ応答に
# なる(実機確認済み)ため、凍結とは限らない旨を表示に含める。
REASON_LABELS = {"suspended": "削除(凍結)されている", "protected": "非公開(鍵垢)になっている"}


def find_archived_record(data_root: Path, username: str) -> dict | None:
    """data_root/.deleted/accounts.json から、指定アカウントの最新の退避記録を返す
    (無ければNone)。GUIが「このアカウントは退避済み」と案内するために使う。"""
    record_path = data_root / DELETED_DIR_NAME / "accounts.json"
    if not record_path.is_file():
        return None
    try:
        records = json.loads(record_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    for rec in reversed(records):
        if str(rec.get("username", "")).lower() == username.lower():
            return rec
    return None


def resolve_cookies(cookies_path: Path, username: str) -> Path:
    """cookies_pathがディレクトリの場合、中の*.txtファイル群から対象アカウント名の
    CRC32ハッシュに基づいて決定的に1つを選ぶ(同じアカウントは常に同じCookieファイルを
    使う。複数の捨て垢に負荷を分散しつつ、取得対象ごとの一貫性は保つ狙い)。
    ファイルの場合はそのまま返す(従来通りの単一Cookie運用)。"""
    if cookies_path.is_dir():
        pool = sorted(cookies_path.glob("*.txt"))
        if not pool:
            raise FileNotFoundError(
                f"{cookies_path} 内に .txt ファイルが見つかりません。"
            )
        idx = zlib.crc32(username.encode("utf-8")) % len(pool)
        return pool[idx]
    return cookies_path


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

    try:
        resolved_cookies = resolve_cookies(cookies_path, username)
    except FileNotFoundError as exc:
        emit(f"[xarchive] エラー: {exc}")
        return 1
    if cookies_path.is_dir():
        emit(f"[xarchive] 使用するCookie: {resolved_cookies.name}")

    d = data_dir(data_root, username)
    (d / "media").mkdir(parents=True, exist_ok=True)
    (d / "posts").mkdir(parents=True, exist_ok=True)

    config = build_gallery_dl_config(
        resolved_cookies, d / "media", d / "posts", sleep_request, sleep,
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
        reason = detect_unavailable_account(result.output, username)
        if reason:
            label = REASON_LABELS[reason]
            dest = archive_unavailable_account(data_root, username, reason)
            emit(
                f"[xarchive] {username} は{label}ため取得できません。"
                f"データを {dest} へ移動し、記録しました"
                f"({data_root / DELETED_DIR_NAME / 'accounts.json'})。"
            )
            # 想定内の結果として正常終了扱いにする。呼び出し側はdata_root/<username>が
            # 無くなっている点で判別できる(fetch-allはその場合buildをスキップする)。
            return 0

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

    try:
        resolved_cookies = resolve_cookies(cookies_path, username)
    except FileNotFoundError as exc:
        emit(f"[xarchive] エラー: {exc}")
        return 1
    if cookies_path.is_dir():
        emit(f"[xarchive] 使用するCookie: {resolved_cookies.name}")

    d = data_dir(data_root, username)
    posts = models.load_posts(d)
    if not posts:
        emit("[xarchive] 保存済み投稿がありません。先に fetch を実行してください。")
        return 1

    deleted_path = d / "deleted.json"
    known_deleted = models.load_deleted(d)

    candidates = sorted(posts.values(), key=lambda p: p.get("date") or "", reverse=True)[:limit]
    config = build_gallery_dl_config(resolved_cookies, d / "media", d / "posts", sleep_request, "0")

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

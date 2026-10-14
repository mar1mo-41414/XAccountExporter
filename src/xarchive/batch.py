"""複数アカウントをまとめて処理する処理(CLIのfetch-all/--allとGUIの「全アカウント」で共通)。"""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator, Sequence

from . import fetch as fetch_mod
from . import render as render_mod

OnLine = Callable[[str], None]


class LockBusy(Exception):
    """別のfetch-all(cronやGUI)が実行中でロックを取れなかった。"""


def list_accounts(data_root: Path) -> list[str]:
    """data/配下の取得済みアカウント(posts/を持つディレクトリ)を名前順で返す。
    `.`始まり(`.deleted`等)は対象外。"""
    if not data_root.is_dir():
        return []
    return sorted(
        p.name for p in data_root.iterdir()
        if p.is_dir() and not p.name.startswith(".") and (p / "posts").is_dir()
    )


@contextmanager
def fetch_all_lock(data_root: Path) -> Iterator[None]:
    """全アカウント取得の多重実行防止ロック(fcntl.flock、非ブロッキング)。
    ロックファイルは削除せず使い回す(プロセスが保持している間だけロック状態になる)。
    fcntlが無い環境(Windows等)ではロック無しで続行する。取れなければLockBusy。"""
    data_root.mkdir(parents=True, exist_ok=True)
    lock_file = open(data_root / ".fetch-all.lock", "w")
    try:
        try:
            import fcntl
            fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except ImportError:
            pass
        except BlockingIOError:
            raise LockBusy() from None
        yield
    finally:
        lock_file.close()


def fetch_accounts(
    usernames: Sequence[str],
    data_root: Path,
    cookies_path: Path,
    *,
    sleep_request: str = "3.0-6.0",
    sleep: str = "1.0-3.0",
    abort_after: int = 5,
    include_retweets: bool = False,
    include_replies: bool = True,
    on_line: OnLine = print,
) -> int:
    """各アカウントを差分取得し、成功したらビューアも再生成する。失敗しても残りは続行し、
    最後に最後の失敗コードを返す。"""
    on_line(f"[xarchive] {len(usernames)}件のアカウントを順に差分取得します: {', '.join(usernames)}")
    exit_code = 0
    for username in usernames:
        on_line(f"[xarchive] === {username} ===")
        rc = fetch_mod.fetch(
            username, data_root, cookies_path,
            full=False,
            sleep_request=sleep_request,
            sleep=sleep,
            abort_after=abort_after,
            include_retweets=include_retweets,
            include_replies=include_replies,
            on_line=on_line,
        )
        if rc != 0:
            on_line(f"[xarchive] {username} の取得に失敗しました(code={rc})。次へ進みます。")
            exit_code = rc
            continue
        if not (data_root / username).is_dir():
            continue  # 削除(凍結)・鍵垢化で.deletedへ退避されたのでbuild不要
        out = render_mod.build(data_root / username, username)
        on_line(f"[xarchive] {username} のビューアを更新しました: {out}")
    return exit_code


def check_deleted_accounts(
    usernames: Sequence[str],
    data_root: Path,
    cookies_path: Path,
    *,
    limit: int = 100,
    sleep_request: str = "3.0-6.0",
    on_line: OnLine = print,
) -> int:
    """各アカウントの削除検知のあと、結果をビューアに反映するためbuildまで続けて行う。"""
    exit_code = 0
    for username in usernames:
        if len(usernames) > 1:
            on_line(f"[xarchive] === {username} ===")
        rc = fetch_mod.check_deleted(
            username, data_root, cookies_path,
            limit=limit, sleep_request=sleep_request, on_line=on_line,
        )
        if rc != 0:
            on_line(f"[xarchive] {username} の削除確認に失敗しました(code={rc})。")
            exit_code = rc
            continue
        out = render_mod.build(data_root / username, username)
        on_line(f"[xarchive] {username} のビューアを更新しました: {out}")
    return exit_code


def build_accounts(usernames: Sequence[str], data_root: Path, on_line: OnLine = print) -> int:
    for username in usernames:
        out = render_mod.build(data_root / username, username)
        on_line(f"[xarchive] 生成しました: {out}")
    return 0

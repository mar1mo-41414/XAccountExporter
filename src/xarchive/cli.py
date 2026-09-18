from __future__ import annotations

import argparse
import http.server
import sys
from pathlib import Path

from . import fetch as fetch_mod
from . import render as render_mod

# クライアント側の接続切断(動画のシーク・タブを閉じる等)で頻発するが無害なので
# トレースバックを出さずに黙って無視する例外
_QUIET_CONNECTION_ERRORS = (ConnectionResetError, BrokenPipeError, ConnectionAbortedError)


class _ViewerHTTPServer(http.server.ThreadingHTTPServer):
    def handle_error(self, request, client_address):
        if sys.exc_info()[0] in _QUIET_CONNECTION_ERRORS:
            return
        super().handle_error(request, client_address)


def _data_root(root: Path) -> Path:
    return root / "data"


def cmd_fetch(args: argparse.Namespace) -> int:
    root = fetch_mod.find_project_root()
    return fetch_mod.fetch(
        args.username,
        _data_root(root),
        root / "cookies.txt",
        full=args.full,
        sleep_request=args.sleep_request,
        sleep=args.sleep,
        abort_after=args.abort_after,
        include_retweets=args.include_retweets,
        include_replies=not args.no_replies,
        on_line=print,
    )


def cmd_fetch_all(args: argparse.Namespace) -> int:
    """data/ 配下の取得済みアカウントすべてに対して差分取得+ビューア再生成を順に行う。
    cron等での定期実行を想定し、多重実行防止のロックを取る。"""
    root = fetch_mod.find_project_root()
    data_root = _data_root(root)
    data_root.mkdir(parents=True, exist_ok=True)

    lock_path = data_root / ".fetch-all.lock"
    lock_file = open(lock_path, "w")
    try:
        try:
            import fcntl
            fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except ImportError:
            pass  # fcntl非対応環境(Windows等)ではロック無しで続行
        except BlockingIOError:
            print("[xarchive] 既に fetch-all が実行中のためスキップします。", file=sys.stderr)
            return 1

        usernames = sorted(
            p.name for p in data_root.iterdir()
            if p.is_dir() and not p.name.startswith(".") and (p / "posts").is_dir()
        )
        if not usernames:
            print("[xarchive] data/ 配下に取得済みアカウントが見つかりません。")
            return 0

        print(f"[xarchive] {len(usernames)}件のアカウントを順に差分取得します: {', '.join(usernames)}")
        exit_code = 0
        for username in usernames:
            print(f"[xarchive] === {username} ===")
            rc = fetch_mod.fetch(
                username,
                data_root,
                root / "cookies.txt",
                full=False,
                sleep_request=args.sleep_request,
                sleep=args.sleep,
                abort_after=args.abort_after,
                include_retweets=args.include_retweets,
                include_replies=not args.no_replies,
                on_line=print,
            )
            if rc != 0:
                print(f"[xarchive] {username} の取得に失敗しました(code={rc})。次へ進みます。",
                      file=sys.stderr)
                exit_code = rc
                continue
            out = render_mod.build(data_root / username, username)
            print(f"[xarchive] {username} のビューアを更新しました: {out}")
        return exit_code
    finally:
        lock_file.close()


def cmd_check_deleted(args: argparse.Namespace) -> int:
    root = fetch_mod.find_project_root()
    return fetch_mod.check_deleted(
        args.username,
        _data_root(root),
        root / "cookies.txt",
        limit=args.limit,
        sleep_request=args.sleep_request,
        on_line=print,
    )


def cmd_build(args: argparse.Namespace) -> int:
    root = fetch_mod.find_project_root()
    out = render_mod.build(_data_root(root) / args.username, args.username)
    print(f"[xarchive] 生成しました: {out}")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    root = fetch_mod.find_project_root()
    serve_dir = _data_root(root) / args.username
    index = serve_dir / "index.html"
    if not index.is_file():
        print(f"[xarchive] {index} がありません。先に `xarchive build {args.username}` を実行してください。",
              file=sys.stderr)
        return 1

    handler_cls = lambda *a, **kw: http.server.SimpleHTTPRequestHandler(  # noqa: E731
        *a, directory=str(serve_dir), **kw
    )
    with _ViewerHTTPServer((args.host, args.port), handler_cls) as httpd:
        print(f"[xarchive] http://{args.host}:{args.port}/ で配信中 (Ctrl+Cで終了)")
        if args.host == "0.0.0.0":
            print("[xarchive] 警告: 同一ネットワーク上の他端末からもアクセス可能です"
                  "(取得済みの投稿データが閲覧されます)。信頼できるネットワークでのみ使用してください。")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="xarchive",
        description="X(Twitter)の指定アカウントの投稿を保存し、オフライン閲覧用HTMLを生成する",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_fetch = sub.add_parser("fetch", help="投稿を取得(差分更新)")
    p_fetch.add_argument("username", help="対象アカウントのスクリーンネーム(@なし)")
    p_fetch.add_argument("--full", action="store_true", help="全件再取得する")
    p_fetch.add_argument("--include-retweets", action="store_true",
                          help="単独リツイート(本文なしの純粋なRT)も含める(既定: 含めない)")
    p_fetch.add_argument("--no-replies", action="store_true",
                          help="リプライ(返信投稿)を含めない(既定: 含める)")
    p_fetch.add_argument("--sleep-request", default="3.0-6.0",
                          help="リクエスト間隔の秒数範囲 (既定: 3.0-6.0)")
    p_fetch.add_argument("--sleep", default="1.0-3.0",
                          help="ファイルダウンロード間隔の秒数範囲 (既定: 1.0-3.0)")
    p_fetch.add_argument("--abort-after", type=int, default=5,
                          help="差分更新時、何件連続でスキップしたら打ち切るか (既定: 5)")
    p_fetch.set_defaults(func=cmd_fetch)

    p_fetch_all = sub.add_parser(
        "fetch-all", help="data/配下の全アカウントを順に差分取得+ビューア再生成(cron向け)"
    )
    p_fetch_all.add_argument("--include-retweets", action="store_true",
                              help="単独リツイートも含める(既定: 含めない)")
    p_fetch_all.add_argument("--no-replies", action="store_true",
                              help="リプライを含めない(既定: 含める)")
    p_fetch_all.add_argument("--sleep-request", default="3.0-6.0")
    p_fetch_all.add_argument("--sleep", default="1.0-3.0")
    p_fetch_all.add_argument("--abort-after", type=int, default=5)
    p_fetch_all.set_defaults(func=cmd_fetch_all)

    p_check = sub.add_parser("check-deleted", help="保存済み投稿の削除有無を確認(任意・要ネットワーク)")
    p_check.add_argument("username")
    p_check.add_argument("--limit", type=int, default=100, help="確認する直近投稿の件数上限")
    p_check.add_argument("--sleep-request", default="3.0-6.0")
    p_check.set_defaults(func=cmd_check_deleted)

    p_build = sub.add_parser("build", help="取得済みデータからHTMLビューアを生成")
    p_build.add_argument("username")
    p_build.set_defaults(func=cmd_build)

    p_serve = sub.add_parser("serve", help="生成済みビューアを簡易HTTPサーバーで配信")
    p_serve.add_argument("username")
    p_serve.add_argument("--port", type=int, default=8000, help="配信ポート番号 (既定: 8000)")
    p_serve.add_argument("--host", default="127.0.0.1",
                          help="バインドするアドレス。0.0.0.0を指定すると同一ネットワーク上の"
                               "他端末からもアクセス可能になる (既定: 127.0.0.1)")
    p_serve.set_defaults(func=cmd_serve)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

import sys

from xarchive.fetch import GALLERY_DL_REEXEC_FLAG


def main() -> None:
    # PyInstallerでフリーズした単一実行ファイルには外部の`gallery-dl`コマンドが
    # 存在しないため、fetch.pyはこの実行ファイル自身をこのフラグ付きで再実行して
    # gallery-dlを動かす(詳細はxarchive/fetch.pyのコメント参照)。
    if len(sys.argv) > 1 and sys.argv[1] == GALLERY_DL_REEXEC_FLAG:
        sys.argv = ["gallery-dl", *sys.argv[2:]]
        import gallery_dl

        raise SystemExit(gallery_dl.main())

    from xarchive.gui import main as gui_main

    gui_main()


if __name__ == "__main__":
    main()

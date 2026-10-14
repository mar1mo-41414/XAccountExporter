from __future__ import annotations

import queue
import threading
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from . import batch
from . import fetch as fetch_mod
from .settings import Settings


class _TaskDone:
    def __init__(self, returncode: int):
        self.returncode = returncode


class _Notice:
    """ワーカースレッドから、メインスレッドでダイアログを出してもらうための通知。"""

    def __init__(self, title: str, text: str):
        self.title = title
        self.text = text


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("xarchive - X投稿アーカイブ&ビューア")
        self.geometry("760x580")
        self.minsize(600, 440)

        self.settings = Settings.load()
        self.running = False
        self.queue: queue.Queue = queue.Queue()

        self.username_var = tk.StringVar(value=self.settings.username)
        self.cookies_var = tk.StringVar(value=self.settings.cookies_path)
        self.cookies_mode_var = tk.StringVar(value=self.settings.cookies_mode or "file")
        self.data_root_var = tk.StringVar(value=self.settings.data_root)
        self.retweets_var = tk.BooleanVar(value=self.settings.include_retweets)
        self.replies_var = tk.BooleanVar(value=self.settings.include_replies)
        self.target_var = tk.StringVar(value="all" if self.settings.target_all else "one")

        self._build_widgets()
        self._refresh_controls()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(100, self._poll_queue)

    # ---- UI構築 ----

    def _build_widgets(self) -> None:
        pad = {"padx": 8, "pady": 4}

        form = ttk.Frame(self)
        form.pack(fill="x", **pad)
        form.columnconfigure(1, weight=1)

        ttk.Label(form, text="アカウント名 (@なし)").grid(row=0, column=0, sticky="w")
        self.username_entry = ttk.Entry(form, textvariable=self.username_var)
        self.username_entry.grid(row=0, column=1, sticky="ew", padx=4)

        ttk.Label(form, text="Cookie").grid(row=1, column=0, sticky="w")
        ttk.Entry(form, textvariable=self.cookies_var).grid(row=1, column=1, sticky="ew", padx=4)
        ttk.Button(form, text="参照...", command=self._browse_cookies).grid(row=1, column=2)
        cookie_mode_frame = ttk.Frame(form)
        cookie_mode_frame.grid(row=1, column=3, padx=(6, 0))
        ttk.Radiobutton(cookie_mode_frame, text="ファイル", variable=self.cookies_mode_var,
                         value="file").pack(side="left")
        ttk.Radiobutton(cookie_mode_frame, text="フォルダ", variable=self.cookies_mode_var,
                         value="dir").pack(side="left")

        ttk.Label(form, text="保存先ディレクトリ").grid(row=2, column=0, sticky="w")
        ttk.Entry(form, textvariable=self.data_root_var).grid(row=2, column=1, sticky="ew", padx=4)
        ttk.Button(form, text="参照...", command=self._browse_data_root).grid(row=2, column=2)

        opts = ttk.Frame(self)
        opts.pack(fill="x", **pad)
        ttk.Checkbutton(opts, text="単独リツイートを含める", variable=self.retweets_var).pack(side="left")
        ttk.Checkbutton(opts, text="リプライを含める", variable=self.replies_var).pack(side="left", padx=12)

        scope = ttk.Frame(self)
        scope.pack(fill="x", **pad)
        ttk.Label(scope, text="対象:").pack(side="left")
        ttk.Radiobutton(scope, text="上のアカウントのみ", variable=self.target_var, value="one",
                         command=self._refresh_controls).pack(side="left", padx=(6, 0))
        ttk.Radiobutton(scope, text="全アカウント(保存先配下すべて)", variable=self.target_var,
                         value="all", command=self._refresh_controls).pack(side="left", padx=(6, 0))

        btns = ttk.Frame(self)
        btns.pack(fill="x", **pad)
        self.buttons: dict[str, ttk.Button] = {}
        for key, text, cmd in [
            ("fetch", "取得(差分)", lambda: self._do_fetch(full=False)),
            ("full", "全件取得", lambda: self._do_fetch(full=True)),
            ("check", "削除確認", self._do_check_deleted),
            ("build", "ビューア生成", self._do_build),
            ("open", "ビューアを開く", self._do_open),
        ]:
            b = ttk.Button(btns, text=text, command=cmd)
            b.pack(side="left", padx=4)
            self.buttons[key] = b

        self.progress = ttk.Progressbar(self, mode="indeterminate")
        self.progress.pack(fill="x", padx=8, pady=(0, 4))

        log_frame = ttk.Frame(self)
        log_frame.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self.log = ScrolledText(log_frame, state="disabled", wrap="word", height=18)
        self.log.pack(fill="both", expand=True)

    def _all_mode(self) -> bool:
        return self.target_var.get() == "all"

    def _refresh_controls(self) -> None:
        """実行中は全ボタン無効。全アカウント対象では、全件取得(CLIのfetch-allに無い重い操作)と
        ビューアを開く(1アカウント前提)を無効にし、アカウント名欄も無効にする。"""
        all_mode = self._all_mode()
        self.username_entry.state(["disabled"] if all_mode else ["!disabled"])
        for key, b in self.buttons.items():
            disabled = self.running or (all_mode and key in ("full", "open"))
            b.state(["disabled"] if disabled else ["!disabled"])

    # ---- 参照ダイアログ ----

    def _browse_cookies(self) -> None:
        if self.cookies_mode_var.get() == "dir":
            path = filedialog.askdirectory(
                title="Cookie(*.txt)をまとめたディレクトリを選択"
            )
        else:
            path = filedialog.askopenfilename(
                title="Cookieファイル(Netscape形式)を選択",
                filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
            )
        if path:
            self.cookies_var.set(path)

    def _browse_data_root(self) -> None:
        path = filedialog.askdirectory(title="保存先ディレクトリを選択")
        if path:
            self.data_root_var.set(path)

    # ---- 入力検証・設定保存 ----

    def _current_settings(self) -> Settings:
        return Settings(
            username=self.username_var.get().strip(),
            cookies_path=self.cookies_var.get().strip(),
            cookies_mode=self.cookies_mode_var.get(),
            data_root=self.data_root_var.get().strip(),
            include_retweets=self.retweets_var.get(),
            include_replies=self.replies_var.get(),
            target_all=self._all_mode(),
        )

    def _validate(self, *, need_cookies: bool) -> tuple[str, Path, Path] | None:
        """全アカウント対象のときはアカウント名を要求せず、usernameは空文字で返す。"""
        s = self._current_settings()
        s.save()
        if not s.target_all and not s.username:
            messagebox.showwarning("入力不足", "アカウント名を入力してください。")
            return None
        if not s.data_root:
            messagebox.showwarning("入力不足", "保存先ディレクトリを選択してください。")
            return None
        if need_cookies:
            if not s.cookies_path or not Path(s.cookies_path).exists():
                messagebox.showwarning(
                    "入力不足",
                    "有効なCookieファイル(またはCookieをまとめたディレクトリ)を選択してください。",
                )
                return None
        return s.username, Path(s.data_root), Path(s.cookies_path) if s.cookies_path else Path()

    def _targets(self, username: str, data_root: Path) -> list[str] | None:
        """対象アカウント一覧。全アカウント対象で1件も無ければ案内してNone。"""
        if not self._all_mode():
            return [username]
        accounts = batch.list_accounts(data_root)
        if not accounts:
            messagebox.showwarning("アカウントがありません", "保存先に取得済みのアカウントが見つかりません。")
            return None
        return accounts

    # ---- 退避済みアカウントの案内 ----

    @staticmethod
    def _archived_text(username: str, rec: dict) -> str:
        label = fetch_mod.REASON_LABELS.get(rec.get("reason", ""), "取得できない状態")
        return (
            f"{username} は{label}ため取得できませんでした。\n\n"
            f"保存済みのデータは「{fetch_mod.DELETED_DIR_NAME}」フォルダへ移動し、"
            f"記録しました。\n(保存先ディレクトリ/{fetch_mod.DELETED_DIR_NAME}/{rec.get('moved_to', username)})"
        )

    @staticmethod
    def _archived_summary(data_root: Path, usernames: list[str]) -> str | None:
        """全アカウント取得中に退避されたアカウントの一覧文(無ければNone)。"""
        lines = []
        for name in usernames:
            rec = fetch_mod.find_archived_record(data_root, name)
            label = fetch_mod.REASON_LABELS.get((rec or {}).get("reason", ""), "取得できない状態")
            lines.append(f"・{name}: {label}")
        if not lines:
            return None
        return (
            "次のアカウントは取得できなかったため、データを"
            f"「{fetch_mod.DELETED_DIR_NAME}」フォルダへ移動し、記録しました。\n\n" + "\n".join(lines)
        )

    def _warn_no_data(self, data_root: Path, username: str) -> None:
        rec = fetch_mod.find_archived_record(data_root, username)
        if rec:
            messagebox.showwarning(
                "退避済みのアカウント",
                self._archived_text(username, rec).replace("取得できませんでした。", "取得できません。", 1),
            )
        else:
            messagebox.showwarning(
                "データがありません",
                f"{username} のデータが保存先にありません。先に「取得」を実行してください。",
            )

    # ---- ボタン動作 ----

    def _busy(self) -> bool:
        if self.running:
            messagebox.showinfo("実行中", "他の処理が完了するまでお待ちください。")
            return True
        return False

    def _do_fetch(self, *, full: bool) -> None:
        if self._busy():
            return
        v = self._validate(need_cookies=True)
        if v is None:
            return
        username, data_root, cookies_path = v
        include_retweets = self.retweets_var.get()
        include_replies = self.replies_var.get()

        if self._all_mode():
            # 全件取得は全アカウント対象では無効化している(CLIのfetch-allも差分のみ)
            if not batch.list_accounts(data_root):
                messagebox.showwarning("アカウントがありません", "保存先に取得済みのアカウントが見つかりません。")
                return
            self._clear_log()
            self._append_log("[xarchive] 全アカウントの差分取得を開始します...")

            def task_all(on_line):
                try:
                    with batch.fetch_all_lock(data_root):
                        usernames = batch.list_accounts(data_root)
                        rc = batch.fetch_accounts(
                            usernames, data_root, cookies_path,
                            include_retweets=include_retweets,
                            include_replies=include_replies,
                            on_line=on_line,
                        )
                except batch.LockBusy:
                    msg = "既に全アカウント取得(cron等)が実行中のため中止しました。"
                    on_line(f"[xarchive] {msg}")
                    self.queue.put(_Notice("実行中", msg))
                    return 1
                archived = sorted(set(usernames) - set(batch.list_accounts(data_root)))
                summary = self._archived_summary(data_root, archived)
                if summary:
                    self.queue.put(_Notice("アカウントを退避しました", summary))
                return rc

            self._start(task_all)
            return

        self._clear_log()
        self._append_log(f"[xarchive] {username} の{'全件' if full else '差分'}取得を開始します...")

        def task(on_line):
            rc = fetch_mod.fetch(
                username, data_root, cookies_path,
                full=full,
                include_retweets=include_retweets,
                include_replies=include_replies,
                on_line=on_line,
            )
            # 削除(凍結)・鍵垢化で退避された場合、fetch()は0を返しdata/<username>が無くなる
            if rc == 0 and not (data_root / username).is_dir():
                rec = fetch_mod.find_archived_record(data_root, username)
                if rec:
                    self.queue.put(_Notice("アカウントを退避しました", self._archived_text(username, rec)))
            return rc

        self._start(task)

    def _do_check_deleted(self) -> None:
        if self._busy():
            return
        v = self._validate(need_cookies=True)
        if v is None:
            return
        username, data_root, cookies_path = v
        if not self._all_mode() and not (data_root / username).is_dir():
            self._warn_no_data(data_root, username)
            return
        targets = self._targets(username, data_root)
        if targets is None:
            return
        self._clear_log()
        self._append_log(
            "[xarchive] 全アカウントの削除確認を開始します..." if self._all_mode()
            else f"[xarchive] {username} の削除確認を開始します..."
        )

        def task(on_line):
            return batch.check_deleted_accounts(
                targets, data_root, cookies_path, on_line=on_line,
            )

        self._start(task)

    def _do_build(self) -> None:
        if self._busy():
            return
        v = self._validate(need_cookies=False)
        if v is None:
            return
        username, data_root, _cookies = v
        if not self._all_mode() and not (data_root / username).is_dir():
            self._warn_no_data(data_root, username)
            return
        targets = self._targets(username, data_root)
        if targets is None:
            return
        self._clear_log()
        self._append_log(
            "[xarchive] 全アカウントのビューアを生成します..." if self._all_mode()
            else f"[xarchive] {username} のビューアを生成します..."
        )

        def task(on_line):
            try:
                return batch.build_accounts(targets, data_root, on_line=on_line)
            except Exception as exc:  # noqa: BLE001
                on_line(f"[xarchive] エラー: {exc}")
                return 1

        self._start(task)

    def _do_open(self) -> None:
        s = self._current_settings()
        if not s.username or not s.data_root:
            messagebox.showwarning("入力不足", "アカウント名と保存先ディレクトリを入力してください。")
            return
        index = Path(s.data_root) / s.username / "index.html"
        if not index.is_file():
            if not (Path(s.data_root) / s.username).is_dir():
                self._warn_no_data(Path(s.data_root), s.username)
            else:
                messagebox.showwarning("未生成", "先に「ビューア生成」を実行してください。")
            return
        webbrowser.open(index.as_uri())

    # ---- バックグラウンド実行 ----

    def _start(self, func) -> None:
        self.running = True
        self._refresh_controls()
        self.progress.start(12)

        def worker():
            try:
                rc = func(self.queue.put)
            except Exception as exc:  # noqa: BLE001
                self.queue.put(f"[xarchive] 予期しないエラー: {exc}")
                rc = 1
            self.queue.put(_TaskDone(rc))

        threading.Thread(target=worker, daemon=True).start()

    def _poll_queue(self) -> None:
        try:
            while True:
                item = self.queue.get_nowait()
                if isinstance(item, _TaskDone):
                    self._on_task_done(item.returncode)
                elif isinstance(item, _Notice):
                    messagebox.showwarning(item.title, item.text)
                else:
                    self._append_log(str(item))
        except queue.Empty:
            pass
        self.after(100, self._poll_queue)

    def _on_task_done(self, returncode: int) -> None:
        self.running = False
        self.progress.stop()
        self._refresh_controls()
        self._append_log(f"[xarchive] 終了 (code={returncode})")

    # ---- ログ ----

    def _append_log(self, text: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _clear_log(self) -> None:
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    def _on_close(self) -> None:
        self._current_settings().save()
        self.destroy()


def main() -> None:
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()

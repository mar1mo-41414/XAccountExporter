from __future__ import annotations

import queue
import threading
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from . import fetch as fetch_mod
from . import render as render_mod
from .settings import Settings


class _TaskDone:
    def __init__(self, returncode: int):
        self.returncode = returncode


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("xarchive - X投稿アーカイブ&ビューア")
        self.geometry("760x560")
        self.minsize(600, 420)

        self.settings = Settings.load()
        self.running = False
        self.queue: queue.Queue = queue.Queue()

        self.username_var = tk.StringVar(value=self.settings.username)
        self.cookies_var = tk.StringVar(value=self.settings.cookies_path)
        self.data_root_var = tk.StringVar(value=self.settings.data_root)
        self.retweets_var = tk.BooleanVar(value=self.settings.include_retweets)
        self.replies_var = tk.BooleanVar(value=self.settings.include_replies)

        self._build_widgets()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(100, self._poll_queue)

    # ---- UI構築 ----

    def _build_widgets(self) -> None:
        pad = {"padx": 8, "pady": 4}

        form = ttk.Frame(self)
        form.pack(fill="x", **pad)
        form.columnconfigure(1, weight=1)

        ttk.Label(form, text="アカウント名 (@なし)").grid(row=0, column=0, sticky="w")
        ttk.Entry(form, textvariable=self.username_var).grid(row=0, column=1, sticky="ew", padx=4)

        ttk.Label(form, text="Cookieファイル").grid(row=1, column=0, sticky="w")
        ttk.Entry(form, textvariable=self.cookies_var).grid(row=1, column=1, sticky="ew", padx=4)
        ttk.Button(form, text="参照...", command=self._browse_cookies).grid(row=1, column=2)

        ttk.Label(form, text="保存先ディレクトリ").grid(row=2, column=0, sticky="w")
        ttk.Entry(form, textvariable=self.data_root_var).grid(row=2, column=1, sticky="ew", padx=4)
        ttk.Button(form, text="参照...", command=self._browse_data_root).grid(row=2, column=2)

        opts = ttk.Frame(self)
        opts.pack(fill="x", **pad)
        ttk.Checkbutton(opts, text="単独リツイートを含める", variable=self.retweets_var).pack(side="left")
        ttk.Checkbutton(opts, text="リプライを含める", variable=self.replies_var).pack(side="left", padx=12)

        btns = ttk.Frame(self)
        btns.pack(fill="x", **pad)
        self.buttons: list[ttk.Button] = []
        for text, cmd in [
            ("取得(差分)", lambda: self._do_fetch(full=False)),
            ("全件取得", lambda: self._do_fetch(full=True)),
            ("ビューア生成", self._do_build),
            ("ビューアを開く", self._do_open),
        ]:
            b = ttk.Button(btns, text=text, command=cmd)
            b.pack(side="left", padx=4)
            self.buttons.append(b)

        self.progress = ttk.Progressbar(self, mode="indeterminate")
        self.progress.pack(fill="x", padx=8, pady=(0, 4))

        log_frame = ttk.Frame(self)
        log_frame.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self.log = ScrolledText(log_frame, state="disabled", wrap="word", height=18)
        self.log.pack(fill="both", expand=True)

    # ---- 参照ダイアログ ----

    def _browse_cookies(self) -> None:
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
            data_root=self.data_root_var.get().strip(),
            include_retweets=self.retweets_var.get(),
            include_replies=self.replies_var.get(),
        )

    def _validate(self, *, need_cookies: bool) -> tuple[str, Path, Path] | None:
        s = self._current_settings()
        s.save()
        if not s.username:
            messagebox.showwarning("入力不足", "アカウント名を入力してください。")
            return None
        if not s.data_root:
            messagebox.showwarning("入力不足", "保存先ディレクトリを選択してください。")
            return None
        if need_cookies:
            if not s.cookies_path or not Path(s.cookies_path).is_file():
                messagebox.showwarning("入力不足", "有効なCookieファイルを選択してください。")
                return None
        return s.username, Path(s.data_root), Path(s.cookies_path) if s.cookies_path else Path()

    # ---- ボタン動作 ----

    def _do_fetch(self, *, full: bool) -> None:
        if self.running:
            messagebox.showinfo("実行中", "他の処理が完了するまでお待ちください。")
            return
        v = self._validate(need_cookies=True)
        if v is None:
            return
        username, data_root, cookies_path = v
        self._clear_log()
        self._append_log(f"[xarchive] {username} の{'全件' if full else '差分'}取得を開始します...")
        self._start(
            lambda on_line: fetch_mod.fetch(
                username, data_root, cookies_path,
                full=full,
                include_retweets=self.retweets_var.get(),
                include_replies=self.replies_var.get(),
                on_line=on_line,
            )
        )

    def _do_build(self) -> None:
        if self.running:
            messagebox.showinfo("実行中", "他の処理が完了するまでお待ちください。")
            return
        v = self._validate(need_cookies=False)
        if v is None:
            return
        username, data_root, _cookies = v
        data_dir = data_root / username
        self._clear_log()
        self._append_log(f"[xarchive] {username} のビューアを生成します...")

        def task(on_line):
            try:
                out = render_mod.build(data_dir, username)
                on_line(f"[xarchive] 生成しました: {out}")
                return 0
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
            messagebox.showwarning("未生成", "先に「ビューア生成」を実行してください。")
            return
        webbrowser.open(index.as_uri())

    # ---- バックグラウンド実行 ----

    def _start(self, func) -> None:
        self.running = True
        for b in self.buttons:
            b.state(["disabled"])
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
                else:
                    self._append_log(str(item))
        except queue.Empty:
            pass
        self.after(100, self._poll_queue)

    def _on_task_done(self, returncode: int) -> None:
        self.running = False
        self.progress.stop()
        for b in self.buttons:
            b.state(["!disabled"])
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

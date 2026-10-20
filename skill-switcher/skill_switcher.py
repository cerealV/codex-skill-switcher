"""A local, per-path Codex skill switcher. No model calls or third-party packages."""
import ctypes
import json
import os
from pathlib import Path
import queue
import subprocess
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from skill_catalog import enrich_skills, ordered_groups


def codex_home():
    return Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))


def find_codex():
    candidates = [
        codex_home() / "plugins/.plugin-appserver/codex.exe",
        Path(os.environ.get("APPDATA", "")) / "npm/node_modules/@openai/codex/node_modules/@openai/codex-win32-x64/vendor/x86_64-pc-windows-msvc/bin/codex.exe",
    ]
    for path in candidates:
        if path.is_file():
            return path
    raise RuntimeError("找不到 Codex 程序。请先安装并运行 Codex 桌面版或 Codex CLI。")


class AppServer:
    def __init__(self, executable=None, home=None):
        env = os.environ.copy()
        if home is not None:
            env["CODEX_HOME"] = str(home)
        self.process = subprocess.Popen(
            [str(executable or find_codex()), "app-server", "--listen", "stdio://"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding="utf-8", env=env,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        self.responses = queue.Queue()
        self.sequence = 0
        threading.Thread(target=self._read, daemon=True).start()
        try:
            result = self.call("initialize", {"clientInfo": {"name": "local_skill_switcher", "version": "0.1.0"}})
            self.home = Path(result.get("codexHome", home or codex_home()))
            self._send({"method": "initialized", "params": {}})
        except Exception:
            self.close()
            raise

    def _read(self):
        try:
            for line in self.process.stdout:
                self.responses.put(json.loads(line))
        finally:
            self.responses.put(None)

    def _send(self, message):
        self.process.stdin.write(json.dumps(message) + "\n")
        self.process.stdin.flush()

    def call(self, method, params):
        self.sequence += 1
        request_id = self.sequence
        self._send({"id": request_id, "method": method, "params": params})
        deadline = time.monotonic() + 30
        while True:
            try:
                response = self.responses.get(timeout=max(0.01, deadline - time.monotonic()))
            except queue.Empty:
                raise RuntimeError("Codex 接口等待超过 30 秒。请关闭窗口后重新启动。") from None
            if response is None:
                raise RuntimeError("Codex 接口进程已退出。请检查 Codex 配置后重新启动。")
            if response.get("id") != request_id:
                if time.monotonic() >= deadline:
                    raise RuntimeError("Codex 接口等待超时。")
                continue
            if "error" in response:
                raise RuntimeError(response["error"].get("message", str(response["error"])))
            return response.get("result", {})

    def skills(self, cwd):
        result = self.call("skills/list", {"cwds": [str(cwd)], "forceReload": True})
        data = result.get("data", [])
        if not data:
            raise RuntimeError("Codex 未返回 skill 列表。")
        return data[0].get("skills", []), data[0].get("errors", [])

    def close(self):
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        self.process.stdin.close()
        self.process.stdout.close()


def config_bytes(home):
    path = home / "config.toml"
    return path.read_bytes() if path.exists() else None


def save_changes(server, changes, expected):
    """Backup once, stop on concurrent edits, and report any partial completion."""
    if config_bytes(server.home) != expected:
        raise RuntimeError("配置已被其他程序修改。请先刷新列表，再重新选择开关。")
    if not changes:
        return None
    backup = None
    if expected is not None:
        stamp = time.strftime("%Y%m%d-%H%M%S") + f"-{time.time_ns() % 1000000000:09d}"
        backup = server.home / f"config.toml.skill-switcher-{stamp}.bak"
        with backup.open("xb") as stream:
            stream.write(expected)
    completed = 0
    try:
        for path, enabled in changes.items():
            if config_bytes(server.home) != expected:
                raise RuntimeError("保存期间配置被其他程序修改，已停止后续写入。")
            server.call("skills/config/write", {"path": path, "enabled": enabled})
            completed += 1
            expected = config_bytes(server.home)
    except Exception as error:
        location = f"\n备份：{backup}" if backup else ""
        raise RuntimeError(f"已保存 {completed}/{len(changes)} 项。其余项目未确认保存。\n{error}{location}\n请刷新列表核对实际状态。") from error
    return backup


class Switcher:
    def __init__(self, root, server):
        self.root, self.server = root, server
        self.cwd = Path(__file__).resolve().parent
        self.skills, self.pending = {}, {}
        self.group_members = {}
        self.expected = None
        self.open_states = {}
        self.search_active = False
        root.title("Codex · Skill 开关")
        root.geometry("1200x760")
        root.minsize(820, 560)
        ttk.Style().configure("Treeview", rowheight=30, font=("Microsoft YaHei UI", 10))
        ttk.Style().configure("Footer.TButton", font=("Microsoft YaHei UI", 10), padding=(16, 9))
        body = ttk.Frame(root, padding=18)
        body.pack(fill="both", expand=True)
        header = ttk.Frame(body)
        header.pack(fill="x")
        ttk.Label(header, text="Skill 开关", font=("Microsoft YaHei UI", 20, "bold")).pack(side="left")
        self.count = tk.StringVar(value="正在读取 Skill 数量…")
        ttk.Label(header, textvariable=self.count, font=("Microsoft YaHei UI", 22, "bold"), foreground="#146c43").pack(side="right")
        self.count_note = tk.StringVar()
        ttk.Label(body, textvariable=self.count_note, foreground="#b45c00").pack(anchor="e")
        ttk.Label(body, text="点击状态列逐个切换；保存后重启 Codex。设置按路径全局保存，关闭 skill 不会卸载它。", padding=(0, 6)).pack(anchor="w")
        self.project = tk.StringVar()
        bar = ttk.Frame(body)
        bar.pack(fill="x", pady=(6, 12))
        ttk.Label(bar, textvariable=self.project).pack(side="left", fill="x", expand=True)
        ttk.Button(bar, text="选择项目目录", command=self.choose_project).pack(side="right")
        filters = ttk.Frame(body)
        filters.pack(fill="x", pady=(0, 10))
        ttk.Label(filters, text="搜索").pack(side="left", padx=(0, 8))
        self.query = tk.StringVar()
        ttk.Entry(filters, textvariable=self.query).pack(side="left", fill="x", expand=True)
        self.filter = tk.StringVar(value="全部状态")
        ttk.Combobox(filters, textvariable=self.filter, values=["全部状态", "已开启", "已关闭", "待保存"], state="readonly", width=12).pack(side="left", padx=(10, 0))
        self.grouping = tk.StringVar(value="GitHub 来源 → 用途")
        ttk.Combobox(filters, textvariable=self.grouping, values=["GitHub 来源 → 用途", "用途 → GitHub 来源"], state="readonly", width=23).pack(side="left", padx=(10, 0))
        self.query.trace_add("write", lambda *_: self.render())
        self.filter.trace_add("write", lambda *_: self.render())
        self.grouping.trace_add("write", lambda *_: self.render())
        bulk = ttk.Frame(body)
        bulk.pack(fill="x", pady=(0, 6))
        ttk.Button(bulk, text="全部开启", command=lambda: self.set_bulk(True)).pack(side="left")
        ttk.Button(bulk, text="全部关闭", command=lambda: self.set_bulk(False)).pack(side="left", padx=(8, 20))
        self.group_on_button = ttk.Button(bulk, text="开启所选分类", command=lambda: self.set_bulk(True, selected_group=True), state="disabled")
        self.group_on_button.pack(side="left")
        self.group_off_button = ttk.Button(bulk, text="关闭所选分类", command=lambda: self.set_bulk(False, selected_group=True), state="disabled")
        self.group_off_button.pack(side="left", padx=8)
        ttk.Label(body, text="批量操作包含搜索和筛选隐藏的 Skill；修改先暂存，点击“保存开关”后生效。", foreground="#737373").pack(anchor="w", pady=(0, 8))
        table = ttk.Frame(body)
        table.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(table, columns=("state", "purpose", "source", "path"), show="tree headings", selectmode="browse")
        for col, title, width in [("#0", "分类 / Skill 名称", 350), ("state", "开关（点击切换）", 140), ("purpose", "用途", 170), ("source", "GitHub / 安装来源", 240), ("path", "独立路径", 360)]:
            self.tree.heading(col, text=title)
            self.tree.column(col, width=width, minwidth=90, stretch=col in ("#0", "path"))
        scroll = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview)
        horizontal = ttk.Scrollbar(table, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=scroll.set, xscrollcommand=horizontal.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")
        horizontal.grid(row=1, column=0, sticky="ew")
        table.rowconfigure(0, weight=1)
        table.columnconfigure(0, weight=1)
        self.tree.tag_configure("off", foreground="#737373")
        self.tree.tag_configure("pending", foreground="#b45c00")
        self.tree.bind("<ButtonRelease-1>", self.click)
        self.tree.bind("<space>", lambda _: self.toggle())
        self.tree.bind("<<TreeviewSelect>>", self.show_detail)
        self.detail = tk.Text(body, height=6, wrap="word", font=("Microsoft YaHei UI", 9), relief="flat", background="#f2f2f2")
        self.detail.pack(fill="x", pady=(10, 0))
        self.detail.configure(state="disabled")
        bottom = ttk.Frame(body)
        # Reserve the footer before the expanding table, including at high DPI.
        bottom.pack(side="bottom", fill="x", pady=(12, 0), before=header)
        ttk.Separator(bottom).pack(fill="x", pady=(0, 8))
        self.status = tk.StringVar(value="正在读取…")
        ttk.Label(bottom, textvariable=self.status, foreground="#737373").pack(anchor="w", pady=(0, 8))
        actions = ttk.Frame(bottom)
        actions.pack(fill="x")
        ttk.Button(actions, text="刷新 / 放弃修改", command=self.refresh, style="Footer.TButton").pack(side="left")
        self.save_button = tk.Button(actions, text="保存开关", command=self.save, font=("Microsoft YaHei UI", 10, "bold"),
                                     padx=20, pady=9, relief="flat", borderwidth=0, background="#e2e8f0", foreground="#64748b",
                                     activebackground="#1d4ed8", activeforeground="white", disabledforeground="#64748b", state="disabled")
        self.save_button.pack(side="right", fill="y")
        self.restart_button = ttk.Button(actions, text="重启 Codex", command=self.restart, style="Footer.TButton")
        self.restart_button.pack(side="right", padx=(0, 12))
        self.restart_process = None
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.after(100, self.refresh)

    def render(self):
        selected = self.tree.selection()
        # Search temporarily expands matches without replacing the browsing state.
        if not self.search_active:
            for parent in self.tree.get_children():
                for item in (parent, *self.tree.get_children(parent)):
                    self.open_states[item] = bool(self.tree.item(item, "open"))
        self.tree.delete(*self.tree.get_children())
        query = self.query.get().casefold().strip()
        self.search_active = bool(query)
        self.group_members = {}
        for (primary, secondary), members in ordered_groups(self.skills.values(), self.grouping.get()):
            parent = "group:" + json.dumps([self.grouping.get(), primary], ensure_ascii=False)
            child = "group:" + json.dumps([self.grouping.get(), primary, secondary], ensure_ascii=False)
            paths = [skill["path"] for skill in members]
            self.group_members.setdefault(parent, []).extend(paths)
            self.group_members[child] = paths
        visible = []
        for path, skill in self.skills.items():
            enabled = self.pending.get(path, skill["enabled"])
            if query and query not in " ".join([skill["name"], path, skill["source_group"], skill["purpose"], skill.get("description", "")]).casefold():
                continue
            if self.filter.get() == "已开启" and not enabled or self.filter.get() == "已关闭" and enabled or self.filter.get() == "待保存" and path not in self.pending:
                continue
            visible.append(skill)
        groups = ordered_groups(visible, self.grouping.get())
        totals = {}
        for (primary, _), members in groups:
            totals[primary] = totals.get(primary, 0) + len(members)
        for (primary, secondary), members in groups:
            parent = "group:" + json.dumps([self.grouping.get(), primary], ensure_ascii=False)
            child = "group:" + json.dumps([self.grouping.get(), primary, secondary], ensure_ascii=False)
            if not self.tree.exists(parent):
                self.tree.insert("", "end", iid=parent, text=f"{primary}  ({totals[primary]})", open=bool(query) or self.open_states.get(parent, False))
            self.tree.insert(parent, "end", iid=child, text=f"{secondary}  ({len(members)})", open=bool(query) or self.open_states.get(child, False))
            for skill in sorted(members, key=lambda s: not self.pending.get(s["path"], s["enabled"])):
                path = skill["path"]
                enabled = self.pending.get(path, skill["enabled"])
                state = "● 开启" if enabled else "○ 关闭"
                tag = "pending" if path in self.pending else "on" if enabled else "off"
                if path in self.pending:
                    state += " *"
                self.tree.insert(child, "end", iid=path, text=skill["name"], values=(state, skill["purpose"], skill["source_group"], path), tags=(tag,))
        if selected and self.tree.exists(selected[0]):
            self.tree.selection_set(selected[0])
        enabled_count = sum(self.pending.get(p, s["enabled"]) for p, s in self.skills.items())
        self.count.set(f"已开启 {enabled_count} / 共 {len(self.skills)} 个 Skill")
        saved_count = sum(s["enabled"] for s in self.skills.values())
        self.count_note.set(f"当前选择 · 待保存 {len(self.pending)} 项 · 已保存开启 {saved_count} 个" if self.pending else "已保存状态 · 统计全部 Skill")
        self.status.set(f"共 {len(self.skills)} 个 · 开启 {enabled_count} · 匹配 {len(visible)} · 待保存 {len(self.pending)}")
        self.save_button.configure(state="normal" if self.pending else "disabled",
                                   background="#2563eb" if self.pending else "#e2e8f0",
                                   foreground="white" if self.pending else "#64748b")
        self.show_detail()

    def click(self, event):
        if self.tree.identify_column(event.x) == "#1" and self.tree.identify_region(event.x, event.y) == "cell":
            row = self.tree.identify_row(event.y)
            if row:
                self.tree.selection_set(row)
                self.toggle()

    def toggle(self):
        selected = self.tree.selection()
        if not selected:
            return
        path = selected[0]
        if path not in self.skills:
            return
        new = not self.pending.get(path, self.skills[path]["enabled"])
        if new == self.skills[path]["enabled"]:
            self.pending.pop(path, None)
        else:
            self.pending[path] = new
        self.render()

    def set_bulk(self, enabled, selected_group=False):
        paths = self.skills
        if selected_group:
            selected = self.tree.selection()
            paths = self.group_members.get(selected[0], []) if selected else []
        for path in paths:
            if enabled == self.skills[path]["enabled"]:
                self.pending.pop(path, None)
            else:
                self.pending[path] = enabled
        self.render()

    def show_detail(self, _=None):
        selected = self.tree.selection()
        members = self.group_members.get(selected[0], []) if selected else []
        state = "normal" if members else "disabled"
        self.group_on_button.configure(state=state)
        self.group_off_button.configure(state=state)
        text = "选择一个 skill 查看描述。按路径独立控制，同名 skill 也可分别设置。"
        if selected and selected[0] in self.skills:
            skill = self.skills[selected[0]]
            text = f"{skill['name']} · {skill['purpose']}\n来源：{skill['source_group']}\n依据：{skill['source_evidence']}\n{selected[0]}\n{skill.get('description', '')}"
        elif selected:
            text = self.tree.item(selected[0], "text") + f"\n完整分类共 {len(members)} 个 Skill（包含搜索和筛选隐藏项）。\n点击“开启所选分类 / 关闭所选分类”可批量切换，折叠状态也可操作；点击“保存开关”后生效。"
        self.detail.configure(state="normal")
        self.detail.delete("1.0", "end")
        self.detail.insert("1.0", text)
        self.detail.configure(state="disabled")

    def discard_ok(self):
        return not self.pending or messagebox.askyesno("未保存的修改", "放弃尚未保存的开关修改？", parent=self.root)

    def load(self):
        before = config_bytes(self.server.home)
        skills, errors = self.server.skills(self.cwd)
        if config_bytes(self.server.home) != before:
            raise RuntimeError("读取期间配置发生变化，请再次刷新。")
        self.skills = {s["path"]: s for s in enrich_skills(skills, self.server.home)}
        self.pending.clear()
        self.expected = before
        self.project.set(f"项目：{self.cwd}")
        self.render()
        self.show_detail()
        if errors:
            messagebox.showwarning("部分 skill 未能加载", f"{len(errors)} 个加载错误：\n" + "\n".join(str(e) for e in errors), parent=self.root)

    def refresh(self):
        if not self.discard_ok():
            return
        try:
            self.load()
        except Exception as error:
            messagebox.showerror("读取失败", str(error), parent=self.root)

    def choose_project(self):
        if not self.discard_ok():
            return
        path = filedialog.askdirectory(title="选择要查看项目 skill 的目录", parent=self.root)
        if path:
            previous = self.cwd
            self.cwd = Path(path)
            try:
                self.load()
            except Exception as error:
                self.cwd = previous
                messagebox.showerror("读取失败", str(error), parent=self.root)

    def save(self, notify=True):
        changes = dict(self.pending)
        count = len(changes)
        self.root.configure(cursor="watch")
        self.root.update_idletasks()
        try:
            backup = save_changes(self.server, changes, self.expected)
            self.load()
            mismatches = [p for p, enabled in changes.items() if p not in self.skills or self.skills[p]["enabled"] != enabled]
            if mismatches:
                raise RuntimeError("配置已写入，但以下 skill 的有效状态不符合选择，可能存在项目配置覆盖：\n" + "\n".join(mismatches) + f"\n备份：{backup}")
            if notify:
                messagebox.showinfo("已保存", f"已保存 {count} 个 skill 开关，并重新读取核对。\n请重启 Codex。" + (f"\n\n配置备份：\n{backup}" if backup else ""), parent=self.root)
            return True
        except Exception as error:
            messagebox.showerror("保存未完成", str(error), parent=self.root)
            return False
        finally:
            self.root.configure(cursor="")

    def restart(self):
        if self.pending:
            choice = messagebox.askyesnocancel("重启 Codex", "重启会中断 Codex 正在运行的任务。\n是否先保存开关再重启？\n\n是：保存并重启\n否：直接重启，保留窗口中的未保存选择\n取消：不重启", parent=self.root)
            if choice is None or choice and not self.save(notify=False):
                return
        elif not messagebox.askyesno("重启 Codex", "将退出并重新打开 Codex，正在运行的任务会中断。继续？", parent=self.root):
            return
        try:
            script = Path(__file__).with_name("restart_codex.ps1")
            powershell = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
            self.restart_process = subprocess.Popen([str(powershell), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace", creationflags=subprocess.CREATE_NO_WINDOW)
            self.restart_button.configure(state="disabled")
            self.status.set("正在重启 Codex…")
            self.root.after(300, self.poll_restart)
        except Exception as error:
            messagebox.showerror("重启未完成", str(error), parent=self.root)

    def poll_restart(self):
        if self.restart_process.poll() is None:
            self.root.after(300, self.poll_restart)
            return
        output, error = self.restart_process.communicate()
        result = self.restart_process.returncode
        self.restart_process = None
        self.restart_button.configure(state="normal")
        self.render()
        if result:
            messagebox.showerror("重启未完成", (error or output).strip(), parent=self.root)
        else:
            messagebox.showinfo("Codex 已重新打开", "已确认 Codex 窗口打开并响应。", parent=self.root)

    def close(self):
        if self.discard_ok():
            self.server.close()
            self.root.destroy()


def main():
    if os.name == "nt":
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    root = tk.Tk()
    root.withdraw()
    server = None
    try:
        server = AppServer()
        Switcher(root, server)
        root.deiconify()
        root.mainloop()
    except Exception as error:
        messagebox.showerror("Skill 开关启动失败", str(error), parent=root)
        root.destroy()
    finally:
        if server is not None:
            server.close()


if __name__ == "__main__":
    main()

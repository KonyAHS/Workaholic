import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog
import subprocess, sys, os, json, time, signal
from pathlib import Path

CONFIG_FILE = Path(__file__).with_name("services_config.json")

class Service:
    def __init__(self, path: str):
        self.path = path
        self.proc: subprocess.Popen | None = None
        self.start_time: float | None = None
        self.last_returncode: int | None = None
        self.log_path = Path(path).with_suffix(".log")
        self._log_handle = None

    @property
    def is_running(self): return self.proc is not None and self.proc.poll() is None
    @property
    def pid(self): return self.proc.pid if self.is_running else ""
    @property
    def mtime(self):
        try:
            return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(os.path.getmtime(self.path)))
        except OSError:
            return "N/A"

    def start(self):
        if self.is_running: return
        if not Path(self.path).exists(): raise FileNotFoundError(self.path)
        self._log_handle = open(self.log_path, "a", encoding="utf-8")
        self._log_handle.write(f"\n=== START {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n"); self._log_handle.flush()
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0

        # Decide how to launch the service depending on file type.
        # - .py -> launch with the Python interpreter used to run this program
        # - .exe -> run directly
        # - otherwise -> try running directly
        ext = Path(self.path).suffix.lower()
        if ext == ".py":
            cmd = [sys.executable, self.path]
        elif ext == ".exe":
            if os.name == "nt":
                cmd = [self.path]
            else:
                # This tool is Windows-first; do not attempt to run .exe on non-Windows here.
                raise RuntimeError(".exe support is Windows-only for this tool. Please run on Windows.")
        else:
            # Attempt to run directly (may fail if not executable)
            cmd = [self.path]

        self.proc = subprocess.Popen(
            cmd,
            stdout=self._log_handle,
            stderr=subprocess.STDOUT,
            creationflags=creationflags,
            cwd=Path(self.path).parent  # <-- ensure service runs in its own folder
        )
        self.start_time = time.time()
        self.last_returncode = None

    def stop(self, force=False):
        if not self.is_running: return
        try:
            if os.name == "nt":
                if force:
                    self.proc.kill()
                else:
                    try: os.kill(self.proc.pid, signal.CTRL_BREAK_EVENT)
                    except Exception: pass
                    self.proc.terminate()
            else:
                self.proc.terminate()
                if force:
                    time.sleep(0.8)
                    if self.is_running: self.proc.kill()
            try: self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try: self.proc.kill()
                except Exception: pass
        finally:
            if self.proc: self.last_returncode = self.proc.returncode
            self.proc = None; self.start_time = None
            if self._log_handle:
                try: self._log_handle.flush(); self._log_handle.close()
                except Exception: pass
                self._log_handle = None

    def restart(self):
        self.stop(force=True)
        self.start()

class ServiceAggregator(tk.Tk):
    COLUMNS = ("status", "pid", "uptime", "mtime", "path")

    def __init__(self):
        super().__init__()
        self.title("Python Services Aggregator")
        self.geometry("1040x520")
        self.configure(bg='#1e1e1e')
        self.services: list[Service] = []
        self.groups: dict[str, list[str]] = {}
        self.autostart_groups: list[str] = []
        self._load_config()
        self._build_ui()
        self._refresh_loop()
        self.after(800, self._run_autostart_groups)
        self.protocol("WM_DELETE_WINDOW", self.on_close)

    # ---------- UI ----------
    def _build_ui(self):
        style = ttk.Style()
        try: style.theme_use('default')
        except Exception: pass
        style.configure('Treeview',
                        background='#252526',
                        fieldbackground='#252526',
                        foreground='#e0e0e0',
                        bordercolor='#333333',
                        rowheight=22,
                        font=('Segoe UI', 9))
        style.map('Treeview',
                  background=[('selected', '#094771')],
                  foreground=[('selected', '#ffffff')])
        style.configure('Treeview.Heading',
                        background='#2d2d30',
                        foreground='#dcdcdc',
                        relief='flat',
                        font=('Segoe UI', 9, 'bold'))
        style.map('Treeview.Heading',
                  background=[('active', '#3e3e42')])
        toolbar = tk.Frame(self, bg='#1e1e1e')
        toolbar.pack(fill="x", padx=6, pady=4)

        def add_btn(text, cmd):
            tk.Button(toolbar, text=text, command=cmd,
                      bg='#2d2d30', fg='#dcdcdc', activebackground='#3e3e42',
                      relief='flat', padx=10, pady=4).pack(side="left", padx=3)

        add_btn("Add", self.add_service)
        add_btn("Remove", self.remove_selected)
        add_btn("Start", self.start_selected)
        add_btn("Stop", self.stop_selected)
        add_btn("Restart", self.restart_selected)
        add_btn("Start All", self.start_all)
        add_btn("Stop All", self.stop_all)
        add_btn("Open Log", self.open_log_selected)
        add_btn("Refresh", self.refresh_now)

        self.auto_refresh_var = tk.BooleanVar(value=True)
        tk.Checkbutton(toolbar, text="Auto", variable=self.auto_refresh_var,
                       bg='#1e1e1e', fg='#dcdcdc', selectcolor='#1e1e1e',
                       activebackground='#1e1e1e').pack(side="left", padx=12)

        # Group / bulk frame
        group_frame = tk.Frame(self, bg='#1e1e1e')
        group_frame.pack(fill='x', padx=6, pady=(0,6))
        tk.Label(group_frame, text="Groups:", bg='#1e1e1e', fg='#bbbbbb').pack(side='left')

        self.group_var = tk.StringVar()
        self.group_combo = ttk.Combobox(group_frame, textvariable=self.group_var, width=28,
                                        values=sorted(self.groups.keys()), state='readonly')
        self.group_combo.pack(side='left', padx=6)
        self.group_combo.bind("<<ComboboxSelected>>", lambda e: self._on_group_selected())

        tk.Button(group_frame, text="Start Group", command=self.start_group,
                  bg='#2d2d30', fg='#dcdcdc', relief='flat').pack(side='left', padx=3)
        tk.Button(group_frame, text="Save Sel→Group", command=self.save_selection_as_group,
                  bg='#2d2d30', fg='#dcdcdc', relief='flat').pack(side='left', padx=3)
        tk.Button(group_frame, text="Delete Group", command=self.delete_group,
                  bg='#2d2d30', fg='#dcdcdc', relief='flat').pack(side='left', padx=3)

        self.autostart_var = tk.BooleanVar(value=False)
        self.autostart_chk = tk.Checkbutton(group_frame, text="Autostart",
                                            variable=self.autostart_var,
                                            command=self.toggle_group_autostart,
                                            bg='#1e1e1e', fg='#dcdcdc', selectcolor='#1e1e1e',
                                            activebackground='#1e1e1e')
        self.autostart_chk.pack(side='left', padx=12)

        self.tree = ttk.Treeview(self, columns=self.COLUMNS,
                                 show="headings", height=16, selectmode='extended')
        widths = {"status":110, "pid":70, "uptime":90, "mtime":160, "path":600}
        for col in self.COLUMNS:
            self.tree.heading(col, text=col.upper())
            self.tree.column(col, width=widths.get(col, 100),
                             anchor="w", stretch=(col == "path"))
        self.tree.pack(fill="both", expand=True, padx=6, pady=4)

        self.tree.bind("<Double-1>", lambda _: self.start_selected())
        self.tree.tag_configure('RUNNING', foreground='#4CAF50')
        self.tree.tag_configure('STOPPED', foreground='#b0b0b0')
        self.tree.tag_configure('EXIT', foreground='#ffb347')
        self.tree.tag_configure('MISSING', foreground='#ff5252')

        self.status_bar = tk.Label(self, text="", anchor="w",
                                   bg='#2d2d30', fg='#dcdcdc')
        self.status_bar.pack(fill="x")

        self.refresh_now()

    # ---------- Helpers ----------
    def _find_service_by_path(self, path: str):
        for s in self.services:
            if s.path == path: return s
        return None

    def _selected_paths(self):
        sels = self.tree.selection()
        return list(sels)

    def get_selected_service(self):
        paths = self._selected_paths()
        if not paths: return None
        return self._find_service_by_path(paths[0])

    # ---------- Actions ----------
    def add_service(self):
        path = filedialog.askopenfilename(
            title="Select Python script or Windows executable",
            filetypes=[("All Files", "*.*")]
        )
        if not path: return
        if self._find_service_by_path(path):
            messagebox.showinfo("Exists", "Already added.")
            return
        self.services.append(Service(path))
        self._save_config(); self.refresh_now()

    def remove_selected(self):
        s = self.get_selected_service()
        if not s: return
        if s.is_running and not messagebox.askyesno("Running", "Service running. Stop first?"):
            return
        s.stop(force=True)
        self.services = [x for x in self.services if x.path != s.path]
        # remove from groups
        for g in list(self.groups.keys()):
            self.groups[g] = [p for p in self.groups[g] if p != s.path]
            if not self.groups[g]: del self.groups[g]
        self.autostart_groups = [g for g in self.autostart_groups if g in self.groups]
        self._save_config(); self._refresh_groups_ui(); self.refresh_now()

    def start_selected(self):
        for s in [self._find_service_by_path(p) for p in self._selected_paths()]:
            if not s: continue
            try: s.start()
            except Exception as e: messagebox.showerror("Start failed", f"{s.path}\n{e}")
        self.refresh_now()

    def stop_selected(self):
        for s in [self._find_service_by_path(p) for p in self._selected_paths()]:
            if s: s.stop(force=True)
        self.refresh_now()

    def restart_selected(self):
        for s in [self._find_service_by_path(p) for p in self._selected_paths()]:
            if not s: continue
            try: s.restart()
            except Exception as e: messagebox.showerror("Restart failed", f"{s.path}\n{e}")
        self.refresh_now()

    def start_all(self):
        errs=[]
        for s in self.services:
            try: s.start()
            except Exception as e: errs.append(f"{Path(s.path).name}: {e}")
        if errs: messagebox.showerror("Errors", "\n".join(errs))
        self.refresh_now()

    def stop_all(self):
        for s in self.services: s.stop(force=True)
        self.refresh_now()

    def open_log_selected(self):
        s = self.get_selected_service()
        if not s: return
        if not s.log_path.exists():
            messagebox.showinfo("Log", "No log yet."); return
        if os.name == "nt": os.startfile(str(s.log_path))
        else: subprocess.Popen(["xdg-open", str(s.log_path)])

    # ---------- Groups ----------
    def _refresh_groups_ui(self):
        self.group_combo['values'] = sorted(self.groups.keys())
        cur = self.group_var.get()
        if cur not in self.groups:
            self.group_var.set('')
            self.autostart_var.set(False)
        else:
            self.autostart_var.set(cur in self.autostart_groups)

    def _on_group_selected(self):
        g = self.group_var.get()
        self.autostart_var.set(g in self.autostart_groups)

    def save_selection_as_group(self):
        sel_paths = self._selected_paths()
        if not sel_paths:
            messagebox.showinfo("No selection", "Select one or more services first.")
            return
        name = simpledialog.askstring("Group Name", "Enter group name:")
        if not name: return
        self.groups[name] = sel_paths
        self._save_config(); self._refresh_groups_ui()
        self.group_var.set(name)
        self.autostart_var.set(name in self.autostart_groups)

    def delete_group(self):
        g = self.group_var.get()
        if not g: return
        if not messagebox.askyesno("Delete Group", f"Delete group '{g}'?"): return
        if g in self.groups: del self.groups[g]
        self.autostart_groups = [x for x in self.autostart_groups if x != g]
        self._save_config(); self._refresh_groups_ui()

    def start_group(self):
        g = self.group_var.get()
        if not g or g not in self.groups: return
        for p in self.groups[g]:
            s = self._find_service_by_path(p)
            if s and not s.is_running:
                try: s.start()
                except Exception as e: print(f"[Group Start Error] {p}: {e}")
        self.refresh_now()

    def toggle_group_autostart(self):
        g = self.group_var.get()
        if not g: return
        if self.autostart_var.get():
            if g not in self.autostart_groups: self.autostart_groups.append(g)
        else:
            self.autostart_groups = [x for x in self.autostart_groups if x != g]
        self._save_config()

    def _run_autostart_groups(self):
        for g in self.autostart_groups:
            for p in self.groups.get(g, []):
                s = self._find_service_by_path(p)
                if s and not s.is_running:
                    try: s.start()
                    except Exception as e: print(f"[Autostart Error] {p}: {e}")
        self.refresh_now()

    # ---------- Refresh ----------
    def refresh_now(self):
        existing_iids = set(self.tree.get_children())
        wanted_iids = set(s.path for s in self.services)
        for iid in existing_iids - wanted_iids:
            self.tree.delete(iid)
        for idx, s in enumerate(self.services):
            if s.is_running: status = "RUNNING"
            elif s.last_returncode is not None: status = f"EXIT({s.last_returncode})"
            else: status = "STOPPED"
            if not Path(s.path).exists(): status = "MISSING"
            uptime=""
            if s.is_running and s.start_time:
                diff=int(time.time()-s.start_time); m, sec=divmod(diff,60); h,m=divmod(m,60)
                uptime=f"{h:02d}:{m:02d}:{sec:02d}"
            row=(status, s.pid, uptime, s.mtime, s.path)
            tag = ('RUNNING' if status=="RUNNING"
                   else 'MISSING' if status=="MISSING"
                   else 'EXIT' if status.startswith("EXIT") else 'STOPPED')
            if s.path in existing_iids:
                self.tree.item(s.path, values=row, tags=(tag,))
            else:
                self.tree.insert("", "end", iid=s.path, values=row, tags=(tag,))
        self.status_bar.config(text=f"Services: {len(self.services)}  |  Groups: {len(self.groups)}")

    def _refresh_loop(self):
        if self.auto_refresh_var.get(): self.refresh_now()
        self.after(2000, self._refresh_loop)

    # ---------- Persistence ----------
    def _load_config(self):
        if not CONFIG_FILE.exists():
            try:
                CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
                CONFIG_FILE.write_text(json.dumps({
                    "services": [],
                    "groups": {},
                    "autostart_groups": []
                }, indent=2), encoding="utf-8")
            except Exception as e:
                print(f"[Config Create Error] {e}")

        if CONFIG_FILE.exists():
            try:
                data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
                for p in data.get("services", []):
                    if p and isinstance(p, str): self.services.append(Service(p))
                self.groups = data.get("groups", {}) or {}
                self.autostart_groups = data.get("autostart_groups", []) or []
            except Exception:
                # If loading fails, fall back to empty defaults
                self.services = []
                self.groups = {}
                self.autostart_groups = []

    def _save_config(self):
        data = {
            "services": [s.path for s in self.services],
            "groups": self.groups,
            "autostart_groups": self.autostart_groups
        }
        try:
            CONFIG_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception as e:
            messagebox.showerror("Save Error", str(e))

    # ---------- Close ----------
    def on_close(self):
        # Graceful stop all (non-force), then force remaining
        self.status_bar.config(text="Stopping services...")
        self.update_idletasks()
        for s in self.services:
            try: s.stop(force=False)
            except Exception: pass
        deadline = time.time() + 4
        while any(s.is_running for s in self.services) and time.time() < deadline:
            time.sleep(0.3); self.refresh_now(); self.update()
        for s in self.services:
            if s.is_running:
                try: s.stop(force=True)
                except Exception: pass
        self.destroy()

if __name__ == "__main__":
    ServiceAggregator().mainloop()
# Workaholic مرهق
<div align="center">
   <img width="250" height="250" alt="شعار مرهق" src="https://github.com/user-attachments/assets/dd67bb4a-d6f4-4e3c-88e6-bd6615e684a0" />
   <div style="display:inline-block; text-align:left; max-width:800px; margin-top:16px;">

   </div>
</div>
A small desktop app to run your Python scripts and programs side by side — start/stop/restart them, group them, start them automatically, and watch their output live.

## Requirements
- Python 3.10+
- Tkinter (included with Python on Windows/macOS; on some Linux distros install `python3-tk`)

## Quick start
- Windows executable: double-click `Workaholic.exe`. No batch file or Python installation is needed to run Workaholic itself. Keep `services_config.json` beside the executable to use your saved services and groups.
- From source, first run `python -m pip install -r requirements.txt`.
- Windows: `python service_aggregator.py` (or `pythonw service_aggregator.py` to run without a console window)
- macOS/Linux: `python3 service_aggregator.py`

## Features
- **Add apps or services** — use the button in any view (or `Ctrl+N`). Search desktop apps from your Windows Start menu by name, select several, and click **Add selected**. Shortcut arguments and working folders are preserved. Use **Browse files** for scripts or apps without a Start menu shortcut. This list covers desktop shortcuts; Microsoft Store apps and URL shortcuts are not included.
- **Services** — add `.py`, `.pyw`, `.exe`, `.bat`, `.cmd` and `.ps1` files (several at once). Give them a display name, arguments, a specific Python interpreter (e.g. a venv), working folder, and optionally auto-restart them when they crash.
- **Already running** — before starting, Workaholic checks for the same executable path or script path. An existing process is shown as **Running elsewhere**, and no duplicate is started. Those processes remain running when Workaholic closes; stop/restart them from their own apps. Different Python scripts are checked separately. Processes whose paths or command lines Windows restricts cannot always be identified.
- **Icons** — each service shows its program's own icon: an `.exe` uses its icon, Python scripts use the Python interpreter's icon, and batch/PowerShell files use their shell's. In **Edit service** → **Icon**, click **Choose…** to pick from the program's icons, the Windows icon libraries, or any `.ico`, `.exe` or `.dll` file. **Use automatic** goes back to the default.
- **Live output** — select a service to stream its log in the output panel; errors and warnings are highlighted.
- **Groups** — create a group from the sidebar `+` (or from selected services via right-click → Groups). Pick which services belong to it, set their start order with the arrows, and set a delay between starts.
- **Restart a group** — click **Restart all** in the group, or right-click its sidebar entry → **Restart all in group**. Workaholic stops the services it owns, waits for them to stop, then starts the group in order with its configured delay. Apps running elsewhere are left alone.
- **Start with a game or app** — edit a group and click **Pick running app…** next to **Start when this process runs**. Like OBS's game capture list, it shows open windows with their program (tick *Include background programs* for ones without a window). Workaholic saves the program's executable name, so later sessions are detected automatically; tick *Only match this exact file location* to save the full path instead. You can also type a name such as `ffxiv_dx11.exe` or browse for an executable. Clear the field to disable it. While Workaholic is open, it checks about every two seconds and starts the group once when the process is detected (including if it is already running). The trigger resets after the process exits. Closing the game leaves the group running; manually stopping a group keeps it stopped until the next game session.
- **Startup** — tick *Start on launch* on any group to start it when Workaholic opens; groups start in sidebar order. In *Settings* you can launch Workaholic when you sign in to Windows (optionally minimized) and choose how long to wait before autostart begins.
- **Single instance** — launching Workaholic again just brings the existing window to the front.

### Keyboard shortcuts
| Keys | Action |
| --- | --- |
| `Ctrl+N` | Add services |
| `Ctrl+G` | New group (pre-filled with the selection) |
| `Ctrl+F` | Search |
| `Enter` | Start / stop selection |
| `Ctrl+R` | Restart selection |
| `F2` | Edit service |
| `Del` | Remove from Workaholic |
| `Ctrl+,` | Settings |

### Command-line options
- `--minimized` — start minimized
- `--no-autostart` — skip launch and process-triggered group startup this time
- `--startup` — used by the sign-in entry; honours the *Start minimized* setting

## Notes & behavior
- `.py` services need an installed Python interpreter even with the compiled app. Source mode uses Workaholic's interpreter; the executable finds Python on PATH. Set a specific interpreter in **Edit service** for virtual environments. Each service runs in its own folder unless a working folder is set.
- Script output (stdout/stderr) is appended to `<script>.log` next to the file. Executable logs go in `%LOCALAPPDATA%\Workaholic\logs` so apps under Program Files can start without write-permission errors. Output from apps already running elsewhere is not captured.
- Stopping sends a graceful signal first (Ctrl+Break on Windows, SIGTERM elsewhere), then kills the whole process tree after a few seconds. Stopping never freezes the window.
- Configuration is saved to `services_config.json` next to the app. Configs from older versions are upgraded automatically.
- The sign-in option writes a `Workaholic` entry under `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`; turning it off removes it.

## Contributing
- Bug reports or small improvements welcome. This is a tool made for my personal use, but any suggestions are appreciated

## Build the Windows executable
Run `powershell -ExecutionPolicy Bypass -File .\build.ps1` from this folder. This installs psutil and PyInstaller and creates a single `Workaholic.exe` beside your configuration. The executable runs without a console window. Rebuild after changing the source.

Process checks: `python -m unittest test_services -v`.

# Workaholic

A small Tkinter GUI to manage multiple Python Scripts — start/stop/restart them, group them, and view logs.

Requirements
- Python 3.10+ (uses `|` union types and modern stdlib features)
- Tkinter available for your Python build (usually included on Windows/macOS; on some Linux distros you may need to install `python3-tk`)

Quick start
1. Clone or copy this repo.
2. Run:
   - Windows: run `python service_aggregator.py`
   - macOS/Linux: run `python3 service_aggregator.py`

Main features
- Add / Remove Python scripts
- Start / Stop / Restart a single service or multiple selected services
- Start All / Stop All
- Create named groups of services and Start Group
- Mark groups as Autostart — autostart groups run at app startup
- Open service logs (each service writes to a `.log` file next to the script)
- Persistent config saved to [services_config.json](services_config.json) (path controlled by [`CONFIG_FILE`](service_aggregator.py))

Notes & behavior
- Services are launched with the same Python interpreter running the GUI (`sys.executable`), and each service's working directory is set to the script's folder.
- Logs: when a service starts, output (stdout/stderr) is appended to `<script>.log` in the same directory as the script.
- Windows: the code uses process group/CTRL_BREAK handing to try a graceful stop; on other OSes it sends SIGTERM then SIGKILL if needed.
- If a service path is missing, the UI marks it as MISSING. Remove or update paths via the UI.

Contributing
- Bug reports or small improvements welcome. This is a tool made for my personal use, but any suggestions are appreciated

# Workaholic guide

- [Adding services](#adding-services)
- [Groups](#groups)
- [Starting with a game or app](#starting-with-a-game-or-app)
- [Startup](#startup)
- [Icons](#icons)
- [Apps that are already running](#apps-that-are-already-running)
- [Output and logs](#output-and-logs)
- [Stopping and closing](#stopping-and-closing)
- [Configuration](#configuration)
- [Other platforms](#other-platforms)

## Adding services

Click **Add app / service** (or press `Ctrl+N`).

- **Start menu apps**: type to search, select one or more, and click **Add selected**. Shortcut arguments and working folders are kept. Microsoft Store apps and URL shortcuts aren't listed.
- **Files**: click **Browse files** to add `.py`, `.pyw`, `.exe`, `.bat`, `.cmd` or `.ps1` files. You can select several at once.

Select a service and press `F2` to edit it. You can set:

| Setting            | Use                                                                 |
| ------------------ | ------------------------------------------------------------------- |
| Display name       | The name shown in the list. Leave empty to use the file name.       |
| Arguments          | Passed to the program, for example `--port 8080`.                   |
| Python interpreter | For `.py` files only. Point it at a virtual environment's Python.   |
| Working folder     | Leave empty to run in the program's own folder.                     |
| Icon               | See [Icons](#icons).                                                |
| Restart on crash   | Restarts the service if it exits with an error, waiting a little longer after each crash. |

## Groups

Create a group with the `+` in the sidebar, or select services and press `Ctrl+G`.

- Tick the services to include, and use the arrows to set the order they start in.
- Set how many seconds to wait between starting each one.
- **Restart all** stops the group's services, waits for them to exit, then starts them again in order.
- Right-click a group in the sidebar to start, stop, restart, reorder or delete it.

## Starting with a game or app

A group can start by itself when a particular program runs.

1. Start the game or program.
2. Edit the group and click **Pick running app…** next to **Start when this process runs**.
3. Choose it from the list of open windows, then save.

Workaholic remembers the program's executable name, so next time it starts the group without you doing anything. Some details:

- Tick **Include background programs** to list programs that have no window.
- Tick **Only match this exact file location** to match one particular copy of the program instead of any program with that name.
- You can also type a name such as `ffxiv_dx11.exe`, or browse for the executable.
- Workaholic checks about every two seconds, and must be open to notice.
- The group starts once per session of the program. Closing the program leaves the group running. If you stop the group yourself, it stays stopped until the program is started again.
- Avoid picking something that runs as `python.exe`: every Python program would then count as a match.

## Keyboard shortcuts for groups

A group can have its own shortcut that starts it from any program, even while Workaholic is minimized.

1. Edit the group and click the **Keyboard shortcut** box.
2. Press the keys you want, for example `Ctrl+Alt+1`, then save. Press `Backspace` in the box or click **Clear** to remove it.

Some details:

- A shortcut must include `Alt`, `Win` or `Ctrl+Shift`, so it can't take over keys like `Ctrl+C`. F-keys only need one modifier, and `F13`–`F24` work on their own.
- Workaholic must be open for shortcuts to work.
- If another program already uses the shortcut, or Windows reserves it (such as `Win+E`), Workaholic shows a message and the shortcut does nothing. Pick a different one.
- The shortcut only starts the group. Services that are already running are left alone.

## Startup

- Tick **Start on launch** on a group to start it when Workaholic opens. Groups start in sidebar order.
- In **Settings**, turn on **Launch Workaholic when I sign in**, optionally minimized, and choose how long to wait before autostart begins.
- Opening Workaholic a second time brings the existing window to the front instead.

## Icons

Each service shows an icon next to its name:

| Service type      | Default icon                              |
| ----------------- | ----------------------------------------- |
| `.exe`            | The program's own icon                    |
| `.py`, `.pyw`     | The icon of the Python that runs it       |
| `.bat`, `.cmd`    | Command Prompt                            |
| `.ps1`            | PowerShell                                |

To change it, edit the service and click **Choose…** next to **Icon**. You can pick from the program's own icons, two built-in Windows icon sets, or any `.ico`, `.exe` or `.dll` file via **Browse…**. **Use automatic** goes back to the default.

## Apps that are already running

Before starting a service, Workaholic checks whether the same program or script is already running. If it is, the service shows **Running elsewhere** and no second copy is started.

Apps running elsewhere belong to you, not Workaholic: they aren't stopped or restarted from here, and they keep running when Workaholic closes. Different Python scripts are told apart by their script path. Some protected processes can't be inspected by Windows, so they may not be detected.

## Output and logs

Select a service to see its output in the bottom panel. It updates live, and errors and warnings are highlighted.

- Scripts write to `<script>.log` next to the script.
- Executables write to `%LOCALAPPDATA%\Workaholic\logs`, so apps in Program Files can start without permission errors.
- Output from apps running elsewhere isn't captured.

## Stopping and closing

Stopping first asks the program to exit (Ctrl+Break on Windows), then closes it and everything it started after a few seconds. The window never freezes while waiting.

When you close Workaholic, services it started are stopped. You can turn off the confirmation in **Settings**.

## Configuration

Services, groups and settings are saved in `%APPDATA%\Workaholic\services_config.json` on Windows. You can open the location from **Settings**. On other platforms, the file is under `$XDG_CONFIG_HOME/Workaholic` (or `~/.config/Workaholic`).

On first launch, an older `services_config.json` beside the EXE (or source script) is copied to AppData if no settings exist there. The old file is left untouched; an existing AppData config always takes priority. Configs from older versions are upgraded automatically, and a broken file is backed up before being replaced.

To update, close Workaholic and replace the EXE. Settings stay in AppData. Keep the EXE at the same path if launch at sign-in is enabled. To move settings to another PC, copy the AppData config, and check any program paths stored inside it.

The sign-in option adds a `Workaholic` entry under `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`. Turning it off removes it.

## Other platforms

Workaholic is built for Windows. From source it also runs on macOS and Linux (`python3 service_aggregator.py`, with `python3-tk` installed on some Linux distributions), but without Start menu search, program icons, the running-app list or sign-in launch.

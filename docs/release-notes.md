Download **Workaholic.exe** to run Workaholic without installing Python or building anything.
The **windows-x64.zip** includes the same EXE, a short guide and third-party notices.

### Features

- Run and monitor scripts and programs in one window.
- Group services and launch them in order, with delays.
- Start groups when a game or app opens, when Workaholic starts, or with a keyboard shortcut from any program.
- View live output, choose service icons and avoid duplicate processes.
- Optionally launch Workaholic when signing in to Windows.

### Updating and requirements

- Windows 10/11 x64. This executable is unsigned; Windows may show a SmartScreen warning.
- Settings live in `%APPDATA%\Workaholic\services_config.json`. An existing config beside the EXE is copied there if no AppData config exists yet.
- Close Workaholic before replacing the EXE. Settings remain in AppData.
- Workaholic bundles its own runtime. Python scripts managed by Workaholic still need Python installed separately.
- `SHA256SUMS.txt` contains download checksums. The **source.zip** contains the source at this release's commit; GitHub also provides its standard source archives.

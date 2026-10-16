# Releasing Workaholic

The Windows workflow tests and builds on pushes to `main`, pull requests, and manual runs.
Those runs provide downloadable build artifacts. A version tag also publishes a GitHub Release.

1. Update `version.py` and `docs/release-notes.md` for the release.
2. Commit and push to `main`. Check that **Windows build and release** passes.
3. Tag that commit with the exact version, then push the tag:

   ```powershell
   git tag v1.0.0
   git push origin v1.0.0
   ```

The workflow rejects tags that do not match `version.py`. It publishes only after tests,
packaging and an isolated GUI smoke test of the built EXE succeed.
The release contains `Workaholic.exe`, a portable Windows x64 ZIP, a source ZIP and SHA-256 checksums.
GitHub also adds its standard source ZIP and tar.gz links.

For a local build, run `./build.ps1`. This keeps the previous EXE as a backup.
For release files in `dist/release`, use `./build.ps1 -Release` from a clean,
committed checkout: the source archive comes from Git's `HEAD`, not uncommitted files.
Build on 64-bit Windows with 64-bit Python 3.13.

The app is unsigned. Code signing and an installer can be added separately if needed.
Do not bundle your local `services_config.json`, logs or previous EXEs.

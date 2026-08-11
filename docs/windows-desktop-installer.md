# Windows desktop installer

JLU Writing Agent reuses the existing nanobot Gateway and bundled React WebUI. The desktop
launcher starts the Gateway as a hidden child process, waits for its health endpoint, and opens
the local application in a native WebView2 window. User configuration and workspaces remain in
the user's `.nanobot` directory and are not stored under the installation directory.

## Build requirements

- Windows 10 or Windows 11 (x64)
- `uv`
- Bun
- Inno Setup 6

Install Inno Setup when necessary:

```powershell
winget install --id JRSoftware.InnoSetup -e
```

## Produce the installer

From the repository root:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/build_windows_installer.ps1
```

The distributable installer is written to:

```text
dist/installer/JLU-Writing-Agent-Setup-<version>.exe
```

Run the isolated install/start/shutdown smoke test with:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/smoke_test_windows_installer.ps1
```

Use `-SkipWebBuild` when the bundled WebUI is already current, `-SkipDesktopBuild` to reuse an
existing PyInstaller application directory, or `-SkipInstaller` to verify only the application
directory. Desktop Python dependencies are isolated in `.venv-desktop`, so building does not
modify the project's development virtual environment.

## Runtime notes

- The application listens only on `127.0.0.1` and scans a short local port range starting at
  `8765` if that port is occupied.
- If an existing healthy nanobot Gateway is already using the requested port, the desktop window
  attaches to it and does not stop it when the window closes.
- Gateway startup diagnostics are written to
  `%USERPROFILE%\.nanobot\logs\desktop-gateway.log`.
- WebView2 is included with current Windows installations. Managed or older Windows images may
  need the Microsoft Edge WebView2 Runtime installed separately.

## GitHub release

The `Windows desktop installer` workflow can be run manually to download an Actions artifact.
Pushing a tag such as `v0.3.0` also builds, smoke-tests, and publishes the installer to the matching
GitHub Release.

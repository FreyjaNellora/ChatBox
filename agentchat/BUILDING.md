# Building the AgentChat Windows Installer

This document explains how to build `AgentChat-Setup.exe` — a single-file Windows installer that installs the broker as a Windows service.

## Prerequisites

1. **Python 3.11+** with the project dependencies installed:
   ```powershell
   cd agentchat
   pip install -r requirements.txt
   ```

2. **PyInstaller**:
   ```powershell
   pip install pyinstaller
   ```

3. **Inno Setup 6** from [jrsoftware.org](https://jrsoftware.org/isdl.php). Install to the default location:
   ```
   C:\Program Files (x86)\Inno Setup 6\ISCC.exe
   ```

4. **NSSM** is already vendored in `installer/vendor/nssm-2.24/`. Do not download it again.

## Build Steps

Run the top-level build script from the repo root:

```powershell
.\build\build.ps1
```

This script:

1. Cleans previous build artifacts (`build/`, `dist/`, `installer/Output/`)
2. Runs PyInstaller to freeze `broker_daemon.py` into `dist\agentchat.exe`
3. Runs Inno Setup to produce `installer\Output\AgentChat-Setup.exe`

## Output

```
installer\Output\AgentChat-Setup.exe
```

## What the Installer Does

- Installs `agentchat.exe` and `nssm.exe` to `C:\Program Files\AgentChat`
- Registers the broker as a Windows service (`AgentChatBroker`) via NSSM
- Creates a workspace directory at `%USERPROFILE%\Documents\AgentChat` for the SQLite DB, token, and dispatch logs
- Auto-starts the service on boot
- Opens `http://localhost:8765` on finish (optional)

## Uninstall

Uninstall via **Settings → Apps → AgentChat**. The uninstaller:
- Stops and removes the Windows service
- Deletes program files
- **Preserves** the workspace directory (user data)

## Code Signing (Optional)

The installer is unsigned by default, which triggers a Windows SmartScreen warning on first run. To eliminate this:

1. Purchase a code signing certificate from a trusted CA
2. Sign the installer after build:
   ```powershell
   signtool sign /f certificate.pfx /p password /tr http://timestamp.digicert.com /td sha256 /fd sha256 installer\Output\AgentChat-Setup.exe
   ```

## Troubleshooting

| Problem | Solution |
|---------|----------|
| `iscc.exe not found` | Install Inno Setup 6 to the default location |
| `PyInstaller did not produce dist\agentchat.exe` | Check PyInstaller output for import errors; add missing `hiddenimports` to `build/agentchat.spec` |
| Port 8765 in use during install | The installer warns you. Stop the conflicting process or choose a different port (edit `installer/AgentChat.iss`) |
| Service fails to start | Check `C:\Program Files\AgentChat\broker.log` for errors |

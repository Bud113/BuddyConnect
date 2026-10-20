# Buddy Connect v8.2.2

- Settings sub-tabs now highlight the currently selected section.
- Update URL field removed. Check for Updates discovers the latest GitHub release automatically.
- Downloads require a matching SHA-256 in `update.json` (version and URL must match latest release) or a release asset named `BuddyConnect.exe.sha256` containing the 64-character SHA-256.
- Run `build_windows.bat` on Windows to build `dist/BuddyConnect.exe`.
- Do not publish an EXE without testing the Windows build.

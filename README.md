# Buddy Connect v8.3.0 — Synchronized PC stats

Both Buddy Connect PC Stats and the Buddy OS Simulator now use the **same cached CPU sample** from the local background agent (`/stats`). The agent samples CPU every 250ms, and both interfaces request the shared snapshot every 250ms. No separate `psutil.cpu_percent` measurements occur in the desktop UI or on HTTP requests.

**Note:** Independent displays may briefly differ by one sample because their redraws are not simultaneous; they converge on the same values. Other hardware measurements update every ~2 seconds.

**Important:** Close any existing Buddy Connect agent before starting the new build. An old agent running on port 8765 would still serve the old stats. On Windows, stop the old background BuddyConnect.exe process in Task Manager (or restart Windows) before testing.

Build using `build_windows.bat`; then launch `dist/BuddyConnect.exe` and open `index.html`. This retains version **8.3.0** as requested.

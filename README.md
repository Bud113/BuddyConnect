# Buddy Connect v8.3.0

- New **PC Stats** dashboard with live CPU, RAM, system drive usage, battery, CPU temperature (if available), and Wi-Fi status. Refreshes every two seconds using psutil.
- Removed the **Test Tutorial** button. The first-run tutorial remains available to new users.
- Removed **Show demonstration** from the Buddy OS simulator's PC Companion menu. The simulator now displays actual data only when connected to the local Buddy Connect agent.
- Existing Home, Appearance, Settings, Organizer, and GitHub updater retained.

## Build on Windows

Extract this ZIP, run `build_windows.bat`, then launch `dist\BuddyConnect.exe`. Keep the `index.html` simulator alongside the project. Test before publishing a release. The PC Stats dashboard runs on the desktop app; the simulator reads the background agent's existing `/stats` endpoint.

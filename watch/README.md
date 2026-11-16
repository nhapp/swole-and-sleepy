# Swole Fuel: fēnix 6 watch widget

Shows today's **calories eaten against your goal** on your wrist:

- **Glance** (in the widget loop): `1,840 / 2,350` with a progress bar.
- **Full screen** (press Start on the glance): a ring filling toward the goal, calories eaten,
  calories left (or over), and when it last updated.

The watch reads the numbers straight from Garmin's Personal API, through your phone's Bluetooth
connection. They come from Garmin's food log, which MyFitnessPal fills when the two accounts are linked:
- **Goal:** the calorie goal Garmin holds.
- **Eaten:** what MyFitnessPal has synced so far today.

It refreshes every 15 minutes in the background, and right away when you open it.

Built for fēnix 6 / 6 Pro / 6S / 6S Pro / 6X Pro (Connect IQ 3.1+).

## Build and install (Mac, about 30 minutes the first time)

Apps you install yourself can't be configured from the phone, so your Garmin key is baked into
the app when you build it. The key file stays on your Mac: it's git-ignored and never uploaded.

### 1. Install Garmin's tools (once)
1. Install **Visual Studio Code**: https://code.visualstudio.com
2. Install the **Connect IQ SDK Manager**: https://developer.garmin.com/connect-iq/sdk/
   - Open it, sign in with your Garmin account, and download the latest SDK. Click **Use as current SDK**.
   - Under **Devices**, download your exact watch model. On the watch it's in **Settings → System → About**,
     for example *fēnix 6 Pro / Sapphire*.
3. In VS Code, open the **Extensions** panel (⌘⇧X), search **Monkey C**, and install Garmin's extension.
4. Press ⌘⇧P and run **Monkey C: Generate a Developer Key**. Save it somewhere you'll keep, for example `~/garmin/`.

### 2. Get the project and add your key
1. Download the latest repo ZIP (GitHub → Code → Download ZIP) and unzip it.
2. Copy `watch/properties.xml.example` to `watch/resources/settings/properties.xml`.
3. Open that new file and replace `PASTE-YOUR-GARMIN-PERSONAL-API-KEY-HERE` with your Garmin personal API key.
   That's the same kind of key as the `GARMIN_PERSONAL_API_KEY` GitHub secret.
   If Garmin won't show the old key again, create a new one at
   https://connect.garmin.com/app/settings/personalApiToken. If that replaces the old key, update the GitHub
   secret to match, or the dashboard sync stops.

### 3. Try it in the simulator
1. In VS Code: **File → Open Folder…** and choose the `watch` folder.
2. Press ⌘⇧P, run **Monkey C: Run**, and pick your watch model.
3. The simulator calls Garmin for real. After a few seconds you should see today's calories.
   - "Key rejected" means the key in `properties.xml` is wrong.
   - "Sync error" plus a number: send me the number.

### 4. Build and copy it to the watch
1. Press ⌘⇧P, run **Monkey C: Build for Device**, pick your model, and choose an output folder. You get a `.prg` file.
2. The fēnix 6 connects over MTP, which macOS can't browse on its own. Install **OpenMTP** (free):
   https://openmtp.ganeshrvel.com
3. Plug the watch in with its charging cable and open OpenMTP. Copy the `.prg` into **GARMIN/APPS** on the watch.
4. Unplug. The watch installs it within a minute.

### 5. Show it on the watch
From the watch face, press **Up** or **Down** to scroll the glances.
If **Swole Fuel** isn't there: hold **Menu** → **Glances** (or **Widgets**) → **Add** → **Swole Fuel**,
then move it near the top.

## If something's off

| Watch shows | Meaning |
|---|---|
| Add API key | `properties.xml` is missing or still has the placeholder. Rebuild after fixing it. |
| Key rejected | Garmin refused the key. Make a new one and rebuild. |
| Phone offline | The watch can't reach your phone. Check that Garmin Connect is running and Bluetooth is on. |
| Open to sync | No reading yet today. Open the full view once to fetch. |
| 0 eaten | Nothing has synced from MyFitnessPal today yet. MFP pushes to Garmin a few minutes after you log. |

# Turntable-1 for Android: a phone and tablet app, and a TV app

One Android Studio project builds both:

- **Turntable-1** (phone and tablet): the whole site, full screen. **Listen live** plays in the app's own player, so it keeps going in the background, with notification and lock-screen controls. Links that leave your site (like Discogs) open in your browser. Long-press the app's icon for **Change server**.
- **Turntable-1 TV** (Google TV, Android TV): opens straight into the TV view with the stream playing, keeps the screen awake, and retries by itself if the server drops out. Browse with the remote: **Now**, **Beepnik**, **Log**, **Cargo** and **Stars** along the top (arrows to move, OK to open or play). **Back** steps out one level at a time; from Now it opens a menu: Reload, Change server address, Exit.

Both ask for your server's address on first launch: `http://192.168.0.20:8080` (fastest at home) or your public `https://...` address (works anywhere).

## First time in Android Studio

1. **Open Android Studio.** The first time, a setup wizard runs: choose **Standard**, accept the licenses, and let it download the Android SDK (a few minutes).
2. **Open this project:** **File > Open** (or **Open** on the welcome screen), and choose this `Turntable-1` folder (the one with `settings.gradle.kts`).
3. **Let it sync.** The first sync downloads Gradle and the libraries; give it several minutes. If it offers to **upgrade the Android Gradle Plugin**, accept: that's safe.
4. If it asks to **trust the project**, choose **Trust**.

## Building the two apps

The two apps are two **build variants** of one project:

1. Open **Build Variants** (**View > Tool Windows > Build Variants**, or the tab on the left edge).
2. Set **app** to **mobileDebug**, then choose **Build > Build App Bundle(s) / APK(s) > Build APK(s)**. When it finishes, click **locate** in the popup: the file is `app/build/outputs/apk/mobile/debug/app-mobile-debug.apk`.
3. Set **app** to **tvDebug** and build again: `app/build/outputs/apk/tv/debug/app-tv-debug.apk`.

## Installing

**On your phone or tablet:** copy `app-mobile-debug.apk` to the phone (USB, Google Drive, or email it to yourself) and open it. Android asks to allow installing from that source the first time: allow it, then install. On Android 13 and later, allow notifications when the app asks, so the playback controls can show.

**On the Google TV,** the easy way is to put the APK on the Pi and download it from the TV:

1. Copy `app-tv-debug.apk` to the Pi, into the site's static folder, as `turntable-tv.apk`:

   ```bash
   scp app-tv-debug.apk you@192.168.0.20:~/turntable-web/static/turntable-tv.apk
   ```

2. On the TV, install the **Downloader** app (by AFTVnews) from the Play Store.
3. On the TV, allow Downloader to install apps: **Settings > Apps > Security & restrictions > Unknown sources**, and switch on **Downloader**. (On some TVs: Settings > System > Developer options first; the wording varies.)
4. In Downloader, enter `http://192.168.0.20:8080/static/turntable-tv.apk`, download, and **Install**.
5. **Turntable-1 TV** appears in your apps. To put it on the home screen row, long-press it there and choose to add it to favorites.

To update either app later, build the new APK and install it over the old one the same way: your server address is kept.

## With Home Assistant

The **Android TV Remote** integration can turn the TV on and open the app in one step, triggered when the turntable starts playing (the `sensor.turntable_state` REST sensor from the Fully Kiosk notes works here too):

```yaml
actions:
  - action: remote.turn_on
    target:
      entity_id: remote.YOUR_TV
    data:
      activity: io.github.torinml.turntable.tv
```

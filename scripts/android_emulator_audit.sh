#!/usr/bin/env bash
# One command, start to finish, on a Mac: install the Android command-line
# tools (no Android Studio needed), create a Pixel emulator with the Google
# Play image (real Chrome), boot it, open Chrome without its first-run screens,
# connect to it over adb, and run scripts/prebid_vast_audit.py in it
# (ATTACH + DRIVE). Re-running skips whatever is already installed.
#
#   cd ~/yield-dashboard-vast && git pull && bash scripts/android_emulator_audit.sh
#
# Knobs: LOADS (default 20), API (default 35), AVD (default nw_pixel),
# KEEP=1 to leave the emulator running afterwards.
#
# Caution: an emulator is itself an invalid-traffic signal (MRC lists emulators
# under SIVT), so a bidder declining to bid here says little about real users.
# A real Android phone over USB is the better device (docs/prebid_viewability.md).
set -euo pipefail

LOADS="${LOADS:-20}"
API="${API:-35}"
AVD="${AVD:-nw_pixel}"
SDK="${ANDROID_HOME:-$HOME/Library/Android/sdk}"
REPO="$(cd "$(dirname "$0")/.." && pwd)"
if [ "$(uname -m)" = arm64 ]; then ABI=arm64-v8a; JARCH=aarch64; else ABI=x86_64; JARCH=x64; fi
IMG="system-images;android-${API};google_apis_playstore;${ABI}"
step() { printf '\n==> %s\n' "$*"; }

[ "$(uname)" = Darwin ] || { echo "This script is for macOS."; exit 1; }

step "1/7 Java (the Android SDK manager needs it)"
if [ -x "/Applications/Android Studio.app/Contents/jbr/Contents/Home/bin/java" ]; then
  export JAVA_HOME="/Applications/Android Studio.app/Contents/jbr/Contents/Home"
elif ! /usr/libexec/java_home >/dev/null 2>&1; then
  JDK="$HOME/.nw-jdk17"
  if [ ! -x "$JDK/Contents/Home/bin/java" ]; then
    curl -fL --progress-bar -o /tmp/nw-jdk.tar.gz \
      "https://api.adoptium.net/v3/binary/latest/17/ga/mac/${JARCH}/jdk/hotspot/normal/eclipse"
    mkdir -p "$JDK" && tar -xzf /tmp/nw-jdk.tar.gz -C "$JDK" --strip-components=1
  fi
  export JAVA_HOME="$JDK/Contents/Home"
else
  export JAVA_HOME="$(/usr/libexec/java_home)"
fi
export PATH="$JAVA_HOME/bin:$PATH"
java -version 2>&1 | head -1

step "2/7 Android command-line tools"
SDKM="$SDK/cmdline-tools/latest/bin/sdkmanager"
if [ ! -x "$SDKM" ]; then
  mkdir -p "$SDK/cmdline-tools"
  curl -fL --progress-bar -o /tmp/nw-cmdline-tools.zip \
    "https://dl.google.com/android/repository/commandlinetools-mac-15641748_latest.zip"
  rm -rf "$SDK/cmdline-tools/latest" "$SDK/cmdline-tools/cmdline-tools"
  unzip -q /tmp/nw-cmdline-tools.zip -d "$SDK/cmdline-tools"
  mv "$SDK/cmdline-tools/cmdline-tools" "$SDK/cmdline-tools/latest"
fi
export ANDROID_HOME="$SDK"
ADB="$SDK/platform-tools/adb"
EMU="$SDK/emulator/emulator"

step "3/7 Emulator, adb and the Google Play system image (a few GB the first time)"
yes | "$SDKM" --licenses >/dev/null 2>&1 || true
"$SDKM" --install "platform-tools" "emulator" "$IMG" | grep -v '^\[' || true

step "4/7 Virtual phone '$AVD'"
if ! "$EMU" -list-avds | grep -qx "$AVD"; then
  AVDM="$SDK/cmdline-tools/latest/bin/avdmanager"
  for dev in pixel_8 pixel_7 pixel_6 pixel; do
    if echo no | "$AVDM" create avd -n "$AVD" -k "$IMG" -d "$dev" >/dev/null 2>&1; then
      echo "created $AVD ($dev)"; break
    fi
  done
  "$EMU" -list-avds | grep -qx "$AVD" || { echo "could not create the AVD"; exit 1; }
fi

step "5/7 Booting it (a window opens; leave it alone)"
if ! "$ADB" devices | grep -q '^emulator-'; then
  "$EMU" -avd "$AVD" -no-snapshot-save -no-boot-anim >/tmp/nw-emulator.log 2>&1 &
fi
"$ADB" wait-for-device
for _ in $(seq 1 180); do
  [ "$("$ADB" shell getprop sys.boot_completed 2>/dev/null | tr -d '\r')" = 1 ] && break
  sleep 2
done
"$ADB" shell getprop sys.boot_completed | grep -q 1 || { echo "emulator didn't finish booting"; exit 1; }
echo booted

step "6/7 Chrome, skipping its welcome screens, connected over adb"
# Chrome reads extra flags from this file when it is the debug app.
"$ADB" shell am set-debug-app --persistent com.android.chrome >/dev/null
"$ADB" shell "echo '_ --disable-fre --no-default-browser-check --no-first-run' > /data/local/tmp/chrome-command-line"
"$ADB" shell am force-stop com.android.chrome
"$ADB" shell am start -a android.intent.action.VIEW -d https://www.newsweek.com/ com.android.chrome >/dev/null
ok=""
for _ in $(seq 1 30); do
  "$ADB" forward tcp:9222 localabstract:chrome_devtools_remote >/dev/null 2>&1 || true
  if curl -fs http://localhost:9222/json/version >/dev/null 2>&1; then ok=1; break; fi
  sleep 2
done
if [ -z "$ok" ]; then
  echo "Chrome isn't answering yet. In the emulator window, tap through any Chrome"
  echo "welcome screen until Newsweek loads, then re-run this script."
  exit 1
fi
curl -fs http://localhost:9222/json/version | grep -E '"(Browser|User-Agent)"'

step "7/7 Running the audit in the emulator's Chrome ($LOADS articles)"
cd "$REPO"
[ -d .venv ] || python3 -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate
python -c "import playwright" 2>/dev/null || pip install -q playwright
OUT_DIR="${OUT_DIR:-prebid-vast-audit-android}"
caffeinate -i env ATTACH=9222 DRIVE=1 LOADS="$LOADS" OUT_DIR="$OUT_DIR" \
  python scripts/prebid_vast_audit.py

if [ -z "${KEEP:-}" ]; then "$ADB" emu kill >/dev/null 2>&1 || true; fi
echo
echo "Done. Send $REPO/$OUT_DIR/report.txt (and auctions.csv for Onetag)."

#!/bin/sh
# Build Read Smoother as a Mac app (one window, its own Dock icon; it starts and stops the server itself).
# Needs the Xcode command line tools (xcode-select --install).
#   app/build-app.sh                         -> ~/Desktop/Read Smoother.app
#   app/build-app.sh "My Reader" ~/Applications
set -e
cd "$(dirname "$0")/.."
NAME="${1:-Read Smoother}"
OUT="${2:-$HOME/Desktop}"
PROJ="$(pwd)"
APP="$OUT/$NAME.app"
TMP="$OUT/.$NAME.app.building"
xml() { printf '%s' "$1" | sed -e 's/&/\&amp;/g' -e 's/</\&lt;/g' -e 's/>/\&gt;/g'; }

rm -rf "$TMP"
mkdir -p "$TMP/Contents/MacOS" "$TMP/Contents/Resources"
# Some Command Line Tools installs keep a stale usr/include/swift/module.modulemap next to bridging.modulemap
# ("redefinition of module 'SwiftBridging'"); hide it for this build instead of touching system files.
CLT=/Library/Developer/CommandLineTools/usr/include/swift
if [ -f "$CLT/module.modulemap" ] && [ -f "$CLT/bridging.modulemap" ]; then
  : > "$TMP/empty.modulemap"
  printf '{"version":0,"roots":[{"name":"%s","type":"directory","contents":[{"name":"module.modulemap","type":"file","external-contents":"%s"}]}]}' "$CLT" "$TMP/empty.modulemap" > "$TMP/overlay.yaml"
  SWIFT_FLAGS="$SWIFT_FLAGS -vfsoverlay $TMP/overlay.yaml"
fi
swiftc -O $SWIFT_FLAGS -o "$TMP/Contents/MacOS/ReadSmoother" app/ReadSmoother.swift -framework Cocoa -framework WebKit
cp app/icon.icns "$TMP/Contents/Resources/AppIcon.icns"
cat > "$TMP/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>$(xml "$NAME")</string>
  <key>CFBundleDisplayName</key><string>$(xml "$NAME")</string>
  <key>CFBundleIdentifier</key><string>local.readsmoother.app</string>
  <key>CFBundleExecutable</key><string>ReadSmoother</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>1.0</string>
  <key>CFBundleVersion</key><string>1</string>
  <key>LSMinimumSystemVersion</key><string>12.0</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>NSAppTransportSecurity</key><dict><key>NSAllowsLocalNetworking</key><true/></dict>
  <key>RSProjectPath</key><string>$(xml "$PROJ")</string>
  <key>RSPort</key><integer>8765</integer>
</dict>
</plist>
EOF
rm -f "$TMP/empty.modulemap" "$TMP/overlay.yaml"
codesign --force --sign - "$TMP" >/dev/null 2>&1 || true
rm -rf "$APP"
mv "$TMP" "$APP"
touch "$APP"
echo "built $APP"

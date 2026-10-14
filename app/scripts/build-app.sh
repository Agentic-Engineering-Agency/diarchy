#!/usr/bin/env bash
# Builds "Diarchy.app" under app/build/ (release, arm64, ad-hoc signed).
#
# Usage: build-app.sh [--install]
#   --install   copy the bundle to ~/Applications (refuses to replace a bundle
#               that is not owned by Diarchy) and register it.
#
# Environment:
#   DIARCHY_SCRATCH_PATH      SwiftPM --scratch-path (avoids the default .build lock).
#   DIARCHY_SKIP_LSREGISTER   when "1", --install skips lsregister (test harness only).
#   DIARCHY_TEST_PLANT_PYC    when "1", plants a .pyc in the payload before the final
#                         payload check, proving the check fires (test harness only).
set -euo pipefail

BUNDLE_ID="dev.sebastiangmz.diarchy"
BUNDLE_NAME="Diarchy.app"
LSREGISTER="/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister"

# Build artifacts that must never ship inside the payload (test runs leave .pyc
# files that embed absolute local paths).
PAYLOAD_EXCLUDES=(--exclude '__pycache__/' --exclude '*.pyc' --exclude '.DS_Store')

install=0
case "${1:-}" in
  "") ;;
  --install) install=1 ;;
  *) echo "usage: build-app.sh [--install]" >&2; exit 2 ;;
esac
[ "$#" -le 1 ] || { echo "usage: build-app.sh [--install]" >&2; exit 2; }

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
app_dir="$(cd "$script_dir/.." && pwd)"
repo_root="$(cd "$app_dir/.." && pwd)"

# Fail-fast checks: everything below runs before any compilation.
version_file="$repo_root/host/VERSION"
missing_msg="build-app.sh: host/VERSION is missing (the host team creates it); cannot build the payload"
if [ ! -s "$version_file" ]; then
  echo "$missing_msg" >&2
  exit 1
fi
version="$(tr -d '[:space:]' < "$version_file")"
if [ -z "$version" ]; then
  echo "$missing_msg" >&2
  exit 1
fi
# The version is substituted into the plist through sed; reject anything that could break it.
case "$version" in
  *[!0-9A-Za-z.+_-]*) echo "build-app.sh: host/VERSION contains unsupported characters" >&2; exit 1 ;;
esac

dest_dir="$HOME/Applications"
dest="$dest_dir/$BUNDLE_NAME"
if [ "$install" -eq 1 ] && { [ -e "$dest" ] || [ -L "$dest" ]; }; then
  existing_id="$(defaults read "$dest/Contents/Info" CFBundleIdentifier 2>/dev/null || true)"
  if [ "$existing_id" != "$BUNDLE_ID" ]; then
    echo "build-app.sh: refusing to replace $dest (not owned by Diarchy)" >&2
    exit 1
  fi
fi

work_dir="$(mktemp -d "${TMPDIR:-/tmp}/diarchy-build-app.XXXXXX")"
trap 'rm -rf "$work_dir"' EXIT

swift_args=(--package-path "$app_dir" -c release --arch arm64)
if [ -n "${DIARCHY_SCRATCH_PATH:-}" ]; then
  swift_args+=(--scratch-path "$DIARCHY_SCRATCH_PATH")
fi

swift build "${swift_args[@]}" >&2
bin_dir="$(swift build "${swift_args[@]}" --show-bin-path)"
binary="$bin_dir/Diarchy"
[ -f "$binary" ] || { echo "build-app.sh: built binary not found at $binary" >&2; exit 1; }

bundle="$app_dir/build/$BUNDLE_NAME"
rm -rf "$bundle"
mkdir -p "$bundle/Contents/MacOS" "$bundle/Contents/Resources/payload"

# Binary: always a copy, never a symlink.
cp "$binary" "$bundle/Contents/MacOS/Diarchy"
chmod +x "$bundle/Contents/MacOS/Diarchy"

# Info.plist: only @VERSION@ is substituted.
sed "s/@VERSION@/$version/g" "$app_dir/Resources/Info.plist.template" > "$bundle/Contents/Info.plist"
plutil -lint "$bundle/Contents/Info.plist" >&2

# Icon: iconset from AppIcon.png, then iconutil.
iconset="$work_dir/AppIcon.iconset"
mkdir -p "$iconset"
for px in 16 32 128 256 512; do
  sips -z "$px" "$px" "$app_dir/Resources/AppIcon.png" --out "$iconset/icon_${px}x${px}.png" >/dev/null
  sips -z "$((px * 2))" "$((px * 2))" "$app_dir/Resources/AppIcon.png" --out "$iconset/icon_${px}x${px}@2x.png" >/dev/null
done
iconutil -c icns "$iconset" -o "$bundle/Contents/Resources/AppIcon.icns"

# Payload: copies with modes preserved, minus build artifacts; scripts (no extension
# or .sh) forced executable. host/VERSION is data and stays non-executable.
payload="$bundle/Contents/Resources/payload"
rsync -a "${PAYLOAD_EXCLUDES[@]}" "$repo_root/client/" "$payload/client/"
rsync -a "${PAYLOAD_EXCLUDES[@]}" "$repo_root/host/" "$payload/host/"
cp -p "$version_file" "$payload/VERSION"
find "$payload/client" "$payload/host" -type f \( -name '*.sh' -o ! -name '*.*' \) \
  ! -path "$payload/host/VERSION" -exec chmod +x {} +

if [ "${DIARCHY_TEST_PLANT_PYC:-}" = "1" ]; then
  : > "$payload/client/planted.cpython-312.pyc"
fi

# Final check: no build artifact may remain anywhere in the payload.
offenders="$(find "$payload" \( -name '__pycache__' -o -name '*.pyc' -o -name '.DS_Store' \) -print)"
if [ -n "$offenders" ]; then
  {
    echo "build-app.sh: payload contains files that must not ship:"
    echo "$offenders"
  } >&2
  exit 1
fi

codesign --force --deep --sign - "$bundle" >&2
codesign --verify --deep --strict "$bundle" >&2

if [ "$install" -eq 1 ]; then
  mkdir -p "$dest_dir"
  rm -rf "$dest"
  cp -R "$bundle" "$dest"
  if [ "${DIARCHY_SKIP_LSREGISTER:-}" != "1" ]; then
    "$LSREGISTER" -f "$dest"
  fi
fi

echo "$bundle"

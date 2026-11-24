#!/usr/bin/env bash
# .deb paketi üretir:  scripts/build-deb.sh [sürüm]
# Sürüm verilmezse VERSION dosyası kullanılır. "v1.2.0" gibi etiketler de kabul edilir.
set -euo pipefail
cd "$(dirname "$0")/.."

VERSION="${1:-$(cat VERSION)}"
VERSION="${VERSION#v}"
MAINTAINER="${DEB_MAINTAINER:-rkmon maintainer <noreply@example.com>}"
HOMEPAGE="${DEB_HOMEPAGE:-https://github.com/}"
OUT="${OUT_DIR:-dist}"

STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
chmod 755 "$STAGE"

install -Dm755 src/rkmon.py "$STAGE/usr/bin/rkmon"
sed -i "s/^__version__ = .*/__version__ = \"$VERSION\"/" "$STAGE/usr/bin/rkmon"

DOC="$STAGE/usr/share/doc/rkmon"
install -Dm644 LICENSE "$DOC/copyright"
install -Dm644 README.md "$DOC/README.md"
printf 'rkmon (%s) stable; urgency=medium\n\n  * Release %s\n\n -- %s  %s\n' \
  "$VERSION" "$VERSION" "$MAINTAINER" "$(date -R)" | gzip -9n > "$DOC/changelog.gz"

install -d -m755 "$STAGE/usr/share/man/man1"
gzip -9nc packaging/rkmon.1 > "$STAGE/usr/share/man/man1/rkmon.1.gz"
chmod 644 "$STAGE/usr/share/man/man1/rkmon.1.gz"

mkdir -p "$STAGE/DEBIAN"
SIZE="$(du -sk --exclude=DEBIAN "$STAGE" | cut -f1)"
sed -e "s|@VERSION@|$VERSION|" -e "s|@SIZE@|$SIZE|" \
    -e "s|@MAINTAINER@|$MAINTAINER|" -e "s|@HOMEPAGE@|$HOMEPAGE|" \
    packaging/control > "$STAGE/DEBIAN/control"

mkdir -p "$OUT"
DEB="$OUT/rkmon_${VERSION}_all.deb"
dpkg-deb --root-owner-group -Zxz --build "$STAGE" "$DEB" >/dev/null
echo "$DEB"

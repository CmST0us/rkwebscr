#!/bin/sh
# 在 Linux 构建环境中校验真实 DEB；从仓库根目录运行。
set -eu

package=${1:?用法：sh tests/check-deb.sh <rkwebscr.deb>}
test "$(dpkg-deb -f "$package" Package)" = rkwebscr
test "$(dpkg-deb -f "$package" Version)" = "$(dpkg-parsechangelog -S Version)"
test "$(dpkg-deb -f "$package" Architecture)" = arm64

test_dir=$(mktemp -d)
trap 'rm -rf "$test_dir"' EXIT HUP INT TERM
dpkg-deb -R "$package" "$test_dir"
cmp server/rkwebscrd.py "$test_dir/usr/bin/rkwebscrd"
cmp scripts/rkwebscr-setup "$test_dir/usr/bin/rkwebscr-setup"
for name in postinst prerm postrm; do
  cmp "debian/$name" "$test_dir/DEBIAN/$name"
done
for path in systemd/*.service; do
  cmp "$path" "$test_dir/usr/lib/systemd/user/${path##*/}"
done
for path in systemd-system/*.service systemd-system/*.socket; do
  cmp "$path" "$test_dir/usr/lib/systemd/system/${path##*/}"
done
for name in index.html app.js styles.css; do
  cmp "web/$name" "$test_dir/usr/share/rkwebscr/web/$name"
done
cmp avahi/rkwebscr.service "$test_dir/etc/avahi/services/rkwebscr.service"
test "$(cat "$test_dir/DEBIAN/conffiles")" = /etc/avahi/services/rkwebscr.service
test -x "$test_dir/usr/bin/rkwebscrd"
test -x "$test_dir/usr/bin/rkwebscr-setup"
encoder="$test_dir/usr/lib/rkwebscr/rkwebscr-dmabuf-encoder"
test -x "$encoder"
readelf -h "$encoder" | grep -q 'Machine:.*AArch64'
readelf -d "$encoder" > "$test_dir/dynamic"
grep -q 'Shared library: \[librga.so.2\]' "$test_dir/dynamic"
grep -q 'Shared library: \[librockchip_mpp.so.1\]' "$test_dir/dynamic"
if grep -Eq '\((RPATH|RUNPATH)\)' "$test_dir/dynamic"; then
  echo '编码器不应携带构建机的运行时库搜索路径。' >&2
  exit 1
fi
printf '%s\n' 'DEB identity, payload and ARM64 linkage checks passed'

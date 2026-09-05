#!/bin/sh
set -eu

: "${FLANGE_SOURCE_DIR:?请通过 flange app build 启动构建。}"
: "${FLANGE_TARGET_ARCH:?缺少 Flange 目标架构。}"
: "${FLANGE_TARGET_DIR:?缺少 Flange 目标目录。}"
: "${FLANGE_SYSROOT:?缺少 Flange 依赖安装树。}"
: "${FLANGE_APP_OUTPUT_DIR:?缺少 Flange 产物目录。}"

if [ "$FLANGE_TARGET_ARCH" != aarch64 ]; then
  echo "rkwebscr 当前只支持 aarch64，收到：$FLANGE_TARGET_ARCH" >&2
  exit 2
fi
case "${FLANGE_TARGET_DIR##*/}" in
  debug) export DEB_BUILD_OPTIONS="${DEB_BUILD_OPTIONS:-} noopt nostrip" ;;
  release) ;;
  *) echo "无法识别 Flange 的 debug/release 变体。" >&2; exit 2 ;;
esac

cd "$FLANGE_SOURCE_DIR"
# Flange 已重定位依赖树中的 .pc，并提供目标工具链与 pkg-config 搜索路径。
# 该树只补充 MPP/RGA；PipeWire、DRM 和 libc 仍使用容器中的目标架构开发包。
unset PKG_CONFIG_PATH DESTDIR
pkg-config --exists libpipewire-0.3 libdrm rockchip_mpp librga
RGA_LIBS=$(pkg-config --libs librga)
SHLIBDEPS_LIBRARY_DIR="$FLANGE_SYSROOT/usr/lib/aarch64-linux-gnu"
export RGA_LIBS SHLIBDEPS_LIBRARY_DIR

make check
# MPP/RGA 来自 Flange 构建依赖，不在容器 dpkg 数据库中，因此跳过包名检查。
# 编译、链接和 dpkg-shlibdeps 仍会验证实际头文件、库与运行依赖。
dpkg-buildpackage --build=binary --host-arch=arm64 --no-sign -d

version=$(dpkg-parsechangelog -S Version)
sh tests/check-deb.sh "../rkwebscr_${version}_arm64.deb"
install -m 644 "../rkwebscr_${version}_arm64.deb" "$FLANGE_APP_OUTPUT_DIR/"

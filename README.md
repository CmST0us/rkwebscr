# rkwebscr

rkwebscr 是一套面向 Rockchip RK3588 设备的低延迟 GNOME Wayland 远程桌面
方案。它会通过 Mutter 创建虚拟显示器，再把无头桌面以 WebRTC 的方式传输到
局域网内的浏览器。项目目前主要在安装 Ubuntu 24.04、GNOME 46 的 Radxa
ROCK 5B 上测试。

## 功能特性

- 使用 Mutter `RecordVirtual` 创建原生 GNOME 无头桌面，无需连接 HDMI
- 通过 PipeWire 以线性 DMA-BUF 捕获画面
- 使用 Rockchip MPP 硬件编码 H.264
- 支持双向 Opus 音频：桌面声音传到浏览器，浏览器麦克风传回 GNOME
- 直接使用浏览器原生 WebRTC 视频渲染，避免二次画布时钟造成周期性跳帧
- 通过 libei 传递鼠标、键盘和滚轮输入，并合并高频鼠标移动事件
- 支持 Wayland 与浏览器之间双向传递文本剪贴板
- 通过 mDNS 和 DNS-SD 发布 `rkwebscr.local`
- 可在可信局域网内直接通过 HTTP 访问，无需令牌

视频主链路如下：

```text
GNOME/Mutter -> PipeWire DMA-BUF -> RGA NV12 -> MPP H.264 -> WebRTC -> 浏览器
```

Python 服务负责 Mutter D-Bus 会话、输入校验、WebRTC 协商和 HTTP 服务；C++
桥接程序负责捕获 DMA-BUF、转换像素格式以及调用 MPP 编码；GStreamer 负责
WebRTC、RTP 和 Opus 音频。安装 rkwebscr 不会替换或修改系统中已有的
GStreamer。

浏览器直接使用原生 `video` 元素完成 WebRTC 视频渲染，不再额外维护一套画布
刷新时钟。

## 项目结构

```text
native/          DMA-BUF 到 Rockchip MPP 的编码桥接程序
server/          GNOME、WebRTC、输入控制和 HTTP 服务
web/             浏览器客户端
systemd/         无头 GNOME 和 rkwebscr 的用户服务
systemd-system/  Avahi、80 端口代理和性能调优等系统服务
avahi/           DNS-SD 服务声明
udev/            Rockchip 媒体设备权限规则
scripts/         安装后的初始化脚本
debian/          Debian 源码包元数据
tests/           快速检查脚本
```

## 构建

请在 RK3588 目标设备上安装构建依赖：

```bash
sudo apt install build-essential dpkg-dev pkg-config \
  libpipewire-0.3-dev libdrm-dev librga2 rockchip-mpp-dev
```

编译原生编码器并运行检查：

```bash
make
make check
```

构建 Debian 二进制包：

```bash
make deb
```

`dpkg-buildpackage` 会按照 Debian 的常规目录结构，把
`rkwebscr_0.4.5_<架构>.deb` 写入项目的上一级目录。

## 安装路径

DEB 安装包遵循 Ubuntu 的标准文件系统布局：

```text
/usr/bin/rkwebscrd                               服务程序
/usr/bin/rkwebscr-setup                          首次安装初始化脚本
/usr/lib/rkwebscr/rkwebscr-dmabuf-encoder        包内使用的原生编码程序
/usr/share/rkwebscr/web/                         浏览器前端
/usr/lib/systemd/user/rkwebscr.service            rkwebscr 用户服务
/usr/lib/systemd/user/rkwebscr-headless.service   无头 GNOME 用户服务
/usr/lib/systemd/system/avahi-daemon.service.d/   mDNS 主机名配置
/usr/lib/systemd/system/rkwebscr-http.*            80 端口代理
/etc/avahi/services/rkwebscr.service              DNS-SD 服务声明
/usr/lib/udev/rules.d/99-rkwebscr-rockchip.rules  设备权限规则
/usr/share/doc/rkwebscr/                          项目文档
```

## 安装与首次启动

以下示例假定桌面用户为 `flange`。先安装 DEB，并允许 `flange` 访问视频设备：

```bash
sudo apt install ../rkwebscr_0.4.5_arm64.deb
sudo usermod -aG video flange
```

rkwebscr 以 systemd 用户服务运行。无头设备开机后通常没有 `flange` 的登录
会话，因此必须为这个用户开启 linger，让用户服务能够随系统启动：

```bash
sudo loginctl enable-linger flange
```

修改用户组后需要重新登录或重启设备，新的权限才会生效。

rkwebscr 使用 GNOME Shell 的原生 `user` 会话模式，不依赖 `ubuntu-session`
或 Ubuntu 专用的 Shell 扩展。

GNOME Remote Desktop 不能与 rkwebscr 同时创建虚拟显示器。如果当前登录的
就是 `flange`，请关闭该服务，然后运行初始化脚本：

```bash
systemctl --user disable --now gnome-remote-desktop.service
rkwebscr-setup
```

`rkwebscr-setup` 会启用 `rkwebscr-headless.service` 和 `rkwebscr.service`，并
打印本机和局域网访问地址。这个命令必须以 `flange` 身份运行，不能直接在
ADB root shell 中执行。

如果当前只能使用 ADB root shell，可以执行：

```bash
loginctl enable-linger flange
runuser -u flange -- env XDG_RUNTIME_DIR=/run/user/1000 rkwebscr-setup
```

首次安装时不能跳过这一步。否则系统的 80 端口虽然仍在监听，但 8080 后端
不会启动，浏览器会收到空响应（`Empty response`）。后续升级 DEB 时，只要
用户服务的启用状态和 linger 没有被清除，就不需要再次运行初始化脚本。

## 访问服务

在同一局域网内打开：

```text
http://rkwebscr.local/
```

也可以直接使用设备的 Wi-Fi 或以太网 IP，例如
`http://192.168.10.65/`。Avahi 还会发布 `_rkwebscr._tcp` 和
`_http._tcp` 服务。如果局域网中已有设备占用了同名 mDNS 主机名，Avahi 可能
会自动在名称后追加数字。

对外 HTTP 服务使用 TCP 80 端口，WebRTC 音视频优先使用 UDP 8090，并可回退
到 TCP 8090。设备启用防火墙时，需要允许可信局域网访问这些端口。WebRTC
媒体经过 DTLS-SRTP 加密，但 HTTP 控制接口没有鉴权，因此不要把服务直接
暴露到公网。

工具栏中的剪贴板按钮可以双向传递文本。在普通 HTTP 局域网页面中，如果浏览器
禁止调用 Clipboard API，请改用弹窗内的文本框。

大多数浏览器只允许 HTTPS 页面使用麦克风。通过普通 HTTP 局域网地址访问时，
画面、桌面声音和输入控制不受影响，但浏览器可能拒绝发送麦克风音频。

## 配置

安装包默认使用 1280×720、60 FPS、6 Mbps CBR 和一秒 GOP。如果需要调整
分辨率、帧率或码率，请使用下面的命令覆盖用户服务配置：

```bash
systemctl --user edit rkwebscr.service
```

`RKWEBSCR_CAPTURE_FPS` 用于校准虚拟显示器的刷新时钟。RGA 在 DMA-BUF 之间
完成 BGRx 到 NV12 的硬件转换。ROCK 5B 默认设为 63，使 Mutter 的实际输出稳定
在 60 FPS；编码器和 WebRTC 仍按 60 FPS 工作。Mutter 只会在画面发生变化时
产生帧，这个值是刷新率上限，并不代表静止画面也会持续产生相同帧率。

安装包会固定 RK3588 的 CPU 和内存频率，并将 GPU 限制在 900 MHz。这套配置
可以减少帧时间尖峰，同时避开 ROCK 5B 在所有频率拉满时可能出现的不稳定问题。
如果更看重功耗，可以关闭性能调优服务：

```bash
sudo systemctl disable --now rkwebscr-performance.service
```

服务中的四个 `RKWEBSCR_*_HZ` 环境变量用于按具体硬件校准频率。

## 状态检查与故障排查

常用命令：

```bash
systemctl --user status rkwebscr-headless.service rkwebscr.service
journalctl --user -u rkwebscr.service -f
systemctl --user restart rkwebscr.service
```

如果在 ADB root shell 中检查 `flange` 的用户服务，请使用：

```bash
runuser -u flange -- env XDG_RUNTIME_DIR=/run/user/1000 \
  systemctl --user status rkwebscr-headless.service rkwebscr.service
```

遇到 `Empty response` 时，先检查 8080 后端是否存在：

```bash
ss -ltnp | grep -E ':(80|8080)\b'
loginctl show-user flange -p Linger -p State -p RuntimePath
```

正常情况下，80 端口由系统级代理监听，`127.0.0.1:8080` 由 `rkwebscrd`
监听。如果只有 80 端口，通常是 `flange` 没有开启 linger、尚未运行
`rkwebscr-setup`，或者用户服务启动失败。

已经建立连接但画面全黑，不一定表示视频链路故障，也可能只是无头工作区内没有
任何窗口。可以在 `WAYLAND_DISPLAY=wayland-0` 上启动应用来区分这两种情况。
正常的编码器日志应当持续输出帧，并且 `dropped` 和 `failed` 都为零。

连接信息面板会显示浏览器端的帧间隔、丢帧和卡顿次数。如果只在 Wi-Fi 下周期性
出现 RTT 尖峰，可能是 NetworkManager 的无线节能造成的。低延迟优先时，可以
为当前连接关闭节能：

```bash
WIFI_CONNECTION="$(nmcli -g GENERAL.CONNECTION device show wlan0)"
sudo nmcli connection modify "$WIFI_CONNECTION" 802-11-wireless.powersave 2
sudo nmcli connection up "$WIFI_CONNECTION" ifname wlan0
```

重新启用连接时 Wi-Fi 会短暂中断。该设置会保存在对应的 NetworkManager 连接
配置中。

## 开发与部署

Git 仓库是唯一代码源。不要把单个文件直接复制到 `/opt`、`/usr` 或用户的
systemd 目录。每次更新设备都应按照以下流程进行：

```bash
# 在仓库中开发、检查并提交
make check

# 在 debian/changelog 顶部增加新版本，然后构建
make deb

# 只部署生成的安装包
adb push ../rkwebscr_版本号_arm64.deb /data/local/tmp/
adb shell 'apt install -y /data/local/tmp/rkwebscr_版本号_arm64.deb'

# 以 flange 身份重新加载并重启用户服务
adb shell 'runuser -u flange -- env XDG_RUNTIME_DIR=/run/user/1000 systemctl --user daemon-reload'
adb shell 'runuser -u flange -- env XDG_RUNTIME_DIR=/run/user/1000 systemctl --user restart rkwebscr-headless.service rkwebscr.service'
```

`rkwebscr-setup` 只需要在首次安装，或用户服务启用状态被清除后再次运行。构建前
应先提交对应版本，确保设备上安装的每个 DEB 都能追溯到 Git 提交。

## 许可证

项目采用 MIT 许可证，详见 [LICENSE](LICENSE)。

<p align="center">
  <img src="custom_components/nyxr_companion/brand/icon.png" width="160" alt="NyxR Companion">
</p>

<h1 align="center">NyxR Companion</h1>

<p align="center">把跑 Home Assistant 的那台机器变成 AirPlay 2 音箱的 CD 机和蓝牙接收器。</p>

<p align="center"><a href="README.md">English</a> | 简体中文</p>

NyxR Companion 由一个 Home Assistant 加载项和一个集成组成。加载项播放光驱里的音乐 CD，也接收手机、播放器的蓝牙音频（A2DP），经 Nyx Refrain 的命令行发送端 [nyxr](https://github.com/pStrikeZ/Nyx-Refrain) 推到 HomePod 等 AirPlay 2 音箱；集成在 Home Assistant 里控制这一切，用起来像一台无头 CD 机加蓝牙音箱。

> 当前版本：0.1.0，nyxr 来自 Nyx Refrain 0.9.5。主要在 Home Assistant OS（amd64，Proxmox 虚拟机）+ HomePod mini 上测过，欢迎反馈其他环境下的情况。

> **LLM Disclaimer**
>
> 本项目的全部代码 100% 由 AI 生成，维护者可能会保证项目的易用性，但无法保证代码质量。维护者也有可能使用 100% 复制粘贴的形式回答用户的问题，这是正常现象，还请知悉。

---

## 功能

- **CD 机**：像 CD 机一样直接读扇区，切歌约半秒出声；16 位 44.1 kHz 的音频逐位不变地送到音箱。曲名和封面先查 MusicBrainz，再用 CD-Text。全部循环、单曲循环、随机。
- **蓝牙接收器**：手机、播放器像配对音箱一样配对（不需要配对码）；设备一开始播放就自动切到蓝牙。曲目信息和播放 / 暂停 / 切歌走 AVRCP。
- **由接收端发起配对**：自己发起配对总是失败的设备（发射功率弱的老 iPod）可以从附近设备列表里选出来配对。
- **同一时间只推一路**：音源选择器在 CD 和蓝牙之间切换，CD 记住播放位置。
- **音箱上的控制**：在 HomePod 或它的 Home Assistant 卡片上播放 / 暂停 / 切歌，会转给 CD 机或蓝牙设备。
- **不动音箱的音量**：推流开始时保持音箱当前的音量。
- **自动设置**：加载项带着 API 令牌向 Home Assistant 报告自己，并列出网络上找到的 AirPlay 接收端。

## 需要

- **Home Assistant OS**（或 Supervised 安装），amd64 或 aarch64（比如树莓派 4 / 5）。
- **AirPlay 2 接收端**，要支持 realtime 音频，比如 HomePod / HomePod mini。
- 放 CD：Home Assistant OS 能看到的**光驱**（`/dev/sr0`；USB，或电脑上的 SATA）。
- 蓝牙：**蓝牙适配器**（经典蓝牙 BR/EDR），最好专用，见[蓝牙](#蓝牙)。

## 安装

1. **加载项**：Home Assistant 里打开 **设置 → 应用 → 应用商店**，右上角菜单（⋮）→ **仓库**，添加 `https://github.com/pStrikeZ/Nyx-Refrain-Companion`。安装 **NyxR Companion** 并启动。
2. **集成**：在 HACS 里把 `https://github.com/pStrikeZ/Nyx-Refrain-Companion` 添加为 *Integration* 类型的自定义仓库，下载 **NyxR Companion**。不用 HACS 的话，把 `custom_components/nyxr_companion` 拷到配置目录的 `custom_components` 里。重启 Home Assistant。
3. **设置**：**设置 → 设备与服务 → 已发现** 里会出现 NyxR Companion，点 **添加**。地址和令牌由加载项自动填好。
4. **选择音箱**：在设备的「配置」里用 **AirPlay 接收端** 选（网络上找到的接收端），或者在 **目标接收端** 里填 `IP` / `IP:PORT`。

放入 CD 后打开 **推流**；或者打开 **蓝牙配对**，用手机配对「NyxR Companion」。

## 实体

| 实体 | 作用 |
|---|---|
| 推流（开关） | 连接音箱并播放所选音源；关闭时停止，并记住 CD 的位置 |
| 音源（选择） | `cd` 或 `bluetooth`，同一时间只推一路 |
| 播放模式（选择） | 全部循环、单曲循环、随机（CD） |
| 弹出（按钮） | 停止并弹出光盘 |
| 状态（传感器） | 光驱和播放状态；属性里有光盘、曲目和错误信息 |
| 蓝牙配对（开关） | 打开 3 分钟配对窗口：手机可以配对，同时列出附近的设备。有设备配上后自动关闭 |
| 配对附近的设备（选择） | 配对窗口打开期间找到的设备；选一个即由接收端发起配对 |
| 蓝牙设备（传感器） | 当前连着的设备；属性里有已配对设备、编码、采样率和曲目 |
| 断开蓝牙设备（按钮） | 断开当前设备，配对保留 |
| 删除已配对的设备（选择） | 删除所选设备的配对 |

配置类实体：AirPlay 接收端、目标接收端、延迟档位（端到端 `low` ≈ 100 ms / `balanced` ≈ 140 ms / `stable` ≈ 240 ms）、读取方式、光驱转速、预读缓冲、蓝牙名称、蓝牙自动切换、蓝牙缓冲。

播放、暂停、切歌没有单独的实体：用音箱的媒体播放器，它的控制会转给 NyxR Companion。

## 蓝牙

- **整体关系**：Home Assistant OS 上，BlueZ 管着所有蓝牙适配器，系统自带的声音服务（PulseAudio）在每个适配器上接收 A2DP 音频。加载项直接和这两者打交道：经 BlueZ 配对设备，从声音服务录它们的音频。Home Assistant 自带的蓝牙集成是另一回事，它只把适配器用于低功耗蓝牙（BLE），加载项不需要它。宿主机上什么都不用改。
- **Home Assistant 自带的蓝牙集成**也会提议使用这个适配器，选 **忽略**：BlueZ 不适合跑 Home Assistant 的 BLE 设备（ESPHome 蓝牙代理做得更好），而且 BLE 扫描会和音乐共用射频。即使没有忽略，加载项也不会动它的 BLE 配对：只有能发送音频的设备才算加载项的。
- **音量**：手机的音量和音箱的音量是叠加的。会把音量告诉接收端的手机（大多数安卓手机）由接收端按它压低音频；其他设备自己先压低。把手机音量开大，在音箱上调音量。
- **设备那边删掉了配对**：Home Assistant OS 会拒绝它仍然认识的设备重新配对。打开 **蓝牙配对**，从设备上配对：第一次会失败，加载项看到设备连上又没解析出服务就断开，于是删掉旧的配对，第二次就能成功。
- 距离：蓝牙音频要信号好。用 USB 延长线把适配器从金属机箱里拉出来效果明显。

## CD

- 读取方式：**直读**（默认）直接读扇区；**直读 + 对齐** 把每块按上一块对齐，给读取位置不精确的光驱用；**cd-paranoia（overlap / full）** 校验每次读取，适合划伤的碟，但每次跳转后要 3–7 秒才出声。
- 光驱默认限速 4x（**光驱转速**）：更安静，而且部分超薄光驱全速起转时会从 SATA 链路掉线。
- 混合模式盘、CD-Extra 只播放音轨；数据盘和 DVD 拒绝播放。

## 在虚拟机里跑 Home Assistant OS

蓝牙适配器按 USB 设备直通。光驱要按原始 SCSI 设备直通，虚拟机才能读音轨：模拟出来的 CD-ROM 读不了。Proxmox VE 上先找到光驱对应的 SCSI generic 设备（`ls /sys/block/sr0/device/scsi_generic/`，比如 `sg2`），再在虚拟机配置（`/etc/pve/qemu-server/<vmid>.conf`）里加：

```
args: -device virtio-scsi-pci,id=cdscsi -blockdev driver=host_device,filename=/dev/sg2,node-name=cdpt -device scsi-generic,bus=cdscsi.0,drive=cdpt
```

网页界面不显示 `args`。要把虚拟机完全关机再启动才会生效。

## 已知限制

- 每次推流只连一个 AirPlay 接收端，不支持多房间。
- 蓝牙：SBC（以及宿主机声音服务支持的其他编码）；不能拖动进度；只有提供曲目信息的播放器才有 AVRCP 曲名。
- HTTP API（见 [API.zh-cn.md](API.zh-cn.md)）只监听 `127.0.0.1`、需要令牌，是给集成用的。

## 开发

```sh
python3 -m unittest discover -s tests
```

测试让集成连一个假的加载项（`tests/fake_server.py`，按 [API.zh-cn.md](API.zh-cn.md) 实现），Home Assistant 的模块用替身代替。想试加载项的改动：把 `nyxr_companion/` 拷到宿主机的 `/mnt/data/supervisor/apps/local/`，刷新应用商店后安装；镜像里的 nyxr 取自 Nyx Refrain 的 Release（Dockerfile 里的 `NYXR_VERSION`）。改了 `config.yaml` 里的权限要提高 `version`：Supervisor 只在更新时应用新权限。

---

## 许可证

[MIT](LICENSE) © 2026 pStrikeZ and contributors

图标是 Nyx Refrain 的图标，来自 [Microsoft Fluent Emoji](https://github.com/microsoft/fluentui-emoji)（MIT），见 [NOTICE](NOTICE) 和 [LICENSE-fluentui-emoji](LICENSE-fluentui-emoji)。

# NyxR Companion HTTP API

[English](API.md) | 简体中文

守护进程默认监听 `127.0.0.1:8765`（加载项选项 `api_listen`，或环境变量 `NYXR_COMPANION_LISTEN`）。所有请求和响应都是 JSON（UTF-8）。

## 认证

每个请求（包括 WebSocket）都要带 `Authorization: Bearer <令牌>`，否则返回 `401`。令牌在首次启动时随机生成，存在 `/data/api_token`（环境变量 `NYXR_COMPANION_TOKEN` 可指定）。作为 Home Assistant 加载项运行时，守护进程启动后经 Supervisor 发现（服务名 `nyxr_companion`）把 `host`、`port`、`token` 告诉 Home Assistant，集成据此自动设置；已有的同地址条目只更新令牌。

## 状态对象

```json
{
  "source": "cd",
  "drive": "ok",
  "disc": {
    "id": "MB disc id",
    "album": "专辑名",
    "artist": "专辑艺人",
    "cover_url": "https://coverartarchive.org/release/<mbid>/front-500",
    "tracks": [
      {"number": 1, "title": "曲名", "artist": "艺人", "length": 223.4}
    ]
  },
  "streaming": true,
  "playback": "playing",
  "track": 3,
  "position": 12.5,
  "mode": "repeat_all",
  "error": null,
  "bluetooth": {
    "available": true,
    "name": "NyxR Companion",
    "pairing": false,
    "scanning": false,
    "devices": [{"address": "00:11:22:33:44:55", "name": "iPod", "connected": true}],
    "nearby": [],
    "connected": "iPod",
    "playing": true,
    "codec": "SBC",
    "rate": 44100,
    "streaming": true,
    "playback": "playing",
    "track": {"number": 1, "title": "曲名", "artist": "艺人", "album": "专辑", "length": 223.4},
    "error": null
  },
  "airplay": {
    "available": true,
    "name": "NyxR Companion",
    "connected": "iPhone",
    "playing": false,
    "streaming": false,
    "playback": "paused",
    "track": {"number": 1, "title": "曲名", "artist": "艺人", "album": "专辑", "length": 215.0, "cover_url": "file:///run/nyxr-companion/airplay-cover-1.jpg"},
    "error": null
  },
  "receivers": [
    {"name": "Living Room", "address": "192.0.2.10:7000", "model": "AudioAccessory5,1"}
  ],
  "settings": {
    "read_mode": "raw",
    "read_speed": 4,
    "buffer_seconds": 10.0,
    "latency_profile": "balanced",
    "target": "192.0.2.10:7000",
    "bluetooth_name": "NyxR Companion",
    "bluetooth_auto_switch": true,
    "bluetooth_buffer_ms": 150,
    "airplay_name": "NyxR Companion",
    "airplay_auto_switch": true,
    "cd_auto_play": true
  }
}
```

| 字段 | 取值 |
|---|---|
| `source` | 推哪一路：`cd`（默认）、`bluetooth` 或 `airplay`。同一时间只推一路；`streaming`、`playback`、`error` 都是所选这一路的 |
| `drive` | `ok`（有碟）、`no_disc`、`tray_open`、`not_ready`（读盘中）、`not_audio`（没有音轨：数据盘、DVD，或目录连续 3 次读不出；拒绝播放）、`missing`（光驱不在，如 USB 掉线） |
| `disc` | 只在 `drive == "ok"` 且 TOC 已读出时非 `null`。只含音轨：混合模式盘、CD-Extra 的数据轨不列出、不播放，`number` 从 1 连续编号。元数据先取 MusicBrainz，查不到用光盘的 CD-Text（没有封面），都没有时 `album` / `artist` / `cover_url` 为 `null`，曲名为 `Track01`、`Track02`… |
| `streaming` | nyxr 是否正连着接收端 |
| `playback` | `stopped`、`playing`、`paused` |
| `track` | 当前曲目号（1 起），`stopped` 且没有选曲时为 `null` |
| `position` | 当前曲目内的秒数（浮点） |
| `mode` | `repeat_all`（全部循环，默认；放完最后一首回到第 1 首）、`repeat_one`（单曲循环）、`shuffle`（全部随机，放完重新洗牌继续） |
| `error` | 最近一次错误的说明字符串，没有则 `null` |
| `receivers` | 网络上找到的 AirPlay 接收端（`nyxr discover`，启动时和之后每 5 分钟找一次）：`{"name", "address", "model"}`，`model` 不知道时为 `null` |
| `bluetooth` | 蓝牙接收端，见下文。宿主机没有可用的 D-Bus、BlueZ 或适配器时 `available` 为 `false` |
| `airplay` | AirPlay 接收端，见下文。shairport-sync 启动不了时 `available` 为 `false` |
| `settings` | 可调设置，见下表 |

### 设置

设置保存在 `/data/settings.json`（加载项的数据目录），经 `POST /api/settings` 修改。

| 名称 | 取值 | 生效 |
|---|---|---|
| `read_mode` | `raw`（直读，默认）、`raw_aligned`（直读 + 块间对齐，给读取位置不精确的光驱用）、`paranoia_overlap`、`paranoia_full`（经 cd-paranoia 校验，切歌要等 3–7 秒） | 立即：从当前位置按新方式重读 |
| `read_speed` | 1–24 的整数（倍速）。部分超薄光驱全速起转时会从 SATA 链路掉线，所以默认 4x | 立即 |
| `buffer_seconds` | 2–60，预读缓冲秒数 | 立即 |
| `latency_profile` | `low`、`balanced`、`stable` | 下次打开推流 |
| `target` | 接收端 `IP` 或 `IP:PORT`；初始为 `null`，设置之前不能推流 | 下次打开推流 |
| `bluetooth_name` | 1–64 个字符，手机上看到的蓝牙名称，默认 `NyxR Companion` | 立即 |
| `bluetooth_auto_switch` | `true`（默认）/ `false`：已连接的设备开始播放时自动切到蓝牙并推流 | 立即 |
| `bluetooth_buffer_ms` | 20–2000 的整数，蓝牙音频的抖动缓冲（毫秒），默认 150 | 下次打开蓝牙推流 |
| `airplay_name` | 1–64 个字符，AirPlay 列表里显示的名称，默认 `NyxR Companion` | 立即：接收端用新名称重启，已连接的发送端会断开 |
| `airplay_auto_switch` | `true`（默认）/ `false`：发送端开始播放时自动切到 AirPlay 并推流 | 立即 |
| `cd_auto_play` | `true`（默认）/ `false`：放入光盘时切到 CD（结束蓝牙或 AirPlay 推流），从第一轨开始播放。加载项启动时已在光驱里的盘不会自动播放 | 立即 |

### 蓝牙

| 字段 | 取值 |
|---|---|
| `pairing` | 配对窗口是否打开（可被发现、接受新设备，同时扫描附近的设备）。打开 3 分钟，或有设备配上后自动关闭 |
| `scanning` | 配对窗口打开期间正在扫描附近的设备 |
| `nearby` | 扫描到的、能放音乐的未配对设备（电脑、手机、音频设备，经典蓝牙且有名字）：`{"address", "name"}`。只在配对窗口打开时有内容 |
| `devices` | 能发送音频（A2DP Source）的已配对设备；适配器上的其他配对（如 Home Assistant 的 BLE 设备）不列出、不处理 |
| `connected` | 当前连着的设备名，没有则 `null` |
| `playing` | 设备正在发送音频（BlueZ A2DP 传输的 `State` 为 `active`） |
| `codec`、`rate` | 当前连接的编码和采样率（如 `SBC`、`44100`），没有连接时为 `null` |
| `streaming` | 蓝牙这一路的 nyxr 是否连着接收端 |
| `playback` | 设备的 AVRCP 播放状态；设备不报时按是否在发音频推断 |
| `track` | AVRCP 曲目信息；设备不报曲名时 `title` 是设备名 |

### AirPlay

AirPlay 2 接收端（shairport-sync），iPhone、iPad、Mac 可以往这里推；收到的音频像 CD 一样经 nyxr 推到音箱。

| 字段 | 取值 |
|---|---|
| `connected` | 当前连着的发送端名称，没有则 `null` |
| `playing` | 发送端正在发送音频 |
| `streaming` | AirPlay 这一路的 nyxr 是否连着接收端 |
| `playback` | 有音频时 `playing`；发送端连着（或推流开着）但没有音频时 `paused`；否则 `stopped` |
| `track` | 发送端给的曲目信息和封面；没有曲名时 `title` 是发送端名称。`cover_url` 是加载项里的文件，给 nyxr 用 |

## 端点

| 方法 | 路径 | 请求体 | 说明 |
|---|---|---|---|
| GET | `/api/state` | — | 返回状态对象 |
| GET | `/api/ws` | — | WebSocket。连上立即收到一条状态对象，之后每次状态变化推送完整状态对象（文本帧，JSON）；`position` 播放中每 5 秒推一次。客户端不需要发任何消息 |
| POST | `/api/stream` | `{"on": true}` | 打开：连接接收端并开始播放（从当前曲目，没有则第 1 首）。关闭：停止播放并断开接收端，记住当前曲目和位置，再打开时从那里继续（弹出光盘后从头开始）。没有碟、或还没设置接收端（`target`）时打开返回 409 |
| POST | `/api/mode` | `{"mode": "shuffle"}` | 设置播放模式 |
| POST | `/api/eject` | `{}` | 断开推流并弹出光盘 |
| POST | `/api/play` | `{"track": 3}` | 播放指定曲目（会打开推流）；不带 `track` 时等同继续播放 |
| POST | `/api/pause` | `{}` | 暂停（推流保持） |
| POST | `/api/next` | `{}` | 下一首 |
| POST | `/api/previous` | `{}` | 上一首（播放超过 3 秒时回到本曲开头） |
| POST | `/api/settings` | `{"read_speed": 8, ...}` | 修改一项或多项设置；未知名称或取值不合法返回 400，全部不生效 |
| POST | `/api/receivers/refresh` | `{}` | 立即重新查找 AirPlay 接收端（约 3 秒），返回状态对象 |
| POST | `/api/source` | `{"source": "bluetooth"}` | 切换音源。切走的那一路停止推流（CD 记住曲目和位置）；切到蓝牙时设备正在发音频就开始推流 |
| POST | `/api/bluetooth/pairing` | `{"on": true}` | 打开 / 关闭配对窗口（Just Works，不需要配对码；配上的设备自动信任） |
| POST | `/api/bluetooth/pair` | `{"address": "AA:BB:CC:DD:EE:FF"}` | 由接收端主动和 `nearby` 里的设备配对（给自己发起配对总是失败的设备用，如发射功率弱的 iPod）；配上后自动信任、尝试连接，并关闭配对窗口。地址不在 `nearby` 里返回 400 |
| POST | `/api/bluetooth/disconnect` | `{}` | 断开当前连着的设备（配对保留） |
| POST | `/api/bluetooth/forget` | `{"address": "AA:BB:CC:DD:EE:FF"}` | 删除与该设备的配对；不带 `address` 时删除 `devices` 里的全部。地址不在 `devices` 里返回 400。设备那边删掉配对的情况不需要它：配对窗口打开期间，已配对设备连上后 10 秒内没解析出服务就断开，守护进程会自动删掉旧配对，之后就能重新配对 |

`/api/stream`、`/api/play`、`/api/pause`、`/api/next`、`/api/previous` 作用于当前音源。音源是蓝牙时：打开推流需要有设备连着（否则 409），播放 / 暂停 / 切歌经 AVRCP 转给设备，`track` 参数被忽略。蓝牙不可用时 `/api/bluetooth/*` 返回 409。音源是 AirPlay 时：打开推流需要有发送端连着（否则 409），发送端停止或断开 10 秒后推流结束；buffered AirPlay 2 没有从接收端控制发送端的通道，所以暂停 / 切歌不起作用，要在发送端上操作。

POST 成功返回 `200` 和最新状态对象；参数错误返回 `400`，当前状态不允许（如没有碟）返回 `409`，令牌不对返回 `401`，都返回 `{"error": "说明"}`。

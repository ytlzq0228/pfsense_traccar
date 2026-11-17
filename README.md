# pfSense Traccar GPS

将 USB GNSS 定位分别上报到 Traccar 和 APRS-IS，提供 pfSense 原生配置、状态及日志页面。当前实机验证平台为 **pfSense CE 2.9.0 / amd64 / FreeBSD 16 / Python 3.11**。

## 已实现

- CH340 直接 USB 读取：通过系统 libusb，不需要 GPSD、PyUSB 或额外内核驱动；自动发现唯一的 `1a86:7523` 设备。也支持系统 `/dev/cua*` 串口。
- RMC/GGA 定位组合，GSA/GSV 多星座卫星详情：本地 SVG 信号柱状图、天空方位图、星座及参与定位筛选、可展开的原始信号明细；图形按卫星合并多个频点并显示最高 C/N₀。严格 NMEA 校验和校验。拒绝无效、过期、弱质量及不前进的定位时间。
- 移动/停止独立间隔、速度回差、状态确认时间；首次定位和恢复定位立即采样。
- `deviceTemp` 设备温度（摄氏度，保留一位小数）：优先最高 CPU 核心温度，无可用 CPU 温度时取最高 ACPI 热区温度。独立线程每 10 秒读取，读取失败或数据超过 30 秒时省略字段；温度随位置入队，历史补传保留原值，状态页可查看当前温度和来源。
- APRS 图标选择器：普通表、备用表和覆盖字符预览；按图标名称/代码搜索，点击自动填写编码，手工修改同步预览。图标随包本地提供，选择不自动提交，保存并应用后生效。
- APRS-IS：独立启停、呼号/SSID、Passcode、服务器、图标与 UTF-8 备注；校验服务器 `verified` 登录响应后发送。复用 TCP 连接并自动重连，独立 SQLite 历史队列，实时优先、限速补传。
- GNSS UTC 时间戳；HTTP 发送时转换为毫秒精度 ISO 8601。补传保留原始采集时间，不依赖重启前的单调时钟。
- SQLite WAL + `synchronous=FULL` 持久队列。两通道位置先入库；Traccar 在 HTTP 2xx 后删除，APRS 在认证连接上完成 TCP 写入后删除。APRS 没有逐条位置回执；详见可靠性边界。
- 每次发送优先选择刚采集的新点，其余时隙补传历史。网络失败退避，最高约 60 秒；网络等待不阻塞 GPS 采集。
- 缓存超出点数或预算时淘汰最旧记录，状态页显示累计丢弃数及时间。磁盘低于保留空间时暂停新增记录。
- 原生 pfSense 菜单、配置备份、服务管理、后台守护和日志轮转。卸载保留配置、队列及日志，重装后继续运行。

不包含 APRS 消息收发、射频/TNC 接入、NTRIP/RTK 差分注入、群晖上传、OLED、GPIO 或蓝牙 IMU。

## 安装与使用

本次构建产物在 `dist/pfSense-pkg-Traccar-0.2.1.pkg`，仅在上述 pfSense 版本实测。将包复制到目标机器后执行：

```sh
pkg add /tmp/pfSense-pkg-Traccar-0.2.1.pkg
```

依赖为对应 pfSense 软件源中的 `python311` 和 `py311-sqlite3`。测试机原本已安装这两个依赖。新机器若缺少依赖，先从其 **pfSense 对应的软件源** 安装，勿混用普通 FreeBSD 软件源的二进制包。

安装后打开 **Services → Traccar GPS**：

- 系统配置：`/services_traccar.php`，包含总开关、GPS、定位质量/运动判定、缓存与日志。
- Traccar 配置：`/services_traccar.php?tab=traccar`，包含通道开关、地址/设备 ID、移动/停止采样与 HTTP 补传速率。
- APRS 配置：`/services_traccar.php?tab=aprs`，包含通道开关、台站参数、独立移动/停止间隔与发送限速。保存任意一页只更新该页字段；Passcode 不回显，留空保留已保存值。
- 状态与日志：**系统状态（Status）→ Traccar GPS**，或 `/services_traccar.php?tab=status`
- 也可在 **Status → Services** 启停 `traccar`。

新安装默认不启用、不内置上报地址或设备 ID。升级保留既有 Traccar 设置，APRS 默认关闭且不内置呼号或 Passcode。测试机使用用户授权的 `BI1FQO-SK` 和 APRS `BI1FQO-PF`；APRS 连接 `china.aprs2.net:14580`，按本次要求移动/停止均为 30 秒，补传最小间隔 5 秒。

主要默认值：

| 参数 | 默认值 |
| --- | --- |
| 数据源 / 波特率 | 自动 CH340 / 115200 |
| Traccar 移动 / 停止采样间隔 | 1 秒 / 120 秒 |
| 进入移动 / 进入停止速度 | ≥3 km/h / ≤2 km/h |
| 移动 / 停止确认时间 | 3 秒 / 15 秒 |
| 定位过期时间 | 5 秒 |
| 最少定位卫星 / 最大 HDOP | 4 / 5 |
| 缓存目录 | `/conf/traccar` |
| 每通道缓存点数 / 数据预算 | 100000 / 128 MiB |
| 磁盘保留空间 | 64 MiB |
| HTTP 超时 / 最大发送速率 | 3 秒 / 5 点每秒 |
| APRS 移动 / 停止采样间隔 | 30 秒 / 300 秒 |
| APRS 发送最小间隔 / 超时 | 5 秒 / 5 秒 |
| 每类日志 | 当前 2 MiB + 3 个轮转备份 |
| 位置日志 / 原始 NMEA 日志 | 开 / 关 |

`/var` 在测试机上是内存盘，因此队列不放在那里。自定义目录允许 `/conf` 或 `/mnt` 下的专用子目录，使用者需确保实际挂载是持久存储。修改目录不会自动迁移旧数据；旧队列留在原处，可切回补传。修改地址或设备 ID 后，历史点仍使用其采集时绑定的地址和标识。

## 可靠性边界

- Traccar 采用“至少一次发送”：服务器已经处理，但响应丢失或确认入库前断电，可能重复发送同一时间戳的点。客户端不能保证服务器端恰好写入一次。
- APRS-IS 没有逐条位置回执：完成 TCP 写入不等于服务器保存或其他站点收到，断线附近可能漏传或重传。状态显示“已写入连接”，不声称服务端确认成功。认证失败不发送位置且保留缓存，Passcode 不写入队列/状态/日志。历史报文绑定采集时的呼号和服务器，使用当前保存的 Passcode 登录；更换基础呼号后，旧记录可能因认证不匹配保留并告警。
- APRS 历史点使用原始 UTC 的 `DDHHMMz` 时间戳，并附完整 UTC 日期和秒以保留月份/年份信息；部分 APRS 客户端对长时间历史、中文或非数字 SSID 的显示支持不同。APRS 标准位置精度为分的小数点后两位；速度为 knots，高度为 feet，负高度省略。
- 两通道各自使用独立发送线程与数据库，单个通道网络失败不阻塞另一个。每个通道的实时优先与历史补传共用有超时的发送线程；慢请求期间新点仍持续入库，当前请求完成后优先处理新点。持续产生速率超过网络吞吐时会形成积压。
- 仅采集真实有效定位。无定位期间不新增位置，也不做惯性推算；已有历史点仍可按原始时间戳补传。卫星展示条目 15 秒无更新后移除，界面将最后位置明确标为历史状态。
- HDOP 是几何精度因子，不等于米制误差。本项目传 `hdop`，不伪造 `accuracy`；速度按 OsmAnd 默认单位 knots 发送。若服务端修改了 OsmAnd 速度单位，需保持一致。
- 缓存 MiB 为数据和每条固定开销的估算预算，索引、WAL、SQLite 已分配空闲页及日志另占空间。点数上限和磁盘保留空间共同限制增长。缓存预算与点数上限分别应用于两个队列；全部启用时，合计预算为配置值的两倍。队列和日志必须做好容量规划。
- 磁盘耗尽、损坏或底层存储不兑现同步写入时不能承诺零丢失。重启恢复已通过进程级故障验证；没有对测试防火墙执行整机重启或硬断电。
- 自动 CH340 模式只在唯一设备匹配时打开；多设备需要指定 `ugenBUS.ADDRESS`。指定地址可能在插拔后变化。当前读法支持芯片版本 ≥0x30，实机 CH340 已验证。
- 停止/移动及弱 GPS 行为通过数据回放验证，仍建议进行实际道路、隧道和长时间弱网测试。

## 本地文件与服务

```text
/usr/local/share/pftracker/          Python 模块、默认配置、rc 模板
/usr/local/pkg/traccar.xml          pfSense 包菜单及服务定义
/usr/local/pkg/traccar.inc          配置及包生命周期钩子
/usr/local/www/services_traccar.php 原生 Web 页面
/usr/local/etc/traccar.json         由 pfSense 配置生成的运行参数（0600）
/usr/local/etc/rc.d/traccar.sh       安装/配置同步时生成的服务脚本
/var/run/traccar-status.json        临时状态快照
/conf/traccar/queue.sqlite          Traccar 持久化队列
/conf/traccar/aprs.sqlite           APRS 持久化队列
/conf/traccar/service.log           轮转服务日志
/conf/traccar/positions.jsonl        轮转位置日志
/conf/traccar/nmea.log              可选原始语句日志
```

参数保存在 pfSense `config.xml` 的 `installedpackages/traccar/settings` 中，可随系统配置备份；编码仅用于存储，不是加密。轨迹数据库和日志不包含在 pfSense XML 配置备份中。

```sh
/usr/local/etc/rc.d/traccar.sh status
/usr/local/etc/rc.d/traccar.sh restart
pkg delete pfSense-pkg-Traccar
```

升级使用同平台构建的新包 `pkg add -f /tmp/新包.pkg`。这是本地安装包，尚未加入 Netgate 官方包仓库，系统跨版本升级后可能需要针对新版本重新构建并安装。

## 开发与测试

应用使用 Python 标准库，CH340 通过 `ctypes` 调用基础系统 libusb。无需 pip 安装运行依赖。PHP 页面复用 pfSense 登录、权限、CSRF 和原生表单框架。

```sh
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

在目标 pfSense 或匹配的 FreeBSD 构建环境执行：

```sh
python3.11 tools/build-package.py
```

`tools/render-web-test.php` 是 **仅 CLI 可运行** 的原生页面渲染测试，使用独立临时会话，不修改 Web 服务器鉴权配置。`system`、`traccar`、`aprs`、`status`、`status-json`、`invalid-save` 模式用于验证；`settings` 兼容跳转至系统配置。`save-current system|traccar|aprs` 会重新保存对应页面现有配置并重启服务。

故障集成测试只使用环回 HTTP / APRS-IS 接收器和人工 NMEA，不会把模拟坐标发到真实 Traccar 或公共 APRS-IS。参见 [部署验证记录](docs/VALIDATION.md)。

协议与平台参考：[APRS-IS 登录与传输](https://www.aprs-is.net/Connecting.aspx)、[APRS 登录响应](https://www.aprs-is.net/ServerDesign.aspx)、[APRS 位置报文规范](https://www.aprs.org/doc/APRS101.PDF)、[pfSense 包开发](https://docs.netgate.com/pfsense/en/latest/development/develop-packages.html)、[Traccar OsmAnd](https://www.traccar.org/osmand/)、[FreeBSD CH340 寄存器定义](https://github.com/freebsd/freebsd-src/blob/main/sys/dev/usb/serial/uchcom.c)。

## APRS 图标资源

本项目代码使用 MIT 许可；内置的第三方图标与名称索引按各自许可提供，不适用项目代码的 MIT 许可。图标来自 [OH7LZB / aprs.fi](https://github.com/hessu/aprs-symbols)，索引来自 [aprs-symbol-index](https://github.com/hessu/aprs-symbol-index)（CC BY-SA 4.0）。原始图片未修改，保留完整版权说明、上游版本和来源记录，见 `package/files/usr/local/www/traccar-symbols/SOURCES.txt` 与同目录的 `COPYRIGHT.md`、`index-README.md`；选择器页面亦提供来源链接。

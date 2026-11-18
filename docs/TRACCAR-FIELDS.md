# Traccar 上报字段（0.3.4）

采用 OsmAnd HTTP 表单协议。优先使用 Traccar Position 原生字段或通用属性键；RTK 没有通用原生枚举或完整质量字段，其余以自定义位置属性发送。

依据：[OsmAnd 官方协议](https://www.traccar.org/osmand/)、[官方解码器](https://github.com/traccar/traccar/blob/master/src/main/java/org/traccar/protocol/OsmAndProtocolDecoder.java)、[Position 模型](https://github.com/traccar/traccar/blob/master/src/main/java/org/traccar/model/Position.java)。当前上游将其他表单参数自动识别为数字、布尔或字符串，存入 `attributes`。本次不依赖 JSON 上传，也无需修改服务端协议配置。

## 原生字段和通用属性键

| 字段 | 含义 |
| --- | --- |
| `timestamp` | 实际上报位置的 GNSS UTC 时间；发出时为 ISO 8601 毫秒格式 |
| `lat` / `lon` | 当前选用的真实位置 |
| `valid` | 当前上报定位有效；无有效定位不新增位置 |
| `accuracy` | 只在采用软件 RTK 时填写，单位 m，等于 `sqrt(σN²+σE²)`；不是实测误差或 95% 误差半径 |
| `status` | Traccar 标准属性键；本项目自定义枚举 `FIX` / `FLOAT` / `GGA`，表示实际采用的定位，不声称这是 Traccar 内置 RTK 枚举 |
| `sat` | 当前选用定位的参与卫星数 |
| `satVisible` | 接收机 GSV 可见卫星去重数，不重复计算同一星座/PRN 的多信号；未知时省略 |
| `hdop` | 同时刻接收机 GGA 的 HDOP；不是米制精度，不由标准差换算 |
| `speed` / `bearing` / `altitude` | 保留现有速度、航向和高度语义；未知项省略 |
| `motion` | 当前运动判定 |
| `deviceTemp` | 设备温度，°C；未知或过期时省略 |

GGA 回退时省略 `accuracy`，不能拿被拒绝或过期的 RTK 标准差当作 GGA 精度。服务端 Position 的默认 accuracy 可能显示 0，这不代表 GGA 零误差。

`status` 位于每条位置的 `attributes` 中，不改变 Traccar 设备本身的在线/离线状态。

## 自定义定位与 RTK 诊断

| 字段 | 类型 / 单位 | 含义 |
| --- | --- | --- |
| `positionSource` | 字符串 | `rtk_fix` / `rtk_float` / `gga`，保留旧字段兼容性 |
| `fixQuality` | 数字 | 当前位置的 NMEA 质量码；软件 FIX=4、FLOAT=5，GGA 保留接收机质量码 |
| `rtkEnabled` | 布尔 | 是否配置启用软件 RTK |
| `rtkUsed` | 布尔 | 此位置是否实际采用软件 RTK；判断是否使用 RTK 应优先看它或 `positionSource` |
| `rtkState` | 字符串 | 原生求解器状态，如 `fix`、`float`、`no_rtk`、`waiting_solution`、`error`、`disabled` |
| `rtkQuality` | 数字 | 最近原生解的 RTKLIB Q 码，如 1=FIX、2=FLOAT、5=SINGLE；未知时省略 |
| `rtkSigmaH` / `rtkSigmaV` | m | 最近原生解的水平合成标准差、垂直标准差；可以是被拒绝的结果 |
| `rtkSat` | 数字 | 最近原生解的卫星数 |
| `rtkRatio` | 数字 | 最近原生解的模糊度比值；不是精度或概率 |
| `rtkAge` | s | 最近原生解报告的基站差分龄期 |
| `rtkSolutionAge` | s | 距离上一次前进的有效解算历元被接收的时间 |
| `rtkSolutionFresh` | 布尔 | 解算更新年龄是否在配置的 `fix_timeout` 内；不等于通过全部质量门槛 |
| `rtkTimestamp` | UTC 字符串 | 最近原生解的 GNSS 历元；可能落后于当前 GGA |
| `rtkGgaTimeOffset` | s | 原生 RTK 历元减原始 GGA 历元；负值表示 RTK 落后，用于排查配对失败 |
| `rtkMsmAge` | s | 本机最近 MSM 观测的接收年龄 |
| `rtkFallbackReason` | 字符串 | 未采用软件 RTK 的原因；无回退原因为 `none` |
| `ggaValid` | 布尔 | 原始接收机 GGA/RMC 定位在采样时是否有效 |
| `ggaAge` | s | 原始接收机定位在采样时的更新年龄 |
| `ntripState` | 字符串 | 差分连接状态，如 `streaming`、`waiting_gga`、`retry`、`disabled` |
| `ntripAge` | s | 最近有效差分 RTCM 帧的接收年龄，与原生解的 `rtkAge` 分开 |
| `rtcmFrames` / `rtcmCrcErrors` | 数字 | 当前 USB 连接以来，本机 RTCM 有效帧数和 CRC 错误数；重连时可能重置 |

回退 GGA 时仍上报可用的原生诊断值，例如 `status=GGA`、`rtkUsed=false`、`rtkState=fix`、`rtkSolutionFresh=false` 可以同时成立，表示“最近求解器曾报告 FIX，但结果已过期，当前采用 GGA”。不能只用 `rtkState` 或 `rtkSigmaH` 判断当前定位来源。

## 一致性和补传

- 每个采样点使用与定位选择相同的 RTK 状态快照，避免选点和诊断字段分别读取不同历元。
- 所有质量、年龄、布尔和连接状态在采样入队时冻结；补传不会替换为发送时的新状态，也不会修改旧队列已有字段。
- 因此接收到历史点时，`rtkSolutionFresh=true` 只代表当时新鲜，需结合该点原始 `timestamp` 判断时间。
- 未知值省略，不发送 `None`、NaN 或 Infinity；没有 RTK 时不伪造精度、ratio 或质量码。
- 仅白名单字段上报，不包含 NTRIP 账号、密码、服务器反射错误或配置内容。APRS 报文格式保持原样。

## 验证

原有 56 项测试及新增字段测试覆盖：FIX/FLOAT/GGA 语义、原生 accuracy 仅用于实际 RTK 位置、过期和未知字段、可见卫星去重、凭据不泄露、HTTP 表单编码、同一状态快照和队列重开后仍保留原始属性。人工数据只发往环回测试服务器；实机验证仅使用真实定位上报。

测试机自然采样的 FLOAT 点已包含 `status=FLOAT`、`accuracy=1.689`、`rtkSigmaV=2.639`、`rtkQuality=2`、`rtkSat=7`、`rtkAge=1.0`、`rtkRatio=1.0` 和其余可用诊断，HTTP 返回 200、队列清空；启动时 GGA 点省略 accuracy。此验证证明客户端报文与接收响应正常，未使用服务端管理员 API 核实数据库属性或自定义服务端过滤规则。

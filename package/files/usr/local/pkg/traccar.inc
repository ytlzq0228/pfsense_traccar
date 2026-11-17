<?php
/* pfSense Traccar package integration. */
require_once('config.inc');
require_once('util.inc');

function traccar_defaults() {
    return json_decode(file_get_contents('/usr/local/share/pftracker/defaults.json'), true);
}
function traccar_config() {
    $saved = json_decode(base64_decode(config_get_path('installedpackages/traccar/settings', '')), true);
    return array_merge(traccar_defaults(), is_array($saved) ? $saved : []);
}
function traccar_write_runtime($settings) {
    $path = '/usr/local/etc/traccar.json';
    $tmp = tempnam('/usr/local/etc', '.traccar-');
    if ($tmp === false) { throw new RuntimeException('Cannot create runtime configuration'); }
    chmod($tmp, 0600);
    if (file_put_contents($tmp, json_encode($settings, JSON_PRETTY_PRINT | JSON_UNESCAPED_SLASHES)) === false) {
        @unlink($tmp);
        throw new RuntimeException('Cannot write runtime configuration');
    }
    $output = []; $code = 0;
    exec('/usr/local/libexec/traccar --validate --config ' . escapeshellarg($tmp) . ' 2>&1', $output, $code);
    if ($code !== 0) {
        unlink($tmp);
        throw new RuntimeException(implode("\n", $output));
    }
    if (!rename($tmp, $path)) { @unlink($tmp); throw new RuntimeException('Cannot install configuration'); }
}
function traccar_sync() {
    traccar_write_runtime(traccar_config());
    // pfSense removes registered rcfiles itself during uninstall. Generate this file
    // from a packaged template so it is not also owned by pkg's file manifest.
    if (!copy('/usr/local/share/pftracker/traccar.rc', '/usr/local/etc/rc.d/traccar.sh')) {
        throw new RuntimeException('Cannot install service script');
    }
    chmod('/usr/local/etc/rc.d/traccar.sh', 0755);
    mwexec('/usr/local/etc/rc.d/traccar.sh restart');
}
function traccar_install() { traccar_sync(); }
function traccar_uninstall() {
    if (file_exists('/usr/local/etc/rc.d/traccar.sh')) { mwexec('/usr/local/etc/rc.d/traccar.sh stop'); }
    @unlink('/usr/local/etc/traccar.json');
    @unlink('/var/run/traccar-status.json');
    /* Keep queue and logs for recovery / reinstallation. */
}
function traccar_running() {
    $output=[]; $code=1;
    exec('/usr/local/etc/rc.d/traccar.sh status 2>/dev/null', $output, $code);
    return $code === 0;
}
function traccar_status() {
    $s = json_decode(@file_get_contents('/var/run/traccar-status.json'), true) ?: [];
    $s['running'] = traccar_running();
    $s['stale'] = empty($s['updated']) || time() - $s['updated'] > 10;
    return $s;
}
function traccar_fields($tab = 'system') {
    $fields = [
        '基本设置' => [
            'enabled' => ['启用服务', 'checkbox', '总开关：开机启动 GPS 采集；各上报通道在各自配置页单独启用。'],
            'server_url' => ['Traccar 上报 URL', 'text', 'OsmAnd HTTP(S) 接收地址；历史缓存保留采集时的地址和设备 ID。'],
            'device_id' => ['设备 ID', 'text', '必须与 Traccar 中的设备唯一标识一致。'],
            'source' => ['GPS 读取方式', 'select', 'CH340 直接 USB 无需串口驱动；普通串口适用于已有 /dev/cua* 节点的设备。', ['ch340'=>'CH340 直接 USB', 'serial'=>'系统串口']],
            'usb_selector' => ['USB 设备选择', 'text', '留空自动寻找唯一 CH340（推荐，支持重新枚举）；多设备时填写 ugen0.2 等地址。'],
            'serial_path' => ['串口路径', 'text', '仅系统串口模式使用，例如 /dev/cuaU0。'],
            'baud' => ['波特率', 'select', '需与 GPS 模块输出一致。', array_combine([4800,9600,19200,38400,57600,115200,230400],[4800,9600,19200,38400,57600,115200,230400])],
        ],
        '定位与采样' => [
            'moving_interval' => ['移动间隔（秒）','number','默认 1；按 GPS 实际输出频率采样，不重复制造轨迹点。'],
            'stopped_interval' => ['停止间隔（秒）','number','默认 120；首次定位和恢复定位立即采样。'],
            'speed_threshold' => ['移动速度阈值（km/h）','number','达到此速度并持续满足移动确认时间后进入移动状态。'],
            'speed_hysteresis' => ['速度回差（km/h）','number','停止阈值 = 移动阈值 − 回差，用于减少状态抖动。'],
            'move_delay' => ['移动确认时间（秒）','number','默认 3。'],
            'stop_delay' => ['停止确认时间（秒）','number','默认 15。'],
            'fix_timeout' => ['定位过期时间（秒）','number','无新有效位置超过此时间后停止采样。'],
            'min_sat' => ['最少参与定位卫星数','number','默认 4；低于此值不采集轨迹。'],
            'max_hdop' => ['最大 HDOP','number','默认 5；HDOP 缺失、为零或超限时不采集轨迹。'],
        ],
        '缓存与网络' => [
            'queue_dir' => ['持久化数据目录','text','默认 /conf/traccar；仅允许 /conf 或 /mnt 子目录。不要使用内存盘。改目录后旧缓存留在原目录，可切回继续补传。'],
            'max_points' => ['每通道最大缓存点数','number','默认 100000；超限淘汰最旧未上传记录，并在状态页累计告警。'],
            'queue_mb' => ['每通道缓存预算（MiB）','number','Traccar 与 APRS 各自独立使用此预算；数据库索引、WAL 和日志另占空间。'],
            'reserve_mb' => ['磁盘保留空间（MiB）','number','默认 64；低于此值暂停新增轨迹，继续尝试补传并告警。'],
            'http_timeout' => ['单次请求超时（秒）','number','默认 3；失败重试等待逐步增加，最高约 60 秒。'],
            'upload_rate' => ['最大上传速率（点/秒）','number','实时新点优先，其余时隙补传历史；默认 5，实际速率受网络响应时间限制。'],
        ],
        '本地日志' => [
            'log_positions' => ['记录位置日志','checkbox','将两个通道实际入队的位置记录到 positions.jsonl；服务日志同时记录 APRS 已写入连接的报文。'],
            'log_nmea' => ['记录原始 NMEA','checkbox','调试使用；仅记录通过校验的语句，持续运行会增加磁盘写入。'],
            'log_mb' => ['每个日志文件上限（MiB）','number','服务日志、位置日志、原始 NMEA 各自独立轮转。'],
            'log_backups' => ['每类日志保留备份数','number','默认 3；不包括当前文件。'],
        ],
    ];
    if ($tab === 'traccar') {
        return [
            'Traccar 上报' => [
                'traccar_enabled' => ['启用 Traccar','checkbox','关闭后暂停此通道的采集和上传，保留未上传缓存。'],
                'server_url' => $fields['基本设置']['server_url'],
                'device_id' => $fields['基本设置']['device_id'],
            ],
            '采样与补传' => [
                'moving_interval' => $fields['定位与采样']['moving_interval'],
                'stopped_interval' => $fields['定位与采样']['stopped_interval'],
                'http_timeout' => $fields['缓存与网络']['http_timeout'],
                'upload_rate' => $fields['缓存与网络']['upload_rate'],
            ],
        ];
    }
    if ($tab === 'aprs') {
        return [
            'APRS-IS 连接' => [
                'aprs_enabled' => ['启用 APRS','checkbox','通过 APRS-IS 网络上报；关闭后保留未发送缓存。'],
                'aprs_host' => ['服务器','text','仅填写域名或 IPv4 地址，不含端口。'],
                'aprs_port' => ['TCP 端口','number','通常为 14580（过滤端口）。'],
                'aprs_callsign' => ['呼号及 SSID','text','例如 BI1FQO-PF；登录和位置报文使用同一标识，最多 9 个大写字母、数字和连字符。'],
                'aprs_passcode' => ['Passcode','password','留空保留已保存的值；首次启用时必填。不会回显到页面或日志。'],
                'aprs_timeout' => ['连接/登录超时（秒）','number','失败自动重连，认证失败等待 60 秒再试。'],
            ],
            '台站显示' => [
                'aprs_symbol_table' => ['图标表 / 覆盖字符','text','可从下方预览表选择自动填写；普通表 /，备用表反斜杠，覆盖字符 0–9、A–Z。'],
                'aprs_symbol' => ['图标代码','text','选择下方图标会自动生成代码；也可手工填写一个 ASCII 字符。'],
                'aprs_comment' => ['备注','text','支持中文，最多 160 个 UTF-8 字节；客户端的中文显示能力可能不同。'],
            ],
            '采样与历史补传' => [
                'aprs_moving_interval' => ['移动间隔（秒）','number','默认 30，最小 30。使用系统配置中的运动判定和定位质量要求。'],
                'aprs_stopped_interval' => ['停止间隔（秒）','number','默认 300，最小 30；恢复有效定位立即入队。'],
                'aprs_upload_interval' => ['发送最小间隔（秒）','number','默认 5；实时新点优先，其余时隙补传历史。报文保留原始 UTC 时间。'],
            ],
        ];
    }
    unset($fields['基本设置']['server_url'], $fields['基本设置']['device_id'],
          $fields['定位与采样']['moving_interval'], $fields['定位与采样']['stopped_interval'],
          $fields['缓存与网络']['http_timeout'], $fields['缓存与网络']['upload_rate']);
    return ['服务与 GPS' => $fields['基本设置'], '定位质量与运动判定' => $fields['定位与采样'],
            '持久缓存' => $fields['缓存与网络'], '本地日志' => $fields['本地日志']];
}

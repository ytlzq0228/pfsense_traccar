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
function traccar_fields() {
    return [
        '基本设置' => [
            'enabled' => ['启用服务', 'checkbox', '开机启动，持续采集并上报有效定位。'],
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
            'max_points' => ['最大缓存点数','number','默认 100000；超限淘汰最旧未上传记录，并在状态页累计告警。'],
            'queue_mb' => ['缓存预算（MiB）','number','按数据长度及每点固定开销估算；数据库索引、WAL 和日志另占空间。'],
            'reserve_mb' => ['磁盘保留空间（MiB）','number','默认 64；低于此值暂停新增轨迹，继续尝试补传并告警。'],
            'http_timeout' => ['单次请求超时（秒）','number','默认 3；失败重试等待逐步增加，最高约 60 秒。'],
            'upload_rate' => ['最大上传速率（点/秒）','number','实时新点优先，其余时隙补传历史；默认 5，实际速率受网络响应时间限制。'],
        ],
        '本地日志' => [
            'log_positions' => ['记录位置日志','checkbox','将实际入队位置记录到轮转的 positions.jsonl。'],
            'log_nmea' => ['记录原始 NMEA','checkbox','调试使用；仅记录通过校验的语句，持续运行会增加磁盘写入。'],
            'log_mb' => ['每个日志文件上限（MiB）','number','服务日志、位置日志、原始 NMEA 各自独立轮转。'],
            'log_backups' => ['每类日志保留备份数','number','默认 3；不包括当前文件。'],
        ],
    ];
}

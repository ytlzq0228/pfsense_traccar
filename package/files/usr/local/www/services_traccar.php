<?php
##|+PRIV
##|*IDENT=page-services-traccar
##|*NAME=Services: Traccar GPS
##|*DESCR=Configure and inspect the Traccar GPS tracker.
##|*MATCH=services_traccar.php*
##|-PRIV
require('guiconfig.inc');
require_once('/usr/local/pkg/traccar.inc');
require_once('classes/Form.class.php');

$tab = ($_GET['tab'] ?? '') === 'status' ? 'status' : 'settings';
$settings = traccar_config();
if (($_GET['action'] ?? '') === 'status') {
    header('Content-Type: application/json; charset=utf-8');
    header('Cache-Control: no-store');
    echo json_encode(traccar_status());
    exit;
}
if (($_GET['action'] ?? '') === 'log') {
    $allowed = ['service'=>'service.log','positions'=>'positions.jsonl','nmea'=>'nmea.log'];
    $kind = $_GET['kind'] ?? 'service';
    if (!isset($allowed[$kind])) { http_response_code(400); exit; }
    header('Content-Type: text/plain; charset=utf-8');
    header('Cache-Control: no-store');
    $file = rtrim($settings['queue_dir'], '/') . '/' . $allowed[$kind];
    $handle = @fopen($file, 'r');
    if ($handle) {
        $size = fstat($handle)['size'];
        if ($size > 65536) { fseek($handle, -65536, SEEK_END); fgets($handle); }
        echo stream_get_contents($handle); fclose($handle);
    } else { echo "No log available.\n"; }
    exit;
}
$input_errors = [];
if ($_SERVER['REQUEST_METHOD'] === 'POST') {
    if (isset($_POST['save'])) {
        $candidate = traccar_defaults();
        foreach ($candidate as $key=>$default) {
            if (is_bool($default)) { $candidate[$key] = isset($_POST[$key]); }
            elseif (is_int($default) || is_float($default)) {
                $raw = trim((string)($_POST[$key] ?? ''));
                if (!is_numeric($raw)) { $input_errors[] = "$key: 必须填写数字"; continue; }
                $integer_fields = ['baud','min_sat','max_points','queue_mb','reserve_mb','log_mb','log_backups'];
                if (in_array($key,$integer_fields,true) && (float)$raw != (int)$raw) { $input_errors[] = "$key: 必须填写整数"; }
                $candidate[$key] = in_array($key,$integer_fields,true) ? (int)$raw : (float)$raw;
            } else { $candidate[$key] = trim((string)($_POST[$key] ?? '')); }
        }
        if (!$input_errors) {
            try {
                traccar_write_runtime($candidate);
                config_set_path('installedpackages/traccar/settings', base64_encode(json_encode($candidate)));
                write_config('Traccar: updated configuration');
                $rc = mwexec('/usr/local/etc/rc.d/traccar.sh restart');
                if ($rc !== 0) { $input_errors[] = '配置已保存，但服务重启失败；请检查状态和日志。'; }
                else { $savemsg = '配置已保存并应用。'; }
            } catch (Throwable $e) { $input_errors[] = $e->getMessage(); }
        }
        $settings = $candidate;
    } elseif (in_array($_POST['control'] ?? '', ['start','stop','restart'], true)) {
        $action = $_POST['control'];
        $rc = mwexec('/usr/local/etc/rc.d/traccar.sh ' . $action);
        if ($rc !== 0) { $input_errors[] = '服务操作失败，请检查日志。'; }
        else { $savemsg = '服务操作已执行。停止仅作用于当前运行；永久停用请取消启用并保存。'; }
        $tab = 'status';
    }
}
$pgtitle = [$tab === 'status' ? 'Status' : 'Services', 'Traccar GPS'];
// head.inc builds its own help URL; replace only this page's generated links.
ob_start();
include('head.inc');
$page_header = ob_get_clean();
echo str_replace('href="' . $helpurl . '"',
    'href="https://github.com/ytlzq0228/pfsense_traccar"', $page_header);
if ($input_errors) { print_input_errors($input_errors); }
if (isset($savemsg)) { print_info_box(htmlspecialchars($savemsg), 'success'); }
// pfSense tab tuple: label, active flag, destination URL.
$tabs = [['配置',$tab==='settings','services_traccar.php'],['状态与日志',$tab==='status','services_traccar.php?tab=status']];
display_top_tabs($tabs);
if ($tab === 'settings') {
    $form = new Form('保存并应用');
    foreach (traccar_fields() as $title=>$fields) {
        $section = new Form_Section($title);
        foreach ($fields as $key=>$spec) {
            [$label,$type,$help] = $spec;
            if ($type === 'checkbox') { $field = new Form_Checkbox($key,$label,$help,!empty($settings[$key])); }
            elseif ($type === 'select') { $field = new Form_Select($key,$label,$settings[$key],$spec[3]); $field->setHelp($help); }
            else { $field = new Form_Input($key,$label,$type,$settings[$key]); $field->setHelp($help); if ($type==='number') { $field->setAttribute('step','any'); } }
            $section->addInput($field);
        }
        $form->add($section);
    }
    print($form);
} else {
?>
<div class="panel panel-default"><div class="panel-heading"><h2 class="panel-title">卫星详情</h2></div>
<div class="panel-body">
<style>
.satellite-controls {display:flex;flex-wrap:wrap;gap:12px 24px;align-items:center;margin:12px 0}
.satellite-controls label {margin:0;font-weight:normal}
.satellite-controls select {display:inline-block;width:auto;margin-left:8px}
.satellite-legend-item {display:inline-flex;align-items:center;margin:0 16px 8px 0;font-size:12px}
.satellite-legend-dot {display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:6px}
.satellite-charts {display:flex;flex-wrap:wrap;gap:20px;margin-top:12px}
.satellite-signal-chart {flex:2 1 520px;min-width:0}
.satellite-sky-chart {flex:1 1 320px;min-width:0}
.satellite-chart-scroll {overflow-x:auto;background:#fff;border:1px solid #dce2e8;border-radius:4px}
#satellite-bars {display:block;min-width:100%;height:270px}
#satellite-sky {display:block;width:100%;max-width:380px;margin:auto;background:#fff;border:1px solid #dce2e8;border-radius:4px}
#satellite-bars g:focus,#satellite-sky g:focus {outline:2px solid #526575;outline-offset:2px}
#satellite-detail {min-height:24px;margin-top:12px}
.satellite-charts h3 {font-size:15px;margin-top:0}
.satellite-table {margin-top:14px}
.satellite-table summary {cursor:pointer;font-weight:600;margin-bottom:10px}
</style>
<p id="satellite-summary" aria-live="polite">正在读取卫星数据…</p>
<div class="satellite-controls">
<label for="satellite-system">星座<select id="satellite-system" class="form-control">
<option value="all">全部星座</option><option value="GPS">GPS</option><option value="GLONASS">GLONASS</option>
<option value="Galileo">Galileo</option><option value="BeiDou">北斗</option><option value="QZSS">QZSS</option><option value="NavIC">NavIC</option>
</select></label>
<label><input type="checkbox" id="satellite-used"> 只看参与定位的卫星</label>
</div>
<div id="satellite-legend"></div>
<p class="text-muted">实心表示参与定位，空心表示未参与。图形按卫星合并多个信号，显示最高 C/N₀；“—”表示未报告信号强度。参与标记来自接收机，当前定位是否有效请看下方运行状态。</p>
<div class="satellite-charts">
<section class="satellite-signal-chart"><h3>信号强度 · C/N₀</h3>
<div class="satellite-chart-scroll" tabindex="0" role="region" aria-label="卫星信号强度图，可横向滚动">
<svg id="satellite-bars" role="img" aria-label="卫星信号强度柱状图"></svg></div>
<p class="text-muted">数值越高信号越强；卫星较多时可横向滚动。</p></section>
<section class="satellite-sky-chart"><h3>天空方位图</h3>
<svg id="satellite-sky" viewBox="0 0 360 360" role="img" aria-label="卫星天空方位图，北方在上"></svg>
<p id="satellite-sky-note" class="text-muted"></p></section>
</div>
<p id="satellite-detail" class="text-muted"></p>
<details class="satellite-table"><summary>查看全部信号明细</summary>
<p>明细保留每个信号条目，不受上方图形筛选影响。无更新的卫星在 15 秒后移除。</p>
<div class="table-responsive"><table class="table table-striped table-condensed"><thead><tr><th>星座</th><th>PRN</th><th>信号</th><th>仰角</th><th>方位角</th><th>C/N₀</th><th>参与定位</th></tr></thead><tbody id="tracker-sats"></tbody></table></div>
</details></div></div>
<div class="panel panel-default"><div class="panel-heading"><h2 class="panel-title">运行状态 <small id="refresh-state">正在读取…</small></h2></div>
<div class="panel-body">
<div id="tracker-alert" class="alert alert-warning" style="display:none"></div>
<table class="table table-striped"><tbody id="tracker-status"></tbody></table>
<form method="post" action="services_traccar.php?tab=status">
<button class="btn btn-success" name="control" value="start">启动</button>
<button class="btn btn-primary" name="control" value="restart">重启</button>
<button class="btn btn-default" name="control" value="stop">停止</button>
</form>
</div></div>
<div class="panel panel-default"><div class="panel-heading"><h2 class="panel-title">本地日志（末尾 64 KiB）</h2></div><div class="panel-body">
<p><a href="services_traccar.php?action=log&amp;kind=service" target="_blank" rel="noopener">服务日志</a> ·
<a href="services_traccar.php?action=log&amp;kind=positions" target="_blank" rel="noopener">位置日志</a> ·
<a href="services_traccar.php?action=log&amp;kind=nmea" target="_blank" rel="noopener">NMEA 日志</a></p>
<p>历史记录收到成功响应后才删除。HTTP 响应丢失时可能重传同一时间戳的点。位置和日志保存在配置的数据目录中。</p>
</div></div>
<script src="/js/traccar-satellites.js?v=0.1.2"></script>
<script>
events.push(function() {
    TrackerSatellites.init();
    function date(v) { return v ? new Date(v * 1000).toLocaleString() : '—'; }
    function fmt(v) { return v === null || v === undefined ? '—' : String(v); }
    function row(parent, values) {
        const tr = document.createElement('tr');
        values.forEach(v => { const td = document.createElement('td'); td.textContent = fmt(v); tr.appendChild(td); });
        parent.appendChild(tr);
    }
    async function refresh() {
        try {
            const response = await fetch('services_traccar.php?action=status', {cache:'no-store'});
            if (!response.ok) throw new Error('HTTP ' + response.status);
            const s = await response.json(), g = s.gps || {}, f = g.fix || {}, q = s.queue || {}, n = s.network || {};
            const live = s.running && !s.stale;
            document.getElementById('refresh-state').textContent = '更新于 ' + date(s.updated);
            const body = document.getElementById('tracker-status'); body.replaceChildren();
            const values = [
                ['服务', s.running ? (s.stale ? '进程运行，状态已过期' : '运行中') : '已停止'],
                ['GPS',live ? g.reason : '当前不可用（下方为最后状态）'],['读取设备',s.device],
                ['定位有效',live && g.valid ? '是' : '否'],['定位年龄（秒）',g.fix_age == null ? '—' : g.fix_age.toFixed(1)],
                ['最后位置（纬度，经度）',f.lat == null ? '—' : f.lat.toFixed(7)+', '+f.lon.toFixed(7)],
                ['GPS UTC 时间',f.epoch ? new Date(f.epoch*1000).toISOString() : '—'],
                ['速度（km/h）',f.speed == null ? '—' : (f.speed*1.852).toFixed(2)],
                ['运动状态',live && g.valid ? (s.moving ? '移动' : '停止') : '未知'],
                ['高度（m） / 航向（°）',fmt(f.altitude)+' / '+fmt(f.bearing)],
                ['参与定位卫星 / HDOP',fmt(f.sat)+' / '+fmt(f.hdop)],
                ['NMEA 有效 / 错误语句',fmt(g.valid_lines)+' / '+fmt(g.bad_lines)],
                ['上传状态',fmt(n.state)+' · HTTP '+fmt(n.last_http)],['最近上传错误',n.last_error || '无'],
                ['待传点数 / 数据预算占用',fmt(q.count)+' / '+((q.payload_bytes||0)/1048576).toFixed(2)+' MiB'],
                ['最早 / 最新待传时间',date(q.oldest)+' / '+date(q.newest)],
                ['累计成功 / 失败 / 容量淘汰',fmt(q.sent)+' / '+fmt(q.failures)+' / '+fmt(q.dropped)],
                ['最后成功上报',date(q.last_success)],['最近容量淘汰',date(q.last_drop)],
                ['设备温度',live && s.device_temp != null ? s.device_temp.toFixed(1)+' °C（'+fmt(s.device_temp_source)+'）' : '不可用'],
                ['数据盘可用空间',((s.free_bytes||0)/1048576).toFixed(1)+' MiB'],['版本',s.version]
            ];
            values.forEach(v=>row(body,v));
            const warnings=[];
            if (s.storage_error) warnings.push(s.storage_error);
            if (q.dropped) warnings.push('缓存达到上限，累计淘汰最旧未上传点 '+q.dropped+' 个。');
            if (!live) warnings.push('服务未运行或状态已过期。');
            const alert=document.getElementById('tracker-alert'); alert.textContent=warnings.join(' '); alert.style.display=warnings.length ? '' : 'none';
            TrackerSatellites.render(g.satellites || [], live);
            const sats=document.getElementById('tracker-sats'); sats.replaceChildren();
            if (live) (g.satellites||[]).forEach(x=>row(sats,[x.system,x.prn,x.signal,x.elevation,x.azimuth,x.snr,x.used ? '是' : '否']));
        } catch(e) {
            document.getElementById('refresh-state').textContent = '读取失败，请检查登录状态或网络';
            TrackerSatellites.render([], false);
            document.getElementById('tracker-sats').replaceChildren();
        }
        setTimeout(refresh, 2000);
    }
    refresh();
});
</script>
<?php
}
include('foot.inc');

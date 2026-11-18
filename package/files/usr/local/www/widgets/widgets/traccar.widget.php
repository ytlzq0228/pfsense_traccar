<?php
/* Read-only Dashboard widget. Data uses the authenticated Traccar status API. */
require_once('guiconfig.inc');
?>
<link rel="stylesheet" href="/widgets/css/traccar.css?v=0.3.3.1">
<div id="traccar-mini" class="traccar-mini" data-state="offline">
<div class="traccar-mini-hero" role="status" aria-live="polite">
<strong id="traccar-mini-state">正在读取…</strong>
<span id="traccar-mini-solver">求解器：—</span>
</div>
<p id="traccar-mini-reason" class="traccar-mini-reason">等待定位数据</p>
<div class="traccar-mini-metrics">
<div><span>RTK 水平标准差</span><strong id="traccar-mini-sigma">—</strong></div>
<div><span>定位卫星</span><strong id="traccar-mini-sats">—</strong></div>
<div><span>差分接收年龄</span><strong id="traccar-mini-age">—</strong></div>
</div>
<p id="traccar-mini-position" class="traccar-mini-position">暂无实时坐标</p>
<p class="traccar-mini-channels"><span id="traccar-mini-ntrip">差分：等待</span><span id="traccar-mini-upload">上报：等待</span></p>
<div class="traccar-mini-footer"><small id="traccar-mini-updated">等待刷新</small>
<span><a href="/services_traccar.php?tab=status">详细状态</a> · <a href="/services_traccar.php?tab=rtk">RTK 设置</a></span></div>
</div>
<script src="/js/traccar-rtk.js?v=0.3.2"></script>

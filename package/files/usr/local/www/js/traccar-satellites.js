/* Local SVG satellite charts: no CDN or external requests. */
(function () {
    'use strict';
    const systems = {
        GPS: {label: 'GPS', prefix: 'G', color: '#2368b5'},
        GLONASS: {label: 'GLONASS', prefix: 'R', color: '#aa4b17'},
        Galileo: {label: 'Galileo', prefix: 'E', color: '#7950a1'},
        BeiDou: {label: '北斗', prefix: 'C', color: '#16806a'},
        QZSS: {label: 'QZSS', prefix: 'J', color: '#a32c68'},
        NavIC: {label: 'NavIC', prefix: 'I', color: '#746719'}
    };
    const svgNS = 'http://www.w3.org/2000/svg';
    let latest = [], available = false;
    const el = id => document.getElementById(id);
    const finite = v => typeof v === 'number' && Number.isFinite(v);
    const validAngle = s => finite(s.elevation) && s.elevation >= 0 && s.elevation <= 90 && finite(s.azimuth) && s.azimuth >= 0 && s.azimuth <= 360;
    const meta = s => systems[s.system] || {label: s.system, prefix: s.system, color: '#546372'};
    const label = s => meta(s).prefix + String(s.prn).padStart(2, '0');
    function node(tag, attributes, text) {
        const n = document.createElementNS(svgNS, tag);
        Object.entries(attributes || {}).forEach(([k,v]) => n.setAttribute(k, v));
        if (text !== undefined) n.textContent = text;
        return n;
    }
    function append(svg, tag, attributes, text) {
        const n = node(tag, attributes, text); svg.appendChild(n); return n;
    }
    function describe(s) {
        return `${meta(s).label} PRN ${s.prn} · C/N₀ ${s.snr === null ? '未报告' : s.snr + ' dB-Hz'} · 仰角 ${finite(s.elevation) ? s.elevation + '°' : '未知'} · 方位 ${finite(s.azimuth) ? s.azimuth + '°' : '未知'} · ${s.used ? '参与定位' : '未参与定位'} · ${s.signals} 个信号条目`;
    }
    function aggregate(raw) {
        const bySatellite = new Map();
        raw.forEach(s => {
            if (!s || typeof s.system !== 'string' || !Number.isInteger(s.prn) || s.prn <= 0) return;
            const key = s.system + ':' + s.prn;
            const snr = finite(s.snr) && s.snr >= 0 && s.snr <= 99 ? s.snr : null;
            const prev = bySatellite.get(key);
            if (!prev) bySatellite.set(key, {...s, snr, signals: 1, used: !!s.used});
            else {
                prev.signals++;
                prev.used = prev.used || !!s.used;
                if (snr !== null && (prev.snr === null || snr > prev.snr)) prev.snr = snr;
                if (!validAngle(prev) && validAngle(s)) {
                    prev.elevation = s.elevation; prev.azimuth = s.azimuth;
                }
            }
        });
        const order = Object.keys(systems);
        return [...bySatellite.values()].sort((a,b) => {
            const rank = s => order.includes(s) ? order.indexOf(s) : order.length;
            return rank(a.system)-rank(b.system) || a.system.localeCompare(b.system) || a.prn-b.prn;
        });
    }
    function tooltip(group, satellite) {
        const description = describe(satellite);
        group.setAttribute('tabindex', '0');
        group.setAttribute('role', 'img');
        group.setAttribute('aria-label', description);
        group.appendChild(node('title', {}, description));
        group.addEventListener('mouseenter', () => { el('satellite-detail').textContent = description; });
        group.addEventListener('focus', () => { el('satellite-detail').textContent = description; });
    }
    function bars(satellites) {
        const svg = el('satellite-bars'); svg.replaceChildren();
        const width = Math.max(600, satellites.length*34+60), height = 270;
        svg.setAttribute('viewBox', `0 0 ${width} ${height}`);
        svg.style.width = width + 'px';
        const left = 42, top = 28, bottom = 218, span = width-left-16;
        const max = Math.max(60, Math.ceil(Math.max(0,...satellites.map(s => s.snr || 0))/10)*10);
        for (let value=0; value<=max; value+=10) {
            const y = bottom-(bottom-top)*value/max;
            append(svg,'line',{x1:left,y1:y,x2:width-10,y2:y,stroke:'#d9dfe5'});
            append(svg,'text',{x:left-8,y:y+4,'text-anchor':'end',fill:'#536271','font-size':11},value);
        }
        append(svg,'text',{x:5,y:15,fill:'#536271','font-size':11},'dB-Hz');
        satellites.forEach((s,i) => {
            const step = span/satellites.length, x = left+step*(i+.5);
            const h = s.snr === null ? 0 : (bottom-top)*s.snr/max;
            const g = append(svg,'g',{}); tooltip(g,s);
            // Keep zero/unknown selectable without inventing a signal strength.
            append(g,'rect',{x:x-step/2+1,y:top,width:step-2,height:bottom-top+35,fill:'transparent'});
            if (h > 0) append(g,'rect',{x:x-10,y:bottom-h,width:20,height:h,rx:2,
                fill:s.used ? meta(s).color : '#fff',stroke:meta(s).color,'stroke-width':2});
            append(g,'text',{x,y:bottom-h-6,'text-anchor':'middle',fill:'#27384a','font-size':11},s.snr === null ? '—' : s.snr);
            append(g,'text',{x,y:bottom+18,'text-anchor':'middle',fill:'#27384a','font-size':11},label(s));
        });
        if (!satellites.length) append(svg,'text',{x:width/2,y:120,'text-anchor':'middle',fill:'#536271'},available ? '暂无符合筛选条件的卫星' : '当前卫星数据不可用');
    }
    function sky(satellites) {
        const svg = el('satellite-sky'); svg.replaceChildren();
        const cx = 180, cy = 180, radius = 140;
        [0,30,60].forEach(elevation => {
            const r = radius*(90-elevation)/90;
            append(svg,'circle',{cx,cy,r,fill:'none',stroke:'#ccd5df'});
            append(svg,'text',{x:cx+5,y:cy-r+13,fill:'#667787','font-size':10},elevation+'°');
        });
        for (let angle=0;angle<360;angle+=45) {
            const rad=angle*Math.PI/180;
            append(svg,'line',{x1:cx,y1:cy,x2:cx+radius*Math.sin(rad),y2:cy-radius*Math.cos(rad),stroke:'#e1e6ec'});
        }
        [['北 N',cx,20],['东 E',345,cy+4],['南 S',cx,349],['西 W',16,cy+4]].forEach(([text,x,y]) => append(svg,'text',{x,y,'text-anchor':'middle',fill:'#33485c','font-size':12},text));
        append(svg,'text',{x:cx+5,y:cy+13,fill:'#667787','font-size':10},'90°');
        const located = satellites.filter(validAngle);
        located.forEach(s => {
            const rad=s.azimuth*Math.PI/180, r=radius*(90-s.elevation)/90;
            const x=cx+r*Math.sin(rad), y=cy-r*Math.cos(rad), g=append(svg,'g',{});
            tooltip(g,s);
            append(g,'circle',{cx:x,cy:y,r:9,fill:s.used ? meta(s).color : '#fff',stroke:meta(s).color,'stroke-width':2});
            append(g,'text',{x:x+12,y:y+4,fill:meta(s).color,'font-size':11,'font-weight':600,
                'paint-order':'stroke',stroke:'#fff','stroke-width':3,'stroke-linejoin':'round'},label(s));
        });
        el('satellite-sky-note').textContent = !available ? '当前卫星数据不可用' :
            `中心为天顶，外圈为地平线；已绘制 ${located.length} 颗` + (located.length<satellites.length ? `，另有 ${satellites.length-located.length} 颗缺少有效角度。` : '。');
    }
    function draw() {
        const all = aggregate(latest), filter = el('satellite-system').value;
        const sats = all.filter(s => (filter==='all' || s.system===filter) && (!el('satellite-used').checked || s.used));
        el('satellite-summary').textContent = available ?
            `报告 ${all.length} 颗卫星 · 有信号 ${all.filter(s => s.snr > 0).length} 颗 · 参与定位 ${all.filter(s => s.used).length} 颗 · 当前展示 ${sats.length} 颗` :
            '卫星数据不可用；服务停止、状态过期或连接失败。';
        el('satellite-detail').textContent = '将鼠标移到图形上，或使用 Tab 键查看卫星详情。';
        bars(sats); sky(sats);
    }
    window.TrackerSatellites = {
        init() {
            const legend = el('satellite-legend');
            Object.values(systems).forEach(s => {
                const item = document.createElement('span'); item.className='satellite-legend-item';
                const dot = document.createElement('span'); dot.className='satellite-legend-dot'; dot.style.backgroundColor=s.color;
                item.append(dot, document.createTextNode(s.label+' ('+s.prefix+')')); legend.appendChild(item);
            });
            el('satellite-system').addEventListener('change',draw);
            el('satellite-used').addEventListener('change',draw);
            draw();
        },
        render(satellites, live) {
            available = !!live; latest = live && Array.isArray(satellites) ? satellites : []; draw();
        }
    };
})();

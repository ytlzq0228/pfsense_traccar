/* pfSense loads this script only when the native widget is on the Dashboard. */
(function () {
    'use strict';
    const labels={fix:'RTK FIX 固定解',float:'RTK FLOAT 浮动解',gga:'GGA 回退',invalid:'无有效定位',offline:'状态不可用',disabled:'GPS 定位 · RTK 未启用'};
    const finite=v=>typeof v==='number' && Number.isFinite(v);
    const el=id=>document.getElementById('traccar-mini-'+id);
    const text=(id,value)=>{if(el(id))el(id).textContent=value;};
    function render(s) {
        const container=document.getElementById('traccar-mini');if(!container)return;
        const m=TrackerRTK.model(s,Boolean(s&&s.running&&!s.stale));
        const f=m.g.fix||{}, network=(s||{}).network||{}, q=(s||{}).queue||{};
        container.dataset.state=m.state;
        text('state',labels[m.state]);
        text('solver',m.available ? '求解器：'+m.raw+(m.q.status&&!m.fresh?'（过期）':'') : '求解器：不可用');
        text('reason',m.detail);
        text('sigma',finite(m.q.sigma_horizontal) ? m.q.sigma_horizontal.toFixed(2)+' m' : '—');
        el('sigma').dataset.stale=m.fresh?'no':'yes';
        text('sats',m.available&&m.g.valid&&m.state!=='offline'&&finite(f.sat) ? f.sat+' 颗' : '—');
        text('age',m.available&&finite(m.n.correction_age) ? m.n.correction_age.toFixed(1)+' s' : '—');
        text('position',m.available&&m.g.valid&&m.state!=='offline'&&finite(f.lat)&&finite(f.lon) ? f.lat.toFixed(6)+', '+f.lon.toFixed(6) : '暂无有效实时坐标');
        const connected=m.available&&m.n.state==='streaming'&&finite(m.n.correction_age)&&m.n.correction_age<=m.limits.correction_age;
        text('ntrip',!m.available?'差分：不可用':m.r.state==='disabled'?'差分：未启用':'差分：'+(connected?'在线':'未就绪'));
        el('ntrip').dataset.ok=connected?'yes':'no';
        const uploaded=m.available&&network.state==='online'&&(!s.channels||s.channels.traccar!==false);
        text('upload',!m.available?'Traccar：不可用':s.channels&&s.channels.traccar===false?'Traccar：关闭':'Traccar：'+(uploaded?'在线':'等待 / 重试')+' · 待传 '+(q.count??'—'));
        el('upload').dataset.ok=uploaded?'yes':'no';
        text('updated',m.available&&finite(s.updated) ? '更新 '+new Date(s.updated*1000).toLocaleTimeString() : '未获得实时状态');
    }
    window.TrackerMini={render};
    events.push(function () {
        let pending=false;
        async function refresh() {
            const container=document.getElementById('traccar-mini');if(!container)return;
            if(pending)return;
            if(document.hidden || !container.getClientRects().length){setTimeout(refresh,2000);return;}
            pending=true;
            const controller=new AbortController(),timeout=setTimeout(()=>controller.abort(),5000);
            try {
                const response=await fetch('/services_traccar.php?action=status',{cache:'no-store',signal:controller.signal});
                if(!response.ok)throw new Error('HTTP '+response.status);
                render(await response.json());
            } catch(e) {
                render(null);
                text('reason','读取失败，请检查登录状态、页面权限或网络。');
            } finally {
                clearTimeout(timeout);pending=false;setTimeout(refresh,2000);
            }
        }
        refresh();
    });
})();

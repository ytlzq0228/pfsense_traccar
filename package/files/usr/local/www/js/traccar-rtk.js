/* Local RTK quality display. The acquisition service decides which fix is used. */
(function (root) {
    'use strict';
    const finite = v => typeof v === 'number' && Number.isFinite(v);
    const palette = {fix:'#157653',float:'#2563b8',gga:'#6b46a1',invalid:'#bd3c42',offline:'#79838e',disabled:'#79838e'};
    const labels = {fix:'RTK FIX 已采用',float:'RTK FLOAT 已采用',gga:'GGA 回退',invalid:'无有效定位',offline:'状态不可用',disabled:'RTK 未启用'};
    const reasons = {
        'waiting for RTK solution':'等待 RTK 解算', 'solver has no RTK FIX/FLOAT':'尚未获得 FIX / FLOAT',
        'RTK FLOAT disabled':'已配置为只采用 FIX', 'RTK input disconnected':'RTK 输入未就绪',
        'RTK solution or rover observations stale':'解算或本机观测已过期', 'RTK corrections stale':'差分已过期',
        'too few RTK satellites':'解算卫星不足', 'RTK uncertainty too large':'解算标准差超出门槛',
        'RTK ambiguity ratio below 3':'FIX 模糊度比值不足', 'RTK/GGA epochs do not match':'RTK 与 GGA 历元不匹配'
    };
    function model(s, available) {
        s=s||{};
        available=Boolean(available && s.running && !s.stale);
        const r=s.rtk||{}, g=s.gps||{}, q=r.solution||{}, n=r.ntrip||{}, l=r.limits||{};
        const limits={solution_age:l.solution_age??5,correction_age:l.correction_age??10,
            sigma_horizontal:l.sigma_horizontal??3,sigma_up:l.sigma_up??6,satellites:l.satellites??4,ratio:l.ratio??3};
        const fresh=available && finite(r.solution_age) && r.solution_age<=limits.solution_age;
        let state='offline';
        if (available) {
            if (!g.valid) state='invalid';
            else if (r.state==='disabled') state='disabled';
            else if (g.rtk_used && !fresh) state='offline';
            else if (g.rtk_used && g.source==='rtk_fix') state='fix';
            else if (g.rtk_used && g.source==='rtk_float') state='float';
            else state='gga';
        }
        const raw=({1:'FIX',2:'FLOAT',4:'DGPS',5:'SINGLE',6:'PPP'})[q.status]||'等待解算';
        const detail=state==='offline' ? '服务停止、状态过期或连接中断；不显示为有效 RTK。' :
            state==='fix' ? '当前定位通过全部质量与时效检查。FIX 不代表已验证实际误差。' :
            state==='float' ? '浮点解已通过门槛，模糊度尚未固定。' :
            state==='disabled' ? '当前使用接收机 GGA 定位。' :
            (reasons[g.fallback_reason]||g.fallback_reason||g.reason||'等待有效定位');
        return {state,raw,fresh,detail,limits,r,q,n,g,available};
    }
    function historySummary(history) {
        let count=0,run=0,longest=0,previous=null;
        for (const h of history) {
            if (h.state==='fix') {
                count++;
                run=previous && previous.state==='fix' && h.at-previous.at<=6 ? run+h.at-previous.at : 0;
                longest=Math.max(longest,run);
            } else run=0;
            previous=h;
        }
        return {percent:history.length ? count/history.length*100 : 0,run,longest,
            span:history.length>1 ? history[history.length-1].at-history[0].at : 0,count:history.length};
    }
    let history=[],process=null;
    const el=id=>document.getElementById(id);
    const text=(id,value)=>{const n=el(id);if(n)n.textContent=value;};
    const duration=v=>v>=60 ? Math.floor(v/60)+'分'+Math.floor(v%60)+'秒' : Math.floor(v)+'秒';
    function metric(id,value,unit,limit,higher,usable,description) {
        const card=el('rtk-'+id); if(!card)return;
        const present=finite(value), ok=usable && present;
        text('rtk-'+id+'-value',present ? value.toFixed(id==='satellites'?0:2)+unit : '—');
        text('rtk-'+id+'-note',description);
        card.dataset.tone=!ok ? 'muted' : (higher ? value>=limit : value<=limit) ? 'good' : 'bad';
        const bar=card.querySelector('.rtk-meter-fill');
        const scale=higher ? limit*2 : limit*1.5;
        bar.style.width=present ? Math.max(0,Math.min(100,value/scale*100))+'%' : '0%';
        card.querySelector('.rtk-meter-limit').style.left=(higher?50:100/1.5)+'%';
    }
    function render(s,available) {
        if(!el('rtk-quality'))return;
        const m=model(s,available), now=Date.now()/1000;
        if(s && process!==null && process!==s.pid) history=[];
        if(s && s.pid) process=s.pid;
        history=history.filter(h=>h.at>=now-300);
        if(!history.length || now-history[history.length-1].at>=1) history.push({at:now,state:m.state});
        const hs=historySummary(history);
        el('rtk-quality').dataset.state=m.state;
        text('rtk-quality-title',labels[m.state]);
        text('rtk-quality-reason',m.detail);
        text('rtk-solver-badge',available ? '求解器：'+m.raw+(m.q.status&&!m.fresh?' · 已过期':'') : '求解器：不可用');
        text('rtk-quality-position',available && m.g.valid && m.state!=='offline' ? '当前输出：'+(m.state==='fix'?'RTK 固定解':m.state==='float'?'RTK 浮点解':'GGA 定位') : '当前输出：无可确认的实时定位');
        const age=finite(m.q.age)&&finite(m.n.correction_age) ? Math.max(Math.abs(m.q.age),m.n.correction_age) : null;
        metric('sigma',m.q.sigma_horizontal,' m',m.limits.sigma_horizontal,false,m.fresh,'水平标准差 ≤ '+m.limits.sigma_horizontal+' m；非实测误差');
        metric('vertical',m.q.sigma_up,' m',m.limits.sigma_up,false,m.fresh,'垂直标准差 ≤ '+m.limits.sigma_up+' m；非实测误差');
        metric('ratio',m.q.ratio,'',m.limits.ratio,true,m.fresh,'FIX 要求 ratio ≥ '+m.limits.ratio+'；FLOAT 不作固定判据');
        metric('correction',age,' s',m.limits.correction_age,false,available,'差分龄期 / 接收年龄取较大值，≤ '+m.limits.correction_age+' s');
        metric('satellites',m.q.sat,' 颗',m.limits.satellites,true,m.fresh,'参与 RTK 解算，至少 '+m.limits.satellites+' 颗');
        metric('solution',m.r.solution_age,' s',m.limits.solution_age,false,available,'最后前进的解算历元，≤ '+m.limits.solution_age+' s');
        const steps=[
            ['rover',available && finite(m.r.msm_age) && m.r.msm_age<=m.limits.solution_age,'本机 MSM'],
            ['ntrip',available && m.n.state==='streaming' && finite(m.n.correction_age) && m.n.correction_age<=m.limits.correction_age,'NTRIP 差分'],
            ['solver',m.fresh,'解算更新'],['accepted',m.state==='fix'||m.state==='float','RTK 采纳']
        ];
        steps.forEach(([id,ok,label])=>{const n=el('rtk-step-'+id);n.dataset.ready=ok?'yes':'no';n.textContent=label+' · '+(ok?'正常':'未就绪');});
        text('rtk-history-summary','本页最近 5 分钟 · 已观察 '+duration(hs.span)+' · FIX 样本占比 '+(hs.count<3?'积累中':hs.percent.toFixed(0)+'%')+' · 当前连续 FIX '+duration(hs.run)+' · 最长 '+duration(hs.longest));
        const svg=el('rtk-history');svg.replaceChildren();
        const ns='http://www.w3.org/2000/svg';
        for(let i=0;i<history.length;i++) {
            const h=history[i], next=history[i+1], x=Math.max(0,298-(now-h.at));
            const width=Math.max(.6,Math.min(6,next?next.at-h.at:2));
            const rect=document.createElementNS(ns,'rect');
            rect.setAttribute('x',x);rect.setAttribute('y',0);rect.setAttribute('width',width);rect.setAttribute('height',24);rect.setAttribute('fill',palette[h.state]);
            const title=document.createElementNS(ns,'title');title.textContent=new Date(h.at*1000).toLocaleTimeString()+' · '+labels[h.state];rect.appendChild(title);svg.appendChild(rect);
        }
    }
    const api={model,historySummary,render};
    root.TrackerRTK=api;
    if(typeof module!=='undefined' && module.exports)module.exports=api;
})(typeof window!=='undefined'?window:globalThis);

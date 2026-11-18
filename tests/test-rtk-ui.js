'use strict';
const assert=require('node:assert/strict');
const {model,historySummary}=require('../package/files/usr/local/www/js/traccar-rtk.js');
const base={running:true,stale:false,gps:{valid:true,source:'rtk_fix',rtk_used:true},
    rtk:{state:'fix',solution_age:.5,solution:{status:1,sigma_horizontal:.02,ratio:8,sat:9},
        limits:{solution_age:2,correction_age:7,sigma_horizontal:1,sigma_up:2,satellites:6,ratio:3},ntrip:{state:'streaming'}}};
assert.equal(model(base,true).state,'fix');
assert.equal(model(base,true).limits.sigma_horizontal,1);
assert.equal(model(base,true).limits.satellites,6);
const rejected={...base,gps:{valid:true,source:'gga',rtk_used:false,fallback_reason:'RTK uncertainty too large'}};
assert.equal(model(rejected,true).state,'gga','raw FIX is not an accepted FIX');
assert.equal(model(rejected,true).raw,'FIX');
assert.match(model(rejected,true).detail,/标准差/);
assert.equal(model({...base,stale:true},true).state,'offline');
assert.equal(model({...base,running:false},true).state,'offline');
assert.equal(model(base,false).state,'offline');
assert.equal(model(null,false).state,'offline');
assert.equal(model({...base,rtk:{...base.rtk,solution_age:3}},true).state,'offline','stale solver must not remain green');
assert.equal(model({...base,gps:{...base.gps,valid:false}},true).state,'invalid');
assert.equal(model({...base,gps:{valid:true,source:'gga'},rtk:{state:'disabled'}},true).state,'disabled');
assert.equal(model({...base,gps:{valid:true,source:'rtk_float',rtk_used:true}},true).state,'float');
assert.deepEqual(historySummary([]),{percent:0,run:0,longest:0,span:0,count:0});
const history=[{at:0,state:'gga'},{at:2,state:'fix'},{at:4,state:'fix'},{at:6,state:'fix'},{at:8,state:'gga'},{at:10,state:'fix'}];
assert.equal(historySummary(history).percent,4/6*100);
assert.equal(historySummary(history).longest,4);
assert.equal(historySummary(history).run,0);
assert.equal(historySummary([{at:0,state:'fix'},{at:20,state:'fix'}]).longest,0,'network gaps must not count as continuous FIX');
console.log('PASS RTK UI: accepted source, rejected FIX, stale/offline states, configured limits and FIX continuity');

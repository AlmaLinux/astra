/*!
 * chartjs-plugin-autocolors v0.3.1
 * https://github.com/kurkle/chartjs-plugin-autocolors#readme
 * (c) 2024 Jukka Kurkela <jukka.kurkela@gmail.com>
 * Released under the MIT license
 */
!function(t,o){"object"==typeof exports&&"undefined"!=typeof module?module.exports=o():"function"==typeof define&&define.amd?define(o):(t="undefined"!=typeof globalThis?globalThis:t||self)["chartjs-plugin-autocolors"]=o()}(this,(function(){"use strict";
/*!
 * @kurkle/color v0.3.2
 * https://github.com/kurkle/color#readme
 * (c) 2023 Jukka Kurkela
 * Released under the MIT License
 */function t(t){return t+.5|0}const o=(t,o,e)=>Math.max(Math.min(t,e),o);function e(e){return o(t(255*e),0,255)}function n(t,o,e){const n=(n,r=(n+t/60)%6)=>e-e*o*Math.max(Math.min(r,4-r,1),0);return[n(5),n(3),n(1)]}function r(t,o,r){return a=n,d=t,u=o,c=r,(Array.isArray(d)?a(d[0],d[1],d[2]):a(d,u,c)).map(e);var a,d,u,c}function a(e){return e&&(e.a<255?`rgba(${e.r}, ${e.g}, ${e.b}, ${function(e){return o(t(e/2.55)/100,0,1)}(e.a)})`:`rgb(${e.r}, ${e.g}, ${e.b})`)}function d(t,o,e,n){return"data"===n?(t.backgroundColor=o,t.border=e):(t.backgroundColor=t.backgroundColor||o,t.borderColor=t.borderColor||e),t.backgroundColor===o&&t.borderColor===e}function u(t,o,e){const n=t.next().value;return"function"==typeof o?o(Object.assign({colors:n},e)):n}return{id:"autocolors",beforeUpdate(t,o,e){const{mode:n="dataset",enabled:c=!0,customize:f,repeat:s}=e;if(!c)return;const i=function*(t=1){const o=function*(){for(yield 0;;)for(let t=1;t<10;t++){const o=1<<t;for(let t=1;t<=o;t+=2)yield t/o}}();let e=o.next();for(;!e.done;){let n=r(Math.round(360*e.value),.6,.8);for(let o=0;o<t;o++)yield{background:a({r:n[0],g:n[1],b:n[2],a:192}),border:a({r:n[0],g:n[1],b:n[2],a:144})};n=r(Math.round(360*e.value),.6,.5);for(let o=0;o<t;o++)yield{background:a({r:n[0],g:n[1],b:n[2],a:192}),border:a({r:n[0],g:n[1],b:n[2],a:144})};e=o.next()}}(s);if(e.offset)for(let t=0;t<e.offset;t++)i.next();return"label"===n?function(t,o,e,n){const r={};for(const a of t.data.datasets){const c=a.label??"";r[c]||(r[c]=u(o,e,{chart:t,datasetIndex:0,dataIndex:void 0,label:c}));const f=r[c];d(a,f.background,f.border,n)}}(t,i,f,n):function(t,o,e,n){const r="dataset"===n;let a=u(o,e,{chart:t,datasetIndex:0,dataIndex:r?void 0:0});for(const c of t.data.datasets)if(r)d(c,a.background,a.border,n)&&(a=u(o,e,{chart:t,datasetIndex:c.index}));else{const r=[],f=[];for(let n=0;n<c.data.length;n++)r.push(a.background),f.push(a.border),a=u(o,e,{chart:t,datasetIndex:c.index,dataIndex:n});d(c,r,f,n)}}(t,i,f,n)}}}));
//# sourceMappingURL=chartjs-plugin-autocolors.min.js.map

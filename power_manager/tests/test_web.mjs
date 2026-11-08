import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
const source=readFileSync('power_manager/web/worker.js','utf8');
const worker=await import('data:text/javascript;base64,'+Buffer.from(source).toString('base64'));
const page=await worker.default.fetch(new Request('https://power.example/'),{});
assert.equal(page.status,200);
const html=await page.text();
const script=html.match(/<script>([\s\S]*?)<\/script>/)[1];
new vm.Script(script);
class Element {
 constructor(){this.value='';this.checked=false;this.disabled=false;this.open=false;this.children=[]}
 replaceChildren(...children){this.children=children}
 append(...children){this.children.push(...children)}
 showModal(){this.open=true}close(){this.open=false}addEventListener(){}
}
const elements=new Map(),requests=[],directions=new Map();
const document={getElementById(id){if(!elements.has(id))elements.set(id,new Element());return elements.get(id)},createElement(){return new Element()}};
let failure=null,override=null;
const fetch=async(url,options={})=>{
 const body=options.body?JSON.parse(options.body):null;requests.push({url,body});let value;
 if(url==='/api/plan'&&failure){if(failure.network)throw {name:failure.network};return {ok:false,status:failure.status,headers:new Headers({'x-correlation-id':'gateway-fallback'}),json:async()=>{if(failure.invalidJson)throw Error('not-json');return failure.payload}}}
 if(url==='/api/status')value={state:{state:'ON'},execution_enabled:true};
 else if(url==='/api/inventory')value={resources:[],errors:[],observed_at:new Date().toISOString()};
 else if(url==='/api/plan')value={id:body.direction==='DOWN'?'11111111-1111-4111-8111-111111111111':'22222222-2222-4222-8222-222222222222',expires:Math.floor(Date.now()/1000)+600,direction:body.direction,pending:false,ready:true,confirmation:body.direction==='DOWN'?'DESLIGAR LABORATÓRIO':'LIGAR LABORATÓRIO',blockers:[],targets:[{type:'EC2',id:'unit-instance',name:'unit-instance',state:'running'}],recovery:['Recuperar o estado anterior']};
 else if(url==='/api/operations')value={id:'operation-'+body.planId,direction:body.planId.startsWith('1111')?'DOWN':'UP',state:'RUNNING',index:0};
 else throw Error('Unexpected test request '+url);
 if(url==='/api/plan'&&override)value={...value,...override};
 return {ok:true,status:200,headers:new Headers(),json:async()=>value};
};
const context=vm.createContext({document,fetch,crypto:{randomUUID:()=> '7686e057-d708-47ea-a4f3-f069d663f337'},setInterval:()=>0,setTimeout,clearTimeout,AbortController,Headers,console,Date,Promise,Number,JSON,Error});
vm.runInContext(script,context);
await new Promise(setImmediate);
for(const [button,phrase] of [['down','DESLIGAR LABORATÓRIO'],['up','LIGAR LABORATÓRIO']]){
 await document.getElementById(button).onclick();
 assert.equal(document.getElementById('confirmation').open,true);
 assert.equal(document.getElementById('execute').disabled,true);
 document.getElementById('phrase').value=phrase;document.getElementById('phrase').oninput();
 assert.equal(document.getElementById('execute').disabled,true,'Typed phrase alone cannot start power operation');
 document.getElementById('consent').checked=true;document.getElementById('consent').onchange();
 assert.equal(document.getElementById('execute').disabled,false);
 await document.getElementById('execute').onclick();
}
const operations=requests.filter(r=>r.url==='/api/operations');
assert.equal(operations.length,2);
assert.deepEqual(operations.map(r=>r.body.confirmation),['DESLIGAR LABORATÓRIO','LIGAR LABORATÓRIO']);
assert(operations.every(r=>r.body.firstShutdownConsent===true));
const missing=await worker.default.fetch(new Request('https://power.example/api/status'),{});
assert.equal(missing.status,503);
console.log('PASS: DOWN/UP controls, modal, phrase + explicit consent, progress, browser syntax and backend fail-closed');

for(const status of [401,403,404,500,502]){
 failure={status,payload:{message:'Internal Server Error',correlation_id:'test-correlation'}};
 await document.getElementById('down').onclick();
 assert.equal(document.getElementById('execute').disabled,true);
 assert.match(document.getElementById('planstatus').textContent,new RegExp('HTTP '+status));
 assert.match(document.getElementById('planstatus').textContent,/test-correlation/);
}
failure={status:502,invalidJson:true};await document.getElementById('down').onclick();
assert.match(document.getElementById('planstatus').textContent,/HTTP 502.*gateway-fallback/);
for(const [network,expected] of [['AbortError','NETWORK_TIMEOUT'],['TypeError','NETWORK_ERROR']]){
 failure={network};await document.getElementById('down').onclick();
 assert.equal(document.getElementById('execute').disabled,true);
 assert.match(document.getElementById('planstatus').textContent,new RegExp(expected));
 assert.match(document.getElementById('planstatus').textContent,/correlação/);
}
failure=null;
for(const invalid of [{ready:true,pending:true},{id:'invalid'},{direction:'UP'},{expires:1},{blockers:['blocked']},{confirmation:'wrong'},{targets:[]}]){
 override=invalid;await document.getElementById('down').onclick();
 document.getElementById('phrase').value='DESLIGAR LABORATÓRIO';document.getElementById('consent').checked=true;document.getElementById('consent').onchange();
 assert.equal(document.getElementById('execute').disabled,true);
 assert.match(document.getElementById('planstatus').textContent,/INVALID_PLAN_CONTRACT/);
}
override=null;
await document.getElementById('down').onclick();
vm.runInContext('activePlan.expires=1',context);
document.getElementById('phrase').value='DESLIGAR LABORATÓRIO';document.getElementById('consent').checked=true;document.getElementById('consent').onchange();
assert.equal(document.getElementById('execute').disabled,true);
assert.match(document.getElementById('planstatus').textContent,/expirado/);

const originalFetch=globalThis.fetch;
try{
 const correlation='aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa';
 const env={POWER_API_URL:'https://backend.invalid',POWER_SIGNING_KEY:'unit-test-only'};
 const request=()=>new Request('https://power.example/api/plan',{method:'POST',headers:{origin:'https://power.example','x-client-request-id':correlation},body:'{"direction":"DOWN"}'});
 for(const status of [401,403,404,500,502]){
  globalThis.fetch=async()=>Response.json({message:'Internal Server Error'},{status,headers:{'apigw-requestid':'gateway-request-1'}});
  const result=await worker.default.fetch(request(),env);const body=await result.json();
  assert.equal(result.status,status);assert.equal(body.correlation_id,correlation);assert.equal(body.upstream_request_id,'gateway-request-1');assert(body.error.length>0);
 }
 globalThis.fetch=async()=>new Response('<html>private debug details</html>',{status:502});
 let result=await worker.default.fetch(request(),env);let body=await result.json();assert.equal(result.status,502);assert(!JSON.stringify(body).includes('private debug'));
 globalThis.fetch=async()=>Response.json({error:'Authorization: Bearer do-not-reflect'},{status:500});
 result=await worker.default.fetch(request(),env);body=await result.json();assert(!JSON.stringify(body).includes('do-not-reflect'));
 globalThis.fetch=async()=>Response.json({message:'unexpected success shape'});
 result=await worker.default.fetch(request(),env);assert.equal(result.status,200);
 for(const [name,status] of [['TimeoutError',504],['TypeError',503]]){
  globalThis.fetch=async()=>{throw {name}};result=await worker.default.fetch(request(),env);body=await result.json();assert.equal(result.status,status);assert.equal(body.correlation_id,correlation);
 }
}finally{globalThis.fetch=originalFetch}
console.log('PASS: HTTP error contracts, correlation, JSON/timeout/network failures, invalid/expired plans and secret redaction');

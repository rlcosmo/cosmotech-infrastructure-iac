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
const elements=new Map(),requests=[];
const document={getElementById(id){if(!elements.has(id))elements.set(id,new Element());return elements.get(id)},createElement(){return new Element()}};
const fetch=async(url,options={})=>{
 const body=options.body?JSON.parse(options.body):null;requests.push({url,body});let value;
 if(url==='/api/status')value={state:{state:'ON'},execution_enabled:true};
 else if(url==='/api/inventory')value={resources:[],errors:[],observed_at:new Date().toISOString()};
 else if(url==='/api/plan')value={id:'plan-'+body.direction,direction:body.direction,pending:false,ready:true,confirmation:body.direction==='DOWN'?'DESLIGAR LABORATÓRIO':'LIGAR LABORATÓRIO',blockers:[],targets:[],recovery:['Recuperar o estado anterior']};
 else if(url==='/api/operations')value={id:'operation-'+body.planId,direction:body.planId.endsWith('DOWN')?'DOWN':'UP',state:'RUNNING',index:0};
 else throw Error('Unexpected test request '+url);
 return {ok:true,json:async()=>value};
};
const context=vm.createContext({document,fetch,crypto:{randomUUID:()=> '7686e057-d708-47ea-a4f3-f069d663f337'},setInterval:()=>0,setTimeout,console,Date,Promise,Number,JSON,Error});
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

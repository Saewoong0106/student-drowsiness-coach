import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const html=readFileSync(new URL('../index.html',import.meta.url),'utf8');
const source=readFileSync(new URL('../ui.js',import.meta.url),'utf8');
function ui() {
  const ids=[...html.matchAll(/\bid="([^"]+)"/g)].map(m=>m[1]);
  assert.equal(ids.length,new Set(ids).size,'unique element ids');
  for(const match of source.matchAll(/\$\('([^']+)'\)/g))assert.ok(ids.includes(match[1]),`missing element: ${match[1]}`);
  const elements=new Map(ids.map(id=>[id,{
    value:'',hidden:false,textContent:'',innerHTML:'',disabled:false,listeners:{},
    classList:{toggle(){},add(){},remove(){}},
    addEventListener(name,handler){this.listeners[name]=handler;},setAttribute(){},removeAttribute(){}
  }]));
  const get=id=>elements.get(id);
  let handler=async()=>{throw new Error('unexpected fetch');};
  let choice=null;
  const context=vm.createContext({
    document:{getElementById:get,querySelector:()=>choice,addEventListener(){}},
    window:{addEventListener(){}},location:{protocol:'file:'},setInterval(){},
    navigator:{},performance:{now:()=>100},console,
    fetch:(...args)=>handler(...args)
  });
  vm.runInContext(source,context);
  vm.runInContext(readFileSync(new URL('../demo-ui.js',import.meta.url),'utf8'),context);
  return {get,run:code=>vm.runInContext(code,context),respond:fn=>{handler=fn;},choose:value=>{choice={value};}};
}
const response=body=>({ok:true,status:200,json:async()=>body});

test('registration requires a current username check and opens study selection before the app',async()=>{
  const {get,run,respond,choose}=ui();
  run("studyFocuses=[{id:'coding',label:'Coding',description:'Practice'}];authTab('register')");
  assert.equal(get('username').required,true);assert.equal(get('phone').required,true);
  get('username').value='student_one';get('email').value='one@example.com';get('phone').value='01012345678';get('password').value='password-123456';
  await get('auth-form').listeners.submit({preventDefault(){}});
  assert.ok(get('auth-message').textContent);
  const incomplete={id:1,name:'Student',username:'student_one',phone:'01012345678',onboarding_complete:false,study_focus:null};
  const paths=[];
  respond(async(path,options)=>{
    paths.push(path);
    if(path==='/api/username/check')return response({available:true,username:'student_one'});
    if(path==='/api/register')return response({user:incomplete});
    if(path==='/api/profile'){assert.equal(JSON.parse(options.body).study_focus,'coding');return response({user:{...incomplete,study_focus:'coding',onboarding_complete:true}});}
    if(path==='/api/studies')return response({studies:[]});
    throw Error('Unexpected route '+path);
  });
  await get('check-username').listeners.click();
  assert.equal(run('checkedUsernames.registration'),'student_one');
  await get('auth-form').listeners.submit({preventDefault(){}});
  assert.equal(get('onboarding-view').hidden,false);assert.equal(get('app-view').hidden,true);
  assert.equal(get('profile-fields').hidden,true);assert.match(get('focus-options').innerHTML,/coding/);
  choose('coding');await get('onboarding-form').listeners.submit({preventDefault(){}});
  assert.equal(get('onboarding-view').hidden,true);assert.equal(get('app-view').hidden,false);
  assert.ok(get('study-title').value.startsWith('Coding'));assert.equal(paths.at(-1),'/api/studies');
  run("authTab('login')");assert.equal(get('email').type,'text');assert.equal(get('phone').required,false);
});

test('changing username invalidates checks and ignores late responses for a different value',async()=>{
  const {get,run,respond}=ui();
  let complete;
  respond(()=>new Promise(resolve=>{complete=resolve;}));
  get('username').value='first_user';
  const pending=get('check-username').listeners.click();
  get('username').value='next_user';get('username').listeners.input();
  complete(response({available:true,username:'first_user'}));await pending;
  assert.equal(run('checkedUsernames.registration'),'');
  respond(async()=>response({available:true,username:'next_user'}));
  await get('check-username').listeners.click();assert.equal(run('checkedUsernames.registration'),'next_user');
  get('username').value='third_user';get('username').listeners.input();
  assert.equal(run('checkedUsernames.registration'),'');
});

test('demo reports show synthetic labels, coaching and events instead of real measurement claims',()=>{
  const {run,get}=ui();
  const fixture={title:'Demo',date:'2026.11.17',time:'10:00–10:30',study:1800,rest:0,unobservable:0,events:[],status:'succeeded',is_fallback:false,lead:'가상 코칭',fact:'화면 확인용 가상 통계입니다.',suggestion:'잠깐 쉬어 보세요.',plan:[25,5]};
  run(`studies=[{id:'D-1',title:'Demo',study_ms:3600000,break_ms:0,started_at:1,ended_at:3600001,demo:${JSON.stringify(fixture)}}];selected='D-1';renderHistory();`);
  assert.match(get('real-detail').innerHTML,/DEMO SESSION/);
  assert.ok(get('real-detail').innerHTML.includes(fixture.fact));
  assert.match(get('real-detail').innerHTML,/<table>/);
});

test('overlapping study refreshes keep the newest response and discard responses after logout',async()=>{
  const {run,respond,get}=ui();
  run('user={id:1};');
  const pending=[];respond(()=>new Promise(resolve=>pending.push(resolve)));
  const first=run('loadStudies()'),second=run('loadStudies()');
  const row=title=>({id:title,title,started_at:1,ended_at:2,study_ms:1000,break_ms:0});
  pending[1](response({studies:[row('new')]}));await second;
  pending[0](response({studies:[row('old')]}));await first;
  assert.equal(run('studies[0].title'),'new');
  const late=run('loadStudies()');run('clearAccount()');
  pending[2](response({studies:[row('private')]}));await late;
  assert.equal(run('studies.length'),0);assert.equal(get('real-detail').innerHTML,'');
});

test('camera permission requested before navigation cannot activate after returning',async()=>{
  const {run,get}=ui();let release;
  let stopped=0;
  const media={getTracks:()=>[{stop(){stopped++;}}]};
  const pendingMedia=new Promise(resolve=>{release=resolve;});
  // Supply browser capabilities through the persistent VM's global objects.
  run('user={id:1};navigator.mediaDevices={};');
  run('navigator.mediaDevices').getUserMedia=()=>pendingMedia;
  const camera=run('enableCamera()');
  run("setView('history');setView('study');");
  release(media);await camera;
  assert.equal(stopped,1);assert.equal(run('stream'),null);
  assert.equal(get('camera-badge').textContent,'카메라 꺼짐');
  assert.equal(get('app-message').textContent,'');
});

test('a non-JSON API response gives an actionable server message',async()=>{
  const {run,respond}=ui();
  respond(async()=>({ok:false,status:404,json:async()=>{throw Error('HTML response');}}));
  await assert.rejects(run("api('/api/config')"),/API 서버 연결/);
});

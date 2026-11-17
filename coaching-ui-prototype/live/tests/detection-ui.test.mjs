import {test} from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {readFileSync} from 'node:fs';

test('screen warning and beep fire only for confirmed events; stopping suppresses late responses',async()=>{
  const nodes=new Map();const node=id=>{if(!nodes.has(id))nodes.set(id,{textContent:'',classList:{toggle(){}},addEventListener(){}});return nodes.get(id);};
  let oscillators=0,worker,time=2000,detected;
  class Audio{
    state='running';currentTime=0;destination={};
    createOscillator(){oscillators++;return {frequency:{value:0},connect(){},disconnect(){},start(){},stop(){}};}
    createGain(){return {gain:{setValueAtTime(){},linearRampToValueAtTime(){}},connect(){},disconnect(){}};}
  }
  class WorkerMock{constructor(){worker=this;}postMessage(){}terminate(){}}
  const context=vm.createContext({window:{AudioContext:Audio,addEventListener(){}},document:{hidden:false,getElementById:node,addEventListener(){}},Worker:WorkerMock,performance:{now:()=>time},setTimeout(){},clearTimeout(){},fetch:async()=>({ok:true,json:async()=>({detection:detected})})});
  vm.runInContext(readFileSync(new URL('../detection.js',import.meta.url),'utf8'),context);
  await context.window.studyDetection.armSound();
  context.window.studyDetection.sync({id:'S-test',status:'studying'},true);
  detected={phase:'unobservable',eye_state:'unobservable',head_state:'unobservable',triggered:[],eye_count:0,head_count:0};
  await worker.onmessage({data:{type:'measurement'}});assert.equal(oscillators,0);
  detected={...detected,phase:'ready',eye_state:'eyes_closed',triggered:[{type:'eye_closure_suspected'}],eye_count:1};
  await worker.onmessage({data:{type:'measurement'}});assert.equal(oscillators,2);
  assert.ok(node('detection-message').textContent);
  detected={...detected,triggered:[]};time=4000;
  await worker.onmessage({data:{type:'measurement'}});assert.equal(oscillators,2);
  detected={...detected,eye_state:'normal',head_state:'head_down',triggered:[{type:'head_down'}]};
  await worker.onmessage({data:{type:'measurement'}});assert.equal(oscillators,4);
  context.window.studyDetection.stop();time=6000;
  await worker.onmessage({data:{type:'measurement'}});assert.equal(oscillators,4);
});

test('muting or stopping cancels scheduled tones and rearming preserves the mute label',async()=>{
  const nodes=new Map();
  const node=id=>{if(!nodes.has(id))nodes.set(id,{textContent:'',listeners:{},classList:{toggle(){}},addEventListener(name,fn){this.listeners[name]=fn;}});return nodes.get(id);};
  const tones=[];let bind;
  class Audio{
    state='running';currentTime=0;destination={};
    createOscillator(){const tone={frequency:{value:0},connect(){},disconnect(){},start(){},stops:0,stop(){this.stops++;}};tones.push(tone);return tone;}
    createGain(){return {gain:{setValueAtTime(){},linearRampToValueAtTime(){}},connect(){},disconnect(){}};}
  }
  const context=vm.createContext({window:{AudioContext:Audio,addEventListener(){}},document:{getElementById:node,addEventListener(name,fn){if(name==='DOMContentLoaded')bind=fn;}},performance:{now:()=>0},clearTimeout(){}});
  vm.runInContext(readFileSync(new URL('../detection.js',import.meta.url),'utf8'),context);bind();
  await node('test-sound').listeners.click();assert.equal(tones.length,2);
  node('mute-sound').listeners.click();assert.ok(tones.every(t=>t.stops===2));
  await context.window.studyDetection.armSound();assert.equal(node('sound-status').textContent,'경고음 꺼짐');
  await node('test-sound').listeners.click();assert.equal(tones.length,4);
  context.window.studyDetection.stop();assert.ok(tones.every(t=>t.stops===2));
});

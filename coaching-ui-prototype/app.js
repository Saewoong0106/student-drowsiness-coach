/* Standalone UI mock. No camera, network, database, or model calls. */
'use strict';
const labels = {pending:'대기',running:'생성 중',succeeded:'완료',failed:'실패'};
const eventTypes = {
  eye_closure_suspected:{label:'눈 감김 의심',color:'#d6a268',note:'2초 이상 지속된 눈 감김'},
  head_down:{label:'고개 숙임',color:'#8ca9c6',note:'자세 이벤트 · 졸음으로 합산하지 않음'},
  unobservable:{label:'미측정',color:'#bdc5c0',note:'얼굴 가림 · 관측 불가'},
  break:{label:'휴식',color:'#85ad97',note:'사용자가 선택한 휴식'}
};
const event = (type,start,duration) => ({type,condition_started_s:start,confirmed_s:start+(type==='eye_closure_suspected'?2:type==='head_down'?3:0),ended_s:start+duration});
const sessions = [
  {session_id:'DEMO-1117-03',title:'오후 임베디드 공부',date:'2026.11.17',time:'14:00–15:00',study:3600,rest:0,unobservable:30,status:'succeeded',is_fallback:false,events:[event('head_down',540,80),event('eye_closure_suspected',1080,4),event('unobservable',1740,30),event('head_down',2200,65),event('eye_closure_suspected',2580,5),event('eye_closure_suspected',3300,6)],lead:'후반부에는 잠깐, 쉬어 가도 좋아요.',fact:'공부 60분 동안 눈 감김 의심이 3회 기록되었고, 그중 2회가 마지막 20분에 나타났어요. 고개 숙임 2회는 별도의 자세 기록으로 구분했어요.',suggestion:'다음 세션의 후반부 기록을 비교해 보세요.',plan:[25,5]},
  {session_id:'DEMO-1117-02',title:'점심 후 복습',date:'2026.11.17',time:'12:30–13:05',study:1800,rest:300,unobservable:12,status:'pending',is_fallback:false,events:[event('head_down',420,60),event('break',900,300),event('unobservable',1480,12)],lead:'휴식을 포함한 공부 흐름을 이어가 보세요.',fact:'공부 30분과 휴식 5분이 기록되었어요. 눈 감김 의심은 0회, 고개 숙임은 1회예요. 눈 감김 기록만으로 집중도를 판단할 수는 없어요.',suggestion:'다음 세션에서도 휴식 전후의 기록을 살펴보세요.',plan:[25,5]},
  {session_id:'DEMO-1117-01',title:'오전 전공 읽기',date:'2026.11.17',time:'09:00–09:45',study:2700,rest:0,unobservable:45,status:'running',is_fallback:false,events:[event('eye_closure_suspected',1450,4),event('unobservable',1800,45)],lead:'다음 공부 전, 짧은 휴식을 준비해요.',fact:'공부 45분 동안 눈 감김 의심 1회와 고개 숙임 0회가 기록되었어요. 미측정 45초는 눈 감김 횟수에 포함하지 않았어요.',suggestion:'쉬고 난 뒤 관측 가능한 구간의 기록을 비교해 보세요.',plan:[25,5]},
  {session_id:'DEMO-1116-03',title:'저녁 과제 정리',date:'2026.11.16',time:'20:00–21:10',study:3900,rest:300,unobservable:60,status:'failed',is_fallback:true,events:[event('eye_closure_suspected',700,3),event('head_down',1200,80),event('break',1800,300),event('unobservable',2500,60),event('eye_closure_suspected',2900,4),event('eye_closure_suspected',3400,5),event('eye_closure_suspected',3900,4)],lead:'저장된 기록을 바탕으로 안내해 드려요.',fact:'공부 65분, 휴식 5분 동안 눈 감김 의심 4회와 고개 숙임 1회가 기록되었어요. 모델 응답 검증 실패를 가정하여 규칙 기반 안내를 표시하고 있어요.',suggestion:'다음에는 공부 사이에 휴식을 계획해 보세요.',plan:[25,5]},
  {session_id:'DEMO-1116-02',title:'오후 팀프로젝트',date:'2026.11.16',time:'15:00–15:25',study:1500,rest:0,unobservable:600,status:'failed',is_fallback:false,events:[event('unobservable',300,600),event('head_down',1050,45)],lead:'관측 환경부터 확인해 보세요.',fact:'공부 25분 중 미측정이 10분으로, 관측 가능한 시간은 15분이에요. 눈 감김 의심 0회, 고개 숙임 1회가 기록되었지만 관측이 부족해 전체 세션을 해석하기 어려워요.',suggestion:'카메라 위치와 조명을 확인하고 짧게 다시 기록해 보세요.',plan:[15,5]},
  {session_id:'DEMO-1116-01',title:'아침 알고리즘',date:'2026.11.16',time:'09:00–09:55',study:3000,rest:300,unobservable:20,status:'succeeded',is_fallback:false,events:[event('head_down',600,100),event('break',1500,300),event('head_down',2200,75),event('unobservable',2700,20)],lead:'공부와 휴식의 리듬을 이어가 보세요.',fact:'공부 50분과 휴식 5분이 기록되었어요. 눈 감김 의심은 0회이며 고개 숙임 2회는 자세 이벤트로 따로 남겼어요.',suggestion:'비슷한 길이의 다음 세션과 기록을 비교해 보세요.',plan:[25,5]}
];
let selectedId=sessions[0].session_id;
let activeTab='records';
const $=id=>document.getElementById(id);
const count=(s,type)=>s.events.filter(e=>e.type===type).length;
const minutes=seconds=>Math.round(seconds/60);
const clock=seconds=>`${String(Math.floor(seconds/60)).padStart(2,'0')}:${String(Math.floor(seconds%60)).padStart(2,'0')}`;
function badge(s){return `<span class="badge ${s.is_fallback?'fallback':s.status}"><span class="small-dot"></span>${s.is_fallback?'대체 안내':labels[s.status]}</span>`;}
function renderOverview(){
  const study=sessions.reduce((n,s)=>n+s.study,0);
  const items=[['전체 공부 시간',`${Math.floor(study/3600)}<small>시간</small> ${minutes(study%3600)}<small>분</small>`,'가상 세션 6개의 공부 시간 합계','◷'],['기록된 세션',`6<small>개</small>`,'11월 16일–17일 예시 기록','▤'],['눈 감김 의심',`${sessions.reduce((n,s)=>n+count(s,'eye_closure_suspected'),0)}<small>회</small>`,'고개 숙임과 미측정은 제외','◉'],['코칭 완료',`${sessions.filter(s=>s.status==='succeeded').length}<small>/ 6개</small>`,'대체 안내는 완료와 구분','✧']];
  $('overview').innerHTML=items.map(([title,value,note,icon])=>`<article class="stat"><div class="stat-top">${title}<span class="stat-icon" aria-hidden="true">${icon}</span></div><div class="stat-value">${value}</div><div class="stat-note">${note}</div></article>`).join('');
}
function renderList(){
  const query=$('search').value.trim().toLocaleLowerCase();
  const filter=$('filter').value;
  const list=sessions.filter(s=>(filter==='all'||s.status===filter)&&`${s.title} ${s.session_id}`.toLocaleLowerCase().includes(query));
  $('session-list').innerHTML=list.length?list.map(s=>`<button class="session-item ${s.session_id===selectedId?'active':''}" data-session="${s.session_id}" aria-pressed="${s.session_id===selectedId}"><span class="item-title">${s.title}${badge(s)}</span><div class="item-date">${s.date} <span aria-hidden="true">·</span> ${s.time}</div><div class="item-meta"><span>공부 ${minutes(s.study)}분</span><span>눈 감김 의심 ${count(s,'eye_closure_suspected')}회</span></div></button>`).join(''):'<div class="empty-search">검색 결과가 없어요.<br>검색어나 상태를 바꿔 보세요.</div>';
}
function coaching(s){
  let body;
  if(s.status==='pending'||s.status==='running'||(s.status==='failed'&&!s.is_fallback)){
    const state={pending:['✧','코칭을 기다리고 있어요','기록이 준비되었어요. 버튼을 눌러 가상 코칭 생성 과정을 확인하세요.','가상 코칭 생성'],running:['◌','기록을 읽고 있어요','요약 통계를 바탕으로 코칭을 생성하는 상태입니다. 아래 버튼으로 완료 화면을 확인하세요.','완료 상태로 보기'],failed:['!','코칭을 생성하지 못했어요','모델 연결 실패를 가정한 화면입니다. 세션 기록은 그대로 확인할 수 있어요.','가상 코칭 다시 시도']}[s.status];
    body=`<div class="state-empty"><div class="state-icon ${s.status==='running'?'spin':''}" aria-hidden="true">${state[0]}</div><h3>${state[1]}</h3><p>${state[2]}</p><button class="primary" data-action="generate">${state[3]}</button>${s.status==='failed'?'<p style="margin-top:12px"><button class="secondary" data-action="fallback">대체 안내 보기</button></p>':''}</div>`;
  }else{
    body=`${s.is_fallback?'<div class="alert">모델 응답 검증 실패 예시입니다. 아래 내용은 규칙 기반 대체 안내이며 AI 모델의 생성 결과가 아닙니다.</div>':''}<p class="coach-kicker">A MOMENT TO REFLECT</p><h3 class="coach-lead">${s.lead}</h3><p class="coach-facts">${s.fact}</p><div class="plan"><div><div class="plan-title">다음 공부를 위한 제안</div><div class="plan-copy">${s.suggestion}</div></div><div class="plan-numbers"><div><strong>${s.plan[0]}</strong><span>분 공부</span></div><span class="plus">+</span><div><strong>${s.plan[1]}</strong><span>분 휴식</span></div></div></div><p class="coach-footnote">공부·휴식 시간은 제안이며 측정 결과가 아닙니다. 모든 문구는 화면 시연용 예시입니다.</p>`;
  }
  return `<section class="panel coaching-panel" aria-labelledby="coach-heading"><div class="coaching-heading"><div class="coaching-title"><span class="sparkle" aria-hidden="true">✧</span><h2 id="coach-heading">나의 학습 코칭</h2></div>${badge(s)}</div><div class="coaching-body">${body}</div><div class="coach-meta"><span>${s.is_fallback?'규칙 기반 대체 안내 예시':'KoGPT2 + LoRA · 연결 예정'}</span><span>가상 코칭 · 실제 모델 미실행</span></div></section>`;
}
function records(s){
  const duration=s.study+s.rest;
  const timeline=s.events.map(e=>`<span style="left:${e.condition_started_s/duration*100}%;width:${(e.ended_s-e.condition_started_s)/duration*100}%;background:${eventTypes[e.type].color}" title="${eventTypes[e.type].label} ${clock(e.condition_started_s)}–${clock(e.ended_s)}"></span>`).join('');
  const rows=s.events.map(e=>`<tr><td>${clock(e.condition_started_s)}</td><td><span class="event-dot" style="background:${eventTypes[e.type].color}"></span>${eventTypes[e.type].label}</td><td>${e.ended_s-e.condition_started_s}초</td><td>${eventTypes[e.type].note}</td></tr>`).join('');
  const content=activeTab==='records'?`<div class="record-topline"><h3>세션 타임라인</h3><div class="legend">${Object.values(eventTypes).map(e=>`<span><i style="background:${e.color}"></i>${e.label}</span>`).join('')}</div></div><div class="timeline" role="img" aria-label="세션 ${minutes(duration)}분 중 눈 감김 의심 ${count(s,'eye_closure_suspected')}회, 고개 숙임 ${count(s,'head_down')}회, 미측정 ${s.unobservable}초. 아래 표에서 구간을 확인할 수 있습니다.">${timeline}</div><div class="axis"><span>00:00</span><span>${clock(duration/2)}</span><span>${clock(duration)}</span></div><div class="timeline-note">시간은 세션 시작 후 경과 시간입니다. 고개 숙임은 눈 감김 의심과 따로 기록합니다.</div><table><thead><tr><th scope="col">시작</th><th scope="col">이벤트</th><th scope="col">지속</th><th scope="col">기록 설명</th></tr></thead><tbody>${rows}</tbody></table><p class="record-note">짧은 이벤트 구간은 식별을 위해 타임라인에서 최소 너비로 표시합니다.</p>`:`<div class="alert">모델 비교용 자리입니다. 아래 두 문장은 작성된 예시이며 실제 모델의 학습 전후 성능을 보여 주지 않습니다.</div><div class="comparison"><article><h3>기본 KoGPT2 <span class="badge pending">예시</span></h3><p>공부 기록을 확인하고, 다음 공부 전 잠깐 쉬어 보세요.</p><div class="example">기본 모델 응답이 들어갈 영역</div></article><article><h3>LoRA 적용 모델 <span class="badge">예시</span></h3><p>${s.fact} 다음에는 ${s.plan[0]}분 공부 후 ${s.plan[1]}분 쉬어 보세요.</p><div class="example">미세 튜닝 모델 응답이 들어갈 영역</div></article></div>`;
  return `<section class="panel"><div class="tabs" role="tablist" aria-label="세션 상세 보기"><button id="tab-records" class="tab" role="tab" aria-selected="${activeTab==='records'}" aria-controls="record-content" data-tab="records">이벤트 기록 <span class="muted-count">${s.events.length}</span></button><button id="tab-compare" class="tab" role="tab" aria-selected="${activeTab==='compare'}" aria-controls="record-content" data-tab="compare">모델 응답 비교 <span class="tiny-label">예시</span></button></div><div class="records-body" id="record-content" role="tabpanel" aria-labelledby="tab-${activeTab}" tabindex="0">${content}</div></section>`;
}
function renderDetail(){
  const s=sessions.find(s=>s.session_id===selectedId);
  $('detail').innerHTML=`<section class="panel"><div class="detail-header"><div><div class="overline">SESSION REPORT <span class="footer-dot">/</span> ${s.session_id}</div><h2 class="detail-title">${s.title}</h2><div class="detail-meta">${s.date} <span class="footer-dot">·</span> ${s.time} <span class="footer-dot">·</span> 종료된 가상 세션</div></div><button class="secondary" data-action="export">↓ 기록 내보내기</button></div><div class="metrics"><div class="metric"><div class="metric-label">공부 시간</div><div class="metric-value">${minutes(s.study)}<small>분</small></div><div class="metric-sub">보정·휴식 제외</div></div><div class="metric"><div class="metric-label">휴식 시간</div><div class="metric-value">${minutes(s.rest)}<small>분</small></div><div class="metric-sub">${count(s,'break')?count(s,'break')+'회 휴식':'휴식 기록 없음'}</div></div><div class="metric orange"><div class="metric-label">눈 감김 의심</div><div class="metric-value">${count(s,'eye_closure_suspected')}<small>회</small></div><div class="metric-sub">실제 졸음 확정 아님</div></div><div class="metric"><div class="metric-label">고개 숙임</div><div class="metric-value">${count(s,'head_down')}<small>회</small></div><div class="metric-sub">별도 자세 이벤트</div></div></div><div class="observation">관측 가능 ${clock(s.study-s.unobservable)} <span class="footer-dot">·</span> 미측정 ${s.unobservable}초 <span class="footer-dot">·</span> 미측정 구간은 눈 감김으로 세지 않아요.</div></section>${coaching(s)}${records(s)}`;
}
function render(){renderOverview();renderList();renderDetail();}
$('search').addEventListener('input',renderList);
$('filter').addEventListener('change',renderList);
$('session-list').addEventListener('click',e=>{const button=e.target.closest('[data-session]');if(button){selectedId=button.dataset.session;renderList();renderDetail();}});
$('detail').addEventListener('click',e=>{
  const tab=e.target.closest('[data-tab]');
  if(tab){activeTab=tab.dataset.tab;renderDetail();$(`tab-${activeTab}`).focus();return;}
  const action=e.target.closest('[data-action]')?.dataset.action;
  const s=sessions.find(s=>s.session_id===selectedId);
  if(action==='generate'){
    s.status=s.status==='running'?'succeeded':'running';s.is_fallback=false;render();
  }else if(action==='fallback'){s.status='failed';s.is_fallback=true;render();}
  else if(action==='export'){
    const blob=new Blob([JSON.stringify({source:'synthetic_ui_demo',...s},null,2)],{type:'application/json;charset=utf-8'});
    const url=URL.createObjectURL(blob);const link=document.createElement('a');link.href=url;link.download=`${s.session_id}.json`;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  }
});
$('detail').addEventListener('keydown',e=>{
  if(e.target.matches('[role="tab"]')&&['ArrowLeft','ArrowRight','Home','End'].includes(e.key)){
    e.preventDefault();activeTab=e.key==='Home'?'records':e.key==='End'?'compare':activeTab==='records'?'compare':'records';renderDetail();$(`tab-${activeTab}`).focus();
  }
});
render();

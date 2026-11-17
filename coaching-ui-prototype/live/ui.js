'use strict';
const $ = id => document.getElementById(id);
const escapeHtml = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let user = null, authMode = 'login', studies = [], active = null, selected = null, snapshotAt = 0, stream = null, cameraBusy = false, mutationBusy = false, currentView = 'study';
let studyFocuses = [];
const checkedUsernames = { registration: '', profile: '' };
const normalizedUsername = value => value.trim().toLowerCase();
const elapsed = ms => {
  const s = Math.max(0, Math.floor(ms / 1000));
  return [Math.floor(s/3600),Math.floor(s/60)%60,s%60].map(n=>String(n).padStart(2,'0')).join(':');
};
const formatDate = time => new Date(time).toLocaleString('ko-KR', {timeZone:'Asia/Seoul',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false});
const durationText = ms => `${Math.floor(ms/60000)}분 ${Math.floor(ms/1000)%60}초`;
async function api(path, data) {
  const response = await fetch(path, {method:data === undefined?'GET':'POST', credentials:'same-origin',headers:data===undefined?{}:{'Content-Type':'application/json','X-Study-Request':'1'},...(data===undefined?{}:{body:JSON.stringify(data)})});
  const result = await response.json();
  if (!response.ok) {
    if (response.status === 401 && user) { stopCamera(); user=null; active=null; showAuth(); }
    throw new Error(result.error || '요청을 처리하지 못했습니다.');
  }
  return result;
}
function authTab(mode) {
  authMode=mode;
  $('login-tab').classList.toggle('chosen',mode==='login');
  $('register-tab').classList.toggle('chosen',mode==='register');
  $('name-label').hidden=mode!=='register';$('name').required=mode==='register';
  $('username-group').hidden=mode!=='register';$('username').required=mode==='register';
  $('phone-label').hidden=mode!=='register';$('phone').required=mode==='register';
  $('email').type=mode==='register'?'email':'text';
  $('email').autocomplete=mode==='register'?'email':'username';
  $('email').placeholder=mode==='register'?'you@example.com':'아이디 또는 이메일';
  $('identifier-label').textContent=mode==='register'?'이메일':'아이디 또는 이메일';
  $('password-help').hidden=mode!=='register';$('password').minLength=mode==='register'?12:1;
  $('password').autocomplete=mode==='register'?'new-password':'current-password';
  $('auth-title').textContent=mode==='register'?'나만의 공부 공간 만들기':'다시 만나 반가워요';
  $('submit-auth').textContent=mode==='register'?'회원가입하고 시작하기':'로그인';
  $('auth-message').textContent='';
}
function showAuth() {$('app-view').hidden=true;$('onboarding-view').hidden=true;$('auth-view').hidden=false;}
function showApp() {
  if(!user.onboarding_complete){showOnboarding();return;}
  $('auth-view').hidden=true;$('onboarding-view').hidden=true;$('app-view').hidden=false;
  $('user-name').textContent=user.name;$('user-initial').textContent=user.name.slice(0,1);
  const focus=studyFocuses.find(f=>f.id===user.study_focus);
  $('user-provider').textContent=focus?.label||(user.provider==='google'?'Google 계정':'일반 계정');
  if(!active)$('study-title').value=focus?`${focus.label} 공부`:'오늘의 공부';
  $('today').textContent=new Date().toLocaleDateString('ko-KR',{timeZone:'Asia/Seoul',year:'numeric',month:'long',day:'numeric',weekday:'long'});
}
function showOnboarding() {
  stopCamera();$('auth-view').hidden=true;$('app-view').hidden=true;$('onboarding-view').hidden=false;
  $('profile-fields').hidden=!!(user.username&&user.phone);
  $('profile-username-group').hidden=!!user.username;$('profile-username').required=!user.username;
  $('profile-phone-label').hidden=!!user.phone;$('profile-phone').required=!user.phone;
  $('profile-username').value=user.username||'';$('profile-phone').value=user.phone||'';
  checkedUsernames.profile=user.username||'';
  $('onboarding-title').textContent=user.username&&user.phone?'어떤 공부를 주로 하시나요?':'내 공부 공간을 마무리해요';
  $('focus-options').innerHTML=studyFocuses.map((focus,index)=>`<label class="focus-option"><input type="radio" name="study_focus" value="${escapeHtml(focus.id)}" ${index===0?'required':''} ${user.study_focus===focus.id?'checked':''}><span><strong>${escapeHtml(focus.label)}</strong><small>${escapeHtml(focus.description)}</small></span></label>`).join('');
  $('onboarding-message').textContent='';
}
async function enterAccount() {
  if(!user.onboarding_complete){showOnboarding();return;}
  showApp();await loadStudies();setView('study');
}
function usernameControls(kind) {
  return kind==='registration'?{input:$('username'),button:$('check-username'),message:$('username-message')}:{input:$('profile-username'),button:$('check-profile-username'),message:$('profile-username-message')};
}
async function checkUsername(kind) {
  const {input,button,message}=usernameControls(kind);
  const value=normalizedUsername(input.value);checkedUsernames[kind]='';button.disabled=true;
  message.classList.remove('check-success');message.textContent='사용 가능한 아이디인지 확인 중이에요.';
  try{
    const result=await api('/api/username/check',{username:value});
    if(normalizedUsername(input.value)!==value)return;
    checkedUsernames[kind]=result.available?result.username:'';
    message.textContent=result.available?'사용할 수 있는 아이디입니다.':'이미 사용 중인 아이디입니다.';
    message.classList.toggle('check-success',result.available);
  }catch(error){if(normalizedUsername(input.value)===value)message.textContent=error.message;}
  finally{button.disabled=false;}
}
for(const kind of ['registration','profile']){
  const {input,button,message}=usernameControls(kind);
  button.addEventListener('click',()=>checkUsername(kind));
  input.addEventListener('input',()=>{checkedUsernames[kind]='';message.classList.remove('check-success');message.textContent='아이디를 변경했어요. 중복 확인을 눌러 주세요.';});
}
function setView(view) {
  currentView=view;
  $('study-view').hidden=view!=='study';$('history-view').hidden=view!=='history';
  $('study-nav').classList.toggle('selected',view==='study');$('history-nav').classList.toggle('selected',view==='history');
  $('breadcrumb').textContent=view==='study'?'공부 기록하기':'학습 기록';
  if(view==='history'){stopCamera();renderHistory();}
}
async function loadStudies() {
  const result=await api('/api/studies');
  studies=result.studies;active=studies.find(s=>!s.ended_at)||null;snapshotAt=performance.now();
  if(active)$('study-title').value=active.title;
  if(!selected||!studies.some(s=>s.id===selected))selected=studies[0]?.id||null;
  updateTimerControls();tick();renderHistory();
}
function updateTimerControls() {
  window.studyDetection?.sync(active,!!stream);
  $('study-title').disabled=!!active;
  $('start-study').hidden=!!active;$('active-controls').hidden=!active;
  $('pause-study').textContent=active?.status==='break'?'공부 재개하기':'휴식하기';
  $('timer-badge').textContent=active?(active.status==='break'?'휴식 중':'공부 중'):'시작 전';
  $('timer-badge').className='badge'+(active?'':' pending');
  $('timer-hint').textContent=active?'창을 닫아도 시간은 계속됩니다. 마칠 때 종료하고 저장을 눌러 주세요.':'공부를 시작하면 계정에 세션이 생성됩니다.';
}
function tick() {
  const delta=active?performance.now()-snapshotAt:0;
  $('study-timer').textContent=elapsed((active?.study_ms||0)+(active?.status==='studying'?delta:0));
  $('break-timer').textContent=elapsed((active?.break_ms||0)+(active?.status==='break'?delta:0));
}
function stopCamera() {
  window.studyDetection?.stop();
  if(stream){stream.getTracks().forEach(track=>track.stop());stream=null;}
  $('camera').srcObject=null;$('camera').classList.remove('on');
  $('camera-placeholder').hidden=false;$('camera-overlay').hidden=true;
  $('camera-badge').textContent='카메라 꺼짐';$('camera-badge').className='badge pending';
  $('toggle-camera').textContent='카메라 켜기';
  $('camera-hint').textContent='카메라를 끈 상태에서도 시간을 기록할 수 있어요.';
}
async function enableCamera() {
  window.studyDetection?.armSound();
  if(cameraBusy)return;
  cameraBusy=true;$('app-message').textContent='';
  $('enable-camera').disabled=true;$('toggle-camera').disabled=true;
  try {
    if(!navigator.mediaDevices?.getUserMedia)throw new Error('카메라는 localhost 또는 HTTPS 주소에서 지원됩니다.');
    const media=await navigator.mediaDevices.getUserMedia({video:{facingMode:'user',width:{ideal:1280},height:{ideal:720}},audio:false});
    // A permission prompt can resolve after navigating away or logging out.
    if(!user||currentView!=='study'){media.getTracks().forEach(track=>track.stop());return;}
    stream=media;$('camera').srcObject=stream;
    await $('camera').play();
    $('camera').classList.add('on');$('camera-placeholder').hidden=true;$('camera-overlay').hidden=false;
    window.studyDetection?.sync(active,true);
    $('camera-badge').textContent='미리보기 연결됨';$('camera-badge').className='badge';
    $('toggle-camera').textContent='카메라 끄기';$('camera-hint').textContent='얼굴이 화면 안에 오도록 위치를 조절해 주세요. 감지는 아직 미연결입니다.';
    stream.getVideoTracks()[0].addEventListener('ended',()=>{stopCamera();$('app-message').textContent='카메라 연결이 끊겼습니다. 타이머는 계속 기록 중입니다.';});
  }catch(error){
    stopCamera();
    $('app-message').textContent=error.name==='NotAllowedError'?'카메라 권한을 허용해 주세요. 브라우저 주소창의 카메라 권한을 확인할 수 있어요.':error.name==='NotFoundError'?'연결된 카메라를 찾지 못했습니다.':error.name==='NotReadableError'?'다른 앱이 카메라를 사용 중인지 확인해 주세요.':error.message;
  }finally{cameraBusy=false;$('enable-camera').disabled=false;$('toggle-camera').disabled=false;}
}
async function mutate(action) {
  window.studyDetection?.armSound();
  if(action!=='start')window.studyDetection?.stop(action==='break'?'휴식 중 · 감지와 경고음이 멈췄어요.':'감지 상태를 전환하고 있어요.');
  if(mutationBusy)return;
  mutationBusy=true;$('app-message').textContent='';
  for(const id of ['start-study','pause-study','finish-study'])$(id).disabled=true;
  try {
    let result;
    if(action==='start')result=await api('/api/studies/start',{title:$('study-title').value});
    else {if(!active)return;result=await api(`/api/studies/${active.id}/${action}`,{});}
    if(action==='finish'){
      selected=result.study.id;stopCamera();await loadStudies();setView('history');
      $('app-message').textContent='공부 기록을 내 계정에 저장했습니다.';
    }else{active=result.study;snapshotAt=performance.now();await loadStudies();}
  }catch(error){$('app-message').textContent=error.message;try{if(user)await loadStudies();}catch{}}
  finally{mutationBusy=false;for(const id of ['start-study','pause-study','finish-study'])$(id).disabled=false;}
}
function renderHistory() {
  const finished=studies.filter(s=>s.ended_at);
  const demoCount=finished.filter(s=>s.demo).length;
  const total=finished.reduce((n,s)=>n+s.study_ms,0),rest=finished.reduce((n,s)=>n+s.break_ms,0);
  $('real-overview').innerHTML=[['전체 공부 시간',durationText(total),demoCount?`가상 ${demoCount}개 포함`:'종료된 내 세션 합계'],['종료한 세션',`${finished.length}개`,'진행 중인 세션 제외'],['전체 휴식 시간',durationText(rest),'종료된 내 세션 합계'],['코칭 상태',demoCount?'가상 시연':'연결 예정','실제 모델 미실행']].map(([name,value,note])=>`<article class="stat"><div class="stat-top">${name}</div><div class="stat-value real-value">${value}</div><div class="stat-note">${note}</div></article>`).join('');
  const query=$('history-search').value.trim().toLocaleLowerCase();
  const filtered=studies.filter(s=>s.title.toLocaleLowerCase().includes(query));
  $('real-list').innerHTML=filtered.length?filtered.map(s=>`<button class="session-item ${selected===s.id?'active':''}" data-study="${s.id}" aria-pressed="${selected===s.id}"><span class="item-title">${escapeHtml(s.title)}<span class="badge ${s.ended_at?'':'pending'}">${s.demo?'가상 기록':s.ended_at?'저장 완료':s.status==='break'?'휴식 중':'공부 중'}</span></span><div class="item-date">${formatDate(s.started_at)} (한국시간)</div><div class="item-meta"><span>공부 ${durationText(s.study_ms)}</span></div></button>`).join(''):`<div class="empty-search">${studies.length?'검색 결과가 없어요.':'아직 공부 기록이 없어요.<br>첫 공부를 시작해 보세요.'}</div>`;
  const row=studies.find(s=>s.id===selected);
  if(row?.demo){$('real-detail').innerHTML=renderDemoReport(row);return;}
  if(!row){$('real-detail').innerHTML='<section class="panel empty-record"><h2>기록이 쌓이면 변화가 보여요</h2><p>공부 기록하기에서 세션을 시작하고 종료해 보세요.</p><button class="primary" id="first-study">첫 공부 시작하기</button></section>';return;}
  $('real-detail').innerHTML=`<section class="panel"><div class="detail-header"><div><div class="overline">SESSION REPORT / ${row.id}</div><h2 class="detail-title">${escapeHtml(row.title)}</h2><div class="detail-meta">${formatDate(row.started_at)}${row.ended_at?' – '+formatDate(row.ended_at):' · 진행 중'} (한국시간)</div></div><span class="badge ${row.ended_at?'':'pending'}">${row.ended_at?'저장 완료':'진행 중'}</span></div><div class="metrics"><div class="metric"><div class="metric-label">공부 시간</div><div class="metric-value real-value">${elapsed(row.study_ms)}</div></div><div class="metric"><div class="metric-label">휴식 시간</div><div class="metric-value real-value">${elapsed(row.break_ms)}</div></div><div class="metric"><div class="metric-label">눈 감김 의심</div><div class="metric-value">—</div><div class="metric-sub">감지 모듈 미연결</div></div><div class="metric"><div class="metric-label">고개 숙임</div><div class="metric-value">—</div><div class="metric-sub">감지 모듈 미연결</div></div></div><div class="real-session-meta">영상은 저장하지 않으며, 이 화면에는 내 계정의 기록만 표시됩니다.${row.ended_at?'':' 진행 중인 시간은 새로고침 시 갱신됩니다.'}</div></section><section class="panel coaching-panel"><div class="coaching-heading"><div class="coaching-title"><span class="sparkle">✧</span><h2>나의 학습 코칭</h2></div><span class="badge pending">연결 예정</span></div><div class="unconnected"><h3>실제 기록과 코칭을 연결할 공간이에요</h3><p>현재는 공부·휴식 시간만 저장합니다. 졸음 감지와 AI 코칭 모듈을 연결하면 같은 세션 ID에 결과를 표시할 수 있어요. 감지하지 않은 횟수를 0회로 표시하거나 가상 코칭을 실제 결과처럼 보여 주지 않습니다.</p><div class="privacy-label">다음 공부를 위한 루틴 예시: 25분 공부 + 5분 휴식<br>이 안내는 AI가 생성한 코칭이 아닙니다.</div></div></section><section class="panel unconnected"><h3>이벤트 기록</h3><p>눈 감김·고개 숙임·미측정 이벤트는 감지 모듈 연결 후 표시됩니다. 현재 카메라 미리보기로 관측 가능 시간을 계산하지 않습니다.</p></section>`;
  if(row.detection_events&&window.studyDetection)$('real-detail').innerHTML=window.studyDetection.renderRecord(row);
}
async function logout() {
  if(active){$('app-message').textContent='진행 중인 공부를 먼저 종료하고 저장해 주세요.';setView('study');return;}
  try{await api('/api/logout',{});stopCamera();user=null;studies=[];active=null;showAuth();$('password').value='';}catch(error){$('app-message').textContent=error.message;}
}
$('login-tab').addEventListener('click',()=>authTab('login'));
$('register-tab').addEventListener('click',()=>authTab('register'));
$('auth-form').addEventListener('submit',async e=>{
  e.preventDefault();$('submit-auth').disabled=true;$('auth-message').textContent='';
  try{
    if(authMode==='register'&&(!checkedUsernames.registration||checkedUsernames.registration!==normalizedUsername($('username').value)))throw new Error('아이디 중복 확인을 먼저 해 주세요.');
    const result=await api(`/api/${authMode}`,{identifier:$('email').value,email:$('email').value,password:$('password').value,name:$('name').value,username:$('username').value,phone:$('phone').value});
    user=result.user;$('password').value='';await enterAccount();
  }catch(error){(user?(user.onboarding_complete?$('app-message'):$('onboarding-message')):$('auth-message')).textContent=error.message;}
  finally{$('submit-auth').disabled=false;}
});
$('onboarding-form').addEventListener('submit',async e=>{
  e.preventDefault();$('save-profile').disabled=true;$('onboarding-message').textContent='';
  try{
    if(!user.username&&(!checkedUsernames.profile||checkedUsernames.profile!==normalizedUsername($('profile-username').value)))throw new Error('아이디 중복 확인을 먼저 해 주세요.');
    const choice=document.querySelector('input[name="study_focus"]:checked');
    if(!choice)throw new Error('주로 할 공부를 하나 선택해 주세요.');
    const result=await api('/api/profile',{username:$('profile-username').value,phone:$('profile-phone').value,study_focus:choice.value});
    user=result.user;await enterAccount();
  }catch(error){$('onboarding-message').textContent=error.message;}
  finally{$('save-profile').disabled=false;}
});
$('onboarding-logout').addEventListener('click',async()=>{
  try{await api('/api/logout',{});stopCamera();user=null;active=null;studies=[];showAuth();}catch(error){$('onboarding-message').textContent=error.message;}
});
for(const id of ['study-nav','mobile-study'])$(id).addEventListener('click',()=>setView('study'));
for(const id of ['history-nav','mobile-history'])$(id).addEventListener('click',async()=>{setView('history');try{await loadStudies();}catch(error){$('app-message').textContent=error.message;}});
for(const id of ['logout','mobile-logout'])$(id).addEventListener('click',logout);
$('enable-camera').addEventListener('click',enableCamera);
$('toggle-camera').addEventListener('click',()=>stream?stopCamera():enableCamera());
$('start-study').addEventListener('click',()=>mutate('start'));
$('pause-study').addEventListener('click',()=>mutate(active?.status==='break'?'resume':'break'));
$('finish-study').addEventListener('click',()=>mutate('finish'));
$('refresh-history').addEventListener('click',()=>loadStudies().catch(error=>{$('app-message').textContent=error.message;}));
$('history-search').addEventListener('input',renderHistory);
$('real-list').addEventListener('click',e=>{const button=e.target.closest('[data-study]');if(button){selected=button.dataset.study;renderHistory();}});
$('real-detail').addEventListener('click',async e=>{
  if(e.target.id==='first-study')setView('study');
  const button=e.target.closest('[data-demo-action]');
  if(!button)return;
  button.disabled=true;$('app-message').textContent='';
  try{await api(`/api/studies/${selected}/demo`,{action:button.dataset.demoAction});await loadStudies();}
  catch(error){$('app-message').textContent=error.message;}
  finally{button.disabled=false;}
});
window.addEventListener('pagehide',stopCamera);
window.addEventListener('detection-reset',()=>window.studyDetection?.sync(active,!!stream));
window.addEventListener('pageshow',()=>{if(user)loadStudies().catch(error=>{$('app-message').textContent=error.message;});});
document.addEventListener('visibilitychange',()=>{if(!document.hidden&&user)loadStudies().catch(error=>{$('app-message').textContent=error.message;});});
setInterval(tick,250);
async function init(){
  if(location.protocol==='file:'){$('file-notice').hidden=false;return;}
  showAuth();
  try{
    const {googleReady,studyFocuses:focuses}=await api('/api/config');studyFocuses=focuses;
    if(!googleReady){$('google-login').setAttribute('aria-disabled','true');$('google-login').removeAttribute('href');$('google-help').textContent='Google 로그인은 관리자 OAuth 설정 후 사용할 수 있어요. 지금은 이메일로 가입할 수 있습니다.';}
    const errors={google_not_configured:'Google OAuth 설정이 아직 없습니다.',google_failed:'Google 로그인을 완료하지 못했습니다. 다시 시도해 주세요.',existing_email:'이미 이메일로 가입한 계정입니다. 이메일과 비밀번호로 로그인해 주세요.'};
    const error=new URLSearchParams(location.search).get('auth_error');if(error){$('auth-message').textContent=errors[error]||'로그인을 확인해 주세요.';history.replaceState(null,'','/');}
    try{user=(await api('/api/me')).user;}catch{}
    if(user)await enterAccount();
  }catch(error){$('auth-message').textContent='서버에 연결하지 못했습니다. '+error.message;}
}
init();

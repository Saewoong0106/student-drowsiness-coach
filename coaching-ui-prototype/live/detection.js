'use strict';
// Video frames remain inside the browser's Web Worker. Only numerical features
// are sent to the loopback Node/Python service; no image upload or persistence.
window.studyDetection=(()=>{
  let worker=null,sessionId=null,epoch=0,timer=null,audio=null,muted=false,enabled=false;
  let loading=false,lastAlarm=0;
  const get=id=>document.getElementById(id);
  const show=(message,warning=false)=>{
    const node=get('detection-message');if(!node)return;
    node.textContent=message;node.classList.toggle('detection-warning',warning);
  };
  async function armSound(){
    try{
      audio ||= new (window.AudioContext||window.webkitAudioContext)();
      if(audio.state==='suspended')await audio.resume();
      get('sound-status').textContent=audio.state==='running'?'경고음 켜짐':'소리 허용 버튼을 눌러 주세요';
    }catch{get('sound-status').textContent='경고음 미지원 · 화면 경고를 확인해 주세요';}
  }
  function beep(test=false){
    if(muted||!audio||audio.state!=='running')return;
    if(!test&&performance.now()-lastAlarm<800)return;
    lastAlarm=performance.now();
    for(let i=0;i<2;i++){
      const at=audio.currentTime+i*.23,osc=audio.createOscillator(),gain=audio.createGain();
      osc.type='sine';osc.frequency.value=880;
      gain.gain.setValueAtTime(0,at);gain.gain.linearRampToValueAtTime(.12,at+.015);gain.gain.linearRampToValueAtTime(0,at+.16);
      osc.connect(gain);gain.connect(audio.destination);osc.start(at);osc.stop(at+.18);
    }
  }
  async function post(path,data){
    const response=await fetch(path,{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-Study-Request':'1'},body:JSON.stringify(data)});
    const result=await response.json();if(!response.ok)throw Error(result.error||'감지 연결 오류');return result;
  }
  function stop(message='감지 대기 · 카메라를 켜고 공부를 시작해 주세요.'){
    enabled=false;epoch++;clearTimeout(timer);worker?.terminate();worker=null;loading=false;
    sessionId=null;show(message);
  }
  async function frame(generation){
    if(generation!==epoch||!enabled||!worker)return;
    const video=get('camera');
    if(document.hidden||video.readyState<2){timer=setTimeout(()=>frame(generation),150);return;}
    try{
      const bitmap=await createImageBitmap(video,{resizeWidth:640,resizeHeight:Math.max(100,Math.round(video.videoHeight/video.videoWidth*640))});
      if(generation!==epoch||!worker){bitmap.close();return;}
      worker.postMessage({type:'frame',bitmap,timestamp:performance.now()},[bitmap]);
    }catch{stop('감지 중단 · 영상 프레임을 읽지 못했습니다. 카메라를 다시 켜 주세요.');}
  }
  function display(result){
    const phase=result.phase;
    if(phase==='calibrating')show(`눈을 뜨고 정면을 봐 주세요. 개인 보정 중 ${Math.round(result.calibration_progress*100)}%`);
    else if(phase==='calibration_retry')show('보정을 다시 시도합니다. 눈을 뜬 채 정면을 10초 동안 봐 주세요.');
    else if(phase==='unobservable')show('얼굴·눈을 측정할 수 없어요. 화면 안으로 정면을 보여 주세요.');
    else if(result.eye_state==='eyes_closed')show('눈 감김 의심 · 눈을 뜨고 잠깐 쉬어 가세요.',true);
    else if(result.head_state==='head_down')show('고개 숙임 주의 · 자세를 확인해 주세요. 졸음 판정과는 별개입니다.',true);
    else if(result.eye_state==='closing')show('눈 감김 확인 중 · 2초 이상 이어지면 경고해요.');
    else if(result.head_state==='bending')show('고개 숙임 확인 중 · 20° 이상 3초 이어지면 자세를 알려요.');
    else show('감지 중 · 눈 감김 2초 / 고개 숙임 20°·3초 기준');
    get('detection-counts').textContent=`눈 감김 의심 ${result.eye_count}회 · 고개 숙임 ${result.head_count}회`;
    if(result.triggered?.length)beep();
  }
  function sync(study,cameraOn){
    const shouldRun=!!(study&&study.status==='studying'&&cameraOn);
    if(!shouldRun){if(enabled||worker)stop(study?.status==='break'?'휴식 중 · 감지와 경고음이 멈췄어요.':undefined);return;}
    if(enabled&&sessionId===study.id)return;
    stop();enabled=true;sessionId=study.id;const generation=epoch;
    show('얼굴 측정 모델을 준비하고 있어요.');loading=true;
    worker=new Worker('/face-worker.js',{type:'module'});
    worker.onerror=()=>{if(generation===epoch)stop('감지 연결 오류 · 서버 실행과 로컬 MediaPipe 파일을 확인해 주세요.');};
    worker.onmessage=async({data})=>{
      if(generation!==epoch)return;
      if(data.type==='error'){stop(data.message);return;}
      if(data.type==='ready'){loading=false;frame(generation);return;}
      if(data.type==='measurement'){
        try{
          const result=await post(`/api/studies/${sessionId}/detection`,data);
          if(generation!==epoch)return;
          display(result.detection);timer=setTimeout(()=>frame(generation),85);
        }catch(error){if(generation===epoch)stop('감지 중단 · '+error.message);}
      }
    };
    worker.postMessage({type:'init'});
  }
  function bind(){
    get('test-sound')?.addEventListener('click',async()=>{muted=false;await armSound();beep(true);get('mute-sound').textContent='소리 끄기';});
    get('mute-sound')?.addEventListener('click',()=>{muted=!muted;get('mute-sound').textContent=muted?'소리 켜기':'소리 끄기';get('sound-status').textContent=muted?'경고음 꺼짐':'경고음 켜짐';if(!muted)armSound();});
    get('recalibrate')?.addEventListener('click',async()=>{
      if(!sessionId)return;
      const id=sessionId;stop('보정을 다시 준비하고 있어요.');
      try{await post(`/api/studies/${id}/detection/reset`,{});window.dispatchEvent(new Event('detection-reset'));}
      catch(error){show(error.message,true);}
    });
  }
  document.addEventListener('DOMContentLoaded',bind);
  window.addEventListener('pagehide',()=>stop());
  function renderRecord(row){
    const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    const clock=ms=>{const seconds=Math.floor(ms/1000);return [Math.floor(seconds/3600),Math.floor(seconds/60)%60,seconds%60].map(n=>String(n).padStart(2,'0')).join(':');};
    const events=row.detection_events;
    const eye=events.filter(e=>e.type==='eye_closure_suspected').length,head=events.filter(e=>e.type==='head_down').length;
    return `<section class="panel"><div class="detail-header"><div><div class="overline">SESSION REPORT / ${esc(row.id)}</div><h2 class="detail-title">${esc(row.title)}</h2><div class="detail-meta">${new Date(row.started_at).toLocaleString('ko-KR')} · ${row.ended_at?'종료된 기록':'진행 중'}</div></div><span class="badge">감지 기록 연결됨</span></div><div class="metrics"><div class="metric"><div class="metric-label">공부 시간</div><div class="metric-value real-value">${clock(row.study_ms)}</div></div><div class="metric"><div class="metric-label">휴식 시간</div><div class="metric-value real-value">${clock(row.break_ms)}</div></div><div class="metric orange"><div class="metric-label">눈 감김 의심</div><div class="metric-value">${eye}<small>회</small></div></div><div class="metric"><div class="metric-label">고개 숙임</div><div class="metric-value">${head}<small>회</small></div></div></div><div class="observation">관측 가능한 구간에서 확정한 이벤트입니다. 얼굴 가림을 완벽하게 구분하지 못할 수 있으며, 카메라를 끈 구간을 정상으로 간주하지 않습니다.</div></section><section class="panel"><div class="panel-heading"><h2>감지 이벤트 기록</h2><span class="tiny-label">${events.length}건</span></div><div class="records-body"><table><thead><tr><th scope="col">시작 후</th><th scope="col">이벤트</th><th scope="col">확정 시점</th><th scope="col">지속</th></tr></thead><tbody>${events.map(e=>`<tr><td>${clock(e.condition_started_s*1000)}</td><td>${e.type==='head_down'?'고개 숙임 · 자세 주의':'눈 감김 의심'}</td><td>${clock(e.confirmed_s*1000)}</td><td>${e.ended_s===null?'종료 확인 안 됨':Math.max(0,e.ended_s-e.condition_started_s).toFixed(1)+'초'}</td></tr>`).join('')||'<tr><td colspan="4">확정된 이벤트가 없습니다. 보정·미측정 상태와 구분해 해석해 주세요.</td></tr>'}</tbody></table><p class="record-note">공부 시작 후 경과 시간 기준입니다. 관측 가능 시간·미측정 비율은 아직 집계하지 않습니다.</p></div></section><section class="panel unconnected"><h3>학습 코칭 · 연결 예정</h3><p>눈 감김과 고개 숙임은 따로 기록합니다. AI 코칭 모델은 아직 연결되지 않았습니다.</p></section>`;
  }
  return {sync,stop,armSound,renderRecord};
})();

const jobsEl=document.querySelector('#jobs');
const sessionStatus=document.querySelector('#sessionStatus');
const createJob=document.querySelector('#createJob');
let localSession=null;
const esc=s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const mask=s=>s.length<10?'***':`${s.slice(0,4)}…${s.slice(-4)}`;

async function loadStats(){
  try{
    const r=await fetch('/api/analytics/summary');const s=await r.json();
    statDownloads.textContent=s.downloads;statInstalls.textContent=s.installs;statDau.textContent=s.dau;statMau.textContent=s.mau30;
    statSuccess.textContent=s.claimSuccesses;statFeedback.textContent=s.feedbackCount;
  }catch(_){['statDownloads','statInstalls','statDau','statMau','statSuccess','statFeedback'].forEach(id=>document.getElementById(id).textContent='—')}
}

function findSession(har){
  const entries=har?.log?.entries;
  if(!Array.isArray(entries))throw new Error('文件不是有效的 HAR');
  const candidates=entries.filter(e=>/https?:\/\/gw\.[^/]+\/rpc/i.test(e?.request?.url||''));
  if(!candidates.length)throw new Error('没有找到 gw.* /rpc 会话请求');
  const entry=candidates[candidates.length-1];
  const headers=Object.fromEntries((entry.request.headers||[]).map(h=>[String(h.name).toLowerCase(),String(h.value)]));
  const keys=['authorization','cookie','silk_id','x-token','token'];
  const matched=keys.filter(k=>headers[k]);
  if(!matched.length&&Object.keys(headers).length<3)throw new Error('找到了请求，但没有识别到可用会话头');
  return {url:entry.request.url,method:entry.request.method||'POST',headers,postData:entry.request.postData?.text||'',matched};
}

document.querySelector('#parseHar').onclick=async()=>{
  const file=document.querySelector('#harFile').files[0];
  if(!file){sessionStatus.className='session-status bad';sessionStatus.textContent='请先选择一个 .har 文件';return}
  try{
    localSession=findSession(JSON.parse(await file.text()));
    const host=new URL(localSession.url).host;
    const shown=localSession.matched.length?localSession.matched.map(k=>`${k}=${mask(localSession.headers[k])}`).join('，'):`请求头 ${Object.keys(localSession.headers).length} 项`;
    sessionStatus.className='session-status ok';sessionStatus.textContent=`识别成功：${host}/rpc；${shown}。数据仅保存在本页内存。`;
    createJob.disabled=false;
  }catch(err){localSession=null;createJob.disabled=true;sessionStatus.className='session-status bad';sessionStatus.textContent=`识别失败：${err.message}`}
};

async function loadJobs(){const r=await fetch('/api/jobs');const jobs=await r.json();jobsEl.innerHTML=jobs.length?jobs.slice().reverse().map(j=>`<article class="job"><div class="job-top"><strong>${esc(j.couponType)} · ${esc(j.targetTime)}</strong><span class="status">${esc(j.status)}</span></div><p class="muted">目标：${esc(j.targetAt||'计算中')} · 尝试：${j.attempts}</p><button class="secondary" onclick="cancelJob('${j.id}')">停止</button><pre>${esc(j.logs.join('\n')||'等待日志')}</pre></article>`).join(''):'<p class="muted">暂无任务</p>'}
async function cancelJob(id){await fetch(`/api/jobs/${id}/cancel`,{method:'POST'});loadJobs()}
document.querySelector('#jobForm').addEventListener('submit',async e=>{e.preventDefault();if(!localSession){alert('请先导入并识别 HAR 登录态');return}const body={couponType:couponType.value,targetTime:targetTime.value,attempts:+attempts.value,intervalSeconds:+intervalSeconds.value,sessionReady:true};const r=await fetch('/api/jobs',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});if(!r.ok)alert((await r.json()).error);loadJobs()});
document.querySelector('#refresh').onclick=loadJobs;document.querySelector('#refreshStats').onclick=loadStats;loadStats();loadJobs();setInterval(loadJobs,2000);

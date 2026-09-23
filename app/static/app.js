const $ = s => document.querySelector(s);
const escape = s => String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let chosen, session;
function select(file){chosen=file;$('#selected').textContent=file?`${file.name} · ${(file.size/1024/1024).toFixed(1)} MB`:'No file selected';}
$('#file').addEventListener('change',e=>select(e.target.files[0]));
for(const event of ['dragenter','dragover']) $('#drop').addEventListener(event,e=>{e.preventDefault();$('#drop').classList.add('over');});
$('#drop').addEventListener('dragleave',()=>$('#drop').classList.remove('over'));
$('#drop').addEventListener('drop',e=>{e.preventDefault();$('#drop').classList.remove('over');$('#file').files=e.dataTransfer.files;select(e.dataTransfer.files[0]);});
$('#upload-form').addEventListener('submit',e=>{
  e.preventDefault(); if(!chosen || !session)return;
  if(chosen.size>session.max_upload_mb*1024*1024){$('#message').textContent=`Choose a PDF smaller than ${session.max_upload_mb} MB.`;return;}
  $('#submit').disabled=true;
  const params=new URLSearchParams({name:chosen.name,ocr:$('#ocr').checked,languages:$('#languages').value});
  const request=new XMLHttpRequest();request.open('POST',`/api/jobs?${params}`);
  request.setRequestHeader('Content-Type','application/pdf');request.setRequestHeader('X-CSRF-Token',session.csrf);
  request.upload.onprogress=e=>{$('#message').textContent=e.lengthComputable?`Uploading ${Math.round(e.loaded/e.total*100)}%…`:'Uploading…';};
  request.onload=()=>{
    $('#submit').disabled=false;let result;try{result=JSON.parse(request.responseText);}catch{result={detail:'Upload failed. Please check the file size and try again.'};}
    $('#message').textContent=request.status===202?'Upload complete. Your private conversion is queued.':typeof result.detail==='string'?result.detail:'Upload failed.';
    if(request.status===202){$('#file').value='';select(null);refresh();}
  };
  request.onerror=()=>{$('#submit').disabled=false;$('#message').textContent='Upload interrupted. Please try again.';};
  request.send(chosen);
});
async function refresh(){
  try{
    const response=await fetch('/api/jobs');if(!response.ok)throw Error('Unable to load conversions. Refresh to start your session.');
    const jobs=await response.json();$('#count').textContent=jobs.length;
    $('#jobs').innerHTML=jobs.length?jobs.map(j=>`<article class="job"><div><h3>${escape(j.name)}</h3><p>${escape(j.status)} · ${j.done} / ${j.total||'…'} pages ${j.ocr?'· OCR enabled':''}</p><small>Expires ${escape(new Date(j.expires*1000).toLocaleString())}</small>${j.error?`<p class="error">${escape(j.error)}</p>`:''}</div><div class="actions">${j.status==='completed'?`<a target="_blank" rel="noopener" href="/books/${j.id}/index.html">Open book ↗</a><a href="/books/${j.id}/manifest.json" target="_blank" rel="noopener">Manifest</a><a href="/api/jobs/${j.id}/download">Download ZIP ↓</a>`:j.status==='failed'&&j.attempts<3?`<button data-retry="${j.id}">Retry conversion</button>`:`<span class="badge">${j.status==='failed'?'ATTEMPTS EXHAUSTED':'BACKGROUND JOB'}</span>`}<button class="delete" data-delete="${j.id}">Delete</button></div><progress aria-label="Conversion progress" value="${j.done}" max="${j.total||1}"></progress></article>`).join(''):'<div class="empty">Your library starts here. Upload your first PDF above.</div>';
  }catch(e){$('#message').textContent=e.message;}
}
$('#jobs').addEventListener('click',async e=>{
  const id=e.target.dataset.retry||e.target.dataset.delete;if(!id||!session)return;
  const deletion=!!e.target.dataset.delete;
  if(deletion&&!confirm('Delete this conversion and its uploaded PDF? This cannot be undone.'))return;
  try{
    const r=await fetch(`/api/jobs/${id}${deletion?'':'/retry'}`,{method:deletion?'DELETE':'POST',headers:{'X-CSRF-Token':session.csrf}});
    if(!r.ok){const data=await r.json();throw Error(data.detail||'Request failed.');}
    await refresh();
  }catch(e){$('#message').textContent=e.message;}
});
$('#refresh').addEventListener('click',refresh);
async function start(){
  $('#submit').disabled=true;
  try{const r=await fetch('/api/session');if(!r.ok)throw Error('Could not start your session.');session=await r.json();$('#limits').textContent=`Up to ${session.max_upload_mb} MB · ${session.max_pages} pages · Deleted after ${session.retention_hours} hours`;$('#submit').disabled=false;await refresh();setInterval(refresh,4000);}
  catch(e){$('#message').textContent=e.message;}
}
start();

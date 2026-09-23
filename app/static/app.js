const $ = s => document.querySelector(s);
const escape = s => String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let chosen, session, pollTimer;
function select(file){chosen=file;$('#selected').textContent=file?`${file.name} · ${(file.size/1024/1024).toFixed(1)} MB`:'No file selected';}
$('#file').addEventListener('change',e=>select(e.target.files[0]));
for(const event of ['dragenter','dragover']) $('#drop').addEventListener(event,e=>{e.preventDefault();$('#drop').classList.add('over');});
$('#drop').addEventListener('dragleave',()=>$('#drop').classList.remove('over'));
$('#drop').addEventListener('drop',e=>{e.preventDefault();$('#drop').classList.remove('over');$('#file').files=e.dataTransfer.files;select(e.dataTransfer.files[0]);});
$('#upload-form').addEventListener('submit',e=>{
  e.preventDefault(); if(!chosen || !session)return;
  if(chosen.size>session.max_upload_mb*1024*1024){$('#message').textContent=`Choose a PDF smaller than ${session.max_upload_mb} MB.`;return;}
  $('#submit').disabled=true;
  if(session.upload_mode==='chunks'){uploadChunks(chosen);return;}
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
    const jobs=await response.json();
    clearTimeout(pollTimer);
    if(document.visibilityState!=='hidden' && jobs.some(j=>['uploading','dispatching','queued','processing'].includes(j.status)))pollTimer=setTimeout(refresh,15000);
    $('#count').textContent=jobs.length;
    $('#jobs').innerHTML=jobs.length?jobs.map(j=>`<article class="job"><div><h3>${escape(j.name)}</h3><p>${escape(j.status)} · ${j.done} / ${j.total||'…'} pages ${j.ocr?'· OCR enabled':''}</p><small>Expires ${escape(new Date(j.expires*1000).toLocaleString())}</small>${j.error?`<p class="error">${escape(j.error)}</p>`:''}</div><div class="actions">${j.status==='completed'?`<a class="course-link" href="/course/${j.id}">◉ Open as course ▸</a><a target="_blank" rel="noopener" href="/books/${j.id}/index.html">Preview pages ↗</a><a href="/api/jobs/${j.id}/download">Download ZIP ↓</a>`:j.status==='failed'&&j.attempts<3?`<button data-retry="${j.id}">Retry conversion</button>`:`<span class="badge">${j.status==='failed'?'ATTEMPTS EXHAUSTED':'BACKGROUND JOB'}</span>`}<button class="delete" data-delete="${j.id}">Delete</button></div>${j.status==='completed'?`<div class="share">${j.shared?`<span class="share-on">🔗 Shared with students</span><input class="share-url" readonly value="${escape(location.origin)}/learn/${escape(j.shared)}" onclick="this.select()"><button data-copy="${escape(location.origin)}/learn/${escape(j.shared)}">Copy link</button><button class="secondary" data-unshare="${j.id}">Unpublish</button>`:`<button data-share="${j.id}">⤴ Share to students</button><small>Publish a public learner link — students open it and learn with the AI tutor, no login.</small>`}</div>`:''}<progress aria-label="Conversion progress" value="${j.done}" max="${j.total||1}"></progress></article>`).join(''):'<div class="empty">Your library starts here. Upload your first PDF above.</div>';
  }catch(e){$('#message').textContent=e.message;}
}
$('#jobs').addEventListener('click',async e=>{
  const d=e.target.dataset;
  if(d.copy!==undefined){try{await navigator.clipboard.writeText(d.copy);e.target.textContent='Copied!';setTimeout(()=>e.target.textContent='Copy link',1500);}catch{$('#message').textContent='Copy failed — select the link and copy it manually.';}return;}
  if(!session)return;
  if(d.share){try{const r=await fetch(`/api/jobs/${d.share}/share`,{method:'POST',headers:{'X-CSRF-Token':session.csrf}});const data=await r.json();if(!r.ok)throw Error(data.detail||'Could not create the share link.');await refresh();}catch(e){$('#message').textContent=e.message;}return;}
  if(d.unshare){if(!confirm('Unpublish this course? The student link will stop working.'))return;try{const r=await fetch(`/api/jobs/${d.unshare}/share`,{method:'DELETE',headers:{'X-CSRF-Token':session.csrf}});if(r.status!==204){const data=await r.json().catch(()=>({}));throw Error(data.detail||'Could not unpublish.');}await refresh();}catch(e){$('#message').textContent=e.message;}return;}
  const id=d.retry||d.delete;if(!id)return;
  const deletion=!!d.delete;
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
  try{const r=await fetch('/api/session');if(!r.ok)throw Error('Could not start your session.');session=await r.json();$('#limits').textContent=`Up to ${session.max_upload_mb} MB · ${session.max_pages} pages · Access expires after ${session.retention_hours} hours`;$('#submit').disabled=false;await refresh();}
  catch(e){$('#message').textContent=e.message;}
}
start();

async function apiJson(url, options={}){
  const response=await fetch(url,options);
  if(!response.ok){let data={};try{data=await response.json();}catch{}throw Error(data.detail||'Request failed. Please try again.');}
  return response.status===204?null:response.json();
}
async function uploadChunks(file){
  try{
    const headers={'Content-Type':'application/json','X-CSRF-Token':session.csrf};
    const job=await apiJson('/api/uploads',{method:'POST',headers,body:JSON.stringify({name:file.name,size:file.size,ocr:$('#ocr').checked,languages:$('#languages').value})});
    for(let offset=0,index=0;offset<file.size;offset+=session.chunk_bytes,index++){
      const chunk=file.slice(offset,offset+session.chunk_bytes);
      for(let attempt=0;;attempt++){
        try{await apiJson(`/api/uploads/${job.id}/chunks/${index}`,{method:'PUT',headers:{'Content-Type':'application/octet-stream','X-CSRF-Token':session.csrf},body:chunk});break;}
        catch(error){if(attempt>=2)throw error;}
      }
      $('#message').textContent=`Uploading ${Math.round(Math.min(offset+chunk.size,file.size)/file.size*100)}%…`;
    }
    await apiJson(`/api/uploads/${job.id}/complete`,{method:'POST',headers:{'X-CSRF-Token':session.csrf}});
    $('#message').textContent='Upload complete. Your conversion will start when a worker is available.';
    $('#file').value='';select(null);
  }catch(error){$('#message').textContent=error.message;}
  finally{$('#submit').disabled=false;await refresh();}
}
document.addEventListener('visibilitychange',()=>{if(document.visibilityState==='visible'&&session)refresh();else clearTimeout(pollTimer);});

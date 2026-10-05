"use strict";
const $ = id => document.getElementById(id);
const token = new URLSearchParams(location.hash.slice(1)).get("token") || sessionStorage.getItem("avatarforge-token") || "";
if(token) sessionStorage.setItem("avatarforge-token", token);
history.replaceState(null, "", "/");
let preset = "preserve", activeJob = null, polling = false, starting = false, preparingUnity = false, previewUrl = null;
let aiConfig = "";
async function loadAiClients() {
  const project=$("ai-project").value.trim(), data=await api("ai_clients",project?{project}:{});
  aiConfig=JSON.stringify({mcpServers:{avatarforge:data.connection}},null,2);
  $("ai-config").textContent=aiConfig;
  $("ai-clients").replaceChildren();
  for(const client of data.clients) {
    const row=document.createElement("label"), box=document.createElement("input"), text=document.createElement("span");
    row.className="ai-client"; box.type="checkbox"; box.value=client.id; box.checked=client.detected && !!client.config; box.disabled=!client.config;
    text.textContent=client.name+(client.detected?" · detected":"")+(!client.config?" · user scope only":""); row.append(box,text); $("ai-clients").append(row);
  }
}
$("ai-toggle").onclick=()=>guarded(async()=> { $("ai-panel").classList.toggle("hidden"); if(!$("ai-panel").classList.contains("hidden")) await loadAiClients(); });
$("ai-project").onchange=()=>guarded(loadAiClients);
$("ai-copy").onclick=()=>guarded(async()=> { await navigator.clipboard.writeText(aiConfig); $("ai-status").textContent="MCP configuration copied."; });
$("ai-connect").onclick=()=>guarded(async()=> {
  const clients=Array.from($("ai-clients").querySelectorAll("input:checked")).map(box=>box.value);
  if(!clients.length) { $("ai-status").textContent="Choose a client, or copy the generic MCP configuration below."; return; }
  const project=$("ai-project").value.trim(); $("ai-connect").disabled=true; $("ai-status").textContent="Checking local tools and registering clients…";
  try {
    let action=await api("connect_ai",{clients,...(project?{project}:{})});
    while(action.state==="running") { await new Promise(resolve=>setTimeout(resolve,1000)); action=await api("action",{id:action.id}); }
    if(action.state!=="complete") throw new Error(action.error || "AI registration stopped.");
    const result=action.result;
    $("ai-status").textContent=result.clients.map(item=>item.client+": "+(item.state==="registered"?"registered; restart or reload the client":item.error)).join("\n")+"\nLocal MCP verified: "+result.handshake.tool_count+" tools.";
  } finally { $("ai-connect").disabled=false; }
});
async function refreshHistory(restore=false) {
  const jobs=await api("jobs"), select=$("history"); select.replaceChildren();
  if(!jobs.length) { const option=document.createElement("option"); option.value=""; option.textContent="No conversions yet"; select.append(option); return; }
  for(const job of jobs) { const option=document.createElement("option"); option.value=job.id; option.textContent=`${job.source.split(/[\\/]/).pop()} · ${job.preset} · ${job.state}`; select.append(option); }
  if(activeJob) select.value=activeJob.id;
  else if(restore) { select.value=jobs[0].id; await loadJob(jobs[0].id); }
}
async function loadJob(id) {
  if(polling || preparingUnity) return;
  activeJob=await api("job",{id}); $("empty").classList.add("hidden"); $("result").classList.remove("hidden");
  $("preview").classList.add("hidden"); $("unity").disabled=true;
  $("job-state").className="status"; $("metrics").replaceChildren(); $("issues").replaceChildren(); $("integrity").textContent="";
  $("open-unity").classList.add("hidden"); $("open-blender").disabled=activeJob.state!=="complete"; $("physics-panel").classList.add("hidden"); $("unity-status").textContent=""; clearUnityChecks();
  $("cancel").classList.toggle("hidden",!["queued","converting"].includes(activeJob.state));
  $("log").textContent=activeJob.log || "";
  if(["queued","converting"].includes(activeJob.state)) { $("convert").disabled=true; pollJob(); }
  else if(activeJob.state==="complete") await renderReport(activeJob);
  else { $("job-state").textContent=activeJob.state; message(activeJob.error || activeJob.state); }
}
$("history").onchange=()=>guarded(()=>loadJob($("history").value));
async function api(method, args={}) {
  const response = await fetch(`/api/${method}`, {method:"POST", headers:{"Content-Type":"application/json", "Authorization":`Bearer ${token}`},body:JSON.stringify(args)});
  const data = await response.json();
  if(!response.ok) throw new Error(data.error || "Local operation failed.");
  return data;
}
function message(text) { $("message").textContent = text; }
function clearUnityChecks() { $("unity-issues").replaceChildren(); $("unity-checks").classList.add("hidden"); $("unity-checks").open=false; }
async function guarded(fn) { try { await fn(); } catch(error) { message(error.message); } }
function candidates(data) {
  const select = $("model"); select.replaceChildren();
  for(const model of data.models || []) {
    const opt = document.createElement("option"); opt.value = model.path; opt.textContent = model.name; select.append(opt);
  }
  $("convert").disabled = !select.value || polling || starting || preparingUnity;
  $("scan-note").textContent = (data.models || []).length ? `${data.models.length} model candidate${data.models.length === 1 ? "" : "s"}. ${data.models.length > 1 ? "Choose the intended model above." : ""}` : "No supported model file found in this folder.";
}
async function scanSource() {
  message("Inspecting model files…");
  let data = await api("scan", {source:$("source").value.trim().replace(/^\"|\"$/g, "")});
  if(data.archive) { message("Extracting into a new output folder…"); data = await api("extract", {source:data.source}); }
  candidates(data); message("");
}
$("scan").onclick = () => guarded(scanSource);
$("source").addEventListener("keydown", event => { if(event.key === "Enter") guarded(scanSource); });
$("model").onchange = () => $("convert").disabled = !$("model").value || polling || starting || preparingUnity;
for(const kind of ["file","folder"]) $("browse-"+kind).onclick = () => guarded(async () => {
  message("Choose a source in the file dialog…"); const data = await api("browse", {kind});
  if(data.path) { $("source").value = data.path; await scanSource(); } else message("");
});
for(const button of document.querySelectorAll("[data-preset]")) button.onclick = () => {
  preset = button.dataset.preset;
  for(const item of document.querySelectorAll("[data-preset]")) item.classList.toggle("active", item === button);
};
$("tools-toggle").onclick = () => $("tools").classList.toggle("hidden");
async function refreshTools() {
  const data = await api("doctor");
  $("tool-status").textContent = `${data.blender ? "Blender found" : "Blender needed"} · ${data.unity ? "Unity found" : "Unity needed for final import"} · VRChat target ${data.unity_version}`;
  if(!data.blender) $("tools").classList.remove("hidden");
}
async function waitAction(action, target) {
  $(target).textContent = `${action.name}: running…`;
  while(action.state === "running") { await new Promise(resolve => setTimeout(resolve, 1800)); action = await api("action", {id:action.id}); }
  if(action.state === "failed") throw new Error(action.error);
  $(target).textContent = `${action.name}: completed. ${action.result?.report ? "Unity verdict: " + action.result.report.status : ""}`;
  return action;
}
for(const button of document.querySelectorAll("[data-install]")) button.onclick = () => guarded(async () => {
  const buttons=[...document.querySelectorAll("[data-install]")]; buttons.forEach(item=>item.disabled=true);
  try { await waitAction(await api("install", {component:button.dataset.install}), "action-status"); await refreshTools(); }
  finally { buttons.forEach(item=>item.disabled=false); }
});
$("convert").onclick = () => guarded(async () => {
  if(polling || starting || preparingUnity) return;
  starting=true; $("convert").disabled=true;
  try {
  const options = $("options").value.trim() ? JSON.parse($("options").value) : {};
  if($("height").value) options.height = Number($("height").value);
  if(!Object.hasOwn(options,"bake_materials")) options.bake_materials = $("material-mode").value==="auto"?"auto":$("material-mode").value==="bake";
  if($("addon-folder").value.trim()) options.addon_paths = [$("addon-folder").value.trim()];
  activeJob = await api("convert", {source:$("model").value, preset, options, blender:$("blender-exe").value.trim() || undefined});
  await refreshHistory();
  $("convert").disabled = true; message("Conversion started. The source file stays intact.");
  $("empty").classList.add("hidden"); $("result").classList.remove("hidden");
  $("metrics").replaceChildren(); $("issues").replaceChildren(); $("integrity").textContent="Checking model…"; $("preview").classList.add("hidden");
  $("unity-status").textContent=""; clearUnityChecks(); $("physics-panel").classList.add("hidden"); $("physics").replaceChildren(); $("mapping").textContent=""; $("log").textContent="";
  $("job-state").textContent="queued"; $("job-state").className="status"; $("open-blender").disabled=true; $("unity").textContent="Create VRChat Unity project";
  $("cancel").classList.remove("hidden"); $("unity").disabled=true; $("open-unity").classList.add("hidden");
  if(!polling) pollJob();
  } finally { starting=false; $("convert").disabled=polling || preparingUnity || !$("model").value; }
});
async function pollJob() {
  polling=true;
  $("history").disabled=true; $("open-blender").disabled=true;
  try {
    while(activeJob) {
      activeJob = await api("job", {id:activeJob.id});
      $("job-state").textContent=activeJob.state; $("job-state").className="status";
      $("log").textContent=activeJob.log || "Waiting for Blender…";
      if(!["queued","converting"].includes(activeJob.state)) break;
      await new Promise(resolve=>setTimeout(resolve,1500));
    }
    $("cancel").classList.add("hidden");
    $("convert").disabled=!$("model").value;
    if(activeJob.state === "complete") { await renderReport(activeJob); message("Conversion finished. Review the checks before continuing to Unity."); }
    else { $("job-state").classList.add("bad"); message(activeJob.error || "Conversion cancelled; diagnostic output was kept."); }
  } catch(error) { message(error.message); $("convert").disabled=!$("model").value; }
  finally { polling=false; $("history").disabled=false; }
  await refreshHistory();
}
async function renderReport(job) {
  const report=job.report, summary=report.summary || {}, integrity=report.integrity || {};
  $("job-state").textContent=report.status.replaceAll("_"," "); $("job-state").className="status "+(report.status==="ready"?"good":report.status==="blocked"?"bad":"warn");
  $("metrics").replaceChildren();
  for(const [key,label] of [["triangles","Triangles"],["bones","Bones"],["materials","Materials"],["shape_keys","Shape keys"]]) { const box=document.createElement("div"); box.className="metric"; const number=document.createElement("b"); number.textContent=Number(summary[key] || 0).toLocaleString(); const name=document.createElement("span"); name.textContent=label; box.append(number,name); $("metrics").append(box); }
  const missing=(integrity.missing_bones || []).length, shapes=Object.keys(integrity.missing_shape_keys || {}).length, weights=(integrity.missing_weighted_bones || []).length;
  $("integrity").textContent=`FBX round trip: ${missing} missing required bones · ${shapes} missing shape-key groups · ${weights} missing weighted bones. ${job.source_unchanged ? "Source bytes unchanged." : ""}`;
  $("integrity").classList.toggle("bad",missing>0 || shapes>0 || weights>0);
  $("issues").replaceChildren();
  for(const item of report.issues || []) { const row=document.createElement("div"); row.className="issue "+(item.severity || "warning"); row.textContent=item.message; $("issues").append(row); }
  $("mapping").textContent=JSON.stringify({selection:report.selection, humanoid:report.humanoid, missing_required_humanoid:report.missing_required_humanoid, controller_exclusions:report.intentionally_excluded_controller_bones},null,2);
  $("physics").replaceChildren();
  for(const [index,item] of (report.physics || []).entries()) { const row=document.createElement("div"); row.className="physics-item"; const box=document.createElement("input"); box.type="checkbox"; box.id=`physics-${index}`; box.value=item.bone; box.checked=Array.isArray(job.approved_physics)?job.approved_physics.includes(item.bone):item.approved===true; const label=document.createElement("label"); label.htmlFor=box.id; label.textContent=`${item.bone} · ${item.category}`; row.append(box,label); $("physics").append(row); }
  $("physics-panel").classList.toggle("hidden",!(report.physics || []).length);
  $("unity").disabled=report.status==="blocked" || Boolean(job.unity_project);
  $("unity").textContent=job.unity_project?"Unity project created":"Create VRChat Unity project";
  $("open-unity").classList.toggle("hidden",!job.unity_project); $("open-blender").disabled=false;
  $("unity-status").textContent=""; clearUnityChecks();
  if(job.unity_report) {
    const unity=job.unity_report, verdict=unity.status.replaceAll("_"," "), lines=[unity.prefab?`Unity: ${verdict} · ${Number(unity.triangles || 0).toLocaleString()} triangles · ${unity.bones} bones · ${unity.blendshapes} shape keys.`:`Unity: ${verdict} · import incomplete. Open the kept project to repair the reported issue.`];
    if(typeof unity.humanoid_pose_verified==="boolean") lines.push(`Humanoid T-pose calibration: ${unity.humanoid_pose_verified?"verified":"needs repair"}.`);
    if(typeof unity.blendshape_defaults_verified==="boolean") lines.push(`Authored shape-key values: ${unity.blendshape_defaults_verified?"restored":"needs review"}.`);
    if(unity.optimization && unity.optimization.status!=="not_needed" && unity.optimization.target_triangles>0) lines.push(`Optimization: ${unity.optimization.status.replaceAll("_"," ")} · target ${Number(unity.optimization.target_triangles).toLocaleString()} triangles.`);
    if(unity.texture_storage_estimate_available) {
      const storage=unity.texture_memory_bytes<1048576?`${(unity.texture_memory_bytes/1024).toFixed(1)} KiB`:`${(unity.texture_memory_bytes/1048576).toFixed(1)} MiB`;
      lines.push(`Imported texture storage estimate: ${storage} across ${unity.referenced_texture_count} textures. Actual VRAM depends on the running client.`);
    }
    lines.push("Open Unity to review materials, movement and SDK build checks.");
    $("unity-status").textContent=lines.join("\n");
    const sourceIssues=new Set((report.issues || []).map(item=>`${item.code || ""}\u0000${item.message || ""}`));
    const issues=(unity.issues || []).filter(item=>item.severity!=="info" && !sourceIssues.has(`${item.code || ""}\u0000${item.message || ""}`));
    for(const issue of [...issues.filter(item=>item.severity==="error"),...issues.filter(item=>item.severity!=="error")]) {
      const row=document.createElement("div"); row.className="issue "+(issue.severity || "review"); row.textContent=issue.message; $("unity-issues").append(row);
    }
    $("unity-checks").classList.toggle("hidden",!issues.length); $("unity-checks").open=unity.status==="blocked";
    $("unity-checks").querySelector("summary").textContent=`Unity checks and review items (${issues.length})`;
  } else if(job.unity_project) {
    $("unity-status").textContent="Unity project kept. No completed import verdict is available; open the project to review the import log and retry AvatarForge → Import conversion folder.";
  }
  if(report.preview) { const response=await fetch(`/preview/${job.id}`,{headers:{Authorization:`Bearer ${token}`}}); if(response.ok) { if(previewUrl) URL.revokeObjectURL(previewUrl); previewUrl=URL.createObjectURL(await response.blob()); $("preview").src=previewUrl; $("preview").classList.remove("hidden"); } }
}
$("save-physics").onclick=()=>guarded(async()=>{ const bones=[...document.querySelectorAll("#physics input:checked")].map(box=>box.value); await api("approve_physics",{id:activeJob.id,bones}); message(`${bones.length} secondary-motion roots selected for Unity. Review their settings in the editor.`); });
$("cancel").onclick=()=>guarded(()=>api("cancel",{id:activeJob.id}));
for(const kind of ["folder","blender","unity"]) $("open-"+kind).onclick=()=>guarded(()=>api("open",{id:activeJob.id,kind}));
$("unity").onclick=()=>guarded(async()=>{
  if(preparingUnity || polling || !activeJob) return;
  const id=activeJob.id, bones=[...document.querySelectorAll("#physics input:checked")].map(box=>box.value);
  preparingUnity=true; $("unity").disabled=true; $("history").disabled=true; $("convert").disabled=true;
  let failure=null;
  try {
    const action=await api("unity",{id,approved_physics:bones});
    await waitAction(action,"unity-status");
  } catch(error) { failure=error;
  } finally {
    try { if(activeJob?.id===id) { activeJob=await api("job",{id}); await renderReport(activeJob); } }
    catch(error) { if(!failure) failure=error; }
    preparingUnity=false; $("history").disabled=polling; $("convert").disabled=polling || starting || !$("model").value;
    $("unity").disabled=activeJob?.state!=="complete" || Boolean(activeJob?.unity_project) || activeJob?.report?.status==="blocked";
  }
  if(failure) throw failure;
});
guarded(refreshTools);
guarded(()=>refreshHistory(true));

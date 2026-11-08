/* DATA CLEAN UI — the API remains the authority for analysis and cleaning. */
'use strict';
const API_BASE = 'http://127.0.0.1:8000';
const $ = id => document.getElementById(id);
const state = {file:null, analysis:null, result:null, mode:'guided', stage:'upload', busy:false,
  guidedSelected:new Set(), expertSelected:new Set(), settings:new Map()};
const actionLabels = {trim_whitespace:'Trim extra spaces', standardize_null_tokens:'Standardize missing-value labels',
  normalize_casing:'Standardize text formatting', fill_nulls:'Handle missing values', drop_duplicates:'Remove duplicate rows',
  coerce_numeric:'Convert text to numbers', parse_date:'Standardize dates', handle_outliers:'Review potential outliers', contextual_fill:'Fill using related values'};
const escapeHtml = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
const number = value => typeof value === 'number' ? value.toLocaleString('en') : (value ?? '—');
const plan = () => state.analysis?.cleaning_plan || [];
const recommended = step => step.executable !== false && step.risk !== 'HIGH IMPACT' && step.auto_eligible === true;
const isKeep = (step, strategy) => ['fill_nulls','handle_outliers'].includes(step.action) && (strategy || 'keep') === 'keep';
const guidedEligible = step => recommended(step) && !isKeep(step, step.strategy) && !(step.action === 'fill_nulls' && step.strategy === 'constant');
const riskLabel = step => step.risk === 'HIGH IMPACT' ? 'High impact' : step.risk === 'SAFE' ? 'Safe' : 'Review';
const riskHtml = step => `<span class="risk ${step.risk === 'HIGH IMPACT' ? 'high' : step.risk === 'SAFE' ? 'safe' : 'review'}">${riskLabel(step)}</span>`;

function navigate() {
  const page = ['about','workspace','guide'].includes(location.hash.slice(1)) ? location.hash.slice(1) : 'about';
  document.querySelectorAll('.page').forEach(el => el.hidden = el.id !== `page-${page}`);
  document.querySelectorAll('[data-page]').forEach(el => {
    if (el.dataset.page === page) el.setAttribute('aria-current','page'); else el.removeAttribute('aria-current');
  });
  document.title = `${page === 'about' ? 'About DATA CLEAN' : page === 'guide' ? 'User Guide' : 'Workspace'} · DATA CLEAN`;
  setMenu(false);
  $(`${page === 'about' ? 'about' : page === 'guide' ? 'guide' : 'workspace'}Title`).focus({preventScroll:true});
}
window.addEventListener('hashchange', navigate);
function setMenu(open) {
  $('sidebar').classList.toggle('open',open);
  $('menuToggle').setAttribute('aria-expanded',String(open));
  $('sidebar').inert = matchMedia('(max-width:780px)').matches && !open;
}
matchMedia('(max-width:780px)').addEventListener('change',() => setMenu(false));
$('menuToggle').addEventListener('click', () => {
  setMenu(!$('sidebar').classList.contains('open'));
});
document.addEventListener('keydown', event => {if (event.key === 'Escape' && $('sidebar').classList.contains('open')) {
  setMenu(false); $('menuToggle').focus();
}});

function clearStatus() {$('status').hidden = true; $('errorDetails').hidden = true; $('errorText').textContent = '';}
function status(message, error) {
  $('status').hidden = false; $('status').classList.toggle('error',Boolean(error)); $('statusText').textContent = message;
  $('errorDetails').hidden = !error; $('errorDetails').open = false; $('errorText').textContent = error ? String(error.message || error) : '';
}
async function readResponse(response) {
  const raw = await response.text(); let data;
  try {data = JSON.parse(raw);} catch (_) {throw new Error(`The server returned an unreadable response (HTTP ${response.status}).`);}
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail || data));
  if (data.status !== 'success') throw new Error('The server did not return a successful result.');
  return data;
}
function downloadUrl(path) {
  // API download paths may never redirect the user to an external origin.
  if (typeof path !== 'string' || !path.startsWith('/download/') || path.startsWith('//')) throw new Error('Invalid download path returned by the server.');
  return API_BASE + path;
}
function stage(next) {
  state.stage = next;
  ['upload','review','result'].forEach(name => $(`${name}Panel`).hidden = name !== next);
  $('workspaceEyebrow').textContent = `WORKSPACE / STEP ${next === 'upload' ? 1 : next === 'review' ? 2 : 3} OF 3`;
  $('workspaceTitle').textContent = next === 'upload' ? 'Start with your file.' : next === 'review' ? 'Review your data.' : 'Your results are ready.';
  ['stepUpload','stepReview','stepDownload'].forEach((id,i) => {
    const current = next === 'upload' ? 0 : next === 'review' ? 1 : 2;
    $(id).classList.toggle('complete',i < current);
    if (i === current) $(id).setAttribute('aria-current','step'); else $(id).removeAttribute('aria-current');
  });
}
function setBusy(active, text='Working…') {
  state.busy = active; $('loading').hidden = !active; $('loadingText').textContent = text;
  $('page-workspace').setAttribute('aria-busy',String(active));
  document.querySelectorAll('#page-workspace button, #page-workspace input, #page-workspace select').forEach(el => {
    if (active) {el.dataset.wasDisabled = String(el.disabled); el.disabled = true;}
    else if (el.dataset.wasDisabled !== undefined) {el.disabled = el.dataset.wasDisabled === 'true'; delete el.dataset.wasDisabled;}
  });
  if (!active) {updateSelection(); $('analyzeButton').disabled = !state.file;}
}
function setMode(mode) {
  if (state.busy) return;
  state.mode = mode;
  $('guidedMode').setAttribute('aria-pressed',String(mode === 'guided'));
  $('expertMode').setAttribute('aria-pressed',String(mode === 'expert'));
  $('guidedPanel').hidden = mode !== 'guided'; $('expertPanel').hidden = mode !== 'expert';
  $('planTitle').textContent = mode === 'guided' ? 'Recommended changes' : 'Review cleaning actions';
  $('modeDescription').textContent = mode === 'guided' ? 'Review the suggestions and choose what to apply.' : 'Select individual actions and adjust how they are applied.';
  updateSelection();
}
$('guidedMode').addEventListener('click',() => setMode('guided'));
$('expertMode').addEventListener('click',() => setMode('expert'));

function chooseFile(file) {
  if (state.busy || !file) return;
  if (!/\.(csv|xlsx|xls)$/i.test(file.name)) {status('Choose a CSV, XLSX, or XLS file to continue.'); return;}
  if (!file.size) {status('This file is empty. Choose a file containing data.'); return;}
  clearStatus(); state.file = file;
  const size = file.size < 1024 * 1024 ? `${(file.size / 1024).toFixed(1)} KB` : `${(file.size / 1024 / 1024).toFixed(1)} MB`;
  $('selectedFile').textContent = `${file.name} · ${size}`; $('analyzeButton').disabled = false;
}
$('fileInput').addEventListener('change',event => {chooseFile(event.target.files[0]); event.target.value = '';});
$('dropzone').addEventListener('dragover',event => {event.preventDefault(); if (!state.busy) $('dropzone').classList.add('drag');});
$('dropzone').addEventListener('dragleave',() => $('dropzone').classList.remove('drag'));
$('dropzone').addEventListener('drop',event => {event.preventDefault(); $('dropzone').classList.remove('drag'); chooseFile(event.dataTransfer.files[0]);});
document.querySelectorAll('.replace-button').forEach(button => button.addEventListener('click',() => {
  if (state.busy) return;
  clearStatus(); state.file = null; $('selectedFile').textContent = 'No file selected'; $('analyzeButton').disabled = true;
  $('cancelReplace').hidden = !state.analysis; stage('upload'); $('workspaceTitle').focus();
}));
$('cancelReplace').addEventListener('click',() => {clearStatus(); stage(state.result ? 'result' : 'review');});
$('backToReview').addEventListener('click',() => {clearStatus(); stage('review'); $('workspaceTitle').focus();});

$('analyzeButton').addEventListener('click',async () => {
  if (state.busy || !state.file) return;
  clearStatus(); setBusy(true,'Analyzing your file and preparing recommendations…');
  const form = new FormData(); form.append('file',state.file);
  try {
    const data = await readResponse(await fetch(`${API_BASE}/analyze`,{method:'POST',body:form}));
    if (!data.filename || !data.analysis || !Array.isArray(data.cleaning_plan)) throw new Error('Incomplete analysis response.');
    const planLink = downloadUrl(data.plan_download_url);
    state.analysis = data; state.result = null; state.guidedSelected.clear(); state.expertSelected.clear(); state.settings.clear();
    plan().forEach((step,i) => {
      if (guidedEligible(step)) {state.guidedSelected.add(i); state.expertSelected.add(i);}
      state.settings.set(i,{strategy:step.strategy || 'keep',target_case:step.strategy || 'title',constant:''});
    });
    renderAnalysis(); renderPlan(); $('planDownload').href = planLink; stage('review');
    status('Analysis complete. Review the suggested changes before applying them.'); $('workspaceTitle').focus();
  } catch (error) {status('Analysis could not finish. Check the file and try again. Make sure the local API is running.',error);}
  finally {setBusy(false);}
});

function renderAnalysis() {
  const data = state.analysis, profile = data.analysis || {}, dup = profile.duplicates || {};
  const missing = Object.values(profile.missing_values || {}).reduce((sum,item) => sum + Number(item?.count || 0),0);
  const cols = Object.keys(profile.schema || {}).length || Object.keys(data.preview?.[0] || {}).length;
  $('fileName').textContent = data.filename;
  $('fileMeta').textContent = `${number(dup.total_rows)} rows / ${number(cols)} columns / ${number(missing)} missing values / ${number(dup.duplicate_rows || 0)} duplicates`;
  const proc = data.processing || {};
  $('processingNote').hidden = !proc.large_dataset_mode;
  $('processingNote').textContent = `Large dataset mode: selected semantic checks use a sample${proc.sample_rows ? ` of ${number(proc.sample_rows)} rows` : ''}. Core counts and cleaning use the full dataset.`;
  const metrics = [['Rows',dup.total_rows],['Columns',cols],['Missing values',missing],['Duplicate rows',dup.duplicate_rows || 0],
    ['Potential outliers',Object.values(profile.outliers || {}).reduce((sum,item) => sum + Number(item?.count || 0),0)],['Sensitive columns',profile.privacy?.count || 0]];
  $('qualityMetrics').innerHTML = metrics.map(([label,value]) => `<div><dt>${label}</dt><dd>${escapeHtml(number(value))}</dd></div>`).join('');
  const integrity = data.integrity || {};
  $('integrityText').textContent = `${integrity.integrity_status || 'No integrity status returned'}. Protected identifiers: ${(integrity.detected_primary_keys || []).join(', ') || 'none detected'}.`;
  renderTable('dataPreview',data.preview,'No preview returned.');
}
function optionsFor(step) {
  const options = (step.strategy_options || []).map(option => typeof option === 'string' ? {value:option,label:option} : option);
  if (!options.length && ['fill_nulls','handle_outliers'].includes(step.action)) options.push({value:step.strategy || 'keep',label:step.strategy || 'Keep unchanged'});
  if (step.action === 'normalize_casing' && !options.length) options.push(...['title','lower','upper'].map(value => ({value,label:value})));
  return options;
}
function settingsHtml(step,i) {
  const config = state.settings.get(i);
  const disabled = step.executable === false ? 'disabled' : '';
  if (['fill_nulls','handle_outliers','normalize_casing'].includes(step.action)) {
    const key = step.action === 'normalize_casing' ? 'target_case' : 'strategy';
    const options = optionsFor(step).map(option => `<option value="${escapeHtml(option.value)}" ${config[key] === option.value ? 'selected' : ''}>${escapeHtml(option.label || option.value)}</option>`).join('');
    return `<label for="setting-${i}">Strategy</label><select id="setting-${i}" data-setting="${key}" data-index="${i}" ${disabled}>${options}</select>` +
      (step.action === 'fill_nulls' ? `<div class="constant-field" id="constant-wrap-${i}" ${config.strategy !== 'constant' ? 'hidden' : ''}><label for="constant-${i}">Constant value</label><input id="constant-${i}" data-setting="constant" data-index="${i}" type="text" value="${escapeHtml(config.constant)}" ${disabled}></div>` : '');
  }
  return `<span class="subtle">${escapeHtml(step.recommended_label || (step.action === 'parse_date' ? 'Recognized dates' : 'Default settings'))}</span>`;
}
function renderPlan() {
  const steps = plan(); const guided = steps.map((step,i) => ({step,i})).filter(({step}) => guidedEligible(step));
  $('guidedActions').innerHTML = guided.length ? guided.map(({step,i}) => `<div class="action-row"><input type="checkbox" id="guided-${i}" data-guided="${i}" ${state.guidedSelected.has(i) ? 'checked' : ''}><div><label for="guided-${i}">${escapeHtml(actionLabels[step.action] || step.description)} <span class="column-name">/ ${escapeHtml(step.column)}</span></label><p>${escapeHtml(step.guided_explanation?.summary || step.reason || step.recommended_label)}</p></div>${riskHtml(step)}</div>`).join('') : '<p class="empty-note">No changes are eligible for Guided Mode. View the analysis or switch to Expert Mode to review other actions.</p>';
  const other = steps.length - guided.length;
  $('guidedNote').textContent = other ? `${other} other recommendation${other === 1 ? '' : 's'} ${other === 1 ? 'is' : 'are'} available in Expert Mode, including actions that keep values unchanged or need individual review.` : 'Only the selected recommendations will be applied.';
  $('reviewReminder').textContent = guided.some(({step}) => step.risk === 'REVIEW') ? 'Some recommendations need review. Read their effect before keeping them selected.' : 'Open the analysis below to inspect your data before continuing.';
  $('expertActions').innerHTML = steps.map((step,i) => `<tr><td><input type="checkbox" aria-label="Apply ${escapeHtml(actionLabels[step.action] || step.action)} to ${escapeHtml(step.column)}" data-expert="${i}" ${state.expertSelected.has(i) ? 'checked' : ''} ${step.executable === false ? 'disabled' : ''}></td><td>${escapeHtml(step.column)}</td><td>${escapeHtml(actionLabels[step.action] || step.description)}<p>${escapeHtml(step.expert_explanation?.summary || step.reason)}</p>${step.executable === false ? '<p>Not executable</p>' : ''}</td><td>${settingsHtml(step,i)}</td><td>${riskHtml(step)}</td></tr>`).join('');
  $('noActions').hidden = steps.length > 0;
  updateSelection();
}
function actionFromStep(step,i,mode=state.mode) {
  const settings = state.settings.get(i) || {};
  const strategy = mode === 'guided' ? step.strategy : settings.strategy;
  if (step.executable === false || isKeep(step,strategy)) return null;
  if (mode === 'guided' && !guidedEligible(step)) return null;
  const action = {action:step.action,column:step.column};
  if (step.action === 'fill_nulls' && strategy === 'contextual_median') {
    return {action:'contextual_fill',column:step.column,context_column:step.context_column,context_values:step.context_values || [],statistic:step.statistic || 'median',round_result:Boolean(step.round_result)};
  }
  if (step.action === 'fill_nulls') {
    action.strategy = strategy;
    if (strategy === 'constant') {
      const raw = settings.constant ?? '';
      const dtype = state.analysis.analysis?.schema?.[step.column] || '';
      if (/int|float|decimal/i.test(dtype)) {
        const value = Number(raw);
        if (!raw.trim() || !Number.isFinite(value)) throw new Error(`Enter a numeric constant for ${step.column}.`);
        action.fill_value = value;
      } else action.fill_value = raw;
    }
  }
  if (step.action === 'normalize_casing') action.target_case = mode === 'guided' ? (step.strategy || 'title') : settings.target_case;
  if (step.action === 'handle_outliers') Object.assign(action,{strategy,lower_bound:step.lower_bound,upper_bound:step.upper_bound});
  if (step.action === 'parse_date' && step.date_format) action.date_format = step.date_format;
  return action;
}
function selectedActions() {
  const selection = state.mode === 'guided' ? state.guidedSelected : state.expertSelected;
  return plan().flatMap((step,i) => selection.has(i) ? [actionFromStep(step,i)].filter(Boolean) : []);
}
function updateSelection() {
  let count = 0;
  // Count actions without validating user constants until Apply is pressed.
  const selection = state.mode === 'guided' ? state.guidedSelected : state.expertSelected;
  plan().forEach((step,i) => {if (selection.has(i) && step.executable !== false && !isKeep(step,state.mode === 'guided' ? step.strategy : state.settings.get(i)?.strategy)) count++;});
  $('selectionCount').textContent = `${number(count)} change${count === 1 ? '' : 's'} selected`;
  $('applyButton').textContent = count ? `Apply ${number(count)} change${count === 1 ? '' : 's'} →` : 'No changes selected';
  $('applyButton').disabled = state.busy || !count;
}
$('guidedActions').addEventListener('change',event => {
  if (event.target.dataset.guided === undefined) return;
  const i = Number(event.target.dataset.guided); if (event.target.checked) state.guidedSelected.add(i); else state.guidedSelected.delete(i); updateSelection();
});
$('expertActions').addEventListener('change',event => {
  if (event.target.dataset.expert !== undefined) {
    const i = Number(event.target.dataset.expert); if (event.target.checked) state.expertSelected.add(i); else state.expertSelected.delete(i);
  }
  if (event.target.dataset.setting) {
    const i = Number(event.target.dataset.index), key = event.target.dataset.setting;
    state.settings.get(i)[key] = event.target.value;
    if (key === 'strategy' && $(`constant-wrap-${i}`)) $(`constant-wrap-${i}`).hidden = event.target.value !== 'constant';
  }
  updateSelection();
});
$('expertActions').addEventListener('input',event => {
  if (event.target.dataset.setting === 'constant') state.settings.get(Number(event.target.dataset.index)).constant = event.target.value;
});
function selectExpert(predicate) {
  state.expertSelected.clear(); plan().forEach((step,i) => {if (step.executable !== false && predicate(step)) state.expertSelected.add(i);});
  document.querySelectorAll('[data-expert]').forEach(input => input.checked = state.expertSelected.has(Number(input.dataset.expert))); updateSelection();
}
$('selectRecommended').addEventListener('click',() => selectExpert(recommended));
$('selectSafe').addEventListener('click',() => selectExpert(step => step.risk === 'SAFE'));
$('clearSelection').addEventListener('click',() => selectExpert(() => false));

$('applyButton').addEventListener('click',async () => {
  if (state.busy || !state.analysis) return;
  let actions;
  try {actions = selectedActions();} catch (error) {status(error.message); return;}
  if (!actions.length) {status('Select at least one change to apply.'); return;}
  clearStatus(); state.result = null; setBusy(true,'Applying selected changes and validating the result…');
  try {
    const data = await readResponse(await fetch(`${API_BASE}/clean`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({filename:state.analysis.filename,actions})}));
    renderResults(data); state.result = data; stage('result'); $('workspaceTitle').focus();
  } catch (error) {status('Cleaning could not finish. Your original file is unchanged. Review the details and try again.',error);}
  finally {setBusy(false);}
});
function renderResults(data) {
  const file = downloadUrl(data.download_url), report = downloadUrl(data.report_download_url), lineage = downloadUrl(data.lineage_download_url);
  const v = data.validation || {};
  $('resultFileName').textContent = data.cleaned_file || 'Cleaned file';
  $('resultMessage').textContent = data.processing?.export_mode === 'fast_csv' ? 'Your cleaned file is ready. This large dataset was exported as CSV for faster output.' : 'Your cleaned file is ready. Review the checks below before using it.';
  $('resultMetrics').innerHTML = [['Rows',v.rows_before,v.rows_after],['Missing values',v.missing_before,v.missing_after],['Duplicates',v.duplicates_before,v.duplicates_after]].map(([label,before,after]) => `<div><dt>${label} / before → after</dt><dd>${escapeHtml(number(before))} → ${escapeHtml(number(after))}</dd></div>`).join('');
  const passed = v.status === 'PASS';
  $('validationMessage').textContent = `Validation: ${v.status || 'not provided'}. ${passed ? 'The reported structural checks passed. Verify that the changes match your expectations.' : 'Review the validation report before using this file.'}`;
  $('validationMessage').classList.toggle('warning',!passed);
  $('downloadFile').href = file; $('downloadReport').href = report; $('downloadLineage').href = lineage;
  $('validationDetails').textContent = JSON.stringify(v,null,2);
  renderTable('appliedActions',(data.applied_actions || []).map(a => ({Column:a.column,Action:actionLabels[a.action] || a.action,'Affected values or rows':a.changed,Strategy:a.strategy || '—'})),'No actions were reported.');
  renderTable('cleanedPreview',data.preview,'No cleaned preview returned.');
}
function renderTable(id,rows,empty) {
  if (!Array.isArray(rows) || !rows.length) {$(id).innerHTML = `<p class="empty-table">${escapeHtml(empty)}</p>`; return;}
  const columns = Object.keys(rows[0]);
  $(id).innerHTML = `<div class="table-scroll" role="region" aria-label="${escapeHtml(id === 'dataPreview' ? 'Original data preview' : id === 'cleanedPreview' ? 'Cleaned data preview' : 'Applied actions')}" tabindex="0"><table class="data-table"><thead><tr>${columns.map(c => `<th scope="col">${escapeHtml(c)}</th>`).join('')}</tr></thead><tbody>${rows.map(row => `<tr>${columns.map(c => `<td>${row[c] === null ? '<span class="null">Missing</span>' : escapeHtml(typeof row[c] === 'object' ? JSON.stringify(row[c]) : row[c])}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;
}
navigate(); setMode('guided'); stage('upload');

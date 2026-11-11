/* DATA CLEAN UI — the API remains the authority for analysis and cleaning. */
'use strict';
const API_BASE = 'http://127.0.0.1:8000';
const apiFetch = (url, options={}) => fetch(url, {...options,credentials:'include'});
const $ = id => document.getElementById(id);
const state = {file:null, analysis:null, result:null, inspection:null, preview:null, readingDirty:false, previewDirty:false, mode:'guided', stage:'upload', busy:false,
  loadedPlanId:null, decisions:null, rules:[], decisionsDirty:false, groups:[], recalled:[], savedPlans:[], guidedSelected:new Set(), expertSelected:new Set(), settings:new Map()};
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
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : data.detail?.message || JSON.stringify(data.detail || data));
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
  $("effectPanel").hidden = true;
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
  if (!active) {updateSelection(); syncReadingButtons();}
  document.querySelectorAll("#page-workspace textarea").forEach(el => el.disabled = active);
}
function setMode(mode) {
  if (state.busy) return;
  state.mode = mode;
  $("effectPanel").hidden = true;
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
  clearStatus(); state.file = file; state.inspection = null; state.preview = null; state.readingDirty = false;
  $('readingPanel').hidden = true; $('confirmReading').checked = false;
  const size = file.size < 1024 ? `${file.size} bytes` : file.size < 1024 * 1024 ? `${(file.size / 1024).toFixed(1)} KB` : `${(file.size / 1024 / 1024).toFixed(1)} MB`;
  $('selectedFile').textContent = `${file.name} · ${size}`; syncReadingButtons();
}
$('fileInput').addEventListener('change',event => {chooseFile(event.target.files[0]); event.target.value = '';});
$('dropzone').addEventListener('dragover',event => {event.preventDefault(); if (!state.busy) $('dropzone').classList.add('drag');});
$('dropzone').addEventListener('dragleave',() => $('dropzone').classList.remove('drag'));
$('dropzone').addEventListener('drop',event => {event.preventDefault(); $('dropzone').classList.remove('drag'); chooseFile(event.dataTransfer.files[0]);});
document.querySelectorAll('.replace-button').forEach(button => button.addEventListener('click',() => {
  if (state.busy) return;
  clearStatus(); state.file = null; state.inspection = null; state.preview = null; $('readingPanel').hidden = true; $('selectedFile').textContent = 'No file selected'; syncReadingButtons();
  $('fileChoice').hidden = false;
  $('cancelReplace').hidden = !state.analysis; stage('upload'); $('workspaceTitle').focus();
}));
$('cancelReplace').addEventListener('click',() => {clearStatus(); stage(state.result ? 'result' : 'review');});
$('backToReview').addEventListener('click',() => {clearStatus(); stage('review'); $('workspaceTitle').focus();});

$('inspectButton').addEventListener('click',async () => {
  if (state.busy || !state.file) return;
  clearStatus(); setBusy(true,'Reading your file and preparing a preview…');
  const form = new FormData(); form.append('file',state.file);
  try {
    const data = await readResponse(await apiFetch(`${API_BASE}/inspect`,{method:'POST',body:form}));
    acceptInspection(data);
  } catch (error) {status('The preview could not load. Make sure the API is running and try again.',error);}
  finally {setBusy(false);}
});

$('analyzeButton').addEventListener('click',async () => {
  if (state.busy || !state.inspection?.valid || state.readingDirty || !$('confirmReading').checked) return;
  clearStatus(); setBusy(true,'Analyzing the confirmed interpretation…');
  try {
    const data = await readResponse(await apiFetch(`${API_BASE}/datasets/${state.inspection.dataset_id}/analyze`,{
      method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({revision:state.inspection.revision})}));
    if (!data.filename || !data.analysis || !Array.isArray(data.cleaning_plan)) throw new Error('Incomplete analysis response.');
    const planLink = downloadUrl(data.plan_download_url);
    state.analysis = data; state.result = null; state.guidedSelected.clear(); state.expertSelected.clear(); state.settings.clear();
    plan().forEach((step,i) => {
      if (guidedEligible(step)) {state.guidedSelected.add(i); state.expertSelected.add(i);}
      state.settings.set(i,{strategy:step.strategy || 'keep',target_case:step.strategy || 'title',constant:''});
    });
    renderAnalysis(); renderPlan(); initQualityTools(); $('planDownload').href = planLink; stage('review');
    status('Analysis complete. Review the suggested changes before applying them.'); $('workspaceTitle').focus();
  } catch (error) {status('Analysis could not finish. Check the file and try again. Make sure the local API is running.',error);}
  finally {setBusy(false);}
});

function renderAnalysis() {
  const data = state.analysis, profile = data.analysis || {}, dup = profile.duplicates || {};
  const missing = Object.values(profile.missing_values || {}).reduce((sum,item) => sum + Number(item?.count || 0),0);
  const cols = Object.keys(profile.schema || {}).length || Object.keys(data.preview?.[0] || {}).length;
  $('fileName').textContent = data.display_filename || data.filename;
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
  return [...plan().flatMap((step,i) => selection.has(i) ? [actionFromStep(step,i)].filter(Boolean) : []), ...state.recalled.filter(a => a.selected).map(({selected,...a}) => a), ...categoryActions()];
}
function updateSelection() {
  let count = 0;
  // Count actions without validating user constants until Apply is pressed.
  const selection = state.mode === 'guided' ? state.guidedSelected : state.expertSelected;
  plan().forEach((step,i) => {if (selection.has(i) && step.executable !== false && !isKeep(step,state.mode === 'guided' ? step.strategy : state.settings.get(i)?.strategy)) count++;});
  count += state.recalled.filter(a => a.selected).length + categoryActions().length;
  $('selectionCount').textContent = `${number(count)} change${count === 1 ? '' : 's'} selected`;
  $('applyButton').textContent = count ? `Apply ${number(count)} change${count === 1 ? '' : 's'} →` : 'Finish without changes →';
  $('applyButton').disabled = state.busy || !state.analysis || state.decisionsDirty;
  $('previewEffects').disabled = state.busy || !state.analysis || state.decisionsDirty;
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
  $("effectPanel").hidden = true;
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
  if (state.decisionsDirty) {status('Confirm the updated meanings and rules first.'); return;}
  clearStatus(); state.result = null; setBusy(true,'Applying selected changes and validating the result…');
  try {
    const data = await readResponse(await apiFetch(`${API_BASE}/clean`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({filename:state.analysis.filename,actions,dataset_id:state.analysis.dataset_id,reading_revision:state.analysis.reading_revision,decision_revision:state.decisions?.revision})}));
    renderResults(data); state.result = data; stage('result'); $('workspaceTitle').focus();
  } catch (error) {status('Cleaning could not finish. Your original file is unchanged. Review the details and try again.',error);}
  finally {setBusy(false);}
});
function renderResults(data) {
  const file = downloadUrl(data.download_url), report = downloadUrl(data.report_download_url), lineage = downloadUrl(data.lineage_download_url);
  const v = data.validation || {};
  $('resultFileName').textContent = data.display_cleaned_file || data.cleaned_file || 'Cleaned file';
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
function syncReadingButtons() {
  $('inspectButton').disabled = state.busy || !state.file;
  $('analyzeButton').disabled = state.busy || !state.inspection?.valid || state.readingDirty || !$('confirmReading').checked;
  $('updateReading').disabled = state.busy || !state.inspection;
  $('previewPrevious').disabled = state.busy || !state.preview || state.preview.offset === 0;
  $('previewNext').disabled = state.busy || !state.preview || state.preview.offset + state.preview.limit >= state.preview.matched_rows;
  $('readingDirtyNote').hidden = !state.readingDirty;
}
function selectValue(id,value) {
  const select = $(id), text = value ?? '';
  if (!Array.from(select.options).some(option => option.value === text)) select.add(new Option(text,text));
  select.value = text;
}
function acceptInspection(data) {
  $('retentionNote').textContent = `Files expire ${data.expires_at ? new Date(data.expires_at).toLocaleString() : 'after the configured retention period'}. Your original is preserved until expiry. Nothing is cleaned until you apply changes.`;
  state.inspection = data; state.preview = data.preview; state.readingDirty = false; state.previewDirty = false;
  $('effectPanel').hidden = true;
  state.analysis = null; state.result = null; $('cancelReplace').hidden = true;
  $('confirmReading').checked = false; $('readingPanel').hidden = false;
  $('fileChoice').hidden = true; $('workspaceTitle').textContent = 'Check your file.';
  $('readingFile').textContent = data.original_filename;
  const csv = data.filename.toLowerCase().endsWith('.csv'), options = data.reading_options || {};
  $('delimiterField').hidden = !csv; $('encodingField').hidden = !csv; $('worksheetField').hidden = csv;
  selectValue('readingDelimiter',options.delimiter); selectValue('readingEncoding',options.encoding);
  $('readingHeader').value = options.header_row || 1;
  $('readingSheet').innerHTML = (data.worksheets || []).map(name => `<option value="${escapeHtml(name)}">${escapeHtml(name)}</option>`).join('');
  if (options.sheet_name) $('readingSheet').value = options.sheet_name;
  $('readingWarnings').innerHTML = (data.warnings || []).map(text => `<li>${escapeHtml(text)}</li>`).join('');
  $('readingError').hidden = data.valid; $('readingErrorText').textContent = data.error || '';
  $('readingIssues').hidden = !data.issues?.length; $('readingIssues').textContent = JSON.stringify(data.issues || [],null,2);
  $('readingData').hidden = !data.valid;
  if (data.valid) {
    $('previewSearch').value = ''; $('previewDirection').value = 'asc';
    const columns = data.preview.schema;
    $('previewSort').innerHTML = '<option value="">Original order</option>' + columns.map(c => `<option value="${escapeHtml(c.name)}">${escapeHtml(c.name)}</option>`).join('');
    $('previewMissing').innerHTML = '<option value="">All rows</option><option value="*">Any column has missing values</option>' + columns.map(c => `<option value="${escapeHtml(c.name)}">${escapeHtml(c.name)} has missing values</option>`).join('');
    $('previewColumns').innerHTML = columns.map((c,i) => `<label><input type="checkbox" data-preview-column="${escapeHtml(c.name)}" checked> ${escapeHtml(c.name)}</label>`).join('');
    renderReadingPreview(data.preview);
  }
  syncReadingButtons();
}
function renderReadingPreview(data) {
  state.preview = data;
  const from = data.shown_rows ? data.offset + 1 : 0, to = data.offset + data.shown_rows;
  $('previewSummary').textContent = `Showing ${number(from)}–${number(to)} of ${number(data.matched_rows)} matching rows (${number(data.total_rows)} total rows). Search, filters and sorting use the full dataset. Row numbers refer to original data records. Missing and empty text are distinct.`;
  if (!data.rows.length) {$('readingPreview').innerHTML = '<p class="empty-table">No rows match this view.</p>'; return;}
  const schema = new Map(data.schema.map(c => [c.name,c]));
  $('readingPreview').innerHTML = `<div class="table-scroll" tabindex="0" role="region" aria-label="Reading preview"><table class="data-table"><thead><tr><th scope="col">Row</th>${data.columns.map(name => {
    const col = schema.get(name);
    return `<th scope="col">${escapeHtml(name)}<span class="preview-type">${escapeHtml(col.dtype)} · ${number(col.missing)} missing · ${number(col.empty_strings)} empty text</span></th>`;
  }).join('')}</tr></thead><tbody>${data.rows.map((row,i) => `<tr><td class="preview-row-number">${number(data.row_numbers[i])}</td>${data.columns.map(name => `<td ${row[name] === null ? 'class="missing-cell"' : ''}>${row[name] === null ? '<span class="null">Missing</span>' : row[name] === '' ? '<span class="empty-string">Empty text</span>' : escapeHtml(row[name])}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;
}
async function queryPreview(offset=0) {
  if (state.busy || !state.inspection?.valid || state.readingDirty) return;
  if (state.previewDirty) offset = 0;
  const columns = Array.from(document.querySelectorAll('[data-preview-column]:checked')).map(el => el.dataset.previewColumn);
  if (!columns.length) {status('Select at least one visible column.'); return;}
  clearStatus(); setBusy(true,'Searching and preparing the selected view…');
  try {
    const data = await readResponse(await apiFetch(`${API_BASE}/datasets/${state.inspection.dataset_id}/preview`,{
      method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({revision:state.inspection.revision,
        offset,limit:50,query:$('previewSearch').value,sort_column:$('previewSort').value || null,
        descending:$('previewDirection').value === 'desc',missing_column:$('previewMissing').value || null,columns})}));
    renderReadingPreview(data); state.previewDirty = false;
  } catch (error) {status('The view was not updated. The table still shows the last successful view.',error);}
  finally {setBusy(false);}
}
['readingDelimiter','readingEncoding','readingSheet','readingHeader'].forEach(id => $(id).addEventListener('input',() => {
  state.readingDirty = true; $('confirmReading').checked = false; syncReadingButtons();
}));
$('confirmReading').addEventListener('change',syncReadingButtons);
['previewSearch','previewSort','previewDirection','previewMissing'].forEach(id => $(id).addEventListener('input',() => state.previewDirty = true));
$('previewColumns').addEventListener('change',() => state.previewDirty = true);
$('updateReading').addEventListener('click',async () => {
  if (state.busy || !state.inspection) return;
  const header = Number($('readingHeader').value);
  if (!Number.isInteger(header) || header < 1 || header > 10000) {status('Choose a header row from 1 to 10000.'); return;}
  clearStatus(); setBusy(true,'Re-reading the original file using your settings…');
  const csv = state.inspection.filename.toLowerCase().endsWith('.csv');
  try {
    const data = await readResponse(await apiFetch(`${API_BASE}/inspect/${state.inspection.dataset_id}`,{
      method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({header_row:header,
        delimiter:csv ? $('readingDelimiter').value || null : null,encoding:csv ? $('readingEncoding').value || null : null,
        sheet_name:csv ? null : $('readingSheet').value || null})}));
    acceptInspection(data);
  } catch (error) {state.readingDirty = true; status('The reading settings could not be applied. Update the preview before analysis.',error);}
  finally {setBusy(false);}
});
$('previewForm').addEventListener('submit',event => {event.preventDefault(); queryPreview(0);});
$('previewPrevious').addEventListener('click',() => queryPreview(Math.max(0,state.preview.offset - 50)));
$('previewNext').addEventListener('click',() => queryPreview(state.preview.offset + 50));
navigate(); setMode('guided'); stage('upload'); syncReadingButtons();

// User-approved semantics and recipes; this does not train a model.
const datasetUrl = suffix => `${API_BASE}/datasets/${state.analysis.dataset_id}/${suffix}`;
const decisionBody = () => ({revision:state.analysis.reading_revision,decision_revision:state.decisions?.revision});
async function postDataset(suffix,body) {
  return readResponse(await apiFetch(datasetUrl(suffix),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}));
}
function initQualityTools() {
  state.loadedPlanId = null; $("saveDecisions").textContent = "Save current plan";
  state.decisions = null; state.rules = []; state.decisionsDirty = false; state.recalled = [];
  state.groups = state.analysis.category_groups || []; $('approvePlan').checked = false; $('effectPanel').hidden = true;
  renderRoles(state.analysis.semantics); renderRules(); renderGroups(); $('ruleFindings').replaceChildren();
  $('ruleColumn').innerHTML = Object.keys(state.analysis.analysis.schema).map(c => `<option value="${escapeHtml(c)}">${escapeHtml(c)}</option>`).join('');
  $('decisionsDirty').hidden = true; renderRecalled(); loadPlans(); updateSelection();
}
function renderRoles(roles) {
  const choices = state.analysis.role_options || {};
  $('semanticRoles').innerHTML = Object.entries(roles || {}).map(([column,spec]) => `<div><label>${escapeHtml(column)}<select data-role-column="${escapeHtml(column)}">${Object.entries(choices).map(([role,info]) => `<option value="${escapeHtml(role)}" ${role === spec.role ? 'selected' : ''}>${escapeHtml(info.label)}</option>`).join('')}</select></label><p class="subtle small">${escapeHtml(spec.confirmed ? 'Confirmed for this file.' : spec.reason)}</p></div>`).join('');
}
function dirtyDecisions() {
  state.decisionsDirty = true; $('decisionsDirty').hidden = false; $('effectPanel').hidden = true; updateSelection();
}
$('semanticRoles').addEventListener('change',dirtyDecisions);
$('ruleKind').addEventListener('change',() => {
  const kind = $('ruleKind').value;
  $('ruleBounds').hidden = !['range','length'].includes(kind); $('ruleAllowed').hidden = kind !== 'allowed'; $('ruleDate').hidden = kind !== 'date';
});
$('ruleForm').addEventListener('submit',event => {
  event.preventDefault();
  const kind = $('ruleKind').value, rule = {column:$('ruleColumn').value,kind};
  if (['range','length'].includes(kind)) {
    if (!$('ruleMin').value && !$('ruleMax').value) {status('Enter at least one bound.'); return;}
    if ($('ruleMin').value !== '') rule.minimum = Number($('ruleMin').value);
    if ($('ruleMax').value !== '') rule.maximum = Number($('ruleMax').value);
    if (rule.minimum !== undefined && rule.maximum !== undefined && rule.minimum > rule.maximum) {status('Minimum must not exceed maximum.'); return;}
  }
  if (kind === 'allowed') {
    rule.values = $('ruleValues').value.split(/\r?\n/).filter(v => v !== '');
    if (!rule.values.length) {status('Enter at least one allowed value.'); return;}
  }
  if (kind === 'date') rule.date_format = $('ruleDateFormat').value;
  state.rules.push(rule); renderRules(); dirtyDecisions();
});
function renderRules() {
  $('rulesList').innerHTML = state.rules.map((r,i) => `<p class="saved-rule">${escapeHtml(r.column)} · ${escapeHtml(r.kind)}${r.minimum !== undefined ? ` · min ${escapeHtml(r.minimum)}` : ''}${r.maximum !== undefined ? ` · max ${escapeHtml(r.maximum)}` : ''}${r.values ? ` · ${escapeHtml(r.values.join(', '))}` : ''}${r.kind === 'date' ? ` · ${escapeHtml(r.date_format)}` : ''} <button class="text-button" data-remove-rule="${i}" aria-label="Remove rule ${i+1}">Remove</button></p>`).join('');
}
$('rulesList').addEventListener('click',event => {
  const i = event.target.dataset.removeRule;
  if (i !== undefined) {state.rules.splice(Number(i),1); renderRules(); dirtyDecisions();}
});
function ruleRows(validation) {
  return (validation?.findings || []).map(f => ({Column:f.rule.column,Check:f.rule.kind,'Rows needing review':f.violations,Examples:f.examples.map(e => `row ${e.row}: ${e.value}`).join('; ')}));
}
async function confirmCurrentDecisions(roles) {
  roles ||= Object.fromEntries(Array.from(document.querySelectorAll('[data-role-column]')).map(el => [el.dataset.roleColumn,el.value]));
  const data = await postDataset('decisions',{revision:state.analysis.reading_revision,roles,rules:state.rules});
  state.decisions = data.decisions; state.decisionsDirty = false; state.groups = data.category_groups;
  state.analysis.cleaning_plan = data.cleaning_plan; state.analysis.semantics = data.decisions.roles;
  state.guidedSelected.clear(); state.expertSelected.clear(); state.settings.clear();
  plan().forEach((step,i) => {
    if (guidedEligible(step)) {state.guidedSelected.add(i); state.expertSelected.add(i);}
    state.settings.set(i,{strategy:step.strategy || 'keep',target_case:step.strategy || 'title',constant:''});
  });
  renderRoles(data.decisions.roles); renderGroups(); renderPlan(); $('decisionsDirty').hidden = true;
  renderTable('ruleFindings',ruleRows(data.rule_validation),'No rules configured.');
  return data;
}
$('confirmDecisions').addEventListener('click',async () => {
  if (state.busy) return;
  clearStatus(); setBusy(true,'Checking meanings and rules…');
  try {await confirmCurrentDecisions(); status('Decisions confirmed. Review the refreshed cleaning suggestions.');}
  catch (error) {status('Decisions were not confirmed.',error);}
  finally {setBusy(false);}
});
function renderGroups() {
  $('categoryDetails').hidden = !state.groups.length;
  $('categoryGroups').innerHTML = state.groups.map((g,i) => `<fieldset class="category-group"><legend>${escapeHtml(g.column)}</legend><p>${g.variants.map(v => `${escapeHtml(v.value)} (${number(v.count)})`).join(' / ')}</p><label for="group-target-${i}">Approved label${g.suggested_target === null ? ' — tied counts; choose a label' : ' — most frequent suggestion'}</label><select id="group-target-${i}" data-group-target="${i}"><option value="">Choose a label</option>${g.variants.map(v => `<option value="${escapeHtml(v.value)}" ${g.suggested_target === v.value ? 'selected' : ''}>${escapeHtml(v.value)}</option>`).join('')}</select><label class="reading-confirmation"><input type="checkbox" data-group-approved="${i}">These labels mean the same thing. Apply this replacement.</label></fieldset>`).join('');
}
function categoryActions() {
  return state.groups.flatMap((g,i) => {
    const checked = document.querySelector(`[data-group-approved="${i}"]`)?.checked;
    const target = $(`group-target-${i}`)?.value;
    if (!checked || !target) return [];
    return [{action:'map_categories',column:g.column,mapping:Object.fromEntries(g.variants.filter(v => v.value !== target).map(v => [v.value,target])),approved:true}];
  });
}
$('categoryGroups').addEventListener('change',event => {
  const i = event.target.dataset.groupApproved;
  if (i !== undefined && event.target.checked && !$(`group-target-${i}`).value) {event.target.checked = false; status('Choose the approved label first.');}
  $('effectPanel').hidden = true; updateSelection();
});
$('previewEffects').addEventListener('click',async () => {
  if (state.busy || state.decisionsDirty) return;
  clearStatus(); setBusy(true,'Previewing effects across the full dataset…');
  try {
    const data = await postDataset('effects',{...decisionBody(),actions:selectedActions()});
    $('effectPanel').hidden = false;
    $('effectSummary').textContent = `${number(data.rows_before)} → ${number(data.rows_after)} rows. ${data.note}`;
    renderTable('effectActions',data.actions,'No changes selected.'); renderTable('effectChanges',data.sample_changes,'No value changes.');
    renderTable('effectRules',ruleRows(data.rule_validation_after),'No validation rules configured.');
    $('effectPanel').scrollIntoView({behavior:'smooth',block:'start'});
  } catch (error) {status('The effect preview could not finish.',error);}
  finally {setBusy(false);}
});
async function loadPlans() {
  const dataset = state.analysis?.dataset_id;
  try {
    const data = await readResponse(await apiFetch(`${API_BASE}/datasets/${dataset}/plans`));
    if (state.analysis?.dataset_id !== dataset) return;
    state.savedPlans = data.plans;
    $('savedPlans').innerHTML = data.plans.length ? data.plans.map((p,i) => `<div class="saved-plan"><strong>${escapeHtml(p.name)}</strong><p class="subtle small">${p.compatible ? 'Columns and types match. Review business meaning before use.' : escapeHtml(p.issues.join(' / '))}</p><button class="text-button" data-load-plan="${i}" ${p.compatible ? '' : 'disabled'}>Load for review</button> <button class="text-button" data-delete-plan="${i}">Delete</button></div>`).join('') : '<p class="subtle">No saved plans in this browser session.</p>';
  } catch (error) {$('savedPlans').textContent = `Saved plans could not load: ${error.message}`;}
}
function renderRecalled() {
  $('recalledActions').innerHTML = state.recalled.length ? '<h3>Loaded actions — review before applying</h3>' + state.recalled.map((a,i) => `<label class="reading-confirmation"><input type="checkbox" data-recalled="${i}" ${a.selected ? 'checked' : ''}>${escapeHtml(a.column)} · ${escapeHtml(actionLabels[a.action] || a.action)} · ${escapeHtml(a.strategy || a.target_case || (a.mapping ? JSON.stringify(a.mapping) : 'default'))}</label>`).join('') : '';
}
$('recalledActions').addEventListener('change',event => {
  if (event.target.dataset.recalled !== undefined) {state.recalled[Number(event.target.dataset.recalled)].selected = event.target.checked; updateSelection();}
});
$('savedPlans').addEventListener('click',async event => {
  const load = event.target.dataset.loadPlan, remove = event.target.dataset.deletePlan;
  if (state.busy || (load === undefined && remove === undefined)) return;
  const saved = state.savedPlans[Number(load ?? remove)];
  clearStatus(); setBusy(true,load !== undefined ? 'Loading decisions for review…' : 'Deleting the saved plan…');
  try {
    if (remove !== undefined) {
      await readResponse(await apiFetch(`${API_BASE}/plans/${saved.id}`,{method:'DELETE'})); if (state.loadedPlanId === saved.id) {state.loadedPlanId = null; $('saveDecisions').textContent = 'Save current plan';}
      await loadPlans(); status('Saved plan deleted.');
    } else {
      if (!saved.compatible) throw new Error('This plan is incompatible with the current file.');
      state.rules = saved.payload.rules; renderRules();
      await confirmCurrentDecisions(Object.fromEntries(Object.entries(saved.payload.roles).map(([c,r]) => [c,r.role])));
      state.guidedSelected.clear(); state.expertSelected.clear();
      // Loading is never execution. Each remembered action starts unchecked.
      state.recalled = saved.payload.actions.map(a => ({...a,selected:false})); renderRecalled(); renderPlan();
      state.loadedPlanId = saved.id; $('saveDecisions').textContent = 'Update saved plan';
      $('planName').value = saved.name; $('approvePlan').checked = false;
      status('Plan loaded. Review its meanings, rules and unchecked actions. Preview effects before applying.');
    }
  } catch (error) {status('The saved-plan operation could not finish.',error);}
  finally {setBusy(false);}
});
$('saveDecisions').addEventListener('click',async () => {
  if (state.busy) return;
  if (!state.decisions || state.decisionsDirty) {status('Confirm column meanings and rules before saving a plan.'); $('meaningDetails').open = true; return;}
  if (!$('approvePlan').checked || !$('planName').value.trim()) {status('Name the plan and approve saving the reviewed decisions.'); return;}
  clearStatus(); setBusy(true,'Saving your approved decisions…');
  try {
    const saved = await postDataset('plans',{...decisionBody(),plan_id:state.loadedPlanId,name:$('planName').value.trim(),approved:true,actions:selectedActions()});
    state.loadedPlanId = saved.id; $('saveDecisions').textContent = 'Update saved plan';
    await loadPlans(); $('approvePlan').checked = false; status('Plan saved privately for this browser session.');
  } catch (error) {status('The plan could not be saved.',error);}
  finally {setBusy(false);}
});

$("reviewPanel").addEventListener("change", () => {$("effectPanel").hidden = true;});
$("reviewPanel").addEventListener("input", () => {$("effectPanel").hidden = true;});

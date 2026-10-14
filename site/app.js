'use strict';

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const original = '# An illustrative coding task\ndef positive_sum(values):\n    # Keep only positive values.\n    return sum(v for v in values if v > 0) if values else None';
const fixed = '# Continued from the restored workspace\ndef positive_sum(values):\n    # Empty inputs and generators both return zero.\n    return sum(v for v in values if v > 0)';
const scenarios = {
  handoff: {
    label: 'CROSS-AGENT CONTINUATION',
    caption: 'The next agent inherits the committed workspace and selected context. Each branch has its own writable copy.',
    source: 'Source agent', target: 'Next agent', symbol: 'B',
    steps: [
      {name:'Task started', title:'Work begins in an isolated workspace.', description:'Agent A makes an initial change. The empty-input case still needs attention; the workspace has not yet been sealed.', state:'RUNNING', source:'Running', checkpoint:'Pending', target:'Waiting', code:original, event:'execution.started', detail:'Agent A owns this execution.'},
      {name:'Work checkpointed', title:'A stable boundary captures the work.', description:'RTV stops the sandbox before publishing a checkpoint. Code, file permissions and context are tied to one committed reference.', state:'SEALED', source:'Quiesced', checkpoint:'Committed', target:'Waiting', code:original, event:'checkpoint.committed', detail:'Workspace and context sealed at cp-demo-01.'},
      {name:'Ownership transferred', title:'The next agent receives a clean handoff.', description:'Agent B restores an independent copy and reads the handoff summary. Its changes cannot modify the source checkpoint.', state:'RESTORED', source:'Sealed', checkpoint:'Verified', target:'Restored', code:original, event:'workspace.restored', detail:'Target workspace matches the committed digest.'},
      {name:'Execution continued', title:'Continue where the work left off.', description:'The receiving agent fixes the empty-input case in its own workspace. The checkpoint remains the immutable point of departure.', state:'RUNNING', source:'Sealed', checkpoint:'Unchanged', target:'Running', code:fixed, event:'execution.continued', detail:'Agent B produces its own candidate change.'},
      {name:'Result verified', title:'Verify the result in a separate environment.', description:'The candidate workspace goes to an independent verifier. The result, source checkpoint and cleanup status remain connected.', state:'VERIFIED', source:'Sealed', checkpoint:'Unchanged', target:'Verified', code:fixed+'\n\n# Illustrative checks\n# positive_sum([])              → 0\n# positive_sum([-2, 1, 3])      → 4\n# positive_sum(iter([1, -2]))   → 1', event:'verification.completed', detail:'Illustrative checks pass; branch resources released.'}
    ]
  },
  recovery: {
    label: 'CHECKPOINT-BASED RECOVERY',
    caption: 'Recovery retains the logical attempt and its deadline. A fresh execution gets a new ownership epoch and restores committed state.',
    source: 'Execution 1', target: 'Execution 2', symbol: 'A′',
    steps: [
      {name:'Execution running', title:'An attempt owns a live execution.', description:'The agent is working on a candidate change. Runtime state distinguishes the logical attempt from its current physical execution.', state:'RUNNING', source:'Epoch 1', checkpoint:'Pending', target:'Waiting', code:original, event:'execution.claimed', detail:'Attempt demo-01, execution 1, ownership epoch 1.'},
      {name:'Checkpoint committed', title:'Committed progress becomes recoverable.', description:'A quiescent workspace and supported native session are sealed together. This boundary is the point a future execution can restore.', state:'SEALED', source:'Checkpointed', checkpoint:'Committed', target:'Waiting', code:original, event:'checkpoint.committed', detail:'Committed boundary cp-demo-01 is available.'},
      {name:'Executor lost', title:'A lost executor loses publication rights.', description:'The in-flight execution stops. Uncommitted changes are discarded, and an interrupted operation remains UNKNOWN rather than being recorded as free.', state:'RECOVERING', source:'Fenced', checkpoint:'Retained', target:'Allocated', code:'# The committed workspace remains available.\n# In-flight edits are not part of this checkpoint.\n\n'+original, event:'execution.fenced', detail:'Epoch 1 can no longer publish. Interrupted usage: UNKNOWN.'},
      {name:'State restored', title:'A fresh execution takes over the attempt.', description:'RTV restores the committed workspace and supported session under a new epoch. The original deadline and accumulated cost record stay with the attempt.', state:'RESTORED', source:'Fenced', checkpoint:'Verified', target:'Epoch 2', code:original, event:'workspace.restored', detail:'Execution 2 restores cp-demo-01; attempt and deadline retained.'},
      {name:'Result verified', title:'The recovered execution finishes the task.', description:'The new execution continues from the checkpoint and reaches independent verification. The interrupted execution stays in the history.', state:'VERIFIED', source:'Fenced', checkpoint:'Unchanged', target:'Verified', code:fixed+'\n\n# Verification completed in an isolated sandbox.\n# Both execution records remain attached to the attempt.', event:'verification.completed', detail:'Recovered candidate verified; old and new resources cleaned.'}
    ]
  }
};
let scenario = 'handoff';
let step = 0;
let view = 'workspace';
let timer = null;

function stopPlayback() {
  if (timer !== null) clearInterval(timer);
  timer = null;
  $('#play-label').textContent = 'Play replay';
  $('#play-icon').textContent = '▶';
  $('#play').setAttribute('aria-label', 'Play replay');
}
function renderInspector() {
  const run = scenarios[scenario];
  const state = run.steps[step];
  let text;
  if (view === 'workspace') {
    text = state.code;
    $('#inspector-filename').textContent = 'positive_sum.py';
  } else if (view === 'handoff') {
    text = step < 1 ? '# No committed checkpoint yet.\n# A handoff can start after the workspace is sealed.' : JSON.stringify({
      example: true, checkpoint: 'cp-demo-01',
      policy: scenario === 'handoff' ? 'summary' : 'native-session',
      workspace: 'independent restored copy',
      remaining_work: step < 4 ? 'Handle empty inputs; verify the candidate.' : 'Verification complete.',
      ...(scenario === 'recovery' ? {attempt:'demo-01', epoch:step >= 3 ? 2 : 1, original_deadline:'preserved', interrupted_usage:step >= 2 ? 'UNKNOWN' : 'not interrupted'} : {source:'Agent A', target:'Agent B', source_snapshot:'immutable'})
    }, null, 2);
    $('#inspector-filename').textContent = 'handoff.json';
  } else {
    text = '# Illustrative events; not a recorded model transcript.\n\n' + run.steps.slice(0, step + 1).map((item, index) => `${String(index + 1).padStart(2, '0')}  ${item.event}\n    ${item.detail}`).join('\n\n');
    $('#inspector-filename').textContent = 'events.jsonl · preview';
  }
  $('#inspector-code').replaceChildren(...text.split('\n').map((line, index, lines) => {
    const span = document.createElement('span');
    span.textContent = line + (index === lines.length - 1 ? '' : '\n');
    if (line.trimStart().startsWith('#')) span.className = 'code-comment';
    else if (line.includes('return sum') && step >= (scenario === 'handoff' ? 3 : 4)) span.className = 'code-add';
    else if (line.startsWith('def ') || /^\d{2}  /.test(line)) span.className = 'code-key';
    return span;
  }));
  $$('.inspector-tabs button').forEach(button => {
    const selected = button.dataset.view === view;
    button.setAttribute('aria-selected', String(selected));
    button.tabIndex = selected ? 0 : -1;
  });
  $('#inspector-panel').setAttribute('aria-labelledby', `tab-${view}`);
}
function render() {
  const run = scenarios[scenario];
  const state = run.steps[step];
  $('#scenario-label').textContent = run.label;
  $('#scenario-caption').textContent = run.caption;
  $('#step-counter').textContent = `STEP ${String(step + 1).padStart(2, '0')} / 05`;
  $('#step-title').textContent = state.title;
  $('#step-description').textContent = state.description;
  $('#state-label').textContent = state.state;
  $('#state-label').classList.toggle('verified', step === 4);
  $('#source-label').textContent = run.source;
  $('#target-label').textContent = run.target;
  $('#target-symbol').textContent = run.symbol;
  $('#source-state').textContent = state.source;
  $('#checkpoint-state').textContent = state.checkpoint;
  $('#target-state').textContent = state.target;
  $('#flow-source').classList.toggle('active', step === 0);
  $('#flow-source').classList.toggle('done', step > 0 && !(scenario === 'recovery' && step >= 2));
  $('#flow-checkpoint').classList.toggle('active', step === 1);
  $('#flow-checkpoint').classList.toggle('done', step > 1);
  $('#flow-target').classList.toggle('active', step >= 2 && step < 4);
  $('#flow-target').classList.toggle('done', step === 4);
  $$('#step-list button').forEach((button, index) => {
    button.setAttribute('aria-label', `Step ${index + 1}: ${run.steps[index].name}`);
    button.querySelector('.step-name').textContent = run.steps[index].name;
    button.querySelector('.number').textContent = index < step ? '✓' : String(index + 1).padStart(2, '0');
    button.classList.toggle('complete', index < step);
    if (index === step) button.setAttribute('aria-current', 'step');
    else button.removeAttribute('aria-current');
  });
  $$('.progress-dots i').forEach((dot, index) => {
    dot.classList.toggle('past', index < step);
    dot.classList.toggle('current', index === step);
  });
  $('#next').disabled = step === 4;
  renderInspector();
}
for (let index = 0; index < 5; index++) {
  const item = document.createElement('li');
  const button = document.createElement('button');
  button.className = 'step-button';
  const number = document.createElement('span');
  number.className = 'number'; number.setAttribute('aria-hidden', 'true');
  const name = document.createElement('span'); name.className = 'step-name';
  button.append(number, name);
  button.addEventListener('click', () => { stopPlayback(); step = index; render(); });
  item.append(button); $('#step-list').append(item);
}
$$('[data-scenario]').forEach(button => button.addEventListener('click', () => {
  stopPlayback(); scenario = button.dataset.scenario; step = 0;
  $$('[data-scenario]').forEach(item => item.setAttribute('aria-pressed', String(item === button)));
  render();
}));
$$('[data-view]').forEach(button => {
  button.addEventListener('click', () => { view = button.dataset.view; renderInspector(); });
  button.addEventListener('keydown', event => {
    const tabs = $$('[data-view]');
    const index = tabs.indexOf(button);
    let target;
    if (event.key === 'ArrowRight') target = (index + 1) % tabs.length;
    if (event.key === 'ArrowLeft') target = (index + tabs.length - 1) % tabs.length;
    if (event.key === 'Home') target = 0;
    if (event.key === 'End') target = tabs.length - 1;
    if (target !== undefined) { event.preventDefault(); tabs[target].click(); tabs[target].focus(); }
  });
});
$('#next').addEventListener('click', () => { stopPlayback(); step = Math.min(4, step + 1); render(); });
$('#reset').addEventListener('click', () => { stopPlayback(); step = 0; render(); });
$('#play').addEventListener('click', () => {
  if (timer !== null) { stopPlayback(); return; }
  if (step === 4) { step = 0; render(); }
  $('#play-label').textContent = 'Pause replay'; $('#play-icon').textContent = 'Ⅱ';
  $('#play').setAttribute('aria-label', 'Pause replay');
  timer = setInterval(() => { step = Math.min(4, step + 1); render(); if (step === 4) stopPlayback(); }, 2400);
});
document.addEventListener('visibilitychange', () => { if (document.hidden) stopPlayback(); });
$('#copy').addEventListener('click', async () => {
  try {
    await navigator.clipboard.writeText('uv sync --locked --dev\nuv run rtv demo --output work/demo');
    $('#copy').textContent = 'Copied ✓'; $('#copy-status').textContent = 'Quick start commands copied.';
  } catch {
    $('#copy').textContent = 'Select to copy'; $('#copy-status').textContent = 'Clipboard unavailable. Select the terminal commands to copy them.';
    const selection = window.getSelection(); const range = document.createRange();
    range.selectNodeContents($('.terminal pre')); selection.removeAllRanges(); selection.addRange(range);
  }
});
function drawStars() {
  const canvas = $('#stars'); const context = canvas.getContext('2d');
  if (!context) return;
  const width = canvas.clientWidth, height = canvas.clientHeight;
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  canvas.width = width * dpr; canvas.height = height * dpr; context.scale(dpr, dpr);
  let seed = 42; const random = () => { seed = (seed * 16807) % 2147483647; return (seed - 1) / 2147483646; };
  for (let index = 0; index < Math.min(510, width * height / 1900); index++) {
    const x = random() * width, y = random() * height;
    const radius = random() > .985 ? 1.4 : random() * .75 + .1;
    const center = Math.abs(x - width / 2) / width < .25 && y > height * .18 && y < height * .76;
    context.beginPath(); context.arc(x, y, radius, 0, Math.PI * 2);
    context.fillStyle = `rgba(220,220,215,${(random() * .65 + .15) * (center ? .35 : 1)})`; context.fill();
    if (radius > 1) { context.strokeStyle = '#d2c9b44a'; context.lineWidth = .45; context.beginPath(); context.moveTo(x - 6, y); context.lineTo(x + 6, y); context.moveTo(x, y - 6); context.lineTo(x, y + 6); context.stroke(); }
  }
}
render(); drawStars();
let resizeTimer;
window.addEventListener('resize', () => { clearTimeout(resizeTimer); resizeTimer = setTimeout(drawStars, 120); });

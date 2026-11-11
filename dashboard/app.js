const state = { taskId: null, plan: [] };
const eventStream = document.getElementById('event-stream');
const planList = document.getElementById('plan-list');
const taskStatus = document.getElementById('task-status');
const report = document.getElementById('task-report');
const modePill = document.getElementById('mode-pill');

function addEvent(text) {
  const timestamp = new Date().toISOString();
  eventStream.textContent = `${timestamp}: ${text}
` + eventStream.textContent;
}

async function submitTask() {
  const goal = document.getElementById('goal-input').value;
  addEvent('Submitting task');
  const response = await fetch('/tasks', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ goal })
  });
  const payload = await response.json();
  state.taskId = payload.task_id;
  state.plan = payload.plan;
  taskStatus.textContent = payload.status;
  renderPlan();
  addEvent(`Task ${state.taskId} created`);
  modePill.textContent = 'demo';
}

function renderPlan() {
  planList.innerHTML = '';
  for (const step of state.plan) {
    const li = document.createElement('li');
    li.textContent = `${step.id}: ${step.name} (${step.status || 'PENDING'})`;
    planList.appendChild(li);
  }
}

document.getElementById('submit-btn').addEventListener('click', submitTask);

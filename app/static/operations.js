const info = JSON.parse(document.getElementById('chart-data').textContent);
const source = document.getElementById('source');
const csv = document.getElementById('csv');
const notice = document.getElementById('notice');

source.onchange = async () => {
  if (!source.files.length) return;
  const bytes = await source.files[0].arrayBuffer();
  try { csv.value = new TextDecoder('utf-8', {fatal: true}).decode(bytes); }
  catch { csv.value = new TextDecoder('gb18030').decode(bytes); }
};

document.getElementById('restore').addEventListener('click', async () => {
  try {
    const reply = await fetch('/api/sample');
    csv.value = (await reply.json()).text;
    notice.textContent = '分段样例已就绪';
  } catch {
    notice.textContent = '样例读取失败，请重试';
  }
});

async function post(url, payload) {
  const response = await fetch(url, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload)});
  const data = await response.json();
  if (!response.ok) throw Error(data.message || '计划处理失败');
  return data;
}

document.getElementById('plan-form').onsubmit = async event => {
  event.preventDefault();
  event.submitter.disabled = true;
  notice.textContent = '正在安排航次…';
  try {
    const run = await post('/api/run', {text: csv.value});
    window.location.assign(window.location.pathname + '?run_id=' + run.id);
  } catch (error) {
    notice.textContent = error.message;
    event.submitter.disabled = false;
  }
};

document.querySelectorAll('[data-decision]').forEach(button => button.addEventListener('click', async () => {
  try {
    const run = await post('/api/review', {id: info.id, revision: info.revision, decision: button.dataset.decision});
    info.revision = run.revision;
    notice.textContent = run.decision + ' / 修订 ' + run.revision;
  } catch (error) {
    notice.textContent = error.message;
  }
}));

// 复核前先确认当前批次不是失效方案，失效时提示改用最新批次
(async () => {
  try {
    const history = await (await fetch('/api/runs')).json();
    const newer = history.find(row => row.superseded_by === info.id);
    if (newer) notice.textContent = '该批为旧方案，已被 ' + newer.id + ' 取代';
  } catch {}
})();

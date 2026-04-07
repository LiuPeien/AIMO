let state = { sessionId: null };

async function api(path, opts = {}) {
  const res = await fetch(path, { headers: { 'Content-Type': 'application/json' }, ...opts });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

function appendMessage(role, content) {
  const chat = document.getElementById('chat');
  const div = document.createElement('div');
  div.className = `msg ${role}`;
  div.textContent = content;
  chat.appendChild(div);
  chat.scrollTop = chat.scrollHeight;
}

async function refreshSessions() {
  const list = await api('/api/sessions');
  const ul = document.getElementById('sessions');
  ul.innerHTML = '';
  list.forEach((s) => {
    const li = document.createElement('li');
    li.textContent = s.title;
    li.onclick = async () => {
      state.sessionId = s.id;
      const msgs = await api(`/api/sessions/${s.id}/messages`);
      document.getElementById('chat').innerHTML = '';
      msgs.forEach((m) => appendMessage(m.role, m.content));
    };
    ul.appendChild(li);
  });
}

async function refreshModels() {
  const data = await api('/api/models');
  const select = document.getElementById('modelSelect');
  select.innerHTML = '';
  data.models.forEach((m) => {
    const op = document.createElement('option');
    op.value = m;
    op.textContent = m;
    select.appendChild(op);
  });
}

async function refreshAbilities() {
  const data = await api('/api/abilities');
  document.getElementById('abilities').textContent = data.map((a) => `• ${a.name}`).join('\n') || '暂无';
}

document.getElementById('sendBtn').onclick = async () => {
  const input = document.getElementById('messageInput');
  const text = input.value.trim();
  if (!text) return;
  appendMessage('user', text);
  input.value = '';
  const modelId = document.getElementById('modelSelect').value;
  const data = await api('/api/chat', {
    method: 'POST',
    body: JSON.stringify({ session_id: state.sessionId, model_id: modelId, message: text }),
  });
  state.sessionId = data.session_id;
  appendMessage('assistant', data.reply);
  refreshSessions();
};

document.getElementById('evolveBtn').onclick = async () => {
  const requirement = prompt('描述想新增的能力模块：');
  if (!requirement) return;
  const modelId = document.getElementById('modelSelect').value;
  const data = await api('/api/evolve', {
    method: 'POST',
    body: JSON.stringify({ model_id: modelId, requirement }),
  });
  alert(`完成: ${data.module_name}`);
  refreshAbilities();
};

document.getElementById('newSessionBtn').onclick = async () => {
  const s = await api('/api/sessions', { method: 'POST', body: JSON.stringify({ title: '新会话' }) });
  state.sessionId = s.session_id;
  document.getElementById('chat').innerHTML = '';
  refreshSessions();
};

refreshModels();
refreshSessions();
refreshAbilities();

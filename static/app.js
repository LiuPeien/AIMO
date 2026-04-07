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
  div.dataset.role = role === 'user' ? '你' : 'Agent';
  div.textContent = content;
  chat.appendChild(div);
  chat.scrollTop = chat.scrollHeight;
}

function setActiveSessionStyles() {
  document.querySelectorAll('.session-item').forEach((el) => {
    if (el.dataset.id === state.sessionId) el.classList.add('active');
    else el.classList.remove('active');
  });
}

async function refreshSessions() {
  const list = await api('/api/sessions');
  const ul = document.getElementById('sessions');
  ul.innerHTML = '';
  list.forEach((s) => {
    const li = document.createElement('li');
    li.className = 'session-item';
    li.dataset.id = s.id;

    const title = document.createElement('span');
    title.className = 'session-title';
    title.textContent = s.title;

    const actions = document.createElement('div');
    actions.className = 'session-actions';

    const editBtn = document.createElement('button');
    editBtn.className = 'session-action-btn';
    editBtn.title = '重命名会话';
    editBtn.setAttribute('aria-label', '重命名会话');
    editBtn.textContent = '✎';
    editBtn.onclick = async (e) => {
      e.stopPropagation();
      const newTitle = window.prompt('请输入新的会话名称', s.title);
      if (!newTitle) return;
      const cleaned = newTitle.trim();
      if (!cleaned) return;
      await api(`/api/sessions/${s.id}`, {
        method: 'PATCH',
        body: JSON.stringify({ title: cleaned }),
      });
      refreshSessions();
    };

    const deleteBtn = document.createElement('button');
    deleteBtn.className = 'session-action-btn danger';
    deleteBtn.title = '删除会话';
    deleteBtn.setAttribute('aria-label', '删除会话');
    deleteBtn.textContent = '🗑';
    deleteBtn.onclick = async (e) => {
      e.stopPropagation();
      const ok = window.confirm(`确认删除会话「${s.title}」吗？`);
      if (!ok) return;
      await api(`/api/sessions/${s.id}`, { method: 'DELETE' });
      if (state.sessionId === s.id) {
        state.sessionId = null;
        document.getElementById('chat').innerHTML = '';
      }
      refreshSessions();
    };

    actions.appendChild(editBtn);
    actions.appendChild(deleteBtn);
    li.appendChild(title);
    li.appendChild(actions);
    li.onclick = async () => {
      state.sessionId = s.id;
      const msgs = await api(`/api/sessions/${s.id}/messages`);
      document.getElementById('chat').innerHTML = '';
      msgs.forEach((m) => appendMessage(m.role, m.content));
      setActiveSessionStyles();
    };
    ul.appendChild(li);
  });
  setActiveSessionStyles();
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

async function sendMessage() {
  const input = document.getElementById('messageInput');
  const text = input.value.trim();
  if (!text) return;

  appendMessage('user', text);
  input.value = '';

  const modelId = document.getElementById('modelSelect').value;
  const data = await api('/api/chat', {
    method: 'POST',
    body: JSON.stringify({
      session_id: state.sessionId,
      model_id: modelId,
      message: text,
      mode: 'chat',
    }),
  });

  state.sessionId = data.session_id;
  appendMessage('assistant', data.reply);
  refreshSessions();
}

document.getElementById('sendBtn').onclick = sendMessage;

document.getElementById('messageInput').addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    sendMessage();
  }
});

document.getElementById('newSessionBtn').onclick = async () => {
  const s = await api('/api/sessions', { method: 'POST', body: JSON.stringify({ title: '新会话' }) });
  state.sessionId = s.session_id;
  document.getElementById('chat').innerHTML = '';
  refreshSessions();
};

refreshModels();
refreshSessions();

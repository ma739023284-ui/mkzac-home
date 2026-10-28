const $ = (id) => document.getElementById(id);

const state = {
  sid: localStorage.getItem('xh_sid') || (window.crypto?.randomUUID ? crypto.randomUUID() : 's' + Date.now()),
  mode: 'customer',
  token: sessionStorage.getItem('xh_token') || '',
  tellerName: sessionStorage.getItem('xh_name') || '',
  busy: false,
  meta: null,
};
localStorage.setItem('xh_sid', state.sid);

const CUSTOMER_CHIPS = [
  { label: '开工资卡', send: '我想开个工资卡' },
  { label: '咨询贷款', send: '我想咨询贷款' },
  { label: '了解理财', send: '有什么理财产品' },
  { label: '查业务进度', send: '我查一下业务进度' },
  { label: '我要投诉', send: '我要投诉' },
  { label: '网点信息', send: '网点几点上班，在哪里' },
  { label: '外币兑换', send: '我想兑换外币' },
];

const TELLER_CHIPS = [
  { label: '理财双录口径', send: '查询理财销售适当性与双录口径' },
  { label: '最新存款利率', send: '2026 年 10 月最新存款利率通知' },
  { label: '产品参数', send: '查询产品参数：理财' },
  { label: '挂失止付流程', send: '挂失止付应急处置流程' },
  { label: '审计留痕', command: { name: 'audit_recent', args: {} } },
];

function esc(value) {
  return String(value ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

function scrollDown() {
  const chat = $('chat');
  requestAnimationFrame(() => { chat.scrollTop = chat.scrollHeight; });
}

function addUser(text) {
  const wrap = document.createElement('div');
  wrap.className = 'msg user';
  wrap.innerHTML = '<div class="avatar">我</div><div class="body"><div class="bubble"></div></div>';
  wrap.querySelector('.bubble').textContent = text;
  $('chat').appendChild(wrap);
  scrollDown();
}

function addTyping() {
  const wrap = document.createElement('div');
  wrap.className = 'msg bot';
  wrap.innerHTML = '<div class="avatar">汇</div><div class="body"><div class="typing"><span></span><span></span><span></span></div></div>';
  $('chat').appendChild(wrap);
  scrollDown();
  return wrap;
}

function statusClass(status) {
  if (status === '已废止') return 'old';
  if (status === '即将生效') return 'soon';
  return 'now';
}

function attachOptions(container, options) {
  if (!options || !options.length) return;
  const wrap = document.createElement('div');
  wrap.className = 'options';
  options.forEach((option) => {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'option';
    btn.textContent = option.label;
    btn.onclick = () => {
      wrap.querySelectorAll('button').forEach((b) => (b.disabled = true));
      if (option.command) sendCommand(option.command, option.label);
      else if (option.send) sendText(option.send);
    };
    wrap.appendChild(btn);
  });
  container.appendChild(wrap);
}

function appendCard(card) {
  const msg = document.createElement('div');
  msg.className = 'msg bot';

  const avatar = document.createElement('div');
  avatar.className = 'avatar';
  avatar.textContent = '汇';

  const body = document.createElement('div');
  body.className = 'body';

  const box = document.createElement('div');
  box.className = 'card ' + (card.type || 'answer');

  const meta = card.meta || {};
  const typeLabel = {
    escalate: '升级处理',
    clarify: '需确认',
    form: '待补充',
    receipt: '回执',
    answer: '解答',
  }[card.type || 'answer'] || '解答';

  const head = document.createElement('div');
  head.className = 'card-head';
  head.innerHTML =
    '<span class="card-title">' + esc(card.title || '小汇') + '</span>' +
    '<span class="badge' + (card.type === 'escalate' ? ' danger' : '') + '">' + esc(typeLabel) + '</span>' +
    (meta.label ? '<span class="badge">' + esc(meta.label) + '</span>' : '');
  box.appendChild(head);

  if (card.warning) {
    const warn = document.createElement('div');
    warn.className = 'warning';
    warn.textContent = card.warning;
    box.appendChild(warn);
  }

  if (card.text) {
    String(card.text).split(/\n{2,}/).forEach((para) => {
      if (!para.trim()) return;
      const p = document.createElement('p');
      p.innerHTML = esc(para).replace(/\n/g, '<br>');
      box.appendChild(p);
    });
  }

  if (card.bullets && card.bullets.length) {
    const ul = document.createElement('ul');
    ul.className = 'bullets';
    card.bullets.forEach((item) => {
      if (!item) return;
      const li = document.createElement('li');
      li.textContent = item;
      ul.appendChild(li);
    });
    box.appendChild(ul);
  }

  if (card.sources && card.sources.length) {
    const details = document.createElement('details');
    details.className = 'sources';
    const summary = document.createElement('summary');
    summary.textContent = '依据 ' + card.sources.length + ' 条（点击展开来源与时间）';
    details.appendChild(summary);
    const ul = document.createElement('ul');
    card.sources.forEach((src) => {
      const li = document.createElement('li');
      li.innerHTML =
        '<b>' + esc(src.title) + '</b><br>' +
        esc(src.authority || '') +
        '<br>生效 ' + esc(src.effective_date || '—') + ' · 更新 ' + esc(src.updated_at || '—') +
        ' · <span class="st ' + statusClass(src.status) + '">' + esc(src.status || '') + '</span>';
      ul.appendChild(li);
    });
    details.appendChild(ul);
    box.appendChild(details);
  }

  attachOptions(box, card.options);

  if (meta.intent || meta.confidence || meta.audit_id) {
    const line = document.createElement('div');
    line.className = 'meta';
    const parts = [];
    if (meta.label) parts.push('分流：' + meta.label);
    if (meta.confidence) parts.push('置信度 ' + Math.round(meta.confidence * 100) + '%');
    if (meta.escalated) parts.push('已升级 / 转人工');
    if (meta.elapsed_ms != null) parts.push(meta.elapsed_ms + ' ms');
    if (meta.blocked) parts.push('输出已按合规拦截');
    if (meta.disclaimer_added && meta.disclaimer_added.length) parts.push('已补风险提示');
    if (meta.audit_id) parts.push('审计 #' + meta.audit_id);
    if (meta.mode === 'teller') parts.push('柜员模式');
    line.textContent = parts.join(' · ');
    box.appendChild(line);
  }

  body.appendChild(box);
  msg.appendChild(avatar);
  msg.appendChild(body);
  $('chat').appendChild(msg);
  scrollDown();
}

async function postChat(body) {
  state.busy = true;
  setBusy(true);
  const typing = addTyping();
  try {
    const res = await fetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: state.sid, token: state.token, ...body }),
    });
    const data = await res.json();
    typing.remove();
    if (!res.ok) {
      appendCard({
        type: 'answer',
        title: '暂时无法应答',
        text: data.error || '服务暂时不可用，请稍后重试。',
        meta: { label: '异常' },
      });
      return;
    }
    appendCard(data);
  } catch (err) {
    typing.remove();
    appendCard({
      type: 'answer',
      title: '连接异常',
      text: '网络或服务暂时不可用，请稍后重试。',
      bullets: [String(err.message || err)],
      meta: { label: '异常' },
    });
  } finally {
    state.busy = false;
    setBusy(false);
  }
}

function sendText(text) {
  const content = String(text || '').trim();
  if (!content || state.busy) return;
  addUser(content);
  postChat({ text: content });
}

function sendCommand(command, label) {
  if (state.busy) return;
  addUser('【' + label + '】');
  postChat({ text: '', command });
}

function setBusy(busy) {
  $('sendBtn').disabled = busy;
  $('input').disabled = busy;
  if (!busy) $('input').focus();
}

function renderChips() {
  const chips = state.mode === 'teller' ? TELLER_CHIPS : CUSTOMER_CHIPS;
  const box = $('chips');
  box.innerHTML = '';
  chips.forEach((chip) => {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'chip';
    btn.textContent = chip.label;
    btn.onclick = () => {
      if (chip.command) sendCommand(chip.command, chip.label);
      else sendText(chip.send);
    };
    box.appendChild(btn);
  });
}

function updateIdentity() {
  document.querySelectorAll('.tabs button').forEach((btn) => {
    btn.classList.toggle('active', btn.dataset.mode === state.mode);
  });
  if (state.mode === 'teller') {
    $('who').innerHTML = '柜员 · <b>' + esc(state.tellerName) + '</b>';
    $('logoutBtn').style.display = '';
    $('input').placeholder = '检索产品参数、政策、费率、操作手册或业务 SOP';
  } else {
    $('who').textContent = '客户 · 未登录';
    $('logoutBtn').style.display = 'none';
    $('input').placeholder = '用大白话说您想办什么，例如：我想开个工资卡';
  }
}

function greetCustomer() {
  appendCard({
    type: 'answer',
    title: '您好，我是小汇',
    text: '我是网点智能接待与导览助手，可以帮您：看清要带什么材料、取号或预约、查排队进度、把产品类型和风险讲明白。您用大白话说就行。',
    options: [
      { label: '我要开卡', send: '我想开个借记卡' },
      { label: '我想贷款', send: '我想咨询贷款' },
      { label: '了解理财', send: '有什么理财产品' },
      { label: '我要投诉', send: '我要投诉' },
    ],
    meta: { label: '开场', confidence: 1 },
  });
}

function greetTeller() {
  appendCard({
    type: 'answer',
    title: '柜员模式已开启',
    text: '已通过工号鉴权。可以检索产品参数、最新政策、费率、操作手册与业务 SOP，每条结论都附来源、生效日期与更新时间。',
    bullets: [
      '检索结果仅供行内参考，对外口径请以正式文件和系统为准',
      '命中已废止或即将生效文件时会显式提示',
      '客户模式下不会开放内部知识库与参数检索',
    ],
    options: [
      { label: '查理财双录口径', send: '查询理财销售适当性与双录口径' },
      { label: '查存款利率', send: '2026 年 10 月最新存款利率通知' },
      { label: '查产品参数', send: '查询产品参数：理财' },
      { label: '看审计留痕', command: { name: 'audit_recent', args: {} } },
    ],
    meta: { label: '柜员模式', confidence: 1, mode: 'teller' },
  });
}

async function loadMeta() {
  try {
    const data = await fetch('/api/meta').then((r) => r.json());
    state.meta = data;
    $('branchName').textContent = data.branch.name;
    $('branchInfo').innerHTML =
      '<li><b>营业时间</b><br>' + esc(data.branch.hours) + '</li>' +
      '<li><b>地址</b><br>' + esc(data.branch.address) + '</li>' +
      '<li><b>服务电话</b><br>' + esc(data.branch.phone) + '</li>' +
      '<li><b>知识库</b><br>' + data.kb_docs + ' 篇（分层：产品 / 政策 / 业务 / 网点 / 投诉）</li>';
    $('kbInfo').textContent = '知识库 ' + data.kb_docs + ' 篇' + (data.llm_enabled ? ' · 大模型已接入' : ' · 规则引擎');
    if (data.demo_mode && data.demo) {
      $('demoHint').textContent =
        '演示账号：工号 ' + data.demo.teller_id + ' / 口令 ' + data.demo.otp +
        '；进度查询示例：' + data.demo.ticket + ' + ' + data.demo.tail;
      $('empId').value = data.demo.teller_id;
    }
  } catch (err) {
    $('kbInfo').textContent = '知识库状态未知';
  }
}

async function doLogin() {
  const employee_id = $('empId').value.trim();
  const otp = $('otp').value.trim();
  $('loginErr').textContent = '';
  if (!employee_id || !otp) {
    $('loginErr').textContent = '请填写工号与动态口令';
    return;
  }
  try {
    const res = await fetch('/api/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ employee_id, otp }),
    });
    const data = await res.json();
    if (!res.ok || !data.ok) {
      $('loginErr').textContent = data.error || '鉴权失败';
      return;
    }
    state.token = data.token;
    state.tellerName = data.name + '（' + data.employee_id + '）';
    sessionStorage.setItem('xh_token', state.token);
    sessionStorage.setItem('xh_name', state.tellerName);
    $('loginModal').classList.remove('show');
    $('otp').value = '';
    state.mode = 'teller';
    updateIdentity();
    renderChips();
    greetTeller();
  } catch (err) {
    $('loginErr').textContent = '网络异常，请重试';
  }
}

async function logoutTeller() {
  if (state.token) {
    fetch('/api/logout', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ token: state.token }),
    }).catch(() => {});
  }
  state.token = '';
  state.tellerName = '';
  sessionStorage.removeItem('xh_token');
  sessionStorage.removeItem('xh_name');
  state.mode = 'customer';
  updateIdentity();
  renderChips();
  appendCard({
    type: 'answer',
    title: '已退出柜员模式',
    text: '内部知识库与参数检索已关闭，当前为客户模式。',
    meta: { label: '身份切换' },
  });
}

// ---------------- 事件绑定 ----------------
$('form').addEventListener('submit', (e) => {
  e.preventDefault();
  const value = $('input').value;
  $('input').value = '';
  $('input').style.height = 'auto';
  sendText(value);
});

$('input').addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    $('form').requestSubmit();
  }
});

$('input').addEventListener('input', () => {
  const input = $('input');
  input.style.height = 'auto';
  input.style.height = Math.min(input.scrollHeight, 132) + 'px';
});

document.querySelectorAll('.tabs button').forEach((btn) => {
  btn.addEventListener('click', () => {
    const mode = btn.dataset.mode;
    if (mode === state.mode) return;
    if (mode === 'customer') {
      logoutTeller();
      return;
    }
    if (state.token) {
      state.mode = 'teller';
      updateIdentity();
      renderChips();
      greetTeller();
      return;
    }
    $('loginErr').textContent = '';
    $('loginModal').classList.add('show');
    $('empId').focus();
  });
});

$('cancelLogin').addEventListener('click', () => {
  $('loginModal').classList.remove('show');
  $('loginErr').textContent = '';
});

$('doLogin').addEventListener('click', doLogin);
$('otp').addEventListener('keydown', (e) => {
  if (e.key === 'Enter') doLogin();
});

$('logoutBtn').addEventListener('click', logoutTeller);

$('fontToggle').addEventListener('click', () => {
  document.body.classList.toggle('big');
  localStorage.setItem('xh_big', document.body.classList.contains('big') ? '1' : '0');
});

if (localStorage.getItem('xh_big') === '1') document.body.classList.add('big');

(async function init() {
  await loadMeta();
  if (state.token) {
    state.mode = 'teller';
    updateIdentity();
    renderChips();
    greetTeller();
  } else {
    updateIdentity();
    renderChips();
    greetCustomer();
  }
})();

(() => {
  const panel = document.querySelector('#components-panel');
  const status = document.querySelector('#component-status');
  const directory = document.querySelector('#component-directory');
  let polling = false;
  async function refresh() {
    if (polling) return;
    polling = true;
    try {
      const value = await request('/api/components');
      if (!directory.value) directory.value = value.default_directory;
      status.textContent = `${value.status} · ${value.message}\n运行环境：${value.python}\n模型目录：${value.models}`;
      document.querySelector('#component-log').textContent = value.log || '';
      for (const id of ['component-runtime', 'component-download', 'component-import-d']) {
        document.getElementById(id).disabled = value.status === 'running';
      }
      document.querySelector('#component-cancel').disabled = value.status !== 'running';
    } catch (error) { status.textContent = error.message; }
    finally { polling = false; }
  }
  async function start(action) {
    if (action !== 'cancel' && !confirm(action === 'import_d' ? '校验并登记已有 D 模型包？不会修改包内文件。' : action === 'runtime'
      ? '下载并安装独立 GPU 环境？可能占用十余 GB。不会修改系统 Python 或删除现有模型。'
      : '从 Hugging Face 下载所选模型？需要数 GB 空间和网络流量。')) return;
    try {
      await request('/api/components', {method: 'POST', body: JSON.stringify({
        action, directory: action === 'import_d' ? document.querySelector('#component-d-bundle').value : directory.value,
        model: document.querySelector('#component-model').value,
      })});
      await refresh();
    } catch (error) { status.textContent = error.message; }
  }
  document.querySelector('#component-runtime').onclick = () => start('runtime');
  document.querySelector('#component-download').onclick = () => start('model');
  document.querySelector('#component-cancel').onclick = () => start('cancel');
  document.querySelector('#component-import-d').onclick = () => start('import_d');
  panel.addEventListener('toggle', () => { if (panel.open) refresh(); });
  setInterval(() => { if (panel.open && !document.hidden) refresh(); }, 2000);
})();

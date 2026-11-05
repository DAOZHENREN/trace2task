import {useEffect, useState} from 'react';
import {Button, Combobox, Input, Option} from '@fluentui/react-components';
import {api} from './api';
import {useConfirm} from './ConfirmDialog';
import type {Run} from './types';
import {Field} from './ui';

export function NitrogenTask({busy, onRun, onError, workbench = false}: {
  busy: boolean; onRun: (run: Run) => void; onError: (message: string) => void; workbench?: boolean;
}) {
  const confirm = useConfirm();
  const [pending, setPending] = useState(false);
  const [game, setGame] = useState('');
  const [gameSearch, setGameSearch] = useState('');
  const [gamePickerOpen, setGamePickerOpen] = useState(false);
  const [gameWindows, setGameWindows] = useState<{title: string; process_name: string; process_id: number}[]>([]);
  const [gameScan, setGameScan] = useState(0);
  const [gameLoading, setGameLoading] = useState(true);
  const [gameError, setGameError] = useState('');
  const [gameSeconds, setGameSeconds] = useState('60');
  const [taskText, setTaskText] = useState('');
  const duration = Number(gameSeconds);
  useEffect(() => {
    const request = new AbortController();
    setGameLoading(true);
    setGameError('');
    api<{windows: typeof gameWindows}>('/api/windows', undefined, request.signal).then(({windows}) => {
      if (request.signal.aborted) return;
      setGameWindows(windows.filter(window => /\.exe$/i.test(window.process_name)));
    }).catch(error => {
      if (!request.signal.aborted) {setGameWindows([]); setGameError(String(error));}
    }).finally(() => {
      if (!request.signal.aborted) setGameLoading(false);
    });
    return () => request.abort();
  }, [gameScan]);
  const matches = gameWindows.filter(window =>
    `${window.title} ${window.process_name}`.toLowerCase().includes(gameSearch.trim().toLowerCase()));
  const gameOptions = [...new Map(matches.map(window => [window.process_name.toLowerCase(), window])).values()];
  const selectedGame = gameWindows.find(window => window.process_name === game);
  const isaac = game.toLowerCase() === 'isaac-ng.exe';
  const ambiguousGame = selectedGame && gameWindows.some(window =>
    window.process_name.toLowerCase() === game.toLowerCase() && window.process_id !== selectedGame.process_id);
  async function startNitrogen() {
    if (!selectedGame || ambiguousGame || gameLoading) return;
    const process = selectedGame.process_name;
    if (!await confirm({title: `让 NitroGen 操作 ${process}？`,
      body: isaac ? `请先让《以撒》停在主菜单。启动后切回游戏，按 F5 下移、F6 确认、F7 返回。用 F6 确认「继续游戏」，F8 测试右移，看到角色移动后按 F10，NitroGen 才会开始 ${duration} 秒的操作。F9 可停止。`
        : `请先手动退出暂停菜单，确认角色已能移动。NitroGen 会通过虚拟手柄操作游戏，最多运行 ${duration} 秒。可按 F9 停止。`,
      confirmLabel: '开始游戏控制'})) return;
    setPending(true);
    try {onRun(await api<Run>('/api/nitrogen/start', {
      process, seconds: duration, ...(taskText.trim() ? {task_text: taskText.trim()} : {}),
    }));}
    catch (e) {onError(String(e));} finally {setPending(false);}
  }
  return (
    <section className={workbench ? "target-picker" : "surface engine-config"}>
      <h2>NitroGen · Windows 游戏手柄</h2>
      <p>{isaac ? '请先让《以撒》停在主菜单。启动后，使用同一个虚拟手柄完成绑定和移动测试。' : '先进入可操作的关卡，退出暂停菜单，再启动 NitroGen。'}</p>
      {workbench && <p className="quiet">范围为所选游戏窗口，使用本机 CUDA 和虚拟手柄。每轮模型输入是一张截图。</p>}
      <Field label="截图任务文字（可选）"><Input value={taskText} maxLength={32}
        placeholder="例如：向右走，穿过右侧的门" disabled={busy || pending}
        onChange={(_, data) => setTaskText(data.value)}/></Field>
      <p className="quiet">实验功能。文字会画在模型截图顶部，模型可能忽略它。留空时使用原图。</p>
      <div className="engine-form">
        <Field label="游戏进程"><Combobox aria-label="游戏进程" value={gameSearch}
          selectedOptions={selectedGame ? [game] : []} placeholder="搜索窗口标题或 .exe 名称"
          open={gamePickerOpen} onOpenChange={(_, data) => setGamePickerOpen(data.open)}
          style={{minWidth: 0, width: '100%'}}
          disabled={busy || !!pending || gameLoading}
          onChange={event => {setGameSearch(event.target.value); setGame(''); setGamePickerOpen(true);}}
          onOptionSelect={(_, data) => {if (data.optionValue) {
            setGame(data.optionValue); setGameSearch(data.optionValue);
          }}}>
          {gameOptions.map(window => <Option key={window.process_name} value={window.process_name}
            text={window.process_name}>{window.title} · {window.process_name}</Option>)}
          {!gameOptions.length && <Option disabled>{gameWindows.length ? '没有匹配的窗口' : '没有找到可选窗口'}</Option>}
        </Combobox></Field>
        <Button disabled={busy || !!pending || gameLoading} onClick={() => {
          setGame(''); setGameSearch(''); setGamePickerOpen(false); setGameLoading(true);
          setGameScan(scan => scan + 1);
        }}>{gameLoading ? '正在搜索…' : '刷新运行中的程序'}</Button>
        <Field label="运行时长（秒）"><Input type="number" min={5} max={600} value={gameSeconds} disabled={busy || !!pending}
          onChange={(_, data) => setGameSeconds(data.value)}/></Field>
        <Button appearance="primary" disabled={busy || !!pending || gameLoading || !selectedGame || !!ambiguousGame ||
          !Number.isInteger(duration) || duration < 5 || duration > 600} onClick={() => void startNitrogen()}>{workbench ? "开始任务" : "启动 NitroGen"}</Button>
      </div>
      {ambiguousGame && <p className="quiet">检测到多个同名进程，请只保留要控制的游戏实例，再刷新列表。</p>}
      {gameError && <p role="status" className="quiet">无法读取运行中的程序。{gameError}</p>}
    </section>
  );
}

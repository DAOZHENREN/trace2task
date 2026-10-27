import type {AppState, ModelChoice} from './types';

type Options = AppState['agent_options'];

export function codexEfforts(options: Options, model: string) {
  return options.model_details?.find(m => m.model === model)?.reasoning_efforts ??
    (options.model_details ? [] : options.reasoning_efforts);
}

export function chooseCodexModel(options: Options, model: string, effort: string): ModelChoice {
  const detail = options.model_details?.find(m => m.model === model);
  const efforts = codexEfforts(options, model);
  return {model, reasoning_effort: efforts.includes(effort) ? effort : detail?.default_reasoning_effort || efforts[0] || ''};
}

export function codexChoiceError(options: Options, model: string, effort: string) {
  if (options.catalog_error) return options.catalog_error;
  if (!options.models.includes(model)) return '请选择当前 Codex 目录中的模型。';
  const detail = options.model_details?.find(m => m.model === model);
  if (detail && !detail.input_modalities.includes('image')) return '该模型不支持本任务需要的截图输入。';
  if (!codexEfforts(options, model).includes(effort)) return '请选择该模型支持的思考强度。';
  return '';
}

export function CodexModelOptions({options, value}: {options: Options; value: string}) {
  return <>
    {!options.models.includes(value) && <option value={value} disabled>{value ? `${value} · 已不可用` : '尚未读取模型目录'}</option>}
    {options.models.map(model => {
      const item = options.model_details?.find(m => m.model === model);
      const textOnly = item && !item.input_modalities.includes('image');
      return <option key={model} value={model} disabled={!!textOnly} title={item?.description}>
        {item?.display_name || model}{textOnly ? ' · 不支持截图' : ''}
      </option>;
    })}
  </>;
}

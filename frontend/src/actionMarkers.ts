import type {RawRound} from './modelInputData';
export type ActionMarker = {x: number; y: number; label: string};
const record = (v: unknown): Record<string, unknown> => v && typeof v === 'object' && !Array.isArray(v) ? v as Record<string, unknown> : {};
// Only the normalized execution protocol is accepted; never guess native bin/pixel units.
export function actionMarkers(raw: RawRound | null): ActionMarker[] {
  const plan = record(raw?.prediction);
  if (!Array.isArray(plan.actions) || (plan.coordinate_space != null && plan.coordinate_space !== 'observation_normalized_0_1')) return [];
  const result: ActionMarker[] = [];
  plan.actions.forEach((value, index) => {
    const action = record(value), args = record(action.args);
    const add = (x: unknown, y: unknown, suffix = '') => {
      if (typeof x === 'number' && typeof y === 'number' && Number.isFinite(x) && Number.isFinite(y) && x >= 0 && x <= 1 && y >= 0 && y <= 1)
        result.push({x, y, label: `${index + 1} ${String(action.skill)}${suffix}`});
    };
    add(args.x, args.y);
    add(args.start_x, args.start_y, ' 起点');
    add(args.end_x, args.end_y, ' 终点');
  });
  return result;
}

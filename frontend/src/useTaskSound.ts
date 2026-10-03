import {useEffect, useRef, useState} from 'react';
import type {Run} from './types';
import {active} from './types';

export function useTaskSound(runs: Run[]) {
  const [enabled, setEnabled] = useState(() => {try {return !['off', 'false'].includes(localStorage.getItem('trace2task.taskSound') || '');} catch {return true;}});
  const audio = useRef<AudioContext | null>(null); const previous = useRef(new Map<string, string>());
  function context() {return audio.current ||= new AudioContext();}
  async function play() {
    try {const ctx = context(); await ctx.resume(); const note = ctx.createOscillator(), gain = ctx.createGain();
      note.connect(gain); gain.connect(ctx.destination); note.frequency.setValueAtTime(660, ctx.currentTime);
      gain.gain.setValueAtTime(.045, ctx.currentTime); gain.gain.exponentialRampToValueAtTime(.001, ctx.currentTime + .25);
      note.start(); note.stop(ctx.currentTime + .25);
    } catch { /* Sound is optional; no effect on job results. */ }
  }
  useEffect(() => {
    const unlock = () => {try {void context().resume().catch(() => {});} catch { /* Optional audio. */ }};
    window.addEventListener('pointerdown', unlock, {once: true});
    return () => {window.removeEventListener('pointerdown', unlock); void audio.current?.close(); audio.current = null;};
  }, []);
  useEffect(() => {
    for (const run of runs) {
      const old = previous.current.get(run.job_id);
      if (enabled && old && active({...run, status: old}) && !active(run)) void play();
      previous.current.set(run.job_id, run.status);
    }
  }, [runs, enabled]);
  function set(value: boolean) {setEnabled(value); try {localStorage.setItem('trace2task.taskSound', value ? 'on' : 'off');} catch { /* Keep the in-memory preference. */ }}
  return {enabled, set, play: () => {void play();}};
}

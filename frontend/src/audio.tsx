import {useEffect, useRef, useState} from 'react';
import {Button} from '@fluentui/react-components';
import {api} from './api';

export type Capture = {stream: MediaStream; recorder: MediaRecorder; chunks: Blob[]; started: number; blob?: Blob; finishing?: Promise<Blob>};
let dictationOwner: symbol | null = null;
export const dictationActive = () => dictationOwner !== null;
export async function captureAudio(): Promise<Capture> {
  if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) throw new Error('当前环境不支持麦克风录制。');
  const stream = await navigator.mediaDevices.getUserMedia({audio: {channelCount: 1, echoCancellation: true, noiseSuppression: true}});
  try {
    const mime = ['audio/webm;codecs=opus', 'audio/webm', 'audio/ogg;codecs=opus', 'audio/mp4'].find(t => MediaRecorder.isTypeSupported(t));
    const recorder = new MediaRecorder(stream, mime ? {mimeType: mime} : undefined);
    const result: Capture = {stream, recorder, chunks: [], started: Date.now()};
    recorder.addEventListener('dataavailable', event => {if (event.data.size) result.chunks.push(event.data);});
    recorder.start(1000); return result;
  } catch (e) {stream.getTracks().forEach(t => t.stop()); throw e;}
}
export function releaseAudio(capture?: Capture | null) {
  if (!capture) return;
  if (capture.recorder.state !== 'inactive') capture.recorder.stop();
  capture.stream.getTracks().forEach(t => t.stop());
}
export function finishAudio(capture: Capture): Promise<Blob> {
  if (capture.blob) return Promise.resolve(capture.blob);
  if (capture.finishing) return capture.finishing;
  capture.finishing = new Promise(resolve => {
    const done = () => {capture.stream.getTracks().forEach(t => t.stop());
      capture.blob = new Blob(capture.chunks, {type: capture.recorder.mimeType || 'audio/webm'}); resolve(capture.blob);};
    if (capture.recorder.state === 'inactive') done();
    else {capture.recorder.addEventListener('stop', done, {once: true}); capture.recorder.stop();}
  });
  return capture.finishing;
}
export function audioPayload(blob: Blob): Promise<{audio_base64: string; mime_type: string}> {
  if (!blob.size || blob.size > 20 * 1024 * 1024) return Promise.reject(new Error('音频为空或超过 20 MB；请缩短录音或手动填写。'));
  return new Promise((resolve, reject) => {
    const reader = new FileReader(); reader.onerror = () => reject(new Error('音频读取失败'));
    reader.onload = () => resolve({audio_base64: String(reader.result).split(',', 2)[1], mime_type: blob.type.split(';')[0]});
    reader.readAsDataURL(blob);
  });
}
export function Dictation({disabled, onAppend, onError}: {disabled: boolean; onAppend: (text: string) => void; onError: (text: string) => void}) {
  const capture = useRef<Capture | null>(null); const epoch = useRef(0);
  const owner = useRef(Symbol('dictation'));
  const releaseOwner = () => {if (dictationOwner === owner.current) dictationOwner = null;};
  const [status, setStatus] = useState<'idle' | 'starting' | 'recording' | 'transcribing'>('idle');
  useEffect(() => () => {epoch.current++; releaseAudio(capture.current); releaseOwner();}, []);
  async function toggle() {
    const version = epoch.current;
    try {
      if (!capture.current) {
        if (dictationOwner && dictationOwner !== owner.current) throw new Error('请先结束其他页面的语音输入。');
        dictationOwner = owner.current;
        setStatus('starting'); const next = await captureAudio();
        if (version !== epoch.current) {releaseAudio(next); return;}
        capture.current = next; setStatus('recording');
      } else {
        setStatus('transcribing'); const blob = await finishAudio(capture.current); capture.current = null;
        const result = await api<{transcript?: string; transcription?: {transcript?: string}}>('/api/transcribe', {...await audioPayload(blob), context: '工作台语音输入'});
        releaseOwner();
        if (version === epoch.current) {onAppend(result.transcript || result.transcription?.transcript || ''); setStatus('idle');}
      }
    } catch (e) {releaseAudio(capture.current); capture.current = null; releaseOwner(); if (version === epoch.current) {setStatus('idle'); onError(String(e));}}
  }
  return <Button appearance="subtle" size="small" disabled={(disabled && status !== 'recording') || ['starting', 'transcribing'].includes(status)} onClick={() => void toggle()}>
    {status === 'recording' ? '结束语音输入' : status === 'starting' ? '请求麦克风…' : status === 'transcribing' ? '本地转写中…' : '语音输入'}
  </Button>;
}

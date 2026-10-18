import { assetManifest, validateReferences } from './references.js';

const BASE = ['integrated_multimodal_description', 'overall_soundscape', 'non_diegetic_music'];
const REF = ['subject_definitions', 'summary', 'retention_analysis', 'detailed_description', 'overall_soundscape', 'non_diegetic_music'];

export function isH3Prompt(text) {
  const trimmed = String(text).trimStart();
  return /^(?:subject_definitions:|integrated_multimodal_description:|For the target video,|How the reference pictures align with the target video)/.test(trimmed);
}

export function validateH3Prompt(text, mode, duration, items, frames) {
  const prompt = String(text).trim();
  if (!prompt) throw new Error('H3 提示词不能为空');
  if (Array.from(prompt).length > 7000) throw new Error('H3 提示词超过 7000 字符');
  validateReferences(prompt, mode, items, frames);
  if (mode !== 'references') {
    for (const asset of assetManifest(mode, items, frames)) {
      if (!prompt.includes(asset.tag)) throw new Error(`${asset.role === 'last_frame' ? '尾帧' : '首帧'}缺少 ${asset.tag} 引用`);
    }
  }
  const expected = mode === 'references' ? REF : BASE;
  const headers = [...prompt.matchAll(/^([a-z_]+):/gm)].map(match => match[1]);
  if (JSON.stringify(headers) !== JSON.stringify(expected)) throw new Error(`H3 字段应按顺序且各出现一次：${expected.join(' → ')}`);
  const sections = {};
  for (let index = 0; index < expected.length; index++) {
    const start = prompt.search(new RegExp(`^${expected[index]}:`, 'm')) + expected[index].length + 1;
    const end = index + 1 < expected.length ? prompt.search(new RegExp(`^${expected[index + 1]}:`, 'm')) : prompt.length;
    sections[expected[index]] = prompt.slice(start, end).trim();
    if (!sections[expected[index]]) throw new Error(`${expected[index]} 内容不能为空`);
  }
  if (mode !== 'references') {
    const first = Boolean(frames?.first_frame), last = Boolean(frames?.last_frame);
    if (first && !last && !prompt.startsWith('For the target video, at 0.00 seconds into the target video, <Picture 1>')) throw new Error('仅首帧模式缺少 H3 首帧对齐指令');
    if (last && !prompt.startsWith('How the reference pictures align with the target video')) throw new Error('尾帧模式缺少 H3 图片对齐指令');
    if (last && !prompt.includes(`${Number(duration).toFixed(2)}-second mark`)) throw new Error('尾帧对齐时间与生成时长不一致');
  }
  const timelineField = mode === 'references' ? 'detailed_description' : 'integrated_multimodal_description';
  const timeline = sections[timelineField];
  if (!timeline.includes('[Shot 1]')) throw new Error('H3 提示词缺少 [Shot 1]');
  const shots = [...timeline.matchAll(/\[Shot (\d+)\](?: At (\d{2}):(\d{2})\.(\d{3}))?/g)];
  if (shots[0]?.[1] !== '1' || shots[0]?.[2]) throw new Error('首镜须为不带切换时间的 [Shot 1]');
  let previous = 0;
  for (let i = 1; i < shots.length; i++) {
    const shot = shots[i];
    const time = Number(shot[2]) * 60 + Number(shot[3]) + Number(shot[4]) / 1000;
    if (Number(shot[1]) !== i + 1 || !shot[2] || !Number.isFinite(time) || time <= previous || time >= duration) {
      throw new Error(`镜头 ${i + 1} 的编号或切换时间无效`);
    }
    previous = time;
  }
  if (mode === 'references') {
    // 字段冒号后可直接写首项内容；先提取正文再检查行首标签。
    const defs = sections.subject_definitions;
    const retention = sections.retention_analysis;
    const subjects = [...new Set([...prompt.matchAll(/<Subject (\d+)>/g)].map(match => Number(match[1])))];
    if (assetManifest(mode, items, frames).some(asset => asset.kind !== 'Audio') && !subjects.length) throw new Error('多参考 H3 提示词未归纳 <Subject 1> 可见主体');
    for (const number of subjects) {
      if (!new RegExp(`^<Subject ${number}>\\s`, 'm').test(defs) || !new RegExp(`^<Subject ${number}>.*:\\s*(?:fully_preserved|partially_preserved|attribute_transfer|weak_reference)`, 'm').test(retention)) {
        throw new Error(`<Subject ${number}> 缺少定义或保留关系`);
      }
    }
    for (const [,kind,number] of defs.matchAll(/^<(Picture|Video|Audio) (\d+)>/gm)) {
      const markers = kind === 'Audio' ? 'fully_copy|partially_copy|reference|weak_reference' : 'fully_preserved|partially_preserved|attribute_transfer|weak_reference';
      if (!new RegExp(`^<${kind} ${number}>.*:\\s*(?:${markers})`, 'm').test(retention)) {
        throw new Error(`<${kind} ${number}> 缺少对应保留关系`);
      }
    }
    if (!/^summary:\s*\[(?:reference generation|keyframe completion|video editing|video continuation|audio reuse|audio reference)/m.test(prompt)) {
      throw new Error('summary 缺少 H3 任务类型');
    }
  }
  return prompt;
}

/* 三个生成入口共用后端计时快照，只估算时间，不制造采样百分比。 */
(() => {
  function presentation(job, now = Date.now() / 1000) {
    if(job?.cloud && !job.pendingSubmission && ['queued','running'].includes(job.status)){
      const estimate=job.estimated_remaining_seconds;
      const observed=Number(job.eta_updated_at);
      const valid=Number.isInteger(estimate)&&estimate>=0&&Number.isFinite(observed)&&observed>0;
      const stale=valid && now-observed>90;
      const deadline=valid?observed+estimate:null;
      const remaining=valid?deadline-now:null;
      const waiting=!valid||stale;
      const clock=valid&&!stale&&remaining>0?new Date(deadline*1000).toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit',hour12:false}):'';
      const duration=remaining>=60?`约 ${Math.max(1,Math.ceil(remaining/60))} 分钟`:`约 ${Math.max(10,Math.ceil(remaining/10)*10)} 秒`;
      const started=Number(job.created_at || job.running_at);
      const elapsed=started>0?Math.max(0,Math.floor(now-started)):0;
      return {
        remaining:waiting?'正在获取云端预计时间':remaining>0?`平台预计还需${duration}`:estimate===0?'云端正在收尾，等待状态更新':'用时超过平台预估，仍在处理',
        clock:clock?`${clock} 左右完成`:'',
        evidence:stale?'平台预估暂未更新，正在同步':valid?'平台动态估计，实际完成时间可能调整':'平台暂未提供预计时间',
        stage:job.status==='queued'?'当前阶段 · 云端排队':'当前阶段 · 云端处理',
        elapsed:`已用 ${Math.floor(elapsed/60)}分${String(elapsed%60).padStart(2,'0')}秒`,
      };
    }
    const timing = job?.sampling_timing;
    if (job?.status !== 'running' || !timing || !['sample', 'decode', 'save'].includes(timing.stage)) return null;
    const deadline = Number(timing.estimated_completion_at);
    const valid = timing.estimated_completion_at != null && Number.isFinite(deadline) && deadline > 0;
    const remaining = valid ? deadline - now : null;
    const late = valid && remaining <= 0;
    const estimate = remaining >= 60 ? `约 ${Math.max(1, Math.round(remaining / 60))} 分钟`
      : `约 ${Math.max(10, Math.ceil(remaining / 10) * 10)} 秒`;
    const clock = valid ? new Date(deadline * 1000).toLocaleTimeString('zh-CN', {hour:'2-digit', minute:'2-digit', hour12:false}) : '';
    const first = Number(timing.first_step_seconds);
    const average = Number(timing.average_step_seconds);
    const evidence = [];
    if (first > 0 && Number.isFinite(first)) evidence.push(`首步 ${Math.round(first)} 秒`);
    if (average > 0 && Number.isFinite(average)) evidence.push(`后续平均 ${Math.round(average)} 秒`);
    evidence.push(timing.tail_source === 'history' ? '含同规格解码保存耗时' : '含解码保存预留');
    if (job.kind === 'generate') evidence.push(job.request?.upscale ? '生成后继续对齐与超分' : '生成后继续对齐区间');
    const started = Number(job.started_at || job.running_at);
    const seconds = Math.max(0, Math.floor(started > 0 ? now - started : Number(job.run_seconds) || 0));
    return {
      remaining: late ? '用时比预期稍长' : valid ? `预计还需${estimate}` : timing.stage === 'sample' ? '正在估算完成时间' : '正在处理成片',
      clock: valid && !late ? `${clock} 左右完成` : '',
      evidence: valid ? evidence.join(' · ') : '正在记录实际采样耗时',
      stage: timing.stage === 'sample' ? timing.total_steps > 0 ? `当前阶段 ${timing.completed_steps}/${timing.total_steps}` : '当前阶段 · 首步采样' : timing.stage === 'decode' ? '当前阶段 · 解码音画' : '当前阶段 · 合成保存',
      elapsed: `已用 ${Math.floor(seconds / 60)}分${String(seconds % 60).padStart(2,'0')}秒`,
    };
  }

  function render(element, job, now) {
    if (!element) return;
    const view = presentation(job, now);
    element.hidden = !view;
    if (!view) return;
    for (const field of ['remaining', 'clock', 'evidence', 'stage', 'elapsed']) {
      const target = element.querySelector(`[data-eta-${field}]`) || element.parentElement?.querySelector(`[data-eta-${field}]`);
      if (target && target.textContent !== view[field]) target.textContent = view[field];
      if (target && field === 'clock') target.hidden = !view.clock;
      if (target && field === 'evidence') target.hidden = !job?.cloud || !view.evidence;
    }
  }

  function renderPipeline(element, job) {
    if (!element) return;
    let stage = null;
    if (job?.status === 'running') {
      const message = String(job.message || '');
      const node = Number(message.match(/节点\s*(\d+)/)?.[1]);
      if (element.dataset.pipelineKind === 'clone') {
        stage = job.phase === 'upscale' ? 3 : ['h3','alignment'].includes(job.phase) || job.sampling_timing ? 2
          : ['appearance','supplement'].includes(job.phase) || job.kind === 'analyze' ? 1 : 0;
      } else {
        stage = ['decode','save'].includes(job.sampling_timing?.stage) || [10,23,91,92].includes(node) ? 3
          : job.sampling_timing?.stage === 'sample' || node === 14 ? 2
          : /参考画面编码|参考音频编码|文本与多模态编码|理解素材/.test(message) || node === 104 || node >= 101 && node <= 103 || node >= 201 && node <= 403 ? 1 : 0;
      }
      if (job.cloud && !job.sampling_timing && !['h3','alignment','upscale'].includes(job.phase)) stage = null;
      if (job.phase === 'prompt') stage = -1;
      if (job.phase === 'uploading' || job.pendingSubmission && job.phase === 'submitting') stage = null;
    }
    for (const item of element.querySelectorAll('[data-pipeline-step]')) {
      const index = Number(item.dataset.pipelineStep);
      const active = index === stage;
      item.classList.toggle('is-active', active);
      if (job) item.classList.toggle('done', job.status === 'done' || stage !== null && index < stage);
      if (active) item.setAttribute('aria-current','step');
      else item.removeAttribute('aria-current');
    }
  }
  globalThis.GenerationEta = Object.freeze({ presentation, render, renderPipeline });
})();

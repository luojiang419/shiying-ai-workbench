// 清理记录不删除成片；有输出但尚未下载的任务继续保留并重试保存。
export function abnormalTask(task) {
  if (task.historyHidden || task.localPath) return false;
  const status = String(task.status || '').toLowerCase();
  if (['queued','running','pending','submitted','processing','moderating','preparing','waiting','uploading'].includes(status)) return false;
  if (['done','completed','succeeded'].includes(status)) {
    return task.output_available === false || (!task.output && !task.externalUrl && !task.images?.length);
  }
  return true;
}

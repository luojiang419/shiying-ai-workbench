// 桌面云端素材直接保存 File；与本地 Python 素材 ID 分开，刷新及排队后仍可读取。
const PREFIX = 'desktop-cloud-';
let database;
export const isNativeMaterial = id => String(id).startsWith(PREFIX);
async function db() {
  database ||= new Promise((resolve, reject) => {
    const request = indexedDB.open('minimax-clone-materials-v1', 1);
    request.onupgradeneeded = () => request.result.createObjectStore('assets', {keyPath:'id'});
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(new Error('无法打开参考素材缓存'));
  });
  return database;
}
export async function saveNativeMaterial(file, kind) {
  if (!['image','video'].includes(kind) || !file?.size) throw new Error('请选择有效的参考素材');
  if (file.size > (kind === 'image' ? 128 : 384) * 1024 * 1024)
    throw new Error(kind === 'image' ? '工作台最多处理 128 MiB 的原始参考图' : '云端参考视频最大 384 MiB');
  if (kind === 'image') {
    let bitmap;
    try { bitmap = await createImageBitmap(file); }
    catch { throw new Error('无法读取参考图片，请使用 PNG、JPG 或 WebP 图片'); }
    finally { bitmap?.close(); }
  }
  const asset = {id:PREFIX + crypto.randomUUID(), kind, name:file.name, file};
  const connection = await db();
  await new Promise((resolve,reject) => {
    const tx = connection.transaction('assets','readwrite');
    tx.objectStore('assets').add(asset);
    tx.oncomplete = resolve;
    tx.onerror = tx.onabort = () => reject(new Error('保存参考素材失败，请检查磁盘空间后重试'));
  });
  return {id:asset.id,name:asset.name,kind};
}
export async function readNativeMaterial(id, kind) {
  if (!isNativeMaterial(id)) throw new Error('不是桌面云端素材');
  const connection = await db();
  const asset = await new Promise((resolve,reject) => {
    const request = connection.transaction('assets').objectStore('assets').get(id);
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(new Error('读取参考素材失败'));
  });
  if (!asset?.file || kind && asset.kind !== kind) throw new Error('参考素材已不可用，请重新添加');
  return asset;
}

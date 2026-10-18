"""只读旧 Qwen 库，幂等复制 Key 与其历史产物，不依赖旧项目继续运行。"""
from __future__ import annotations

import json
import re
import shutil
import sqlite3
import time
from contextlib import closing
from pathlib import Path

from fastapi import HTTPException


def _safe_id(value):
    return bool(re.fullmatch(r'[a-f0-9]{32}', str(value)))


def _source_file(base: Path, relative: str) -> Path:
    path = (base / relative).resolve()
    if not path.is_relative_to(base.resolve()) or not path.is_file():
        raise ValueError('旧 Qwen 的图片或参考图不存在，或路径超出原数据目录')
    return path


def migrate_legacy(studio, source_database: Path) -> dict:
    source_database = Path(source_database).resolve()
    marker = 'legacy_migration:' + str(source_database).casefold()
    saved = studio.keys.setting(marker)
    if saved:
        return {**saved, 'already_migrated': True}
    if not source_database.is_file():
        raise HTTPException(404, '未找到旧 Qwen 图库数据库')
    with closing(sqlite3.connect(source_database.as_uri() + '?mode=ro', uri=True)) as db:
        db.row_factory = sqlite3.Row
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if 'api_keys' not in tables:
            return {'keys': 0, 'images': 0, 'jobs': 0, 'source': str(source_database)}
        keys = [dict(row) for row in db.execute('SELECT * FROM api_keys')]
        if not keys:
            return {'keys': 0, 'images': 0, 'jobs': 0, 'source': str(source_database)}
        image_columns = {row['name'] for row in db.execute('PRAGMA table_info(images)')}
        images = [dict(row) for row in db.execute('SELECT * FROM images WHERE key_id IS NOT NULL')] if 'key_id' in image_columns else []
        jobs = [dict(row) for row in db.execute('SELECT * FROM generation_jobs WHERE key_id IS NOT NULL')] if 'generation_jobs' in tables else []
        reference_sets = {row['image_id']: row['reference_set'] for row in db.execute('SELECT * FROM image_reference_sets')} if 'image_reference_sets' in tables else {}
    for key in keys:
        for name, default in [('token_value', None), ('disabled', 0), ('calls_today', 0), ('calls_day', None), ('last_used_at', None)]:
            key.setdefault(name, default)
    key_names = {item['id']: item['name'] for item in keys}
    for image in images:
        image.setdefault('key_name', key_names.get(image['key_id']))
        image.setdefault('favorite', False)
    known_keys = {item['id'] for item in keys}
    images = [item for item in images if item['key_id'] in known_keys]
    jobs = [item for item in jobs if item['key_id'] in known_keys]
    image_jobs = {image_id: job['id'] for job in jobs for image_id in json.loads(job['image_ids'])}
    prepared_jobs = {}
    for job in jobs:
        if not _safe_id(job['id']):
            raise ValueError('旧 Qwen 任务 ID 无效')
        item = {**job, 'images': [], 'references': [], 'legacy': True}
        item['phase'] = job['status']
        if job['status'] in {'queued', 'running'}:
            item.update(status='failed', phase='failed', error='旧服务未完成的历史任务，迁移后保留记录，请重新提交')
        prepared_jobs[job['id']] = item
    files = []
    prepared_images = []
    reference_jobs = set()
    for original in images:
        image_id = original['id']
        if not _safe_id(image_id):
            raise ValueError('旧 Qwen 图片 ID 无效')
        job_id = image_jobs.get(image_id, image_id)
        job = prepared_jobs.setdefault(job_id, {'id': job_id, 'status': 'completed', 'phase': 'completed',
            'prompt': original['prompt'], 'created_at': original['created_at'], 'finished_at': original['created_at'],
            'total': 1, 'completed': 1, 'images': [], 'references': [], 'legacy': True,
            'key_id': original['key_id'], 'key_name': original['key_name'], 'historical': image_id not in image_jobs})
        item = {**original, 'job_id': job_id, 'filename': f'{image_id}.png', 'legacy_filename': original['filename'],
                'url': f'/api/image-studio/images/{image_id}/file', 'legacy': True,
                'batch_index': json.loads(job.get('image_ids', '[]')).index(image_id) + 1 if image_id in json.loads(job.get('image_ids', '[]')) else 1,
                'native_resolution': None, 'sampling_size': '', 'favorite': bool(original['favorite'])}
        source = _source_file(source_database.parent / 'images', original['filename'])
        files.append((source, studio.directory / f'{image_id}.png'))
        reference_set = reference_sets.get(image_id)
        if reference_set and job_id not in reference_jobs:
            if not _safe_id(reference_set):
                raise ValueError('旧 Qwen 参考图集合 ID 无效')
            reference_root = source_database.parent / 'references' / reference_set
            references = sorted(reference_root.glob('*.png'), key=lambda path: int(path.stem) if path.stem.isdigit() else 999)
            for index, path in enumerate(references):
                source = _source_file(reference_root, path.name)
                files.append((source, studio.directory / 'references' / job_id / f'{index}.png'))
                job['references'].append({'name': f'参考图 {index + 1}.png',
                    'url': f'/api/image-studio/jobs/{job_id}/references/{index}'})
            reference_jobs.add(job_id)
        job['reference_count'] = len(job['references'])
        job.setdefault('size', f"{item['width']}x{item['height']}")
        job.setdefault('steps', item['steps'])
        job.setdefault('seed', item['seed'])
        job['images'].append(item)
        prepared_images.append(item)
    columns = ['id', 'name', 'token_hash', 'token_prefix', 'token_value', 'disabled', 'created_at', 'last_used_at', 'calls_today', 'calls_day']
    created = []
    with closing(studio.keys.db()) as db:
        db.execute('BEGIN IMMEDIATE')
        try:
            for key in keys:
                conflict = db.execute('SELECT id,token_hash FROM api_keys WHERE id=? OR name=? COLLATE NOCASE', (key['id'], key['name'])).fetchone()
                if conflict and (conflict['id'] != key['id'] or conflict['token_hash'] != key['token_hash']):
                    raise HTTPException(409, '旧 Key 与当前密钥记录冲突，已停止迁移并保留当前配置')
            for item in prepared_images:
                if db.execute('SELECT 1 FROM images WHERE id=?', (item['id'],)).fetchone():
                    raise HTTPException(409, '旧图片 ID 已存在，已停止迁移，避免覆盖当前作品')
            for job_id in prepared_jobs:
                if db.execute('SELECT 1 FROM jobs WHERE id=?', (job_id,)).fetchone():
                    raise HTTPException(409, '旧任务 ID 已存在，已停止迁移，避免覆盖当前任务')
            for source, destination in files:
                if not destination.resolve().is_relative_to(studio.directory.resolve()):
                    raise ValueError('迁移目标超出当前图片目录')
                if destination.exists():
                    raise HTTPException(409, '迁移目标文件已存在，已停止迁移，避免覆盖')
                destination.parent.mkdir(parents=True, exist_ok=True)
                created.append(destination)
                shutil.copyfile(source, destination)
            for key in keys:
                db.execute(f'INSERT OR IGNORE INTO api_keys({",".join(columns)}) VALUES({",".join("?" for _ in columns)})',
                           [key.get(column) for column in columns])
            for item in prepared_images:
                db.execute('INSERT INTO images VALUES(?,?)', (item['id'], json.dumps(item, ensure_ascii=False)))
            for item in prepared_jobs.values():
                db.execute('INSERT INTO jobs VALUES(?,?)', (item['id'], json.dumps(item, ensure_ascii=False)))
            result = {'source': str(source_database), 'keys': len(keys), 'images': len(prepared_images),
                      'jobs': len(prepared_jobs), 'migrated_at': int(time.time())}
            for name, value in [(marker, result), ('authentication_enabled', True), ('legacy_ports_enabled', True), ('migration', result)]:
                db.execute('INSERT OR REPLACE INTO qwen_api_settings VALUES(?,?)', (name, json.dumps(value, ensure_ascii=False)))
            db.commit()
        except Exception:
            db.rollback()
            # 只回收本轮成功创建的目标文件，原数据与当前既有文件不受影响。
            for destination in created:
                destination.unlink(missing_ok=True)
            raise
    return result

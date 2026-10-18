"""旧 Qwen 生图接口兼容入口，共用当前图片任务库，避开 H3 的同名路由。"""
from __future__ import annotations

import json
import os
from contextlib import closing

from fastapi import Depends, FastAPI, HTTPException, Response
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware

from .qwen_workflow import MODEL
from .qwen_keys import QwenPrincipal, assert_visible


def install_qwen_api(app, studio, request_type, generate, edit, models):
    compat = FastAPI(title='Qwen Image 2.1 Compatibility API')
    origins = [origin.strip() for origin in os.environ.get('QWEN_IMAGE_CORS_ORIGINS', '').split(',') if origin.strip()]
    if origins:
        compat.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=['GET', 'POST', 'DELETE'],
                              allow_headers=['Authorization', 'Content-Type'])

    def image_result(item, index=None):
        return {**item, 'filename': item.get('filename', f'{item["id"]}.png'),
                'favorite': bool(item.get('favorite', False)),
                'batch_index': item.get('batch_index', index or 1),
                'url': f'/qwen/api/images/{item["id"]}/file'}

    def job_result(job, principal):
        assert_visible(job, principal)
        active = job['status'] in {'queued', 'running'}
        images = []
        for index, image in enumerate(job.get('images', []), 1):
            try:
                image = studio.get('images', image['id'])
                assert_visible(image, principal)
                images.append(image_result(image, index))
            except HTTPException as exc:
                if exc.status_code != 404:
                    raise
        phase = job.get('phase', job['status'])
        total, step = job.get('sample_total'), job.get('sample_step')
        sampling = active and phase == 'sampling' and isinstance(total, (int, float)) and total > 0 and isinstance(step, (int, float))
        return {**job, 'id': 'history-' + job['id'] if job.get('historical') else job['id'], 'images': images, 'progress': {
            'phase': 'enhancing' if phase == 'prompt' else phase,
            'current_image': min(job['total'], job['completed'] + 1),
            'prompt_ready': phase != 'prompt', 'events_available': sampling,
            'sample_step': job.get('sample_step') if sampling else None,
            'sample_total': job.get('sample_total') if sampling else None,
            'done_phases': [],
        }}

    @compat.get('/health')
    def health(principal: QwenPrincipal = Depends(studio.keys.principal)):
        try:
            studio.comfy._get_json('/system_stats', timeout=5)
            ready = studio.status()['ready']
        except Exception:
            ready = False
        return {'status': 'ok' if ready else 'comfyui_unavailable', 'model': MODEL,
                'comfyui': ready, 'transparent_background': True, 'prompt_enhancement': False}

    compat.add_api_route('/v1/models', models, methods=['GET'])
    compat.add_api_route('/v1/images/generations', generate, methods=['POST'])
    compat.add_api_route('/v1/images/edits', edit, methods=['POST'])

    # 使用传入的 Pydantic 类型生成请求 schema，同时避免与图片服务循环导入。
    def create_job(payload, principal: QwenPrincipal = Depends(studio.keys.principal)):
        return job_result(studio.create(payload, principal), principal)
    create_job.__annotations__['payload'] = request_type
    compat.add_api_route('/api/jobs', create_job, methods=['POST'], status_code=202)

    @compat.get('/api/jobs')
    def jobs(principal: QwenPrincipal = Depends(studio.keys.principal)):
        items = studio.list('jobs', key_id=principal.id if principal.role == 'api' else None)
        with closing(studio.db()) as db:
            query = "SELECT data FROM jobs" + (" WHERE json_extract(data,'$.key_id')=?" if principal.role == 'api' else '')
            statuses = [json.loads(row[0]).get('status') for row in db.execute(query, (principal.id,) if principal.role == 'api' else ())]
        return {'jobs': [job_result(job, principal) for job in items], 'limit': 100,
                'counts': {'running': statuses.count('running'), 'queued': statuses.count('queued')}}

    @compat.get('/api/jobs/{job_id}')
    def job(job_id: str, principal: QwenPrincipal = Depends(studio.keys.principal)):
        return job_result(studio.get('jobs', job_id.removeprefix('history-')), principal)

    @compat.get('/api/images')
    def images(q: str = '', favorite: bool = False, limit: int = 100, account_id: str = '', key_id: str = '', principal: QwenPrincipal = Depends(studio.keys.principal)):
        if account_id and account_id != 'legacy':
            raise HTTPException(422, '旧账号尚未迁移，请使用 Key 来源筛选')
        items = studio.work_items()
        selected_key = principal.id if principal.role == 'api' else key_id
        if selected_key:
            items = [item for item in items if item.get('key_id') == selected_key]
        if account_id == 'legacy' and principal.role == 'admin':
            items = [item for item in items if not item.get('key_id') and not item.get('owner_id')]
        items.sort(key=lambda item: (item['created_at'], item['id']), reverse=True)
        items = [item for item in items if (not q or q.casefold() in item['prompt'].casefold())
                 and (not favorite or item.get('favorite'))]
        return {'images': [image_result(item) for item in items[:max(1, min(limit, 500))]]}

    @compat.get('/api/images/{image_id}')
    def image(image_id: str, principal: QwenPrincipal = Depends(studio.keys.principal)):
        item = studio.get('images', image_id)
        assert_visible(item, principal)
        job = studio.get('jobs', item['job_id']) if item.get('job_id') else {}
        return {**image_result(item), 'references': [f'/qwen/api/images/{image_id}/references/{index}'
                                                   for index, _ in enumerate(job.get('references', []))]}

    @compat.get('/api/images/{image_id}/references/{index}')
    def reference(image_id: str, index: int, principal: QwenPrincipal = Depends(studio.keys.principal)):
        item = studio.get('images', image_id)
        assert_visible(item, principal)
        if not item.get('job_id'):
            raise HTTPException(404, '参考图不存在')
        return FileResponse(studio.reference_path(item['job_id'], index), media_type='image/png')

    @compat.get('/api/images/{image_id}/file')
    def file(image_id: str, token: str = '', principal: QwenPrincipal = Depends(studio.keys.principal)):
        assert_visible(studio.get('images', image_id), principal)
        return FileResponse(studio.image_path(image_id), media_type='image/png')

    @compat.post('/api/images/{image_id}/favorite')
    def favorite(image_id: str, principal: QwenPrincipal = Depends(studio.keys.principal)):
        with closing(studio.db()) as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT data FROM images WHERE id=?', (image_id,)).fetchone()
            if not row:
                raise HTTPException(404, '图片不存在')
            item = json.loads(row[0])
            assert_visible(item, principal)
            item['favorite'] = not item.get('favorite', False)
            db.execute('UPDATE images SET data=? WHERE id=?', (json.dumps(item, ensure_ascii=False), image_id))
            db.commit()
        return image_result(item)

    @compat.delete('/api/images/{image_id}', status_code=204)
    def delete(image_id: str, principal: QwenPrincipal = Depends(studio.keys.principal)):
        assert_visible(studio.get('images', image_id), principal)
        studio.delete_image(image_id)
        return Response(status_code=204)

    app.mount('/qwen', compat)
    from .qwen_key_routes import install_key_routes
    install_key_routes(app, compat, studio)
    return compat

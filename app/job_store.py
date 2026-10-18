from __future__ import annotations

import json
import math
import sqlite3
import statistics
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable, Iterator

from .config import JOB_DATABASE_FILE

JOB_STATUSES = {"queued", "running", "done", "error", "canceled"}
JOB_COLUMNS = (
    "id",
    "status",
    "message",
    "created_at",
    "started_at",
    "updated_at",
    "output",
    "error",
    "completed_at",
    "elapsed_seconds",
    "mode",
    "prompt",
    "user_brief",
    "prompt_plan_json",
    "resolution",
    "duration",
    "steps",
    "seed",
    "first_frame",
    "last_frame",
    "reference_images_json",
    "reference_videos_json",
    "reference_audios_json",
    "source",
    "acceleration_mode",
    "nsfw_enabled",
    "nsfw_profile",
    "depth_control",
    "control_video",
    "control_strength",
    "sampling_timing_json",
    "requested_resolution",
    "resolution_adjustment",
)


class JobStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path or JOB_DATABASE_FILE)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def save(self, job: dict, source: str = "api") -> None:
        values = _job_values(job, source=source)
        placeholders = ", ".join("?" for _ in JOB_COLUMNS)
        assignments = ", ".join(
            f"{column}=excluded.{column}" for column in JOB_COLUMNS if column != "id"
        )
        sql = f"""
            INSERT INTO jobs ({", ".join(JOB_COLUMNS)})
            VALUES ({placeholders})
            ON CONFLICT(id) DO UPDATE SET {assignments}
            WHERE excluded.updated_at >= jobs.updated_at
        """
        with self._connection() as connection:
            connection.execute(sql, values)

    def get(self, job_id: str) -> dict | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM jobs WHERE id = ?",
                (job_id,),
            ).fetchone()
        return _row_to_job(row) if row else None

    def generation_tail_seconds(self, job: dict) -> float | None:
        """仅复用同规格、同模型和加速模式的最近成功任务解码保存耗时。"""
        with self._connection() as connection:
            rows = connection.execute(
                """SELECT sampling_timing_json, completed_at FROM jobs
                   WHERE status='done' AND resolution=? AND duration=? AND steps=? AND mode=?
                     AND acceleration_mode IS ? AND nsfw_profile IS ? AND depth_control=?
                   ORDER BY completed_at DESC LIMIT 20""",
                (job.get("resolution"), job.get("duration"), job.get("steps"), job.get("mode"),
                 job.get("acceleration_mode"), job.get("nsfw_profile"), int(bool(job.get("depth_control")))),
            ).fetchall()
        samples = []
        for row in rows:
            timing = json.loads(row["sampling_timing_json"] or "null")
            if not isinstance(timing, dict) or timing.get("reference_model") != job.get("reference_model", "ref2va"):
                continue
            finished = timing.get("sampling_finished_at") if isinstance(timing, dict) else None
            if isinstance(finished, (int, float)) and row["completed_at"] is not None:
                duration = row["completed_at"] - finished
                if math.isfinite(duration) and 0 < duration < 7200:
                    samples.append(duration)
                    if len(samples) >= 5:
                        break
        return max(15, statistics.median(samples) * 1.15) if len(samples) >= 3 else None

    def list(
        self,
        offset: int = 0,
        limit: int = 100,
        status: str | None = None,
        order: str = "desc",
    ) -> dict:
        if offset < 0:
            raise ValueError("任务分页偏移不能小于 0")
        if limit < 1 or limit > 100:
            raise ValueError("任务分页数量必须在 1 到 100 之间")
        if status is not None and status not in JOB_STATUSES:
            raise ValueError("任务状态筛选值无效")
        if order not in {"asc", "desc"}:
            raise ValueError("任务排序只支持 asc 或 desc")

        where = " WHERE status = ?" if status else ""
        parameters: list[object] = [status] if status else []
        direction = "ASC" if order == "asc" else "DESC"
        with self._connection() as connection:
            total = connection.execute(
                f"SELECT COUNT(*) FROM jobs{where}",
                parameters,
            ).fetchone()[0]
            rows = connection.execute(
                f"SELECT * FROM jobs{where} ORDER BY created_at {direction}, id {direction} LIMIT ? OFFSET ?",
                [*parameters, limit, offset],
            ).fetchall()

        items = [_row_to_job(row) for row in rows]
        next_offset = offset + len(items)
        return {
            "items": items,
            "total": total,
            "offset": offset,
            "limit": limit,
            "next_offset": next_offset,
            "has_more": next_offset < total,
            "order": order,
            "status": status,
        }

    def matching_outputs(self, query: str) -> set[str]:
        # 只读取匹配的输出键；大提示词留在 SQLite，打开当前页时再查询。
        where = ["status = 'done'", "output IS NOT NULL"]
        params: list[object] = []
        if query:
            where.append("(instr(lower(prompt), lower(?)) > 0 OR instr(lower(user_brief), lower(?)) > 0 OR instr(lower(id), lower(?)) > 0 OR instr(lower(output), lower(?)) > 0)")
            params.extend([query] * 4)
        with self._connection() as connection:
            return {row[0] for row in connection.execute(f"SELECT output FROM jobs WHERE {' AND '.join(where)}", params)}

    def completed_outputs(self) -> list[tuple[str, str]]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT id, output FROM jobs WHERE status = 'done' AND output IS NOT NULL"
            ).fetchall()
        return [(row["id"], row["output"]) for row in rows]

    def clear_failed(self, missing_output_ids: Iterable[str] = ()) -> list[str]:
        retained_statuses = sorted(JOB_STATUSES - {"error", "canceled"})
        placeholders = ", ".join("?" for _ in retained_statuses)
        missing_ids = list(dict.fromkeys(missing_output_ids))
        with self._connection() as connection:
            rows = connection.execute(
                f"DELETE FROM jobs WHERE status NOT IN ({placeholders}) RETURNING id",
                retained_statuses,
            ).fetchall()
            deleted = [row[0] for row in rows]
            for job_id in missing_ids:
                cursor = connection.execute(
                    "DELETE FROM jobs WHERE status = 'done' AND id = ?",
                    (job_id,),
                )
                if cursor.rowcount:
                    deleted.append(job_id)
        return deleted

    def output_metadata(self, outputs: list[str]) -> dict[str, dict]:
        if not outputs:
            return {}
        with self._connection() as connection:
            rows = connection.execute(
                f"SELECT * FROM jobs WHERE output IN ({','.join('?' for _ in outputs)}) ORDER BY updated_at ASC",
                outputs,
            ).fetchall()
        return {row['output']: _row_to_job(row) for row in rows}

    def completed_since(self, since: float) -> int:
        with self._connection() as connection:
            return connection.execute("SELECT COUNT(*) FROM jobs WHERE status = 'done' AND completed_at >= ?", (since,)).fetchone()[0]

    def import_completed(self, items: Iterable[dict]) -> int:
        imported = 0
        with self._connection() as connection:
            for item in items:
                job_id = str(item.get("id") or "").strip()
                output = str(item.get("output") or "").strip()
                if not job_id or not output:
                    continue
                created_at = _number(item.get("created_at"), time.time())
                completed_at = _number(item.get("completed_at"), created_at)
                job = {
                    **item,
                    "id": job_id,
                    "status": "done",
                    "message": "生成完成",
                    "created_at": created_at,
                    "updated_at": completed_at,
                    "completed_at": completed_at,
                    "output": output,
                }
                values = _job_values(job, source="legacy")
                cursor = connection.execute(
                    f"INSERT OR IGNORE INTO jobs ({', '.join(JOB_COLUMNS)}) VALUES ({', '.join('?' for _ in JOB_COLUMNS)})",
                    values,
                )
                imported += cursor.rowcount
        return imported

    def fail_incomplete(self) -> int:
        now = time.time()
        with self._connection() as connection:
            cursor = connection.execute(
                """
                UPDATE jobs
                SET status = 'error',
                    message = '服务重启，任务执行已中断',
                    error = 'Web 服务在任务完成前重启，原进程中的生成队列无法恢复',
                    completed_at = ?,
                    updated_at = ?,
                    elapsed_seconds = CASE
                        WHEN started_at IS NULL THEN elapsed_seconds
                        ELSE MAX(0, ? - started_at)
                    END
                WHERE status IN ('queued', 'running')
                """,
                (now, now, now),
            )
        return cursor.rowcount

    def _initialize(self) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    message TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    started_at REAL,
                    updated_at REAL NOT NULL,
                    output TEXT,
                    error TEXT,
                    completed_at REAL,
                    elapsed_seconds REAL,
                    mode TEXT NOT NULL,
                    prompt TEXT NOT NULL,
                    user_brief TEXT NOT NULL DEFAULT '',
                    prompt_plan_json TEXT NOT NULL DEFAULT '{}',
                    resolution TEXT NOT NULL,
                    duration REAL NOT NULL,
                    steps INTEGER NOT NULL,
                    seed INTEGER,
                    first_frame TEXT,
                    last_frame TEXT,
                    reference_images_json TEXT NOT NULL,
                    reference_videos_json TEXT NOT NULL,
                    reference_audios_json TEXT NOT NULL DEFAULT '[]',
                    source TEXT NOT NULL DEFAULT 'api',
                    acceleration_mode TEXT,
                    nsfw_enabled INTEGER,
                    nsfw_profile TEXT
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_jobs_created_at ON jobs(created_at DESC)"
            )
            columns = {row[1] for row in connection.execute("PRAGMA table_info(jobs)")}
            if "acceleration_mode" not in columns:
                connection.execute("ALTER TABLE jobs ADD COLUMN acceleration_mode TEXT")
            if "nsfw_enabled" not in columns:
                connection.execute("ALTER TABLE jobs ADD COLUMN nsfw_enabled INTEGER")
            if "nsfw_profile" not in columns:
                connection.execute("ALTER TABLE jobs ADD COLUMN nsfw_profile TEXT")
            if "reference_audios_json" not in columns:
                connection.execute("ALTER TABLE jobs ADD COLUMN reference_audios_json TEXT NOT NULL DEFAULT '[]'")
            if "depth_control" not in columns:
                connection.execute("ALTER TABLE jobs ADD COLUMN depth_control INTEGER NOT NULL DEFAULT 0")
            if "control_video" not in columns:
                connection.execute("ALTER TABLE jobs ADD COLUMN control_video TEXT")
            if "control_strength" not in columns:
                connection.execute("ALTER TABLE jobs ADD COLUMN control_strength REAL NOT NULL DEFAULT 1.0")
            if "user_brief" not in columns:
                connection.execute("ALTER TABLE jobs ADD COLUMN user_brief TEXT NOT NULL DEFAULT ''")
            if "prompt_plan_json" not in columns:
                connection.execute("ALTER TABLE jobs ADD COLUMN prompt_plan_json TEXT NOT NULL DEFAULT '{}'")
            if "sampling_timing_json" not in columns:
                connection.execute("ALTER TABLE jobs ADD COLUMN sampling_timing_json TEXT")
            for column in ("requested_resolution", "resolution_adjustment"):
                if column not in columns:
                    connection.execute(f"ALTER TABLE jobs ADD COLUMN {column} TEXT NOT NULL DEFAULT ''")
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_jobs_status_created_at ON jobs(status, created_at DESC)"
            )
            connection.execute("CREATE INDEX IF NOT EXISTS idx_jobs_output ON jobs(output)")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_jobs_completed ON jobs(status, completed_at)")

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=30)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=NORMAL")
            connection.execute("PRAGMA busy_timeout=30000")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()


def _job_values(job: dict, source: str) -> tuple:
    created_at = _number(job.get("created_at"), time.time())
    updated_at = _number(job.get("updated_at"), created_at)
    return (
        str(job.get("id") or ""),
        str(job.get("status") or "queued"),
        str(job.get("message") or "等待开始"),
        created_at,
        _optional_number(job.get("started_at")),
        updated_at,
        _optional_text(job.get("output")),
        _optional_text(job.get("error")),
        _optional_number(job.get("completed_at")),
        _optional_number(job.get("elapsed_seconds")),
        str(job.get("mode") or "keyframes"),
        str(job.get("prompt") or ""),
        str(job.get("user_brief") or ""),
        json.dumps(job.get("prompt_plan") or {}, ensure_ascii=False),
        str(job.get("resolution") or ""),
        _number(job.get("duration"), 0),
        int(job.get("steps") or 0),
        _optional_int(job.get("seed")),
        _optional_text(job.get("first_frame")),
        _optional_text(job.get("last_frame")),
        json.dumps(list(job.get("reference_images") or []), ensure_ascii=False),
        json.dumps(list(job.get("reference_videos") or []), ensure_ascii=False),
        json.dumps(list(job.get("reference_audios") or []), ensure_ascii=False),
        source,
        _optional_text(job.get("acceleration_mode")),
        _optional_bool_int(job.get("nsfw_enabled")),
        _optional_text(job.get("nsfw_profile")),
        int(bool(job.get("depth_control", False))),
        _optional_text(job.get("control_video")),
        _number(job.get("control_strength"), 1.0),
        json.dumps(job.get("sampling_timing"), ensure_ascii=False, allow_nan=False),
        str(job.get("requested_resolution") or ""),
        str(job.get("resolution_adjustment") or ""),
    )


def _row_to_job(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "status": row["status"],
        "message": row["message"],
        "created_at": row["created_at"],
        "started_at": row["started_at"],
        "updated_at": row["updated_at"],
        "output": row["output"],
        "error": row["error"],
        "completed_at": row["completed_at"],
        "elapsed_seconds": row["elapsed_seconds"],
        "mode": row["mode"],
        "prompt": row["prompt"],
        "user_brief": row["user_brief"],
        "prompt_plan": json.loads(row["prompt_plan_json"]),
        "resolution": row["resolution"],
        "duration": row["duration"],
        "steps": row["steps"],
        "seed": row["seed"],
        "first_frame": row["first_frame"],
        "last_frame": row["last_frame"],
        "reference_images": json.loads(row["reference_images_json"]),
        "reference_videos": json.loads(row["reference_videos_json"]),
        "reference_audios": json.loads(row["reference_audios_json"]),
        "acceleration_mode": row["acceleration_mode"],
        "nsfw_enabled": None if row["nsfw_enabled"] is None else bool(row["nsfw_enabled"]),
        "nsfw_profile": row["nsfw_profile"],
        "depth_control": bool(row["depth_control"]),
        "control_video": row["control_video"],
        "control_strength": row["control_strength"],
        "sampling_timing": json.loads(row["sampling_timing_json"] or "null"),
        "requested_resolution": row["requested_resolution"],
        "resolution_adjustment": row["resolution_adjustment"],
    }


def _number(value: object, default: float) -> float:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else default


def _optional_number(value: object) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _optional_int(value: object) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    return int(value)


def _optional_text(value: object) -> str | None:
    return None if value is None else str(value)


def _optional_bool_int(value: object) -> int | None:
    return int(value) if isinstance(value, bool) else None

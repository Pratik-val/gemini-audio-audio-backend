# services/call_service.py
import asyncio
import json
from typing import Optional, Dict, Any, List
from config.database import get_connection
import logging

logger = logging.getLogger(__name__)


def _row_to_call(row: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize a DB row into the JSON shape the API expected from the Mongo version."""
    dynamic_data = row.get("dynamic_data")
    if isinstance(dynamic_data, str):
        try:
            dynamic_data = json.loads(dynamic_data)
        except Exception:
            dynamic_data = {}
    return {
        "call_id": row.get("call_id"),
        "interviewer_id": row.get("interviewer_id"),
        "interview_id": row.get("interview_id"),
        "dynamic_data": dynamic_data,
        "transcripts": row.get("transcripts", ""),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
        "status": row.get("status"),
        "call_analysis": row.get("call_analysis"),
        "start_timestamp": row.get("start_timestamp"),
        "end_timestamp": row.get("end_timestamp"),
    }


def _to_db_json(value):
    return json.dumps(value) if isinstance(value, (dict, list)) else value


class CallService:
    def __init__(self):
        self._in_memory_calls: Dict[str, Dict[str, Any]] = {}

    def _save_call_sync(self, call_data: Dict[str, Any]) -> Dict[str, Any]:
        call_id = call_data.get("call_id")
        if not call_id:
            logger.error("Cannot save call without call_id")
            return {"success": False, "error": "Missing call_id"}

        document = {
            "call_id": call_id,
            "interviewer_id": call_data.get("interviewer_id"),
            "interview_id": call_data.get("interview_id"),
            "dynamic_data": call_data.get("dynamic_data"),
            "transcripts": "",
            "status": "registered",
            "call_analysis": call_data.get("call_analysis", {}),
            "start_timestamp": None,
            "end_timestamp": None,
        }

        conn = get_connection()
        if conn:
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO calls (
                            call_id, interviewer_id, interview_id, dynamic_data,
                            transcripts, status, call_analysis, start_timestamp, end_timestamp
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (call_id) DO UPDATE SET
                            interviewer_id = EXCLUDED.interviewer_id,
                            interview_id = EXCLUDED.interview_id,
                            dynamic_data = EXCLUDED.dynamic_data,
                            status = EXCLUDED.status,
                            updated_at = CURRENT_TIMESTAMP;
                        """,
                        (
                            call_id,
                            document["interviewer_id"],
                            document["interview_id"],
                            _to_db_json(document["dynamic_data"]),
                            document["transcripts"],
                            document["status"],
                            _to_db_json(document["call_analysis"]),
                            document["start_timestamp"],
                            document["end_timestamp"],
                        ),
                    )
                conn.commit()
                conn.close()
                logger.info(f"Call saved to database with ID: {call_id}")
                return {"success": True, "call_id": call_id}
            except Exception as e:
                logger.error(f"Error saving call: {e}")
                if conn:
                    conn.close()

        self._in_memory_calls[call_id] = document
        logger.info(f"Call saved in-memory with ID: {call_id}")
        return {"success": True, "call_id": call_id}

    def _get_call_sync(self, call_id: str) -> Optional[Dict[str, Any]]:
        conn = get_connection()
        if conn:
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT * FROM calls WHERE call_id = %s;", (call_id,))
                    row = cur.fetchone()
                conn.close()
                if row:
                    return _row_to_call(row)
            except Exception as e:
                logger.error(f"Error fetching call: {e}")
                if conn:
                    conn.close()
        return self._in_memory_calls.get(call_id)

    def _get_all_calls_sync(self) -> List[Dict[str, Any]]:
        conn = get_connection()
        if conn:
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT * FROM calls ORDER BY created_at DESC;")
                    rows = cur.fetchall()
                conn.close()
                return [_row_to_call(r) for r in rows]
            except Exception as e:
                logger.error(f"Error fetching all calls: {e}")
                if conn:
                    conn.close()
        return list(self._in_memory_calls.values())

    def _get_calls_by_interviewer_sync(self, interviewer_id: str) -> List[Dict[str, Any]]:
        conn = get_connection()
        if conn:
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT * FROM calls WHERE interviewer_id = %s ORDER BY created_at DESC;",
                        (interviewer_id,),
                    )
                    rows = cur.fetchall()
                conn.close()
                return [_row_to_call(r) for r in rows]
            except Exception as e:
                logger.error(f"Error fetching calls by interviewer: {e}")
                if conn:
                    conn.close()
        return [c for c in self._in_memory_calls.values() if c.get("interviewer_id") == interviewer_id]

    def _update_call_status_sync(self, call_id: str, status: str, additional_data: Optional[Dict] = None) -> Dict[str, Any]:
        conn = get_connection()
        if conn:
            try:
                sets = ["status = %s", "updated_at = CURRENT_TIMESTAMP"]
                params: list = [status]
                if additional_data:
                    for key, value in additional_data.items():
                        if key in ("transcripts", "call_analysis", "dynamic_data"):
                            sets.append(f"{key} = %s")
                            params.append(_to_db_json(value))
                        else:
                            sets.append(f"{key} = %s")
                            params.append(value)
                params.append(call_id)
                with conn.cursor() as cur:
                    cur.execute(
                        f"UPDATE calls SET {', '.join(sets)} WHERE call_id = %s;",
                        params,
                    )
                    modified = cur.rowcount
                conn.commit()
                conn.close()
                return {"success": modified > 0, "modified_count": modified}
            except Exception as e:
                logger.error(f"Error updating call status: {e}")
                if conn:
                    conn.close()
        if call_id in self._in_memory_calls:
            self._in_memory_calls[call_id]["status"] = status
            if additional_data:
                self._in_memory_calls[call_id].update(additional_data)
        return {"success": True}

    def _add_transcripts_sync(self, call_id: str, transcripts: Any) -> Dict[str, Any]:
        conn = get_connection()
        if conn:
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE calls SET transcripts = %s, updated_at = CURRENT_TIMESTAMP WHERE call_id = %s;",
                        (transcripts, call_id),
                    )
                    modified = cur.rowcount
                conn.commit()
                conn.close()
                return {"success": modified > 0, "modified_count": modified, "call_id": call_id}
            except Exception as e:
                logger.error(f"Error adding transcripts: {e}")
                if conn:
                    conn.close()
        if call_id in self._in_memory_calls:
            self._in_memory_calls[call_id]["transcripts"] = transcripts
        return {"success": True, "call_id": call_id}

    def _add_transcript_and_timestamp_sync(self, call_id: str, transcripts: str, start_timestamp: int, end_timestamp: int) -> Dict[str, Any]:
        conn = get_connection()
        if conn:
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE calls
                        SET transcripts = %s, start_timestamp = %s, end_timestamp = %s,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE call_id = %s;
                        """,
                        (transcripts, start_timestamp, end_timestamp, call_id),
                    )
                    modified = cur.rowcount
                conn.commit()
                conn.close()
                return {"success": modified > 0, "modified_count": modified, "call_id": call_id}
            except Exception as e:
                logger.error(f"Error adding transcripts and timestamps: {e}")
                if conn:
                    conn.close()
        if call_id in self._in_memory_calls:
            self._in_memory_calls[call_id]["transcripts"] = transcripts
            self._in_memory_calls[call_id]["start_timestamp"] = start_timestamp
            self._in_memory_calls[call_id]["end_timestamp"] = end_timestamp
        return {"success": True, "call_id": call_id}

    # ---- Async public API (kept for compatibility with main_v5.py) ----

    async def save_call(self, call_data: Dict[str, Any]) -> Dict[str, Any]:
        return await asyncio.to_thread(self._save_call_sync, call_data)

    async def get_call(self, call_id: str) -> Optional[Dict[str, Any]]:
        return await asyncio.to_thread(self._get_call_sync, call_id)

    async def get_all_calls(self) -> List[Dict[str, Any]]:
        return await asyncio.to_thread(self._get_all_calls_sync)

    async def get_calls_by_interviewer(self, interviewer_id: str) -> List[Dict[str, Any]]:
        return await asyncio.to_thread(self._get_calls_by_interviewer_sync, interviewer_id)

    async def update_call_status(self, call_id: str, status: str, additional_data: Optional[Dict] = None) -> Dict[str, Any]:
        return await asyncio.to_thread(self._update_call_status_sync, call_id, status, additional_data)

    async def add_transcripts(self, call_id: str, transcripts: Any) -> Dict[str, Any]:
        return await asyncio.to_thread(self._add_transcripts_sync, call_id, transcripts)

    async def add_transcript_and_timestamp(self, call_id: str, transcripts: str, start_timestamp: int, end_timestamp: int) -> Dict[str, Any]:
        return await asyncio.to_thread(self._add_transcript_and_timestamp_sync, call_id, transcripts, start_timestamp, end_timestamp)


# Singleton instance (kept for compatibility with main_v5.py imports)
call_service = CallService()

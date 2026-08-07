import os
import time
import asyncio
import logging
from typing import Dict, Any, Optional
import psycopg2

logger = logging.getLogger(__name__)

# Database connection string is read from environment (DATABASE_URL).
# Never hardcode credentials; keep them in Secret Manager / env.
DATABASE_URL = os.getenv("DATABASE_URL", "")

# Pricing parameters for Gemini Live API (Audio to Audio)
# Audio Input: $3.00 / 1,000,000 tokens ($0.0000030 / token)
# Audio Output: $12.00 / 1,000,000 tokens ($0.0000120 / token)
AUDIO_INPUT_COST_PER_TOKEN_USD = 0.0000030
AUDIO_OUTPUT_COST_PER_TOKEN_USD = 0.0000120

# Audio sample rates and rates per second
INPUT_BYTES_PER_SEC = 32000  # 16kHz, 16-bit mono PCM (32,000 bytes/sec)
OUTPUT_BYTES_PER_SEC = 48000 # 24kHz, 16-bit mono PCM (48,000 bytes/sec)

# 1 second of audio is approximately 50 tokens
TOKENS_PER_AUDIO_SEC = 50.0

USD_TO_INR_RATE = 86.50

class TelemetryService:
    def __init__(self):
        self.in_memory_telemetry: Dict[str, Dict[str, Any]] = {}

    def get_pg_connection(self):
        try:
            conn = psycopg2.connect(DATABASE_URL)
            return conn
        except Exception as e:
            logger.warning(f"Could not connect to PostgreSQL for telemetry ({e}). Using in-memory store.")
            return None

    def calculate_cost(
        self,
        input_audio_bytes: int,
        output_audio_bytes: int,
        call_duration_seconds: float
    ) -> Dict[str, Any]:
        """Calculate Gemini Live API usage, estimated tokens, and price."""
        input_seconds = round(input_audio_bytes / INPUT_BYTES_PER_SEC, 2) if input_audio_bytes > 0 else 0.0
        output_seconds = round(output_audio_bytes / OUTPUT_BYTES_PER_SEC, 2) if output_audio_bytes > 0 else 0.0
        
        # Estimate audio tokens
        input_tokens = int(input_seconds * TOKENS_PER_AUDIO_SEC)
        output_tokens = int(output_seconds * TOKENS_PER_AUDIO_SEC)
        total_tokens = input_tokens + output_tokens
        
        # Costs in USD
        input_cost_usd = round(input_tokens * AUDIO_INPUT_COST_PER_TOKEN_USD, 6)
        output_cost_usd = round(output_tokens * AUDIO_OUTPUT_COST_PER_TOKEN_USD, 6)
        total_cost_usd = round(input_cost_usd + output_cost_usd, 6)
        
        # Costs in INR
        total_cost_inr = round(total_cost_usd * USD_TO_INR_RATE, 4)
        
        return {
            "input_audio_seconds": input_seconds,
            "output_audio_seconds": output_seconds,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": total_tokens,
            "input_cost_usd": input_cost_usd,
            "output_cost_usd": output_cost_usd,
            "total_cost_usd": total_cost_usd,
            "total_cost_inr": total_cost_inr
        }

    def save_telemetry(self, telemetry_data: Dict[str, Any]) -> Dict[str, Any]:
        """Save telemetry data into PostgreSQL telemetry table."""
        call_id = telemetry_data.get("call_id")
        if not call_id:
            logger.error("Telemetry data missing call_id")
            return {"success": False, "error": "Missing call_id"}

        # Perform cost calculation
        cost_info = self.calculate_cost(
            input_audio_bytes=telemetry_data.get("input_audio_bytes", 0),
            output_audio_bytes=telemetry_data.get("output_audio_bytes", 0),
            call_duration_seconds=telemetry_data.get("call_duration_seconds", 0.0)
        )

        record = {
            "call_id": call_id,
            "interview_id": telemetry_data.get("interview_id", ""),
            "interviewer_id": telemetry_data.get("interviewer_id", ""),
            "candidate_name": telemetry_data.get("candidate_name", ""),
            "model_name": telemetry_data.get("model_name", "models/gemini-3.1-flash-live-preview"),
            "input_audio_seconds": cost_info["input_audio_seconds"],
            "output_audio_seconds": cost_info["output_audio_seconds"],
            "input_audio_bytes": telemetry_data.get("input_audio_bytes", 0),
            "output_audio_bytes": telemetry_data.get("output_audio_bytes", 0),
            "input_audio_chunks": telemetry_data.get("input_audio_chunks", 0),
            "output_audio_chunks": telemetry_data.get("output_audio_chunks", 0),
            "input_tokens": cost_info["input_tokens"],
            "output_tokens": cost_info["output_tokens"],
            "total_tokens": cost_info["total_tokens"],
            "call_duration_seconds": telemetry_data.get("call_duration_seconds", 0.0),
            "input_cost_usd": cost_info["input_cost_usd"],
            "output_cost_usd": cost_info["output_cost_usd"],
            "total_cost_usd": cost_info["total_cost_usd"],
            "total_cost_inr": cost_info["total_cost_inr"],
            "status": telemetry_data.get("status", "completed")
        }

        # Store in-memory fallback
        self.in_memory_telemetry[call_id] = record

        # Persist to PostgreSQL
        conn = self.get_pg_connection()
        if conn:
            try:
                cur = conn.cursor()
                insert_query = """
                INSERT INTO telemetry (
                    call_id, interview_id, interviewer_id, candidate_name, model_name,
                    input_audio_seconds, output_audio_seconds, input_audio_bytes, output_audio_bytes,
                    input_audio_chunks, output_audio_chunks, input_tokens, output_tokens, total_tokens,
                    call_duration_seconds, input_cost_usd, output_cost_usd, total_cost_usd, total_cost_inr, status
                ) VALUES (
                    %s, %s, %s, %s, %s,
                    %s, %s, %s, %s,
                    %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s
                )
                ON CONFLICT (call_id) DO UPDATE SET
                    input_audio_seconds = EXCLUDED.input_audio_seconds,
                    output_audio_seconds = EXCLUDED.output_audio_seconds,
                    input_audio_bytes = EXCLUDED.input_audio_bytes,
                    output_audio_bytes = EXCLUDED.output_audio_bytes,
                    input_audio_chunks = EXCLUDED.input_audio_chunks,
                    output_audio_chunks = EXCLUDED.output_audio_chunks,
                    input_tokens = EXCLUDED.input_tokens,
                    output_tokens = EXCLUDED.output_tokens,
                    total_tokens = EXCLUDED.total_tokens,
                    call_duration_seconds = EXCLUDED.call_duration_seconds,
                    input_cost_usd = EXCLUDED.input_cost_usd,
                    output_cost_usd = EXCLUDED.output_cost_usd,
                    total_cost_usd = EXCLUDED.total_cost_usd,
                    total_cost_inr = EXCLUDED.total_cost_inr,
                    status = EXCLUDED.status,
                    updated_at = CURRENT_TIMESTAMP;
                """
                cur.execute(insert_query, (
                    record["call_id"], record["interview_id"], record["interviewer_id"], record["candidate_name"], record["model_name"],
                    record["input_audio_seconds"], record["output_audio_seconds"], record["input_audio_bytes"], record["output_audio_bytes"],
                    record["input_audio_chunks"], record["output_audio_chunks"], record["input_tokens"], record["output_tokens"], record["total_tokens"],
                    record["call_duration_seconds"], record["input_cost_usd"], record["output_cost_usd"], record["total_cost_usd"], record["total_cost_inr"], record["status"]
                ))
                conn.commit()
                cur.close()
                conn.close()
                logger.info(f"📊 Telemetry saved to PostgreSQL for call_id: {call_id} | Price: ${record['total_cost_usd']} (₹{record['total_cost_inr']})")
                return {"success": True, "record": record}
            except Exception as e:
                logger.error(f"Failed to insert telemetry into PostgreSQL: {e}")
                if conn:
                    conn.close()

        return {"success": True, "record": record, "storage": "in_memory"}

    def get_telemetry(self, call_id: str) -> Optional[Dict[str, Any]]:
        """Get telemetry by call_id from PostgreSQL or in-memory fallback."""
        conn = self.get_pg_connection()
        if conn:
            try:
                cur = conn.cursor()
                cur.execute("SELECT * FROM telemetry WHERE call_id = %s;", (call_id,))
                row = cur.fetchone()
                if row:
                    colnames = [desc[0] for desc in cur.description]
                    res = dict(zip(colnames, row))
                    cur.close()
                    conn.close()
                    return res
                cur.close()
                conn.close()
            except Exception as e:
                logger.error(f"Error fetching telemetry from PostgreSQL: {e}")
                if conn:
                    conn.close()

        return self.in_memory_telemetry.get(call_id)

    def get_all_telemetry(self) -> list:
        """Get all telemetry records from PostgreSQL or in-memory fallback."""
        conn = self.get_pg_connection()
        if conn:
            try:
                cur = conn.cursor()
                cur.execute("SELECT * FROM telemetry ORDER BY created_at DESC;")
                rows = cur.fetchall()
                colnames = [desc[0] for desc in cur.description]
                results = [dict(zip(colnames, row)) for row in rows]
                cur.close()
                conn.close()
                return results
            except Exception as e:
                logger.error(f"Error fetching all telemetry from PostgreSQL: {e}")
                if conn:
                    conn.close()

        return list(self.in_memory_telemetry.values())

    async def save_telemetry_async(self, telemetry_data: Dict[str, Any]) -> Dict[str, Any]:
        """Async non-blocking wrapper to save telemetry into PostgreSQL."""
        return await asyncio.to_thread(self.save_telemetry, telemetry_data)

telemetry_service = TelemetryService()

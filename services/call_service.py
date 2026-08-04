# services/call_service.py
from datetime import datetime
from typing import Optional, Dict, Any, List
from config.database import get_database
import logging

logger = logging.getLogger(__name__)

class CallService:
    def __init__(self):
        self.collection_name = "calls"
        self._in_memory_calls: Dict[str, Dict[str, Any]] = {}

    async def get_collection(self):
        """Get the calls collection or None if DB not connected"""
        db = get_database()
        if db is None:
            return None
        return db[self.collection_name]
    
    async def get_all_calls(self) -> List[Dict[str, Any]]:
        """Get all calls from the database or in-memory fallback"""
        try:
            collection = await self.get_collection()
            if collection is None:
                return list(self._in_memory_calls.values())
            cursor = collection.find({})
            calls = await cursor.to_list(length=None)
            for call in calls:
                call["_id"] = str(call["_id"])
            return calls
        except Exception as e:
            logger.error(f"Error fetching all calls: {e}")
            return list(self._in_memory_calls.values())
    
    async def save_call(self, call_data: Dict[str, Any]) -> Dict[str, Any]:
        """Save call data to MongoDB or in-memory fallback"""
        try:
            call_id = call_data.get("call_id")
            call_document = {
                "call_id": call_id,
                "interviewer_id": call_data.get("interviewer_id"),
                "interview_id": call_data.get("interview_id"),
                "dynamic_data": call_data.get("dynamic_data"),
                "transcripts": "",  # Initially empty
                "created_at": datetime.utcnow().isoformat(),
                "updated_at": datetime.utcnow().isoformat(),
                "status": "registered",
                "call_analysis": call_data.get("call_analysis", {}),
                "end_timestamp": None,
                "start_timestamp": None
            }

            collection = await self.get_collection()
            if collection is None:
                self._in_memory_calls[call_id] = call_document
                logger.info(f"Call saved in-memory with ID: {call_id}")
                return {"success": True, "call_id": call_id}

            result = await collection.insert_one(call_document)
            logger.info(f"Call saved to database with ID: {result.inserted_id}")
            return {
                "success": True,
                "inserted_id": str(result.inserted_id),
                "call_id": call_id
            }
            
        except Exception as e:
            logger.error(f"Error saving call: {e}")
            if call_id:
                self._in_memory_calls[call_id] = call_data
            return {"success": True, "call_id": call_id}
    
    async def get_call(self, call_id: str) -> Optional[Dict[str, Any]]:
        """Get call by call_id"""
        try:
            collection = await self.get_collection()
            if collection is None:
                return self._in_memory_calls.get(call_id)
            call = await collection.find_one({"call_id": call_id})
            if call:
                call["_id"] = str(call["_id"])
            return call
        except Exception as e:
            logger.error(f"Error fetching call: {e}")
            return self._in_memory_calls.get(call_id)
    
    async def update_call_status(self, call_id: str, status: str, additional_data: Optional[Dict] = None) -> Dict[str, Any]:
        """Update call status"""
        try:
            collection = await self.get_collection()
            if collection is None:
                if call_id in self._in_memory_calls:
                    self._in_memory_calls[call_id]["status"] = status
                    if additional_data:
                        self._in_memory_calls[call_id].update(additional_data)
                return {"success": True}
            update_doc = {"status": status, "updated_at": datetime.utcnow()}
            if additional_data:
                update_doc.update(additional_data)
            result = await collection.update_one({"call_id": call_id}, {"$set": update_doc})
            return {"success": result.modified_count > 0, "modified_count": result.modified_count}
        except Exception as e:
            logger.error(f"Error updating call status: {e}")
            return {"success": False, "error": str(e)}
    
    async def get_calls_by_interviewer(self, interviewer_id: str) -> List[Dict[str, Any]]:
        """Get all calls for a specific interviewer"""
        try:
            collection = await self.get_collection()
            if collection is None:
                return [c for c in self._in_memory_calls.values() if c.get("interviewer_id") == interviewer_id]
            cursor = collection.find({"interviewer_id": interviewer_id}).sort("created_at", -1)
            calls = await cursor.to_list(length=None)
            for call in calls:
                call["_id"] = str(call["_id"])
            return calls
        except Exception as e:
            logger.error(f"Error fetching calls by interviewer: {e}")
            return [c for c in self._in_memory_calls.values() if c.get("interviewer_id") == interviewer_id]

    async def add_transcripts(self, call_id: str, transcripts: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Add transcripts to a call"""
        try:
            collection = await self.get_collection()
            if collection is None:
                if call_id in self._in_memory_calls:
                    self._in_memory_calls[call_id]["transcripts"] = transcripts
                return {"success": True, "call_id": call_id}
            update_doc = {"transcripts": transcripts, "updated_at": datetime.utcnow()}
            result = await collection.update_one({"call_id": call_id}, {"$set": update_doc})
            return {"success": result.modified_count > 0, "modified_count": result.modified_count, "call_id": call_id}
        except Exception as e:
            logger.error(f"Error adding transcripts: {e}")
            return {"success": False, "error": str(e)}
            
    async def add_transcript_and_timestamp(self, call_id: str, transcripts: str, start_timestamp: int, end_timestamp: int) -> Dict[str, Any]:
        """Add transcripts and timestamps to a call"""
        try:
            collection = await self.get_collection()
            if collection is None:
                if call_id in self._in_memory_calls:
                    self._in_memory_calls[call_id]["transcripts"] = transcripts
                    self._in_memory_calls[call_id]["start_timestamp"] = start_timestamp
                    self._in_memory_calls[call_id]["end_timestamp"] = end_timestamp
                return {"success": True, "call_id": call_id}
            update_doc = {
                "transcripts": transcripts,
                "start_timestamp": start_timestamp,
                "end_timestamp": end_timestamp,
                "updated_at": datetime.utcnow()
            }
            result = await collection.update_one({"call_id": call_id}, {"$set": update_doc})
            return {"success": result.modified_count > 0, "modified_count": result.modified_count, "call_id": call_id}
        except Exception as e:
            logger.error(f"Error adding transcripts and timestamps: {e}")
            return {"success": False, "error": str(e)}
    
    async def append_transcript(self, call_id: str, transcript_entry: Dict[str, Any]) -> Dict[str, Any]:
        """Append a single transcript entry to existing transcripts"""
        try:
            if "timestamp" not in transcript_entry:
                transcript_entry["timestamp"] = datetime.utcnow().isoformat()

            collection = await self.get_collection()
            if collection is None:
                if call_id in self._in_memory_calls:
                    t = self._in_memory_calls[call_id].get("transcripts", [])
                    if isinstance(t, list):
                        t.append(transcript_entry)
                    else:
                        self._in_memory_calls[call_id]["transcripts"] = [transcript_entry]
                return {"success": True, "call_id": call_id}
            result = await collection.update_one(
                {"call_id": call_id},
                {
                    "$push": {"transcripts": transcript_entry},
                    "$set": {"updated_at": datetime.utcnow()}
                }
            )
            return {"success": result.modified_count > 0, "modified_count": result.modified_count, "call_id": call_id}
        except Exception as e:
            logger.error(f"Error appending transcript: {e}")
            return {"success": False, "error": str(e)}

# Create a singleton instance
call_service = CallService()

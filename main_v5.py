from contextlib import asynccontextmanager
import logging
import os
import asyncio
import json
import traceback
import time
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from pydantic import BaseModel
from websockets import ConnectionClosedOK
from config.config import CONFIG, MODEL, SEND_SR, RECV_SR, CHUNK, client, end_call_tool
from config.prompts import general_interviewer_prompt
from google import genai
from google.genai import types
from config.database import init_db
from services.call_service import call_service
from services.telemetry_service import telemetry_service
from services.audio_buffer import AdaptiveBuffer
app = FastAPI()

# # Ensure config aligns with frontend
# SEND_SR = 16000
# RECV_SR = 16000
# CHUNK = 1024  # Buffer size for audio chunks

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class AudioLoop:
    def __init__(self):
        self.audio_in_buffer = AdaptiveBuffer(name="Incoming")
        self.audio_out_buffer = AdaptiveBuffer(name="Outgoing")
        self.session = None
        self.active = True
        self.last_audio_time = time.time()
        self.conversation = []
        self.agent_id = None
        self.dynamic_data = None
        self.general_interviewer_prompt = general_interviewer_prompt
        self.final_prompt = None
        self.ws = None
        self.call_id = None
        self.start_time = None
        self.end_time = None
        self.session_handle = None
        self.task_group = None
        self.tasks = []
        self.call_completed = False
        self.once=1
        self.input_audio_bytes = 0
        self.input_audio_chunks = 0
        self.output_audio_bytes = 0
        self.output_audio_chunks = 0
        
        
        
    def set_call_id(self, call_id):
        self.call_id = call_id
        logger.info(f"Call ID set to: {self.call_id}")

    def set_websocket(self, ws):
        self.ws = ws
    
    def set_start_time(self):
        """Set the start time of the call."""
        self.start_time = int(time.time())
        logger.info(f"Call started at: {self.start_time}")
    
    def set_end_time(self):
        """Set the end time of the call."""
        self.end_time = int(time.time())
        logger.info(f"Call ended at: {self.end_time}")
    
    def set_dynamic_data(self, dynamic_data):
        self.dynamic_data = dynamic_data

    def set_agent_id(self, agent_id):
        self.agent_id = agent_id

    def add_label(self, label, text):
        return f"{label}: {text}"
    
    async def save_conversation_and_timestamp(self):
        try:
            # convert conversation to a string
            conversation_str = "\n".join(self.conversation)
            self.set_end_time()  # Set end time when saving conversation
            await call_service.add_transcript_and_timestamp(self.call_id, conversation_str, self.start_time, self.end_time)
            logger.info(f"Conversation saved for call ID: {self.call_id}")
            
            # Save telemetry and calculate pricing in PostgreSQL
            duration = (self.end_time - self.start_time) if (self.start_time and self.end_time) else 0.0
            interview_id = ""
            candidate_name = ""
            if isinstance(self.dynamic_data, dict):
                interview_id = self.dynamic_data.get("interview_id", "")
                candidate_name = self.dynamic_data.get("name", "")
            elif isinstance(self.dynamic_data, str):
                try:
                    d = json.loads(self.dynamic_data)
                    interview_id = d.get("interview_id", "")
                    candidate_name = d.get("name", "")
                except:
                    pass

            telemetry_data = {
                "call_id": self.call_id,
                "interview_id": interview_id,
                "interviewer_id": self.agent_id or "",
                "candidate_name": candidate_name,
                "input_audio_bytes": self.input_audio_bytes,
                "output_audio_bytes": self.output_audio_bytes,
                "input_audio_chunks": self.input_audio_chunks,
                "output_audio_chunks": self.output_audio_chunks,
                "call_duration_seconds": duration,
                "status": "completed"
            }
            logger.info(f"Saving telemetry for call_id: {self.call_id} (bytes in: {self.input_audio_bytes}, out: {self.output_audio_bytes})")
            await telemetry_service.save_telemetry_async(telemetry_data)
            
            self.conversation = []
            self.call_completed = True
        except Exception as e:
            logger.error(f"Error saving conversation: {e}", exc_info=True)
  
   

    async def handle_websocket_messages(self):
        while self.active:
            try:
                data = await self.ws.receive()
                if data['type'] == 'websocket.disconnect':
                    logger.info("WebSocket disconnect event received. Saving conversation & telemetry...")
                    await self.save_conversation_and_timestamp()
                    raise WebSocketDisconnect
                elif data['type'] == 'websocket.receive':
                    if 'bytes' in data:
                        flag, pcm = data['bytes'][0], data['bytes'][1:]
                        if flag == 0x01:  # Mic audio
                            # logger.info(f"Received mic audio: {len(pcm)} bytes")
                            self.input_audio_bytes += len(pcm)
                            self.input_audio_chunks += 1
                            self.audio_out_buffer.add_chunk({"data": pcm, "mime_type": "audio/pcm"})
                            self.last_audio_time = time.time()
                    elif 'text' in data:
                        json_data = json.loads(data['text'])
                        # logger.info(f"Received JSON data: {json_data}")
                        if json_data.get("action") == "end_call":
                            logger.info("Received end_call action")
                            self.active = False
                            await self.save_conversation_and_timestamp()
                            await self.ws.close()
                            self.tasks[2].cancel()  # Cancel handle_websocket_messages
                            self.tasks[1].cancel()  # Cancel send_audio_to_gemini
                            self.tasks[3].cancel()  
                            asyncio.current_task().cancel()  # Cancel receive_from_gemini
                            break
                        text = json_data.get("input", "")
                        if text:
                            logger.info(f"Sending text input to Gemini: {text}")
                            await self.session.send_realtime_input(text=text)
                    else:
                        logger.warning(f"Received unknown data type: {data}")
            except WebSocketDisconnect:
                self.active = False
                break
            except Exception as e:
                logger.error(f"Error in handle_websocket_messages: {e}")
                self.active = False
                break

    async def send_audio_to_gemini(self):
        try:
            while self.active:
                msg = await self.audio_out_buffer.get_chunk()
                if msg:
                    # logger.info(f"Sending audio to Gemini: {len(msg['data'])} bytes")
                    await self.session.send_realtime_input(audio=msg)
        except Exception as e:
            logger.error(f"Error in send_audio_to_gemini: {e}")
            self.active = False

    async def handle_function_call(self, function_call):
        """Handle function calls from Gemini"""
        try:
            function_name = function_call.name
            function_args = function_call.args if function_call.args else {}
            
            logger.info(f"Function call received: {function_name} with args: {function_args}")
            
            if function_name == "end_interview_call":
                logger.info("Ending interview call as requested by user")
                
                # Send a final message to the client
                await self.ws.send_json({
                    "action": "interview_ended",
                    "message": "Interview has been ended. Thank you for your time!"
                })
                
                # Send function response back to Gemini
                await self.session.send(
                    input="Interview ended successfully. Goodbye!",
                    end_of_turn=True
                )
                await self.save_conversation_and_timestamp()
                self.tasks[0].cancel()  # Cancel handle_websocket_messages
                self.tasks[1].cancel()  # Cancel send_audio_to_gemini
                self.tasks[3].cancel()  
                asyncio.current_task().cancel()  # Cancel receive_from_gemini
                
                # Set active to False to stop all loops
                self.active = False

                
                # Close the websocket
                # await self.ws.close()
                
                return True
            
            return False
            
        except Exception as e:
            logger.error(f"Error handling function call: {e}")
            return False

    async def process_response_async(self, response):
        """Offload transcript processing and function calls to background task."""
        try:
            # Handle function calls
            if hasattr(response, 'server_content') and response.server_content:
                if hasattr(response.server_content, 'model_turn') and response.server_content.model_turn:
                    for part in response.server_content.model_turn.parts:
                        if hasattr(part, 'function_call') and part.function_call:
                            await self.handle_function_call(part.function_call)
                        elif hasattr(part, 'executable_code') and part.executable_code and "end_interview_call" in part.executable_code.code:
                            class MockFunctionCall:
                                def __init__(self):
                                    self.name = "end_interview_call"
                                    self.args = {}
                            await self.handle_function_call(MockFunctionCall())

            # Handle transcripts
            if hasattr(response, 'server_content') and response.server_content:
                if response.server_content.output_transcription:
                    chunk = response.server_content.output_transcription.text or ""
                    await self.ws.send_json({"ai_text": chunk})
                    if chunk:
                        self.conversation.append(self.add_label("AI", chunk))
                
                if response.server_content.input_transcription:
                    chunk = response.server_content.input_transcription.text or ""
                    await self.ws.send_json({"candidate_text": chunk})
                    if chunk:
                        self.conversation.append(self.add_label("User", chunk))
        except Exception as e:
            logger.error(f"Error in process_response_async: {e}")

    async def receive_from_gemini(self):
        try:
            while self.active:
                async for response in self.session.receive():
                    if not self.active:
                        break
                    # PRIORITY 0: Critical System Messages - Handle inline
                    if response.session_resumption_update:
                        update = response.session_resumption_update
                        if update.resumable and update.new_handle:
                            self.session_handle = update.new_handle
                            logger.info(f"Session handle updated to: {self.session_handle}")
                    
                    if hasattr(response, 'go_away') and response.go_away is not None:
                        logger.info(f"Received GoAway message. Time left: {response.go_away.time_left}")
                    
                    # PRIORITY 1: Audio data - immediate forwarding (critical path)
                    if data := response.data:
                        self.output_audio_bytes += len(data)
                        self.output_audio_chunks += 1
                        self.audio_in_buffer.add_chunk(data)
                    
                    # PRIORITY 2: Everything else offloaded to background
                    if hasattr(response, 'server_content') and response.server_content:
                        asyncio.create_task(self.process_response_async(response))

        except ConnectionClosedOK:
            logger.info("receive_from_gemini: connection closed normally")
        except asyncio.CancelledError:
            logger.info("receive_from_gemini cancelled")
        except Exception as e:
            logger.error(f"Error in receive_from_gemini: {e}")



    async def play_audio(self):
        while self.active:
            try:
                pcm = await self.audio_in_buffer.get_chunk()
                if not pcm:
                    continue
                msg = b'\x02' + pcm
                # logger.info(f"Playing audio chunk of size: {len(msg)} bytes")
                if self.active:  # Check if still active before sending
                    await self.ws.send_bytes(msg)
            except Exception as e:
                logger.error(f"Error in play_audio: {e}")
                self.active = False
                break

    async def monitor_silence(self):
        try:
            while self.active:
                await asyncio.sleep(0.5)
                time_since_last_audio = time.time() - self.last_audio_time
                if time_since_last_audio > 2.0:
                    logger.info("Detected 2 seconds of silence. Signaling end of speech.")
                    self.last_audio_time = time.time()
        except Exception as e:
            logger.error(f"Error in monitor_silence: {e}")
            self.active = False

    def create_final_prompt(self):
        # Initialize json_data
        json_data = self.dynamic_data or {}
        
        # If dynamic_data is a string, parse it as JSON
        if isinstance(json_data, str):
            try:
                json_data = json.loads(json_data)
            except json.JSONDecodeError:
                json_data = {}
                logger.error("Error: Could not parse dynamic_data as JSON. Using empty dict.")
        
        interviewer_personality = "professional, friendly, and encouraging"
        
        # Extract and parse questions
        questions_data = json_data.get("questions", [])
        if isinstance(questions_data, str):
            try:
                questions_data = json.loads(questions_data)
            except json.JSONDecodeError:
                questions_data = []
                logger.error("Error: Could not parse questions as JSON. Using empty list.")
        
        # Format questions and follow-ups
        formatted_questions = ""
        for idx, q in enumerate(questions_data, 1):
            main_question = q.get("question", "")
            follow_ups = q.get("follow_ups", [])
            formatted_questions += f"{idx}. {main_question}\n"
            if follow_ups:
                formatted_questions += "   Follow-up questions:\n"
                for f_idx, follow_up in enumerate(follow_ups, 1):
                    formatted_questions += f"     {f_idx}. {follow_up}\n"
            formatted_questions += "\n"
        
        # Define replacements for the prompt
        replacements = {
            "{{role}}": json_data.get("role", ""),
            "{{mins}}": json_data.get("mins", ""),
            "{{name}}": json_data.get("name", ""),
            "{{objective}}": json_data.get("objective", ""),
            "{{questionFocus}}": json_data.get("questionFocus", ""),
            "{{description}}": json_data.get("description", ""),
            "{{interviewerName}}": json_data.get("interviewerName", ""),
            "{{interviewerPersonality}}": interviewer_personality,
            "{{candidateName}}": json_data.get("name", ""),
            "{{behavioralQuestions}}": json_data.get("behavioralQuestions", ""),
            "{{questions}}": formatted_questions
        }
        
        # Replace placeholders in the general prompt
        final_prompt = self.general_interviewer_prompt
        for placeholder, value in replacements.items():
            final_prompt = final_prompt.replace(placeholder, str(value))
        
        # Add tool usage instructions to the prompt
        tool_instruction = """
        
        IMPORTANT: You have access to an 'end_interview_call' function. Use this function ONLY when the candidate explicitly asks to end the interview or says they want to stop (e.g., "let's end the interview", "I want to stop now", "end the interview", "that's all for today"). Do not use this function for any other reason.
        """
        
        self.final_prompt = final_prompt + tool_instruction
        
        # Write the final prompt to a file for debugging
        # with open("final_prompt.txt", "w", encoding="utf-8") as f:
        #     f.write(self.final_prompt)
            
    async def resume_session(self):
        """Resume the session if it was interrupted."""
        try:
            # 1) Close old session so the SDK stops its internal loops
            if self.session is not None:
                try:
                    await self.session.close()
                except Exception as e:
                    logger.error(f"Error closing old session: {e}")

            # 2) Cancel *only* your four background tasks
            # if self.tasks:
            #     logger.info("Cancelling previous session tasks…")
            #     for task in self.tasks:
            #         if not task.done():
            #             try:
            #                 task.cancel()
            #             except RecursionError:
            #                 pass
            #     await asyncio.gather(*self.tasks, return_exceptions=True)
            #     self.tasks = []

            # 3) Reconfigure and reconnect
            CONFIGR = types.LiveConnectConfig(
                response_modalities=["AUDIO"],
                speech_config=types.SpeechConfig(
                    voice_config=types.VoiceConfig(
                        prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name="puck"),
                    )
                ),
                realtime_input_config=types.RealtimeInputConfig(
                    automatic_activity_detection=types.AutomaticActivityDetection(
                        disabled=False,
                        # start_of_speech_sensitivity=types.StartSensitivity.START_SENSITIVITY_LOW,
                        end_of_speech_sensitivity=types.EndSensitivity.END_SENSITIVITY_LOW,
                        prefix_padding_ms=100,
                        silence_duration_ms=350,
                    )
                ),
                # Comment out input transcription for now
                input_audio_transcription=types.AudioTranscriptionConfig(),
                output_audio_transcription=types.AudioTranscriptionConfig(),
                generation_config=types.GenerationConfig(
                    temperature=0.7,
                    top_p=0.95,
                    top_k=70
                ),
                tools=[end_call_tool],
                system_instruction=types.Content(parts=[types.Part(text="TRANSCRIBE ONLY IN ENGLISH, NO OTHER LANGUAGES")]),
                session_resumption=types.SessionResumptionConfig(
                    handle=self.session_handle
                )
            )

            # logger.info(f"Resuming session with config: {CONFIGR}")

            async with client.aio.live.connect(model=MODEL, config=CONFIGR) as session:
                self.session = session
                # await self.session.send(input=f"{self.final_prompt}", end_of_turn=True)
                self.active = True
                t1 = asyncio.create_task(self.handle_websocket_messages())
                t2 = asyncio.create_task(self.send_audio_to_gemini())
                t3 = asyncio.create_task(self.receive_from_gemini())
                t4 = asyncio.create_task(self.play_audio())
                    # tg.create_task(self.monitor_silence())
                self.tasks = [t1, t2, t3, t4]
                for task in self.tasks:
                    await task
                    
                logger.info("Session resumed and initial prompt sent.")
             
        except asyncio.CancelledError:
            logger.warning("Session cancelled")
            while self.call_completed is False:
                await self.resume_session()
        except Exception as e:
            logger.error(f"Error in AudioLoop: {e}", exc_info=True)
        finally:
            self.active = False
            self.call_completed = True
            logger.info("AudioLoop finished")


    async def run(self):
        try:
            async with client.aio.live.connect(model=MODEL, config=CONFIG) as session:
                self.session = session
                logger.info("Sending initial prompt to Gemini...")
                await self.session.send_realtime_input(text=f"{self.final_prompt}")
                logger.info("Initial prompt sent.")
                self.set_start_time()  # Set start time when session starts
                
                
                t1 = asyncio.create_task(self.handle_websocket_messages())
                t2 = asyncio.create_task(self.send_audio_to_gemini())
                t3 = asyncio.create_task(self.receive_from_gemini())
                t4 = asyncio.create_task(self.play_audio())
                    # tg.create_task(self.monitor_silence())
                self.tasks = [t1, t2, t3, t4]
                for task in self.tasks:
                    await task
            
        except asyncio.CancelledError:
            logger.warning("Session cancelled")
            while self.call_completed is False:
                await self.resume_session()
        except Exception as e:
            logger.error(f"Error in AudioLoop: {e}", exc_info=True)
        finally:
            self.active = False
            logger.info("AudioLoop finished")
            
            
@app.websocket("/ws/audio")
async def audio_ws(ws: WebSocket, agent_id: str = "default-agent"):
    await ws.accept()
    call_id = ws.query_params.get("access_token", None)
    logger.info(f"call_id is {call_id}")
    loop = AudioLoop()
    loop.set_websocket(ws)
    try:
        data = await ws.receive_text()
        data = json.loads(data)
        # logger.info(f"Received initial data: {data}")
        dynamic_data = data.get("dynamic_data", {})
        if not dynamic_data:
            raise ValueError("Dynamic data is required to start the interview.")
        # logger.info(f"data is {data}")
        loop.set_call_id(call_id)
        logger.info(f"Call ID: {loop.call_id}")
        loop.set_dynamic_data(dynamic_data)
        loop.set_agent_id(agent_id)
        
        loop.create_final_prompt()
        await loop.run()
    except WebSocketDisconnect:
        loop.active = False
        try:
            await loop.save_conversation_and_timestamp()
            await ws.close()
        except:
            pass
    except Exception as e:
        logger.error(f"Error: {e}")
        logger.error("An unhandled exception occurred in audio_ws", exc_info=True)
        loop.active = False
        try:
            await ws.close()
        except:
            pass
    finally:
        loop.active = False
        if not loop.call_completed:
            try:
                await loop.save_conversation_and_timestamp()
            except Exception as e:
                logger.error(f"Error saving conversation in finally block: {e}")
        if loop.session:
            try:
                await loop.session.close()
            except:
                pass
        for i in loop.tasks:
            i.cancel()




@app.get("/")
async def root():
    return {"message": "WebSocket server is running. Connect to /ws/audio for audio processing."}


#connect to Cloud SQL on startup
@app.on_event("startup")
async def startup_event():
    await asyncio.to_thread(init_db)
    logger.info("Database schema check complete on startup.")

@app.on_event("shutdown")
async def shutdown_event():
    logger.info("Shutting down audio backend.")

class RegisterCallRequest(BaseModel):
    interviewer_id: str
    dynamic_data: dict
    interview_id: str
    
@app.post("/api/register")
async def register_call(request: RegisterCallRequest):
    interviewer_id = request.interviewer_id
    dynamic_data = request.dynamic_data
    interview_id = request.interview_id
    #generate a call_id based on current time
    
    call_id = f"call_{interview_id}_{int(time.time())}"
    call_data = {
        "call_id": call_id,
        "dynamic_data": dynamic_data,
        "interviewer_id": interviewer_id,
        "interview_id": interview_id,
        "call_analysis": {
            "call_successful": True,
            "call_duration": 0
        }
    }
    await call_service.save_call(call_data)
    logger.info(f"Call registered with ID: {call_id} for interviewer: {interviewer_id}")
    # logger.info(call_service.get_call(call_id))
    
    return {"message": "Call registered successfully.", "call_id": call_id, "access_token": call_id}

@app.get("/api/call/{call_id}")
async def get_call(call_id: str):
    call = await call_service.get_call(call_id)
    
    if not call:
        return {"message": "Call not found."}
    return call

@app.get("/api/calls")
async def get_all_calls():
    """Get all calls from the database"""
    # TypeError: object list can't be used in 'await' expression
    calls = await call_service.get_all_calls()
    return {"calls": calls}

@app.get("/api/telemetry")
async def get_all_telemetry():
    """Get all telemetry records with price details from PostgreSQL"""
    return {"telemetry": telemetry_service.get_all_telemetry()}

@app.get("/api/telemetry/{call_id}")
async def get_telemetry_by_call_id(call_id: str):
    """Get telemetry by call_id from PostgreSQL"""
    res = telemetry_service.get_telemetry(call_id)
    if not res:
        return {"message": "Telemetry not found."}
    return res

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8080"))
    uvicorn.run(app, host="0.0.0.0", port=port)

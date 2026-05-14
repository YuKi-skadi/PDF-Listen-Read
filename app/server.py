import re
import uuid
from pathlib import Path
from typing import Optional

import httpx
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel
import PyPDF2

app = FastAPI(title="PDF Listen Book")

# Mount static files
STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# Storage for processed texts
TEXT_STORE = {}


class TTSRequest(BaseModel):
    text: str
    doc_id: Optional[str] = None
    api_key: str
    api_base: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    model: str = "cosyvoice-v2"
    voice: Optional[str] = None
    use_local_tts: bool = False


class TextProcessRequest(BaseModel):
    text: str


class FetchModelsRequest(BaseModel):
    api_key: str
    api_base: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"


class LLMRequest(BaseModel):
    text: str
    api_key: str
    api_base: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    model: str = "qwen-turbo"
    prompt: str = "请将以下文本转换为更适合朗读的中文口语表达，保持原意不变："


class ConfigRequest(BaseModel):
    text: str
    api_key: str
    api_base: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    model: str = "qwen-turbo"
    prompt: str = "请将以下文本转换为更适合朗读的中文口语表达，保持原意不变："


@app.get("/")
async def index():
    return FileResponse(str(STATIC_DIR / "index.html"))


@app.post("/api/fetch-models")
async def fetch_models(req: FetchModelsRequest):
    """Fetch available models from the API"""
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                f"{req.api_base}/models",
                headers={
                    "Authorization": f"Bearer {req.api_key}",
                    "Content-Type": "application/json"
                }
            )
            
            if response.status_code != 200:
                raise HTTPException(status_code=response.status_code, detail=f"Failed to fetch models: {response.text}")
            
            data = response.json()
            models = []
            tts_models = []
            llm_models = []
            
            # Valid TTS model patterns for Dashscope
            valid_tts_patterns = ["cosyvoice", "sambert"]
            
            for model in data.get("data", []):
                model_id = model.get("id", "")
                models.append(model_id)
                
                # Categorize models
                lower_id = model_id.lower()
                
                # Check for valid TTS models
                if any(pattern in lower_id for pattern in valid_tts_patterns):
                    tts_models.append(model_id)
                # Check for LLM models (exclude TTS models)
                elif any(kw in lower_id for kw in ["qwen", "gpt", "glm", "chat", "completion", "turbo", "plus", "max"]):
                    llm_models.append(model_id)
            
            # Always add known valid Dashscope TTS models (API /models endpoint often doesn't list them)
            known_tts_models = [
                "cosyvoice-v3-flash",
                "cosyvoice-v3-plus", 
                "cosyvoice-v2",
                "cosyvoice-v1",
                "sambert-zhiyan-v1",
                "sambert-zhichu-v1"
            ]
            for m in known_tts_models:
                if m not in tts_models:
                    tts_models.append(m)
            
            # If no LLM models found, add common defaults
            if not llm_models:
                llm_models = ["qwen-turbo", "qwen-plus", "qwen-max"]
            
            return JSONResponse({
                "tts_models": tts_models,
                "llm_models": llm_models,
                "all_models": models[:100]
            })
    except httpx.RequestError as e:
        raise HTTPException(status_code=500, detail=f"Request failed: {str(e)}")


@app.get("/api/tts-voices")
async def get_tts_voices(model: str = "cosyvoice-v2"):
    """Get available voices for a TTS model"""
    voice_map = {
        "cosyvoice-v3": ["longanyang", "longxiaochun", "yuer", "toonyun"],
        "cosyvoice-v3-flash": ["longanyang", "longxiaochun", "yuer", "toonyun"],
        "cosyvoice-v3-plus": ["longanyang", "longxiaochun", "yuer", "toonyun"],
        "cosyvoice-v2": ["longxiaochun_v2", "longyang_v2", "longwan_v2", "longcheng_v2"],
        "cosyvoice-v1": ["longxiaochun", "longyang", "longwan", "longcheng"],
        "sambert": ["zhiyan", "zhichu", "zhibei", "zhimiao", "xiaoyun", "xiaogang"],
        "tts-1": ["alloy", "echo", "fable", "onyx", "nova", "shimmer"],
        "tts-1-hd": ["alloy", "echo", "fable", "onyx", "nova", "shimmer"]
    }
    
    # Try to find matching voices
    for key in voice_map:
        if key in model.lower():
            return JSONResponse({"voices": voice_map[key]})
    
    # Default voices for unknown models (cosyvoice v3 style)
    return JSONResponse({"voices": ["longanyang", "longxiaochun", "yuer", "toonyun"]})


@app.post("/api/upload-pdf")
async def upload_pdf(file: UploadFile = File(...)):
    """Upload PDF and extract text"""
    if not file.filename.endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are allowed")

    try:
        content = await file.read()
        import io
        reader = PyPDF2.PdfReader(io.BytesIO(content))
        
        pages = []
        full_text = ""
        for i, page in enumerate(reader.pages):
            text = page.extract_text()
            pages.append({"page": i + 1, "text": text})
            full_text += text + "\n\n"

        # Process text into chunks for reading
        chunks = split_text_for_reading(full_text)
        
        # Store with unique ID
        doc_id = str(uuid.uuid4())[:8]
        TEXT_STORE[doc_id] = {
            "filename": file.filename,
            "full_text": full_text,
            "pages": pages,
            "chunks": chunks
        }

        return JSONResponse({
            "doc_id": doc_id,
            "filename": file.filename,
            "page_count": len(pages),
            "chunk_count": len(chunks)
        })
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to process PDF: {str(e)}")


@app.post("/api/process-text")
async def process_text(req: TextProcessRequest):
    """Process raw text input into chunks"""
    try:
        chunks = split_text_for_reading(req.text)
        doc_id = str(uuid.uuid4())[:8]
        TEXT_STORE[doc_id] = {
            "filename": "text_input.txt",
            "full_text": req.text,
            "pages": [],
            "chunks": chunks
        }
        return JSONResponse({
            "doc_id": doc_id,
            "filename": "text_input.txt",
            "chunk_count": len(chunks)
        })
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to process text: {str(e)}")


@app.get("/api/document/{doc_id}")
async def get_document(doc_id: str):
    """Get document chunks"""
    if doc_id not in TEXT_STORE:
        raise HTTPException(status_code=404, detail="Document not found")
    
    doc = TEXT_STORE[doc_id]
    return JSONResponse({
        "doc_id": doc_id,
        "filename": doc["filename"],
        "chunks": doc["chunks"]
    })


@app.post("/api/llm-process")
async def llm_process(req: LLMRequest):
    """Process text with LLM for optimization"""
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(
                f"{req.api_base}/chat/completions",
                headers={
                    "Authorization": f"Bearer {req.api_key}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": req.model,
                    "messages": [
                        {"role": "system", "content": "你是一个专业的文本优化助手，请将文本转换为更适合朗读的形式。"},
                        {"role": "user", "content": f"{req.prompt}\n\n{req.text}"}
                    ],
                    "temperature": 0.3,
                    "max_tokens": 4096
                }
            )
            
            if response.status_code != 200:
                raise HTTPException(status_code=response.status_code, detail=f"LLM API error: {response.text}")
            
            result = response.json()
            processed_text = result["choices"][0]["message"]["content"]
            
            # Re-split into chunks
            chunks = split_text_for_reading(processed_text)
            
            return JSONResponse({
                "processed_text": processed_text,
                "chunks": chunks
            })
    except httpx.RequestError as e:
        raise HTTPException(status_code=500, detail=f"Request failed: {str(e)}")


@app.post("/api/tts")
async def text_to_speech(req: TTSRequest):
    """Convert text to speech using Cloud API or Local TTS"""
    try:
        audio_id = str(uuid.uuid4())[:8]
        audio_path = STATIC_DIR / "audio" / f"{audio_id}.mp3"
        audio_path.parent.mkdir(exist_ok=True)
        
        if req.use_local_tts:
            # Use edge-tts (Local, Free, High Quality)
            import edge_tts
            
            voice = req.voice or "zh-CN-XiaoxiaoNeural"
            communicate = edge_tts.Communicate(req.text, voice)
            await communicate.save(str(audio_path))
        else:
            # Use Cloud API (Dashscope or OpenAI compatible)
            api_base_lower = req.api_base.lower()
            
            if "dashscope" in api_base_lower or "aliyun" in api_base_lower:
                # Use Dashscope SDK
                import dashscope
                from dashscope.audio.tts_v2 import SpeechSynthesizer, AudioFormat
                
                dashscope.api_key = req.api_key
                dashscope.base_websocket_api_url = 'wss://dashscope.aliyuncs.com/api-ws/v1/inference'
                
                voice = req.voice or "longanyang"
                
                # Model-voice compatibility check
                if "v3" in req.model.lower():
                    if voice in ["longxiaochun_v2", "Chelsie", "Ethan", "Cherry", "Danny"]:
                        voice = "longanyang"
                elif "v2" in req.model.lower():
                    if voice in ["longanyang", "zhichu", "zhiyan"]:
                        voice = "longxiaochun_v2"
                
                class TTSCallback:
                    def __init__(self):
                        self.audio_data = b""
                        self.error_message = None
                    
                    def on_open(self): pass
                    def on_complete(self): pass
                    def on_close(self): pass
                    def on_event(self, message): pass
                    def on_error(self, message: str):
                        self.error_message = message
                    def on_data(self, data: bytes) -> None:
                        self.audio_data += data
                
                callback = TTSCallback()
                synthesizer = SpeechSynthesizer(
                    model=req.model,
                    voice=voice,
                    format=AudioFormat.PCM_22050HZ_MONO_16BIT,
                    callback=callback
                )
                synthesizer.call(req.text)
                
                if callback.error_message:
                    raise HTTPException(status_code=500, detail=f"TTS failed: {callback.error_message}")
                
                # Convert PCM to MP3 using pydub if available, else WAV
                try:
                    from pydub import AudioSegment
                    audio_segment = AudioSegment(
                        callback.audio_data, 
                        frame_rate=22050, 
                        sample_width=2, 
                        channels=1
                    )
                    audio_segment.export(str(audio_path), format="mp3")
                except ImportError:
                    # Fallback to WAV if pydub not installed
                    import wave
                    wav_path = STATIC_DIR / "audio" / f"{audio_id}.wav"
                    with wave.open(str(wav_path), 'wb') as wav_file:
                        wav_file.setnchannels(1)
                        wav_file.setsampwidth(2)
                        wav_file.setframerate(22050)
                        wav_file.writeframes(callback.audio_data)
                    return JSONResponse({
                        "audio_url": f"/static/audio/{audio_id}.wav",
                        "audio_id": audio_id
                    })
            else:
                # Standard OpenAI format
                async with httpx.AsyncClient(timeout=120.0) as client:
                    response = await client.post(
                        f"{req.api_base}/audio/speech",
                        headers={
                            "Authorization": f"Bearer {req.api_key}",
                            "Content-Type": "application/json"
                        },
                        json={
                            "model": req.model,
                            "input": req.text,
                            "voice": req.voice or "alloy",
                            "response_format": "mp3"
                        }
                    )
                    
                    if response.status_code != 200:
                        raise HTTPException(status_code=response.status_code, detail=f"TTS API error: {response.text}")
                    
                    audio_path.write_bytes(response.content)
        
        # Track audio file for this document
        if req.doc_id and req.doc_id in TEXT_STORE:
            if "audio_ids" not in TEXT_STORE[req.doc_id]:
                TEXT_STORE[req.doc_id]["audio_ids"] = []
            TEXT_STORE[req.doc_id]["audio_ids"].append(audio_id)
            
        return JSONResponse({
            "audio_url": f"/static/audio/{audio_id}.mp3",
            "audio_id": audio_id
        })
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"TTS failed: {str(e)}")


@app.delete("/api/audio/{audio_id}")
async def delete_audio(audio_id: str):
    """Delete temporary audio file"""
    audio_path = STATIC_DIR / "audio" / f"{audio_id}.mp3"
    if audio_path.exists():
        audio_path.unlink()
    return JSONResponse({"status": "ok"})


@app.post("/api/clear-cache")
async def clear_cache():
    """Clear all cached audio files and document data"""
    audio_dir = STATIC_DIR / "audio"
    deleted_count = 0
    if audio_dir.exists():
        for f in audio_dir.iterdir():
            if f.is_file():
                f.unlink()
                deleted_count += 1
    
    doc_count = len(TEXT_STORE)
    TEXT_STORE.clear()
    
    return JSONResponse({
        "status": "ok",
        "deleted_audio_files": deleted_count,
        "cleared_documents": doc_count
    })


@app.get("/api/download-full-audio/{doc_id}")
async def download_full_audio(doc_id: str):
    """Combine all audio chunks into a single file and return it"""
    try:
        from pydub import AudioSegment
        import io
        
        if doc_id not in TEXT_STORE:
            raise HTTPException(status_code=404, detail="Document not found")
        
        doc = TEXT_STORE[doc_id]
        audio_ids = doc.get("audio_ids", [])
        
        if not audio_ids:
            raise HTTPException(status_code=404, detail="No audio generated for this document")
        
        # Load and combine all audio segments
        combined = AudioSegment.empty()
        for audio_id in audio_ids:
            audio_path = STATIC_DIR / "audio" / f"{audio_id}.mp3"
            if audio_path.exists():
                segment = AudioSegment.from_mp3(audio_path)
                combined += segment
        
        # Export combined audio
        output = io.BytesIO()
        combined.export(output, format="mp3")
        output.seek(0)
        
        return Response(content=output.read(), media_type="audio/mpeg", headers={
            "Content-Disposition": f"attachment; filename={doc_id}_full_audio.mp3"
        })
    except ImportError:
        raise HTTPException(status_code=500, detail="pydub is required for audio combining")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to combine audio: {str(e)}")


def split_text_for_reading(text: str, max_chunk_length: int = 200) -> list[dict]:
    """Split text into readable chunks with paragraph awareness"""
    # Clean text
    text = re.sub(r'\s+', ' ', text)
    text = re.sub(r'(\w+)-\s+(\w+)', r'\1\2', text)
    text = text.strip()
    
    # Split by paragraphs first
    paragraphs = re.split(r'\n\s*\n', text)
    
    chunks = []
    chunk_id = 0
    
    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
            
        # If paragraph is too long, split by sentences
        if len(para) > max_chunk_length:
            sentences = re.split(r'([。！？\.!?]+)', para)
            current = ""
            for i in range(0, len(sentences), 2):
                sentence = sentences[i]
                punct = sentences[i + 1] if i + 1 < len(sentences) else ""
                
                if len(current) + len(sentence) + len(punct) > max_chunk_length and current:
                    chunks.append({
                        "id": chunk_id,
                        "text": current.strip()
                    })
                    chunk_id += 1
                    current = sentence + punct
                else:
                    current += sentence + punct
            
            if current.strip():
                chunks.append({
                    "id": chunk_id,
                    "text": current.strip()
                })
                chunk_id += 1
        else:
            chunks.append({
                "id": chunk_id,
                "text": para
            })
            chunk_id += 1
    
    return chunks


# Serve the app
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000, reload=True)

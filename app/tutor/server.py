"""Local web server. Listens on 127.0.0.1 only and rejects requests coming from other websites."""

import hashlib
import json
import mimetypes
import re
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI, File, Form, Request, UploadFile, WebSocket
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from . import config, drills, materials
from .llm import LLM, ModelError
from .store import Duplicate, NotFound, Store
from .live import LiveSession
from .tutor import Tutor
from .voice import Voice, VoiceError

MAX_AUDIO_MB = 20   # about 20 minutes of Opus speech; recordings are capped at 2 minutes in the page

# The page's scripts are ES modules, which the browser refuses unless they are served as JavaScript; on Windows the
# registry decides the type of .js files and is sometimes "text/plain"
mimetypes.add_type("text/javascript", ".js")


class SpeakIn(BaseModel):
    text: str
    voice: str | None = None
    speed: float | None = None


class CourseIn(BaseModel):
    name: str
    description: str = ""


class CoursePatch(BaseModel):
    name: str | None = None
    description: str | None = None


class TopicIn(BaseModel):
    title: str


class TopicPatch(BaseModel):
    title: str | None = None
    notes: str | None = None


class MoveIn(BaseModel):
    direction: int


class GradeIn(BaseModel):
    grade: str


class SessionIn(BaseModel):
    course_id: int | None = None
    topic_id: int | None = None


class SessionPatch(BaseModel):
    title: str


class MessageIn(BaseModel):
    text: str
    voice: bool = False   # spoken, or read aloud: reply in a short conversational style


class MemoryIn(BaseModel):
    kind: str = "note"
    content: str


class MemoryPatch(BaseModel):
    kind: str | None = None
    content: str | None = None
    active: bool | None = None


class VocabIn(BaseModel):
    word: str
    meaning: str
    example: str = ""


class VocabPatch(BaseModel):
    meaning: str | None = None
    example: str | None = None


class WordListIn(BaseModel):
    slug: str
    title: str
    description: str = ""
    source: str = ""
    items: list[dict]
    daily_new: int = 10           # for a new list only


class WordListPatch(BaseModel):
    daily_new: int


class ResolvedIn(BaseModel):
    resolved: bool


class RevalidatedStaticFiles(StaticFiles):
    """Static files the browser must revalidate on every load, so an updated app.js is never stale."""

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        return response


def _safe_filename(name: str) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name.split("/")[-1].split("\\")[-1]).strip(" .")
    return name or "document"


def create_app(store: Store | None = None, llm: LLM | None = None,
               allowed_hosts: set[str] | None = None, voice: Voice | None = None) -> FastAPI:
    store = store or Store(config.DB_PATH)
    llm = llm or LLM(config.OLLAMA_URL, config.MODEL, config.NUM_CTX)
    voice = voice or Voice()   # cheap: the speech models load on first use
    tutor = Tutor(store, llm)
    allowed_hosts = allowed_hosts or {f"127.0.0.1:{config.PORT}", f"localhost:{config.PORT}"}
    allowed_origins = {f"http://{h}" for h in allowed_hosts}

    @asynccontextmanager
    async def lifespan(_app):
        yield
        await llm.close()

    app = FastAPI(title="Personal Tutor", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.store = store
    app.state.tutor = tutor

    @app.middleware("http")
    async def local_only(request: Request, call_next):
        # The Host check blocks DNS-rebinding; the Origin check blocks other sites posting to this server
        if request.headers.get("host", "") not in allowed_hosts:
            return JSONResponse({"detail": "Forbidden host"}, status_code=403)
        if request.method not in ("GET", "HEAD"):
            origin = request.headers.get("origin")
            if (origin and origin not in allowed_origins) or request.headers.get("sec-fetch-site") == "cross-site":
                return JSONResponse({"detail": "Forbidden origin"}, status_code=403)
        return await call_next(request)

    @app.exception_handler(NotFound)
    async def _not_found(_r, e):
        return JSONResponse({"detail": str(e)}, status_code=404)

    @app.exception_handler(Duplicate)
    async def _duplicate(_r, e):
        return JSONResponse({"detail": str(e)}, status_code=409)

    @app.exception_handler(ValueError)
    async def _bad_value(_r, e):
        return JSONResponse({"detail": str(e)}, status_code=400)

    @app.exception_handler(ModelError)
    async def _model(_r, e):
        return JSONResponse({"detail": str(e)}, status_code=503)

    @app.exception_handler(VoiceError)
    async def _voice(_r, e):
        return JSONResponse({"detail": str(e)}, status_code=503)

    # --- page -------------------------------------------------------------
    app.mount("/static", RevalidatedStaticFiles(directory=config.WEB_DIR), name="static")

    @app.get("/")
    async def index():
        return FileResponse(config.WEB_DIR / "index.html", headers={"Cache-Control": "no-cache"})

    @app.get("/api/status")
    async def status():
        return await llm.status()

    # --- profile ----------------------------------------------------------
    @app.get("/api/profile")
    def get_profile():
        return store.get_profile()

    @app.put("/api/profile")
    def put_profile(body: dict):
        return store.update_profile(body)

    # --- courses, materials, topics -----------------------------------------
    @app.get("/api/courses")
    def list_courses():
        return store.list_courses()

    @app.post("/api/courses")
    def create_course(body: CourseIn):
        return store.create_course(body.name, body.description)

    @app.patch("/api/courses/{course_id}")
    def update_course(course_id: int, body: CoursePatch):
        return store.update_course(course_id, body.name, body.description)

    @app.delete("/api/courses/{course_id}")
    def delete_course(course_id: int):
        for doc in store.list_documents(course_id):
            _remove_stored(doc)
        store.delete_course(course_id)
        return {"ok": True}

    @app.get("/api/courses/{course_id}/documents")
    def list_documents(course_id: int):
        store.get_course(course_id)
        return store.list_documents(course_id)

    @app.post("/api/courses/{course_id}/documents")
    async def upload_documents(course_id: int, files: list[UploadFile] = File(...)):
        course = store.get_course(course_id)
        results = []
        for f in files:
            name = _safe_filename(f.filename or "document")
            data = await f.read(config.MAX_UPLOAD_MB * 1024 * 1024 + 1)
            try:
                if len(data) > config.MAX_UPLOAD_MB * 1024 * 1024:
                    raise ValueError(f"File is larger than {config.MAX_UPLOAD_MB} MB")
                doc = await run_in_threadpool(_import_document, course, name, data)
                results.append({"filename": name, "ok": True, "document": doc})
            except (ValueError, Duplicate) as e:
                results.append({"filename": name, "ok": False, "error": str(e)})
            except Exception as e:  # a broken PDF must not stop the other files
                results.append({"filename": name, "ok": False, "error": f"Could not read file: {e}"})
        return results

    def _import_document(course: dict, name: str, data: bytes) -> dict:
        if not name.lower().endswith(materials.SUPPORTED_EXTENSIONS):
            raise ValueError(f"Unsupported file type. Use one of: {', '.join(materials.SUPPORTED_EXTENSIONS)}")
        sha = hashlib.sha256(data).hexdigest()
        if any(d["sha256"] == sha for d in store.list_documents(course["id"])):
            raise Duplicate(f"'{name}' is already in this course")
        pages, page_count = materials.extract_pages(name, data)
        chunks = materials.chunk_pages(pages)
        folder = config.MATERIALS_DIR / f"{course['id']}-{_safe_filename(course['name'])[:60]}"
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / name
        i = 1
        reused = False
        while target.exists():
            # The identical file is already stored there (the course materials that ship with the project, loaded
            # into a fresh database): keep using it instead of saving a second copy as "name (1).ext"
            if target.stat().st_size == len(data) and target.read_bytes() == data:
                reused = True
                break
            target = folder / f"{target.stem.rsplit(' (', 1)[0]} ({i}){target.suffix}"
            i += 1
        if not reused:
            target.write_bytes(data)
        try:
            return store.add_document(course["id"], name, str(target), sha, page_count, chunks)
        except Exception:
            if not reused:
                target.unlink(missing_ok=True)
            raise

    def _remove_stored(doc: dict) -> None:
        path = config.MATERIALS_DIR.joinpath(doc["stored_path"]).resolve()
        if config.MATERIALS_DIR.resolve() in path.parents:
            path.unlink(missing_ok=True)

    @app.delete("/api/documents/{document_id}")
    def delete_document(document_id: int):
        _remove_stored(store.delete_document(document_id))
        return {"ok": True}

    @app.get("/api/courses/{course_id}/topics")
    def list_topics(course_id: int):
        store.get_course(course_id)
        return store.list_topics(course_id)

    @app.post("/api/courses/{course_id}/topics")
    def add_topic(course_id: int, body: TopicIn):
        return store.add_topic(course_id, body.title)

    @app.post("/api/courses/{course_id}/topics/suggest")
    async def suggest_topics(course_id: int):
        return {"topics": await tutor.suggest_topics(course_id)}

    @app.patch("/api/topics/{topic_id}")
    def update_topic(topic_id: int, body: TopicPatch):
        return store.update_topic(topic_id, body.title, body.notes)

    @app.post("/api/topics/{topic_id}/move")
    def move_topic(topic_id: int, body: MoveIn):
        return store.move_topic(topic_id, body.direction)

    @app.post("/api/topics/{topic_id}/review")
    def review_topic(topic_id: int, body: GradeIn):
        return store.review_topic(topic_id, body.grade)

    @app.put("/api/topics/{topic_id}/documents/{document_id}")
    def link_material(topic_id: int, document_id: int):
        store.link_topic_document(topic_id, document_id)
        return {"document_ids": store.topic_document_ids(topic_id)}

    @app.delete("/api/topics/{topic_id}/documents/{document_id}")
    def unlink_material(topic_id: int, document_id: int):
        store.unlink_topic_document(topic_id, document_id)
        return {"document_ids": store.topic_document_ids(topic_id)}

    @app.delete("/api/topics/{topic_id}")
    def delete_topic(topic_id: int):
        store.delete_topic(topic_id)
        return {"ok": True}

    # --- sessions and chat --------------------------------------------------
    @app.get("/api/sessions")
    def list_sessions():
        # The first request the page always makes: today's words from the word lists join here too, so they
        # do not wait until the Today or Vocabulary page is opened
        store.introduce_words()
        return store.list_sessions(limit=100)   # the sidebar's chat list

    @app.post("/api/sessions")
    async def create_session(body: SessionIn):
        # Only one session is open at a time: close (and summarise) any session left open
        closed = []
        for s in store.open_sessions():
            closed.append(await tutor.end_session(s["id"]))
        session = store.create_session(body.course_id, body.topic_id)
        return {"session": session, "closed": closed}

    @app.get("/api/sessions/{session_id}")
    def get_session(session_id: int):
        return {"session": store.get_session(session_id), "messages": store.messages(session_id)}

    @app.patch("/api/sessions/{session_id}")
    def rename_session(session_id: int, body: SessionPatch):
        return store.rename_session(session_id, body.title)

    @app.delete("/api/sessions/{session_id}")
    def delete_session(session_id: int):
        store.delete_session(session_id)
        return {"ok": True}

    @app.post("/api/sessions/{session_id}/end")
    async def end_session(session_id: int):
        return await tutor.end_session(session_id)

    @app.post("/api/sessions/{session_id}/reopen")
    async def reopen_session(session_id: int):
        if not store.get_session(session_id)["ended_at"]:
            raise ValueError("This chat is still open")
        # Only one session is open at a time: the open one is ended the normal way (summary, memory) first
        for s in store.open_sessions():
            await tutor.end_session(s["id"])
        return store.reopen_session(session_id)

    @app.post("/api/sessions/{session_id}/messages")
    async def send_message(session_id: int, body: MessageIn):
        session = store.get_session(session_id)
        if session["ended_at"]:
            raise ValueError("This session has ended. Start a new session.")
        if not body.text.strip():
            raise ValueError("Message is empty")

        async def events():
            try:
                async for event in tutor.chat(session_id, body.text, voice=body.voice):
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            except (ModelError, ValueError) as e:
                yield f"data: {json.dumps({'type': 'error', 'message': str(e)}, ensure_ascii=False)}\n\n"

        return StreamingResponse(events(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    # --- voice --------------------------------------------------------------
    @app.get("/api/voice/status")
    def voice_status():
        return voice.status()

    @app.post("/api/voice/warmup")
    async def voice_warmup():
        return await run_in_threadpool(voice.warmup)

    @app.get("/api/voice/voices")
    async def voice_list():
        return await run_in_threadpool(voice.tts.voices)

    @app.post("/api/voice/transcribe")
    async def transcribe(audio: UploadFile = File(...), session_id: int | None = Form(None)):
        data = await audio.read(MAX_AUDIO_MB * 1024 * 1024 + 1)
        if len(data) > MAX_AUDIO_MB * 1024 * 1024:
            raise ValueError("Recording is too long")
        hint = ""
        if session_id:
            s = store.get_session(session_id)
            hint = ". ".join(x for x in (s["course_name"], s["topic_title"]) if x)
        return await run_in_threadpool(voice.stt.transcribe, data, hint)

    @app.websocket("/api/live")
    async def live(ws: WebSocket, session_id: int):
        # The HTTP middleware does not see WebSockets, so the same local-only checks are done here.
        # Browsers always send Origin on WebSockets; requiring ours blocks other sites from connecting.
        if ws.headers.get("host") not in allowed_hosts or ws.headers.get("origin") not in allowed_origins:
            await ws.close(code=1008)
            return
        try:
            session = store.get_session(session_id)
        except NotFound:
            await ws.close(code=1008)
            return
        if session["ended_at"]:
            await ws.close(code=1008)
            return
        await ws.accept()
        vad_factory = getattr(app.state, "live_vad_factory", None)   # tests swap in a scripted VAD
        await LiveSession(ws, store, tutor, voice, session_id, vad=vad_factory() if vad_factory else None).run()

    @app.post("/api/voice/speak")
    async def speak(body: SpeakIn):
        p = store.get_profile()
        wav = await run_in_threadpool(voice.tts.speak, body.text, body.voice or p["voice"],
                                      body.speed or float(p["speech_speed"]))
        return Response(wav, media_type="audio/wav", headers={"Cache-Control": "no-store"})

    # --- pronunciation ------------------------------------------------------
    @app.get("/api/pronunciation/drills")
    def pron_drills():
        return [{**d, "sounds": [{"phone": p, "ipa": drills.IPA[p], "how": drills.HOW[p]} for p in d["sounds"]]}
                for d in drills.DRILLS]

    @app.get("/api/pronunciation/ipa")
    def pron_ipa(text: str):
        from .assessment import expected_ipa
        return expected_ipa(text[:400])

    @app.post("/api/pronunciation/check")
    async def pron_check(audio: UploadFile = File(...), text: str = Form(...), drill: str | None = Form(None)):
        data = await audio.read(MAX_AUDIO_MB * 1024 * 1024 + 1)
        if len(data) > MAX_AUDIO_MB * 1024 * 1024:
            raise ValueError("Recording is too long")
        text = text.strip()[:400]
        d = drills.DRILL_BY_ID.get(drill or "")
        focus = set(d.get("focus", d["sounds"])) if d else set()
        result = await run_in_threadpool(voice.pron.check, data, text, focus)
        if d and len(result["words"]) == 1:
            # "It sounded closer to sheep": the pair partner the learner's swap turned the word into
            partner = drills.sounded_like(d, focus, result["words"][0])
            if partner:
                result["sounded_like"] = partner
        result["id"] = store.add_pron_attempt(text, d["id"] if d else None, result)
        return result

    @app.get("/api/pronunciation/progress")
    def pron_progress():
        sounds = store.pron_sound_stats()
        for s in sounds:
            s["ipa"] = drills.IPA.get(s["phone"], s["phone"])
            s["instead_ipa"] = [drills.IPA.get(x, "") if x else "nothing" for x in s["instead"]]
            s["drill"] = next((d["id"] for d in drills.DRILLS if s["phone"] in d["sounds"]), None)
        return {"sounds": sounds, "added_vowels": store.pron_added_count(), "recent": store.pron_attempts(15)}

    @app.get("/api/pronunciation/attempts/{attempt_id}")
    def pron_attempt(attempt_id: int):
        return store.pron_attempt(attempt_id)

    # --- memory -------------------------------------------------------------
    @app.get("/api/memories")
    def list_memories():
        return store.list_memories()

    @app.post("/api/memories")
    def add_memory(body: MemoryIn):
        row = store.add_memory(body.kind, body.content, "user")
        if row is None:
            raise Duplicate("A memory with the same meaning already exists")
        return row

    @app.patch("/api/memories/{memory_id}")
    def update_memory(memory_id: int, body: MemoryPatch):
        return store.update_memory(memory_id, body.content, body.kind, body.active)

    @app.delete("/api/memories/{memory_id}")
    def delete_memory(memory_id: int):
        store.delete_memory(memory_id)
        return {"ok": True}

    # --- vocabulary ---------------------------------------------------------
    # Today's new words from the word lists join the vocabulary when the learner opens it or the Today page
    @app.get("/api/vocab")
    def list_vocab():
        store.introduce_words()
        return store.list_vocab()

    @app.get("/api/vocab/due")
    def due_vocab():
        store.introduce_words()
        return store.due_vocab()

    @app.get("/api/word-lists")
    def word_lists():
        return store.word_lists()

    @app.post("/api/word-lists")
    def save_word_list(body: WordListIn):
        return store.save_word_list(body.slug, body.title, body.description, body.source, body.items, body.daily_new)

    @app.patch("/api/word-lists/{list_id}")
    def update_word_list(list_id: int, body: WordListPatch):
        return store.update_word_list(list_id, body.daily_new)

    @app.post("/api/vocab")
    def add_vocab(body: VocabIn):
        row, created = store.add_vocab(body.word, body.meaning, body.example, origin="user")
        if not created:
            raise Duplicate(f"'{row['word']}' is already in your vocabulary")
        return row

    @app.patch("/api/vocab/{vocab_id}")
    def update_vocab(vocab_id: int, body: VocabPatch):
        return store.update_vocab(vocab_id, body.meaning, body.example)

    @app.post("/api/vocab/{vocab_id}/review")
    def review_vocab(vocab_id: int, body: GradeIn):
        return store.review_vocab(vocab_id, body.grade)

    @app.delete("/api/vocab/{vocab_id}")
    def delete_vocab(vocab_id: int):
        store.delete_vocab(vocab_id)
        return {"ok": True}

    # --- mistakes, plan, backup -----------------------------------------------
    @app.get("/api/mistakes")
    def list_mistakes():
        return store.list_mistakes()

    @app.patch("/api/mistakes/{pattern_id}")
    def resolve_mistake(pattern_id: int, body: ResolvedIn):
        return store.set_mistake_resolved(pattern_id, body.resolved)

    @app.delete("/api/mistakes/{pattern_id}")
    def delete_mistake(pattern_id: int):
        store.delete_mistake(pattern_id)
        return {"ok": True}

    @app.get("/api/plan")
    def plan():
        store.introduce_words()
        return store.plan()

    @app.post("/api/backup")
    def backup():
        path = store.backup(config.BACKUP_DIR / f"tutor-{datetime.now():%Y%m%d-%H%M%S}.sqlite3")
        return {"path": str(path)}

    return app

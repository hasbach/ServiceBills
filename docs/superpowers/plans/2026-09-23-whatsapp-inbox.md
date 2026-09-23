# WhatsApp Inbox + Web Push Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an admin **Inbox** sub-tab to Messaging, in three parts:
- It persists every WhatsApp Cloud API conversation.
- It surfaces the conversations that need a human.
- It lets admins play and reply with text/emoji, voice notes, stickers and reactions (templates once the 24h window closes), and it web-pushes admins when a conversation needs attention.

**Architecture:**
- **Webhook.** The webhook persists each inbound message into two new tables, `WhatsAppConversation` and `WhatsAppMessage`, *before* the AI runs. It dedupes Meta retries by `wamid` and flags "needs attention" reasons. Media is downloaded into the existing `storage` backend (R2 in production) in a background greenlet.
- **New modules.** The logic lives in new modules so `app.py` doesn't grow:
  - `whatsapp_inbox.py`: persistence, flags, push, sends.
  - `whatsapp_inbox_routes.py`: the admin API.
  - `media_convert.py`: ffmpeg and Pillow conversions.
  Every function takes the `app` module as its first argument (`appmod`), exactly like `cs_agent_tools.py`, to avoid circular imports.
- **Frontend.** A React/MUI two-pane inbox polls the API. Web push reuses the existing VAPID/`PushSubscription` stack, extended with role and topic targeting.

**Tech Stack:** Flask + Flask-SQLAlchemy + Alembic, gevent, pywebpush, Pillow (new), ffmpeg (new, system binary), React 18 + MUI v5 (CRA), `emoji-picker-react` (new), pytest, and Jest via react-scripts.

**Spec:** `docs/superpowers/specs/2026-09-23-whatsapp-inbox-design.md`

## Global Constraints

**Time and the WhatsApp window**
- The customer-service window is **24h** from `last_inbound_at`.
- AI auto-resumes **24h** after the last admin activity.
- The push throttle is **one push per conversation per 2 minutes**.
- Timestamps are serialized as `strftime('%Y-%m-%d %H:%M:%S')` in UTC. The frontend parses them with `parseUtc` from `frontend/src/components/formatStamp.js`, the existing app convention; there is no `Z` from the backend.

**Allowed values**
- Attention reasons, exactly: `ai_failed`, `escalated`, `awaiting_admin`, `unknown_sender`, `ai_inactive`, `media_received`, `send_failed`.
- `media_received` is flagged only for `image`, `video`, `document`, `location`, `contacts`. It is **never** flagged for `sticker` or `reaction`.
- Push topics: `tickets`, `whatsapp_inbox`. A NULL `topics` column means both.

**Size limits**
- Voice upload ≤ **16 MB**. Sticker source upload ≤ **5 MB**.
- Output sticker is 512×512 webp. Static ≤ **100 KB**; animated webp is accepted as-is only if already 512×512 and ≤ **500 KB**.

**Voice conversion**
- The exact ffmpeg arguments for voice are `-vn -c:a libopus -b:a 32k -ac 1 -ar 48000`, output `.ogg`.
- Inbound audio also gets an mp3 playback copy (`-vn -c:a libmp3lame -b:a 64k`).

**Access control**
- All inbox routes require a JWT whose comma-separated `role` contains `admin`. Check it with `appmod._jwt_roles()`, not the strict `admin_required()`.
- Every query is tenant-scoped via `tenant_query`.

**Webhook behaviour**
- The webhook must always return 200 once the signature is verified, including when persistence fails.
- Messages from `forwarding_mobile` are **not** persisted to the inbox.

**Migrations and tests**
- The Alembic migration creates new tables only. The one column add (`push_subscription.topics`) is guarded with `sa.inspect(bind)`, because production schema drift is known.
- The new migration's `down_revision` is `'f1a2b3c4d5e6'`, the current head.
- Tests use `tests/conftest.py` fixtures (`app`, `client`, `make_tenant`, `auth_headers`) and never make real network calls. Monkeypatch `requests.post` / `requests.get` and `cs_agent_tools.*`.
- ffmpeg-dependent tests are `@pytest.mark.skipif(not media_convert.ffmpeg_available(), ...)`, because the GitHub Actions runner has no ffmpeg.

**Local testing**
- `preview_start` reads the main checkout's `.claude/launch.json`. When browser-testing, run the backend against a throwaway `DATABASE_URL`/`DATABASE_PATH`, never the user's real dev DB.

**Two spec corrections, decided while planning (apply them; they are not open questions)**
1. **Media is streamed, not redirected.** The media endpoint **streams bytes** through Flask for both backends; it does *not* 302 to a presigned R2 URL. The browser must fetch media with the JWT header (via axios as a blob), and a cross-origin redirect to R2 would need bucket CORS rules.
2. **`ai_failed` has a second trigger.** It is also set when the tenant has a Gemini key configured but Gemini produced nothing and the rule-based fallback answered instead. The rule-based fallback *always* returns some text, so "returned no reply" alone would almost never fire.

## Before you start (pre-flight)

When this plan was written, the checkout on branch `feature/whatsapp-inbox` had **uncommitted, unrelated work**:
- a modified `app.py` (a stale-agent-job sweep);
- untracked `tests/test_stale_agent_job_sweep.py` and `graphify-out/`.

Several tasks here modify and commit `app.py`, so `git add app.py` would sweep that work into these commits.

- Run `git status` first.
- If `app.py` still has changes that aren't from this plan, **stop and ask the user** to commit or stash them before Task 3. Do not stash, commit or discard them yourself.
- Work in a fresh worktree off `feature/whatsapp-inbox` (superpowers:using-git-worktrees) only after that is resolved.

---

## File Structure

| File | Status | Responsibility |
|---|---|---|
| `storage.py` | modify | add `save_bytes()`, `read_bytes()` |
| `media_convert.py` | create | ffmpeg (`to_ogg_opus`, `to_mp3`, `ffmpeg_available`) + Pillow (`to_sticker_webp`) |
| `app.py` | modify | models `WhatsAppConversation`, `WhatsAppMessage`; `PushSubscription.topics`; push refactor + push routes; webhook integration; register inbox routes |
| `migrations/versions/a7c3e9d1b2f4_add_whatsapp_inbox.py` | create | tables + guarded column |
| `whatsapp_inbox.py` | create | parse/persist/dedupe, attention flags, window, status updates, media download, push notify, AI hooks, admin sends |
| `whatsapp_inbox_routes.py` | create | `register_inbox_routes(app, appmod)`: the 8 admin endpoints |
| `cs_agent_tools.py` | modify | record AI replies to inbox, expose `ai_source`/`gemini_configured`, `send_whatsapp_voice` returns wamid, escalation flag |
| `requirements.txt`, `Dockerfile` | modify | Pillow, ffmpeg |
| `tests/inbox_helpers.py` | create | shared test helpers |
| `tests/test_storage_bytes.py`, `tests/test_media_convert.py`, `tests/test_inbox_migration.py`, `tests/test_whatsapp_inbox_core.py`, `tests/test_push_targeting.py`, `tests/test_whatsapp_inbox_webhook.py`, `tests/test_whatsapp_inbox_ai.py`, `tests/test_whatsapp_inbox_send.py`, `tests/test_whatsapp_inbox_routes.py` | create | backend tests |
| `frontend/public/manifest.json` | create | PWA manifest (currently missing) |
| `frontend/public/index.html` | modify | iOS PWA meta |
| `frontend/public/service-worker.js` | modify | tag/renotify, postMessage on click |
| `frontend/src/context/AppContext.js` | modify | inbox + push API wrappers |
| `frontend/src/components/inbox/inboxFormat.js` (+ `.test.js`) | create | pure helpers: window countdown, reactions, labels, template params |
| `frontend/src/components/inbox/InboxView.js` | create | two-pane container + polling |
| `frontend/src/components/inbox/ConversationList.js` | create | list, filter, search |
| `frontend/src/components/inbox/ChatThread.js` | create | bubbles, header actions |
| `frontend/src/components/inbox/InboxMedia.js` | create | authenticated blob media renderer |
| `frontend/src/components/inbox/Composer.js` | create | text/emoji/reply/react/sticker/template |
| `frontend/src/components/inbox/VoiceRecorder.js` | create | MediaRecorder capture |
| `frontend/src/components/inbox/InboxNotificationsControl.js` | create | push enable/disable/test |
| `frontend/src/components/MessagingView.js` | modify | Inbox tab first, deep-link prop |
| `frontend/src/App.js` | modify | nav badge, SW message → open conversation |

---

### Task 1: Byte-level storage helpers

**Files:**
- Modify: `storage.py`
- Test: `tests/test_storage_bytes.py`

**Interfaces:**
- Produces:
  - `storage.save_bytes(data: bytes, tenant_id: int, filename: str, content_type: str) -> str`, which returns the key.
  - `storage.read_bytes(key: str) -> bytes`, which raises `FileNotFoundError` if missing.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_storage_bytes.py
import pytest
import storage


def test_local_save_and_read_bytes_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "UPLOAD_ROOT", str(tmp_path))
    monkeypatch.setattr(storage, "_backend", storage.LocalBackend())
    key = storage.save_bytes(b"\x00\x01voice", 7, "voice.ogg", "audio/ogg")
    assert key.startswith("7/") and key.endswith("-voice.ogg")
    assert storage.read_bytes(key) == b"\x00\x01voice"


def test_local_read_missing_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "UPLOAD_ROOT", str(tmp_path))
    monkeypatch.setattr(storage, "_backend", storage.LocalBackend())
    with pytest.raises(FileNotFoundError):
        storage.read_bytes("7/nope.bin")


def test_local_read_rejects_path_traversal(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "UPLOAD_ROOT", str(tmp_path))
    monkeypatch.setattr(storage, "_backend", storage.LocalBackend())
    with pytest.raises(FileNotFoundError):
        storage.read_bytes("../../etc/passwd")
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/test_storage_bytes.py -v`
Expected: FAIL with `AttributeError: module 'storage' has no attribute 'save_bytes'`

- [ ] **Step 3: Implement**

In `storage.py`, add to `LocalBackend` (after `save`):

```python
    def save_bytes(self, data, tenant_id, filename, content_type):
        key = _key(tenant_id, filename)
        path = os.path.join(UPLOAD_ROOT, key)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(data)
        return key

    def read_bytes(self, key):
        root = os.path.abspath(UPLOAD_ROOT)
        path = os.path.abspath(os.path.join(root, key))
        if not path.startswith(root + os.sep) or not os.path.isfile(path):
            raise FileNotFoundError(key)
        with open(path, "rb") as f:
            return f.read()
```

Add to `S3Backend` (after `save`):

```python
    def save_bytes(self, data, tenant_id, filename, content_type):
        key = _key(tenant_id, filename)
        self._c.put_object(Bucket=self._bucket, Key=self._full(key), Body=data,
                           ContentType=content_type or "application/octet-stream")
        return key

    def read_bytes(self, key):
        try:
            obj = self._c.get_object(Bucket=self._bucket, Key=self._full(key))
        except self._c.exceptions.NoSuchKey:
            raise FileNotFoundError(key)
        return obj["Body"].read()
```

Add module-level functions at the end:

```python
def save_bytes(data, tenant_id, filename, content_type):
    """Persist raw bytes (e.g. WhatsApp media) under the tenant's namespace; return the key."""
    return _get().save_bytes(data, tenant_id, filename, content_type)


def read_bytes(key):
    """Read a stored object's bytes. Raises FileNotFoundError when missing."""
    return _get().read_bytes(key)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m pytest tests/test_storage_bytes.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add storage.py tests/test_storage_bytes.py
git commit -m "Add byte-level save/read to storage backends"
```

---

### Task 2: Media conversion (ffmpeg + Pillow) and deploy dependencies

**Files:**
- Create: `media_convert.py`
- Modify: `requirements.txt`, `Dockerfile`
- Test: `tests/test_media_convert.py`

**Interfaces:**
- Produces:
  - `media_convert.ConversionError(Exception)`
  - `media_convert.ffmpeg_available() -> bool`
  - `media_convert.to_ogg_opus(data: bytes) -> bytes`
  - `media_convert.to_mp3(data: bytes) -> bytes`
  - `media_convert.to_sticker_webp(data: bytes) -> bytes`
  - Constants: `VOICE_MAX_BYTES = 16*1024*1024`, `STICKER_SOURCE_MAX_BYTES = 5*1024*1024`

- [ ] **Step 1: Add dependencies**

Append to `requirements.txt`:

```
Pillow
```

In `Dockerfile`, directly after `WORKDIR /app` in Stage 2, add:

```dockerfile
# ffmpeg converts admin-recorded voice notes to WhatsApp's OGG/Opus and makes
# mp3 playback copies of inbound voice notes (see media_convert.py).
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*
```

Run: `pip install Pillow`

- [ ] **Step 2: Write the failing test**

```python
# tests/test_media_convert.py
import io
import math
import struct
import wave

import pytest
from PIL import Image

import media_convert

needs_ffmpeg = pytest.mark.skipif(not media_convert.ffmpeg_available(), reason="ffmpeg not installed")


def _png(w, h, color=(255, 0, 0, 255)):
    buf = io.BytesIO()
    Image.new("RGBA", (w, h), color).save(buf, "PNG")
    return buf.getvalue()


def _wav_bytes(seconds=1):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
        frames = b"".join(struct.pack("<h", int(8000 * math.sin(i / 10))) for i in range(16000 * seconds))
        w.writeframes(frames)
    return buf.getvalue()


def test_png_becomes_512_webp_under_100kb():
    out = media_convert.to_sticker_webp(_png(800, 400))
    img = Image.open(io.BytesIO(out))
    assert img.format == "WEBP"
    assert img.size == (512, 512)
    assert len(out) <= 100 * 1024


def test_ready_webp_passes_through_unchanged():
    buf = io.BytesIO()
    Image.new("RGBA", (512, 512), (0, 255, 0, 255)).save(buf, "WEBP")
    data = buf.getvalue()
    assert media_convert.to_sticker_webp(data) == data


def test_non_image_raises():
    with pytest.raises(media_convert.ConversionError):
        media_convert.to_sticker_webp(b"not an image")


@needs_ffmpeg
def test_to_ogg_opus_produces_ogg():
    out = media_convert.to_ogg_opus(_wav_bytes())
    assert out[:4] == b"OggS"


@needs_ffmpeg
def test_to_mp3_produces_mp3():
    out = media_convert.to_mp3(_wav_bytes())
    assert out[:3] == b"ID3" or out[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2")


@needs_ffmpeg
def test_garbage_audio_raises():
    with pytest.raises(media_convert.ConversionError):
        media_convert.to_ogg_opus(b"garbage")


def test_missing_ffmpeg_raises_conversion_error(monkeypatch):
    monkeypatch.setattr(media_convert.shutil, "which", lambda name: None)
    with pytest.raises(media_convert.ConversionError):
        media_convert.to_ogg_opus(b"x")
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `python -m pytest tests/test_media_convert.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'media_convert'`

- [ ] **Step 4: Implement `media_convert.py`**

```python
"""Media conversions for the WhatsApp inbox.

- Voice: admin-recorded audio (webm/opus from Chrome/Firefox, mp4/aac from
  Safari) -> OGG/Opus, the format WhatsApp plays as a voice note; inbound
  voice notes -> mp3 so Safari/iOS admins can play them back.
- Stickers: any image -> 512x512 transparent webp <= 100 KB.

ffmpeg is a system binary (installed in the Dockerfile). Always invoked with
an argument list (never a shell), a timeout, and private temp files.
"""
import io
import os
import shutil
import subprocess
import tempfile

from PIL import Image

VOICE_MAX_BYTES = 16 * 1024 * 1024
STICKER_SOURCE_MAX_BYTES = 5 * 1024 * 1024
STICKER_SIZE = 512
STICKER_STATIC_MAX = 100 * 1024
STICKER_ANIMATED_MAX = 500 * 1024
FFMPEG_TIMEOUT_SECONDS = 60


class ConversionError(Exception):
    pass


def ffmpeg_available():
    return shutil.which("ffmpeg") is not None


def _ffmpeg(data, out_suffix, args):
    exe = shutil.which("ffmpeg")
    if not exe:
        raise ConversionError("ffmpeg is not installed on this server")
    with tempfile.TemporaryDirectory() as tmp:
        src = os.path.join(tmp, "input.bin")
        dst = os.path.join(tmp, "output" + out_suffix)
        with open(src, "wb") as f:
            f.write(data)
        try:
            result = subprocess.run(
                [exe, "-hide_banner", "-loglevel", "error", "-y", "-i", src, *args, dst],
                capture_output=True, timeout=FFMPEG_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            raise ConversionError("ffmpeg timed out")
        if result.returncode != 0 or not os.path.exists(dst) or os.path.getsize(dst) == 0:
            raise ConversionError(result.stderr.decode("utf-8", errors="replace")[-300:] or "conversion failed")
        with open(dst, "rb") as f:
            return f.read()


def to_ogg_opus(data):
    return _ffmpeg(data, ".ogg", ["-vn", "-c:a", "libopus", "-b:a", "32k", "-ac", "1", "-ar", "48000"])


def to_mp3(data):
    return _ffmpeg(data, ".mp3", ["-vn", "-c:a", "libmp3lame", "-b:a", "64k"])


def to_sticker_webp(data):
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except Exception:
        raise ConversionError("file is not a readable image")
    animated = bool(getattr(img, "is_animated", False))
    if img.format == "WEBP" and img.size == (STICKER_SIZE, STICKER_SIZE):
        limit = STICKER_ANIMATED_MAX if animated else STICKER_STATIC_MAX
        if len(data) <= limit:
            return data
    if animated:
        raise ConversionError("animated stickers must already be 512x512 webp under 500 KB")
    img = img.convert("RGBA")
    img.thumbnail((STICKER_SIZE, STICKER_SIZE), Image.LANCZOS)
    canvas = Image.new("RGBA", (STICKER_SIZE, STICKER_SIZE), (0, 0, 0, 0))
    canvas.paste(img, ((STICKER_SIZE - img.width) // 2, (STICKER_SIZE - img.height) // 2), img)
    for quality in (90, 80, 70, 60, 50, 40, 30):
        buf = io.BytesIO()
        canvas.save(buf, "WEBP", quality=quality, method=6)
        out = buf.getvalue()
        if len(out) <= STICKER_STATIC_MAX:
            return out
    raise ConversionError("sticker is still over 100 KB after compression")
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m pytest tests/test_media_convert.py -v`
Expected: all pass locally. The dev box has ffmpeg at `C:\Program Files\ffmpeg\bin`. On CI the 3 ffmpeg tests show as skipped.

- [ ] **Step 6: Commit**

```bash
git add media_convert.py requirements.txt Dockerfile tests/test_media_convert.py
git commit -m "Add media_convert for WhatsApp voice/sticker conversion; ship ffmpeg + Pillow"
```

---

### Task 3: Inbox models + migration

**Files:**
- Modify: `app.py`: add models after `CSAgentKnowledgeEntry` (around line 1665); add `topics` to `PushSubscription` (line 1509); append both models to `TENANT_OWNED_MODELS` (around line 1768)
- Create: `migrations/versions/a7c3e9d1b2f4_add_whatsapp_inbox.py`
- Test: `tests/test_inbox_migration.py`

**Interfaces:**
- Produces:
  - `appmod.WhatsAppConversation`, with `.to_dict()`.
  - `appmod.WhatsAppMessage`, with `.to_dict()`.
  - `appmod.DEFAULT_PUSH_TOPICS = ('tickets', 'whatsapp_inbox')`
  - `PushSubscription.topics`, a JSON text column.
  - `PushSubscription.topic_list() -> list[str]`
  - `PushSubscription.endpoint() -> str | None`
  - Column names exactly as in the spec's Data model tables.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_inbox_migration.py
"""Runs the inbox migration's upgrade()/downgrade() directly through Alembic
Operations against a throwaway SQLite file. The full chain can't be walked on
SQLite (see tests/test_topology_migration.py for why), so this stubs the few
parent tables the migration references."""
import importlib.util
import os

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

MIGRATION = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "migrations", "versions", "a7c3e9d1b2f4_add_whatsapp_inbox.py")


def _load():
    spec = importlib.util.spec_from_file_location("inbox_migration", MIGRATION)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_inbox_migration_up_and_down(tmp_path):
    mod = _load()
    assert mod.down_revision == "f1a2b3c4d5e6"
    engine = sa.create_engine(f"sqlite:///{(tmp_path / 'm.db').as_posix()}")
    with engine.begin() as conn:
        for ddl in ("CREATE TABLE tenant (id INTEGER PRIMARY KEY)",
                    "CREATE TABLE user (id INTEGER PRIMARY KEY)",
                    "CREATE TABLE customer (id INTEGER PRIMARY KEY)",
                    "CREATE TABLE push_subscription (id INTEGER PRIMARY KEY, tenant_id INTEGER, "
                    "user_id INTEGER, subscription_info TEXT, created_at DATETIME)"):
            conn.exec_driver_sql(ddl)
        ctx = MigrationContext.configure(conn, opts={"render_as_batch": True})
        with Operations.context(ctx):
            mod.upgrade()
        insp = sa.inspect(conn)
        assert {"whatsapp_conversation", "whatsapp_message"} <= set(insp.get_table_names())
        assert "topics" in {c["name"] for c in insp.get_columns("push_subscription")}
        msg_cols = {c["name"] for c in insp.get_columns("whatsapp_message")}
        assert {"wa_message_id", "media_key", "media_playback_key", "reaction_emoji", "transcript"} <= msg_cols

        with Operations.context(ctx):
            mod.downgrade()
        insp = sa.inspect(conn)
        assert "whatsapp_message" not in insp.get_table_names()
        assert "whatsapp_conversation" not in insp.get_table_names()
        assert "topics" not in {c["name"] for c in insp.get_columns("push_subscription")}


def test_models_exist_and_serialize(app, client):
    import app as appmod
    from tests.conftest import make_tenant
    make_tenant(client, "Biz Model", "model_admin")
    with app.app_context():
        tid = appmod.User.query.filter_by(username="model_admin").first().tenant_id
        c = appmod.WhatsAppConversation(tenant_id=tid, wa_phone="96170123456")
        appmod.db.session.add(c); appmod.db.session.commit()
        m = appmod.WhatsAppMessage(tenant_id=tid, conversation_id=c.id, direction="in",
                                   sender="customer", msg_type="text", text="hi", status="received")
        appmod.db.session.add(m); appmod.db.session.commit()
        d = c.to_dict()
        assert d["wa_phone"] == "96170123456" and d["needs_attention"] is False
        assert m.to_dict()["text"] == "hi"
        assert appmod.WhatsAppConversation in appmod.TENANT_OWNED_MODELS
        sub = appmod.PushSubscription(tenant_id=tid, user_id=1, subscription_info='{"endpoint": "https://e/1"}')
        assert sub.topic_list() == ["tickets", "whatsapp_inbox"]
        assert sub.endpoint() == "https://e/1"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/test_inbox_migration.py -v`
Expected: FAIL (migration file does not exist or `AttributeError: WhatsAppConversation`)

- [ ] **Step 3: Add the models to `app.py`**

Replace the `PushSubscription` class (line 1509) with:

```python
DEFAULT_PUSH_TOPICS = ('tickets', 'whatsapp_inbox')


class PushSubscription(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey('tenant.id'), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    subscription_info = db.Column(db.Text, nullable=False) # JSON
    # JSON list of topics this device wants (see DEFAULT_PUSH_TOPICS). NULL
    # means "all topics" so every pre-existing subscription keeps getting
    # ticket pushes and starts getting inbox pushes.
    topics = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    def topic_list(self):
        try:
            value = json.loads(self.topics) if self.topics else None
        except ValueError:
            value = None
        return value if isinstance(value, list) else list(DEFAULT_PUSH_TOPICS)

    def endpoint(self):
        try:
            return (json.loads(self.subscription_info) or {}).get('endpoint')
        except (ValueError, AttributeError):
            return None
```

After the `CSAgentKnowledgeEntry` class (before the `# --- Tenant write scoping` comment), add:

```python
def _utc_stamp(dt):
    return dt.strftime('%Y-%m-%d %H:%M:%S') if dt else None


class WhatsAppConversation(db.Model):
    """One WhatsApp chat (tenant x customer phone) for the admin inbox -- see
    docs/superpowers/specs/2026-09-23-whatsapp-inbox-design.md."""
    __tablename__ = 'whatsapp_conversation'
    __table_args__ = (
        db.UniqueConstraint('tenant_id', 'wa_phone', name='uq_whatsapp_conversation_tenant_phone'),
    )
    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey('tenant.id'), nullable=False, index=True)
    wa_phone = db.Column(db.String(20), nullable=False)
    customer_id = db.Column(db.Integer, db.ForeignKey('customer.id'), nullable=True)
    contact_name = db.Column(db.String(120), nullable=True)
    needs_attention = db.Column(db.Boolean, nullable=False, default=False, index=True)
    attention_reason = db.Column(db.String(30), nullable=True)
    attention_since = db.Column(db.DateTime, nullable=True)
    ai_paused = db.Column(db.Boolean, nullable=False, default=False)
    ai_paused_at = db.Column(db.DateTime, nullable=True)
    last_admin_reply_at = db.Column(db.DateTime, nullable=True)
    last_inbound_at = db.Column(db.DateTime, nullable=True)
    last_message_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)
    last_message_preview = db.Column(db.String(200), nullable=True)
    unread_count = db.Column(db.Integer, nullable=False, default=0)
    last_push_at = db.Column(db.DateTime, nullable=True)

    customer = db.relationship('Customer', foreign_keys=[customer_id])

    def to_dict(self):
        window_expires = self.last_inbound_at + timedelta(hours=24) if self.last_inbound_at else None
        return {
            'id': self.id,
            'wa_phone': self.wa_phone,
            'contact_name': self.contact_name,
            'customer_id': self.customer_id,
            'customer_name': self.customer.name if self.customer else None,
            'needs_attention': bool(self.needs_attention),
            'attention_reason': self.attention_reason,
            'attention_since': _utc_stamp(self.attention_since),
            'ai_paused': bool(self.ai_paused),
            'last_inbound_at': _utc_stamp(self.last_inbound_at),
            'window_expires_at': _utc_stamp(window_expires),
            'last_message_at': _utc_stamp(self.last_message_at),
            'last_message_preview': self.last_message_preview,
            'unread_count': self.unread_count or 0,
        }


class WhatsAppMessage(db.Model):
    """One inbound or outbound WhatsApp message in an inbox conversation."""
    __tablename__ = 'whatsapp_message'
    __table_args__ = (
        db.UniqueConstraint('tenant_id', 'wa_message_id', name='uq_whatsapp_message_tenant_wamid'),
    )
    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey('tenant.id'), nullable=False, index=True)
    conversation_id = db.Column(db.Integer, db.ForeignKey('whatsapp_conversation.id'), nullable=False, index=True)
    direction = db.Column(db.String(3), nullable=False)          # 'in' | 'out'
    sender = db.Column(db.String(10), nullable=False)            # 'customer' | 'ai' | 'admin' | 'system'
    sent_by_user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    msg_type = db.Column(db.String(20), nullable=False)
    text = db.Column(db.Text, nullable=True)
    transcript = db.Column(db.Text, nullable=True)
    wa_message_id = db.Column(db.String(128), nullable=True)
    reply_to_wa_message_id = db.Column(db.String(128), nullable=True)
    reaction_emoji = db.Column(db.String(16), nullable=True)
    reaction_target_wa_id = db.Column(db.String(128), nullable=True)
    wa_media_id = db.Column(db.String(128), nullable=True)
    media_key = db.Column(db.String(300), nullable=True)
    media_playback_key = db.Column(db.String(300), nullable=True)
    media_mime = db.Column(db.String(80), nullable=True)
    media_status = db.Column(db.String(10), nullable=False, default='none')
    status = db.Column(db.String(12), nullable=False, default='received')
    error_code = db.Column(db.String(20), nullable=True)
    error_message = db.Column(db.String(300), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)

    sent_by = db.relationship('User', foreign_keys=[sent_by_user_id])

    def to_dict(self):
        return {
            'id': self.id,
            'direction': self.direction,
            'sender': self.sender,
            'sent_by': self.sent_by.username if self.sent_by else None,
            'msg_type': self.msg_type,
            'text': self.text,
            'transcript': self.transcript,
            'wa_message_id': self.wa_message_id,
            'reply_to_wa_message_id': self.reply_to_wa_message_id,
            'reaction_emoji': self.reaction_emoji,
            'reaction_target_wa_id': self.reaction_target_wa_id,
            'media_status': self.media_status,
            'media_mime': self.media_mime,
            'has_playback': bool(self.media_playback_key),
            'status': self.status,
            'error_code': self.error_code,
            'error_message': self.error_message,
            'created_at': _utc_stamp(self.created_at),
        }
```

In `TENANT_OWNED_MODELS`, change the last line `CustomerPaymentLink, CustomerWhishPaymentAttempt, NetworkNode,` to:

```python
    CustomerPaymentLink, CustomerWhishPaymentAttempt, NetworkNode,
    WhatsAppConversation, WhatsAppMessage,
```

- [ ] **Step 4: Write the migration**

```python
# migrations/versions/a7c3e9d1b2f4_add_whatsapp_inbox.py
"""add whatsapp inbox tables and push_subscription.topics

Revision ID: a7c3e9d1b2f4
Revises: f1a2b3c4d5e6
Create Date: 2026-09-23 00:00:00.000000

New tables only, plus one guarded ADD COLUMN on push_subscription -- production's
real schema is known to drift from migration history, so check before adding.
"""
from alembic import op
import sqlalchemy as sa


revision = 'a7c3e9d1b2f4'
down_revision = 'f1a2b3c4d5e6'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'whatsapp_conversation',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('tenant_id', sa.Integer(), nullable=False),
        sa.Column('wa_phone', sa.String(length=20), nullable=False),
        sa.Column('customer_id', sa.Integer(), nullable=True),
        sa.Column('contact_name', sa.String(length=120), nullable=True),
        sa.Column('needs_attention', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('attention_reason', sa.String(length=30), nullable=True),
        sa.Column('attention_since', sa.DateTime(), nullable=True),
        sa.Column('ai_paused', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('ai_paused_at', sa.DateTime(), nullable=True),
        sa.Column('last_admin_reply_at', sa.DateTime(), nullable=True),
        sa.Column('last_inbound_at', sa.DateTime(), nullable=True),
        sa.Column('last_message_at', sa.DateTime(), nullable=False),
        sa.Column('last_message_preview', sa.String(length=200), nullable=True),
        sa.Column('unread_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('last_push_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id'], name=op.f('fk_whatsapp_conversation_tenant_id_tenant')),
        sa.ForeignKeyConstraint(['customer_id'], ['customer.id'], name=op.f('fk_whatsapp_conversation_customer_id_customer')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_whatsapp_conversation')),
        sa.UniqueConstraint('tenant_id', 'wa_phone', name='uq_whatsapp_conversation_tenant_phone'),
    )
    with op.batch_alter_table('whatsapp_conversation', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_whatsapp_conversation_tenant_id'), ['tenant_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_whatsapp_conversation_needs_attention'), ['needs_attention'], unique=False)
        batch_op.create_index(batch_op.f('ix_whatsapp_conversation_last_message_at'), ['last_message_at'], unique=False)

    op.create_table(
        'whatsapp_message',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('tenant_id', sa.Integer(), nullable=False),
        sa.Column('conversation_id', sa.Integer(), nullable=False),
        sa.Column('direction', sa.String(length=3), nullable=False),
        sa.Column('sender', sa.String(length=10), nullable=False),
        sa.Column('sent_by_user_id', sa.Integer(), nullable=True),
        sa.Column('msg_type', sa.String(length=20), nullable=False),
        sa.Column('text', sa.Text(), nullable=True),
        sa.Column('transcript', sa.Text(), nullable=True),
        sa.Column('wa_message_id', sa.String(length=128), nullable=True),
        sa.Column('reply_to_wa_message_id', sa.String(length=128), nullable=True),
        sa.Column('reaction_emoji', sa.String(length=16), nullable=True),
        sa.Column('reaction_target_wa_id', sa.String(length=128), nullable=True),
        sa.Column('wa_media_id', sa.String(length=128), nullable=True),
        sa.Column('media_key', sa.String(length=300), nullable=True),
        sa.Column('media_playback_key', sa.String(length=300), nullable=True),
        sa.Column('media_mime', sa.String(length=80), nullable=True),
        sa.Column('media_status', sa.String(length=10), nullable=False, server_default='none'),
        sa.Column('status', sa.String(length=12), nullable=False, server_default='received'),
        sa.Column('error_code', sa.String(length=20), nullable=True),
        sa.Column('error_message', sa.String(length=300), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id'], name=op.f('fk_whatsapp_message_tenant_id_tenant')),
        sa.ForeignKeyConstraint(['conversation_id'], ['whatsapp_conversation.id'], name=op.f('fk_whatsapp_message_conversation_id_whatsapp_conversation')),
        sa.ForeignKeyConstraint(['sent_by_user_id'], ['user.id'], name=op.f('fk_whatsapp_message_sent_by_user_id_user')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_whatsapp_message')),
        sa.UniqueConstraint('tenant_id', 'wa_message_id', name='uq_whatsapp_message_tenant_wamid'),
    )
    with op.batch_alter_table('whatsapp_message', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_whatsapp_message_tenant_id'), ['tenant_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_whatsapp_message_conversation_id'), ['conversation_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_whatsapp_message_created_at'), ['created_at'], unique=False)

    cols = {c['name'] for c in sa.inspect(op.get_bind()).get_columns('push_subscription')}
    if 'topics' not in cols:
        with op.batch_alter_table('push_subscription', schema=None) as batch_op:
            batch_op.add_column(sa.Column('topics', sa.Text(), nullable=True))


def downgrade():
    cols = {c['name'] for c in sa.inspect(op.get_bind()).get_columns('push_subscription')}
    if 'topics' in cols:
        with op.batch_alter_table('push_subscription', schema=None) as batch_op:
            batch_op.drop_column('topics')
    with op.batch_alter_table('whatsapp_message', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_whatsapp_message_created_at'))
        batch_op.drop_index(batch_op.f('ix_whatsapp_message_conversation_id'))
        batch_op.drop_index(batch_op.f('ix_whatsapp_message_tenant_id'))
    op.drop_table('whatsapp_message')
    with op.batch_alter_table('whatsapp_conversation', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_whatsapp_conversation_last_message_at'))
        batch_op.drop_index(batch_op.f('ix_whatsapp_conversation_needs_attention'))
        batch_op.drop_index(batch_op.f('ix_whatsapp_conversation_tenant_id'))
    op.drop_table('whatsapp_conversation')
```

- [ ] **Step 5: Run the tests**

Run: `python -m pytest tests/test_inbox_migration.py -v`
Expected: 2 passed.

Run: `python -m pytest tests/ -q -x`
Expected: the whole suite still passes, since nothing else changed behaviour.

- [ ] **Step 6: Commit**

```bash
git add app.py migrations/versions/a7c3e9d1b2f4_add_whatsapp_inbox.py tests/test_inbox_migration.py
git commit -m "Add WhatsApp inbox models, migration, and push subscription topics"
```

---

### Task 4: Inbox core (parse, persist, dedupe, attention, window, statuses)

**Files:**
- Create: `whatsapp_inbox.py`
- Create: `tests/inbox_helpers.py`
- Test: `tests/test_whatsapp_inbox_core.py`

**Interfaces:**
- Consumes: models from Task 3.
- Produces, in `whatsapp_inbox`:
  - `ATTENTION_REASONS`, `FLAG_MEDIA_TYPES`, `REASON_LABELS`, `WINDOW`, `AI_AUTO_RESUME_AFTER`, `PUSH_THROTTLE`
  - `SYNC_BACKGROUND_TASKS: bool`, which tests set to True.
  - `run_background(flask_app, fn, pool=None) -> None`
  - `digits(phone) -> str`
  - `parse_inbound(msg: dict) -> dict`, with keys `msg_type`, `text`, `wa_message_id`, `reply_to_wa_message_id`, `wa_media_id`, `media_mime`, `reaction_emoji`, `reaction_target_wa_id`, `created_at`.
  - `preview_for(msg_type, text=None, reaction_emoji=None) -> str`
  - `is_duplicate(appmod, tenant_id, wa_message_id) -> bool`
  - `upsert_conversation(appmod, tenant_id, wa_phone, contact_name=None) -> WhatsAppConversation`
  - `find_conversation_by_phone(appmod, tenant_id, phone) -> WhatsAppConversation | None`, which matches on the last 8 digits.
  - `record_inbound(appmod, tenant_id, conv, parsed) -> WhatsAppMessage`
  - `record_outbound(appmod, conv, *, sender, msg_type, text=None, transcript=None, sent_by_user_id=None, wa_message_id=None, status='sent', error_code=None, error_message=None, media_key=None, media_playback_key=None, media_mime=None, reaction_emoji=None, reaction_target_wa_id=None, reply_to_wa_message_id=None) -> WhatsAppMessage`
  - `flag_attention(conv, reason) -> None`, `clear_attention(conv) -> None`
  - `inbound_attention_reason(*, ai_paused, ai_will_run, has_customer, msg_type) -> str | None`
  - `maybe_auto_resume(conv, now=None) -> bool`
  - `window_open(conv, now=None) -> bool`
  - `apply_status(appmod, tenant_id, status: dict) -> (WhatsAppMessage | None, WhatsAppConversation | None)`. The second item is the conversation, and only when it was just flagged `send_failed`.
  - None of these commit. The caller commits.

- [ ] **Step 1: Write the shared test helpers**

```python
# tests/inbox_helpers.py
"""Shared helpers for the WhatsApp inbox tests."""
import hashlib
import hmac
import json

import app as appmod
from tests.conftest import make_tenant


def signed_post(client, payload, app_secret):
    body = json.dumps(payload).encode("utf-8")
    sig = hmac.new(app_secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return client.post("/api/whatsapp/webhook", data=body, content_type="application/json",
                       headers={"X-Hub-Signature-256": f"sha256={sig}"})


def seed_customer(client, hdr, phone, name="C"):
    r = client.post("/api/subscription_plans", headers=hdr,
                    json={"name": "P", "price": 10, "billing_cycle": "monthly"})
    pid = r.get_json()["plan"]["id"]
    r = client.post("/api/customers", headers=hdr,
                    json={"name": name, "phone": phone, "address": "a",
                          "subscription_plan_id": pid, "subscription_start_date": "2026-01-01"})
    return r


def setup_wa_tenant(app, client, business, username, pnid, secret="s3cret", token="tok"):
    """Tenant with a configured WhatsApp Cloud API number. Returns (headers, tenant_id)."""
    hdr = make_tenant(client, business, username)
    with app.app_context():
        tid = appmod.User.query.filter_by(username=username).first().tenant_id
        appmod.db.session.add(appmod.WhatsAppSettings(
            tenant_id=tid, phone_number_id=pnid, enabled=True, mode="api",
            app_secret=secret, access_token=token, api_version="v19.0"))
        appmod.db.session.commit()
    return hdr, tid


def wa_payload(pnid, messages=None, statuses=None, name="Cust"):
    value = {"metadata": {"phone_number_id": pnid}, "contacts": [{"profile": {"name": name}}]}
    if messages is not None:
        value["messages"] = messages
    if statuses is not None:
        value["statuses"] = statuses
    return {"entry": [{"changes": [{"value": value}]}]}


class FakeResponse:
    def __init__(self, ok=True, status_code=200, body=None):
        self.ok = ok
        self.status_code = status_code
        self._body = body if body is not None else {"messages": [{"id": "wamid.OUT1"}]}
        self.text = json.dumps(self._body)
        self.content = self.text.encode()

    def json(self):
        return self._body
```

- [ ] **Step 2: Write the failing tests**

```python
# tests/test_whatsapp_inbox_core.py
from datetime import datetime, timedelta

import app as appmod
import whatsapp_inbox as wi
from tests.conftest import make_tenant


def _tid(username):
    return appmod.User.query.filter_by(username=username).first().tenant_id


def test_parse_inbound_every_type():
    ts = "1758600000"
    assert wi.parse_inbound({"id": "w1", "type": "text", "timestamp": ts, "text": {"body": "hi 👋"}})["text"] == "hi 👋"
    audio = wi.parse_inbound({"id": "w2", "type": "audio", "audio": {"id": "M1", "mime_type": "audio/ogg; codecs=opus"}})
    assert audio["msg_type"] == "audio" and audio["wa_media_id"] == "M1"
    voice = wi.parse_inbound({"id": "w2b", "type": "voice", "voice": {"id": "M1b"}})
    assert voice["msg_type"] == "audio" and voice["wa_media_id"] == "M1b"
    img = wi.parse_inbound({"id": "w3", "type": "image", "image": {"id": "M2", "caption": "receipt"}})
    assert img["msg_type"] == "image" and img["text"] == "receipt"
    st = wi.parse_inbound({"id": "w4", "type": "sticker", "sticker": {"id": "M3", "mime_type": "image/webp"}})
    assert st["msg_type"] == "sticker" and st["wa_media_id"] == "M3"
    rx = wi.parse_inbound({"id": "w5", "type": "reaction", "reaction": {"message_id": "wOUT", "emoji": "👍"}})
    assert rx["reaction_emoji"] == "👍" and rx["reaction_target_wa_id"] == "wOUT"
    loc = wi.parse_inbound({"id": "w6", "type": "location", "location": {"latitude": 33.9, "longitude": 35.5, "name": "Office"}})
    assert loc["text"] == "33.9,35.5 Office"
    inter = wi.parse_inbound({"id": "w7", "type": "interactive", "interactive": {"type": "button_reply", "button_reply": {"title": "Yes"}}})
    assert inter["text"] == "Yes"
    ctx = wi.parse_inbound({"id": "w8", "type": "text", "text": {"body": "x"}, "context": {"id": "wPREV"}})
    assert ctx["reply_to_wa_message_id"] == "wPREV"
    assert wi.parse_inbound({"id": "w9", "type": "ephemeral"})["msg_type"] == "unsupported"
    assert wi.parse_inbound({"id": "w1", "type": "text", "timestamp": ts, "text": {"body": "x"}})["created_at"] == datetime(2025, 9, 23, 4, 0)


def test_preview_for():
    assert wi.preview_for("text", "hello") == "hello"
    assert wi.preview_for("audio") == "🎤 Voice note"
    assert wi.preview_for("image", "receipt") == "📷 Photo: receipt"
    assert wi.preview_for("reaction", reaction_emoji="❤️") == "Reacted ❤️"
    assert wi.preview_for("reaction", reaction_emoji="") == "Removed reaction"


def test_inbound_attention_reason_priority():
    r = wi.inbound_attention_reason
    assert r(ai_paused=True, ai_will_run=False, has_customer=False, msg_type="image") == "awaiting_admin"
    assert r(ai_paused=False, ai_will_run=False, has_customer=True, msg_type="text") == "ai_inactive"
    assert r(ai_paused=False, ai_will_run=True, has_customer=False, msg_type="image") == "media_received"
    assert r(ai_paused=False, ai_will_run=True, has_customer=False, msg_type="text") == "unknown_sender"
    assert r(ai_paused=False, ai_will_run=True, has_customer=True, msg_type="sticker") is None
    assert r(ai_paused=False, ai_will_run=True, has_customer=True, msg_type="reaction") is None


def test_upsert_record_and_dedupe(app, client):
    make_tenant(client, "Biz Core", "core_admin")
    with app.app_context():
        tid = _tid("core_admin")
        conv = wi.upsert_conversation(appmod, tid, "+961 70 123 456", "Rami")
        parsed = wi.parse_inbound({"id": "wamid.A", "type": "text", "text": {"body": "hello"}})
        msg = wi.record_inbound(appmod, tid, conv, parsed)
        appmod.db.session.commit()
        assert conv.wa_phone == "96170123456" and conv.contact_name == "Rami"
        assert conv.unread_count == 1 and conv.last_message_preview == "hello"
        assert conv.last_inbound_at is not None
        assert msg.sender == "customer" and msg.status == "received" and msg.media_status == "none"
        assert wi.is_duplicate(appmod, tid, "wamid.A") is True
        assert wi.is_duplicate(appmod, tid, "wamid.B") is False
        again = wi.upsert_conversation(appmod, tid, "96170123456")
        assert again.id == conv.id
        assert wi.find_conversation_by_phone(appmod, tid, "70123456").id == conv.id


def test_media_message_starts_pending(app, client):
    make_tenant(client, "Biz Media", "media_admin")
    with app.app_context():
        tid = _tid("media_admin")
        conv = wi.upsert_conversation(appmod, tid, "96171000000")
        msg = wi.record_inbound(appmod, tid, conv, wi.parse_inbound(
            {"id": "wamid.M", "type": "sticker", "sticker": {"id": "MEDIA1"}}))
        assert msg.media_status == "pending" and msg.wa_media_id == "MEDIA1"


def test_flag_and_clear_attention(app, client):
    make_tenant(client, "Biz Flag", "flag_admin")
    with app.app_context():
        conv = wi.upsert_conversation(appmod, _tid("flag_admin"), "96171111111")
        wi.flag_attention(conv, "ai_failed")
        first_since = conv.attention_since
        wi.flag_attention(conv, "escalated")
        assert conv.needs_attention and conv.attention_reason == "escalated"
        assert conv.attention_since == first_since
        wi.clear_attention(conv)
        assert not conv.needs_attention and conv.attention_reason is None and conv.attention_since is None


def test_window_and_auto_resume(app, client):
    make_tenant(client, "Biz Win", "win_admin")
    with app.app_context():
        conv = wi.upsert_conversation(appmod, _tid("win_admin"), "96172222222")
        now = datetime(2026, 9, 23, 12, 0)
        conv.last_inbound_at = now - timedelta(hours=23)
        assert wi.window_open(conv, now) is True
        conv.last_inbound_at = now - timedelta(hours=25)
        assert wi.window_open(conv, now) is False
        conv.ai_paused = True
        conv.last_admin_reply_at = now - timedelta(hours=2)
        assert wi.maybe_auto_resume(conv, now) is False and conv.ai_paused
        conv.last_admin_reply_at = now - timedelta(hours=25)
        assert wi.maybe_auto_resume(conv, now) is True and not conv.ai_paused


def test_apply_status_advances_and_never_regresses(app, client):
    make_tenant(client, "Biz St", "st_admin")
    with app.app_context():
        tid = _tid("st_admin")
        conv = wi.upsert_conversation(appmod, tid, "96173333333")
        out = wi.record_outbound(appmod, conv, sender="admin", msg_type="text", text="hi", wa_message_id="wamid.O")
        appmod.db.session.commit()
        wi.apply_status(appmod, tid, {"id": "wamid.O", "status": "read"})
        assert out.status == "read"
        wi.apply_status(appmod, tid, {"id": "wamid.O", "status": "delivered"})
        assert out.status == "read"
        m, flagged = wi.apply_status(appmod, tid, {"id": "wamid.O", "status": "failed",
                                                  "errors": [{"code": 131047, "title": "Re-engagement message"}]})
        assert out.status == "failed" and out.error_code == "131047"
        assert flagged is conv and conv.attention_reason == "send_failed"
        assert wi.apply_status(appmod, tid, {"id": "wamid.UNKNOWN", "status": "read"}) == (None, None)


def test_run_background_sync_mode(monkeypatch):
    monkeypatch.setattr(wi, "SYNC_BACKGROUND_TASKS", True)
    calls = []
    wi.run_background(None, lambda: calls.append(1))
    assert calls == [1]
```

Note on the timestamp assertion: `1758600000` is `2025-09-23 04:00:00 UTC`. If it fails, compute the right value with `datetime.fromtimestamp(1758600000, tz=timezone.utc)` and fix the expected value; it must not be local time.

- [ ] **Step 3: Run the tests to verify they fail**

Run: `python -m pytest tests/test_whatsapp_inbox_core.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'whatsapp_inbox'`

- [ ] **Step 4: Implement `whatsapp_inbox.py` (core part)**

```python
"""WhatsApp inbox: persistence, "needs attention" flags, window, statuses,
media, push notifications, AI hooks and admin sends.

Every function that touches the DB takes the app module (`appmod`) first,
exactly like cs_agent_tools.py, so this module never imports app.py at import
time. Functions here do NOT commit unless their docstring says so -- the caller
owns the transaction. See docs/superpowers/specs/2026-09-23-whatsapp-inbox-design.md.
"""
import logging
import re
from datetime import datetime, timedelta, timezone

from sqlalchemy.exc import IntegrityError

WINDOW = timedelta(hours=24)
AI_AUTO_RESUME_AFTER = timedelta(hours=24)
PUSH_THROTTLE = timedelta(minutes=2)

ATTENTION_REASONS = ('ai_failed', 'escalated', 'awaiting_admin', 'unknown_sender',
                     'ai_inactive', 'media_received', 'send_failed')
REASON_LABELS = {
    'ai_failed': "AI couldn't answer",
    'escalated': 'Escalated to a human',
    'awaiting_admin': 'New message (AI paused)',
    'unknown_sender': 'Unknown sender',
    'ai_inactive': 'AI is off',
    'media_received': 'Media received',
    'send_failed': 'Message failed to send',
}
MEDIA_TYPES = ('audio', 'image', 'video', 'document', 'sticker')
FLAG_MEDIA_TYPES = ('image', 'video', 'document', 'location', 'contacts')
PREVIEW_LABELS = {
    'audio': '🎤 Voice note', 'image': '📷 Photo', 'video': '🎬 Video',
    'document': '📄 Document', 'sticker': 'Sticker', 'location': '📍 Location',
    'contacts': '👤 Contact', 'template': 'Template', 'unsupported': 'Unsupported message',
}
STATUS_RANK = {'queued': 0, 'sent': 1, 'delivered': 2, 'read': 3}

# Tests flip this to True so webhook background work (media download, AI
# reply) runs inline and deterministically instead of in a greenlet.
SYNC_BACKGROUND_TASKS = False

_media_pool = None


def media_pool():
    """Small dedicated pool for media downloads -- never the AI-reply pool."""
    global _media_pool
    if _media_pool is None:
        try:
            from gevent.pool import Pool
            _media_pool = Pool(5)
        except ImportError:
            _media_pool = None
    return _media_pool


def run_background(flask_app, fn, pool=None):
    """Run fn() off the request: inline when SYNC_BACKGROUND_TASKS, else in a
    greenlet (through `pool` when given) inside a fresh app context. Never raises."""
    if SYNC_BACKGROUND_TASKS:
        try:
            fn()
        except Exception:
            logging.exception("whatsapp_inbox background task failed")
        return

    def _wrapped():
        with flask_app.app_context():
            try:
                fn()
            except Exception:
                logging.exception("whatsapp_inbox background task failed")

    try:
        import gevent
    except ImportError:
        gevent = None
    if pool is not None:
        pool.spawn(_wrapped)
    elif gevent is not None:
        gevent.spawn(_wrapped)
    else:
        _wrapped()


def digits(phone):
    return re.sub(r'\D', '', str(phone or ''))


def _ts(value):
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc).replace(tzinfo=None)
    except (TypeError, ValueError):
        return datetime.utcnow()


def parse_inbound(msg):
    raw_type = msg.get('type') or 'unsupported'
    msg_type = 'audio' if raw_type == 'voice' else raw_type
    out = {
        'msg_type': msg_type, 'text': None,
        'wa_message_id': msg.get('id'),
        'reply_to_wa_message_id': (msg.get('context') or {}).get('id'),
        'wa_media_id': None, 'media_mime': None,
        'reaction_emoji': None, 'reaction_target_wa_id': None,
        'created_at': _ts(msg.get('timestamp')),
    }
    if msg_type == 'text':
        out['text'] = (msg.get('text') or {}).get('body')
    elif msg_type == 'button':
        out['text'] = (msg.get('button') or {}).get('text')
    elif msg_type == 'interactive':
        inter = msg.get('interactive') or {}
        out['text'] = (inter.get(inter.get('type') or '') or {}).get('title')
    elif msg_type in MEDIA_TYPES:
        media = msg.get(raw_type) or {}
        out['wa_media_id'] = media.get('id')
        out['media_mime'] = media.get('mime_type')
        out['text'] = media.get('caption') or (media.get('filename') if msg_type == 'document' else None)
    elif msg_type == 'reaction':
        reaction = msg.get('reaction') or {}
        out['reaction_emoji'] = reaction.get('emoji') or ''
        out['reaction_target_wa_id'] = reaction.get('message_id')
    elif msg_type == 'location':
        loc = msg.get('location') or {}
        out['text'] = f"{loc.get('latitude')},{loc.get('longitude')}" + (f" {loc['name']}" if loc.get('name') else '')
    elif msg_type == 'contacts':
        names = [((c.get('name') or {}).get('formatted_name') or '') for c in (msg.get('contacts') or [])]
        out['text'] = ', '.join(n for n in names if n) or None
    else:
        out['msg_type'] = 'unsupported'
    return out


def preview_for(msg_type, text=None, reaction_emoji=None):
    if msg_type == 'reaction':
        return f"Reacted {reaction_emoji}" if reaction_emoji else "Removed reaction"
    label = PREVIEW_LABELS.get(msg_type)
    if text and label:
        return f"{label}: {text}"[:200]
    return (text or label or '')[:200]


def is_duplicate(appmod, tenant_id, wa_message_id):
    if not wa_message_id:
        return False
    return appmod.WhatsAppMessage.query.filter_by(
        tenant_id=tenant_id, wa_message_id=wa_message_id).first() is not None


def _match_customer(appmod, tenant_id, wa_phone):
    if len(wa_phone) < 8:
        return None
    return appmod.Customer.query.filter_by(tenant_id=tenant_id).filter(
        appmod.Customer.phone.like(f"%{wa_phone[-8:]}%")).first()


def upsert_conversation(appmod, tenant_id, wa_phone, contact_name=None):
    wa_phone = digits(wa_phone)
    Conv = appmod.WhatsAppConversation
    conv = Conv.query.filter_by(tenant_id=tenant_id, wa_phone=wa_phone).first()
    if conv is None:
        try:
            with appmod.db.session.begin_nested():
                conv = Conv(tenant_id=tenant_id, wa_phone=wa_phone, last_message_at=datetime.utcnow(),
                            needs_attention=False, ai_paused=False, unread_count=0)
                appmod.db.session.add(conv)
        except IntegrityError:
            # A concurrent webhook delivery created it first.
            conv = Conv.query.filter_by(tenant_id=tenant_id, wa_phone=wa_phone).first()
    if contact_name:
        conv.contact_name = contact_name[:120]
    if conv.customer_id is None:
        cust = _match_customer(appmod, tenant_id, wa_phone)
        if cust:
            conv.customer_id = cust.id
    appmod.db.session.flush()
    return conv


def find_conversation_by_phone(appmod, tenant_id, phone):
    d = digits(phone)
    if not d:
        return None
    Conv = appmod.WhatsAppConversation
    exact = Conv.query.filter_by(tenant_id=tenant_id, wa_phone=d).first()
    if exact or len(d) < 8:
        return exact
    return Conv.query.filter_by(tenant_id=tenant_id).filter(
        Conv.wa_phone.like(f"%{d[-8:]}")).order_by(Conv.last_message_at.desc()).first()


def record_inbound(appmod, tenant_id, conv, parsed):
    msg = appmod.WhatsAppMessage(
        tenant_id=tenant_id, conversation_id=conv.id, direction='in', sender='customer',
        msg_type=parsed['msg_type'], text=parsed.get('text'),
        wa_message_id=parsed.get('wa_message_id'),
        reply_to_wa_message_id=parsed.get('reply_to_wa_message_id'),
        reaction_emoji=parsed.get('reaction_emoji'),
        reaction_target_wa_id=parsed.get('reaction_target_wa_id'),
        wa_media_id=parsed.get('wa_media_id'),
        media_mime=(parsed.get('media_mime') or '').split(';')[0].strip() or None,
        media_status='pending' if parsed.get('wa_media_id') else 'none',
        status='received', created_at=parsed.get('created_at') or datetime.utcnow())
    appmod.db.session.add(msg)
    conv.last_inbound_at = msg.created_at
    conv.last_message_at = msg.created_at
    conv.last_message_preview = preview_for(msg.msg_type, msg.text, msg.reaction_emoji)
    conv.unread_count = (conv.unread_count or 0) + 1
    appmod.db.session.flush()
    return msg


def record_outbound(appmod, conv, *, sender, msg_type, text=None, transcript=None, sent_by_user_id=None,
                    wa_message_id=None, status='sent', error_code=None, error_message=None,
                    media_key=None, media_playback_key=None, media_mime=None,
                    reaction_emoji=None, reaction_target_wa_id=None, reply_to_wa_message_id=None):
    now = datetime.utcnow()
    msg = appmod.WhatsAppMessage(
        tenant_id=conv.tenant_id, conversation_id=conv.id, direction='out', sender=sender,
        sent_by_user_id=sent_by_user_id, msg_type=msg_type, text=text, transcript=transcript,
        wa_message_id=wa_message_id, reply_to_wa_message_id=reply_to_wa_message_id,
        reaction_emoji=reaction_emoji, reaction_target_wa_id=reaction_target_wa_id,
        media_key=media_key, media_playback_key=media_playback_key, media_mime=media_mime,
        media_status='stored' if media_key else 'none', status=status,
        error_code=(str(error_code)[:20] if error_code else None),
        error_message=(str(error_message)[:300] if error_message else None), created_at=now)
    appmod.db.session.add(msg)
    conv.last_message_at = now
    conv.last_message_preview = preview_for(msg_type, text, reaction_emoji)
    appmod.db.session.flush()
    return msg


def flag_attention(conv, reason):
    if reason not in ATTENTION_REASONS:
        raise ValueError(f"unknown attention reason: {reason}")
    if not conv.needs_attention or conv.attention_since is None:
        conv.attention_since = datetime.utcnow()
    conv.needs_attention = True
    conv.attention_reason = reason


def clear_attention(conv):
    conv.needs_attention = False
    conv.attention_reason = None
    conv.attention_since = None


def inbound_attention_reason(*, ai_paused, ai_will_run, has_customer, msg_type):
    if ai_paused:
        return 'awaiting_admin'
    if not ai_will_run:
        return 'ai_inactive'
    if msg_type in FLAG_MEDIA_TYPES:
        return 'media_received'
    if not has_customer:
        return 'unknown_sender'
    return None


def maybe_auto_resume(conv, now=None):
    now = now or datetime.utcnow()
    last = conv.last_admin_reply_at or conv.ai_paused_at
    if conv.ai_paused and last and now - last > AI_AUTO_RESUME_AFTER:
        conv.ai_paused = False
        conv.ai_paused_at = None
        return True
    return False


def window_open(conv, now=None):
    now = now or datetime.utcnow()
    return bool(conv.last_inbound_at and now < conv.last_inbound_at + WINDOW)


def apply_status(appmod, tenant_id, status):
    wamid = status.get('id')
    new = status.get('status')
    if not wamid:
        return None, None
    msg = appmod.WhatsAppMessage.query.filter_by(
        tenant_id=tenant_id, wa_message_id=wamid, direction='out').first()
    if msg is None:
        return None, None
    if new == 'failed':
        err = (status.get('errors') or [{}])[0] or {}
        msg.status = 'failed'
        msg.error_code = str(err.get('code') or '')[:20] or None
        msg.error_message = (err.get('title') or err.get('message') or '')[:300] or None
        conv = appmod.db.session.get(appmod.WhatsAppConversation, msg.conversation_id)
        flag_attention(conv, 'send_failed')
        return msg, conv
    if new in STATUS_RANK and msg.status != 'failed' and STATUS_RANK[new] > STATUS_RANK.get(msg.status, -1):
        msg.status = new
    return msg, None
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m pytest tests/test_whatsapp_inbox_core.py -v`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add whatsapp_inbox.py tests/inbox_helpers.py tests/test_whatsapp_inbox_core.py
git commit -m "Add WhatsApp inbox core: parse, persist, dedupe, attention, window, statuses"
```

---

### Task 5: Web push targeting + inbox notify

**Files:**
- Modify: `app.py`: `send_push_notification` (line 7237), `/api/push-subscribe` (line 7263), the ticket caller (line 7320); new routes `/api/push-unsubscribe`, `/api/push-subscription/topics`, `/api/push-test`
- Modify: `whatsapp_inbox.py`: add `notify_conversation`
- Test: `tests/test_push_targeting.py`

**Interfaces:**
- Produces:
  - `appmod.send_push_notification(payload_dict, tenant_id=None, roles=None, topic=None) -> int`, which returns the number sent. It must stay callable as `send_push_notification(payload)` inside a request.
  - `appmod.VAPID_CLAIM_EMAIL`
  - `whatsapp_inbox.notify_conversation(appmod, conv, body=None, now=None) -> bool`. It commits `last_push_at` and returns False when throttled. The payload keys are `title`, `body`, `tag`, `url`, `conversation_id`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_push_targeting.py
import json
from datetime import datetime, timedelta

import app as appmod
import whatsapp_inbox as wi
from tests.conftest import make_tenant, auth_headers


def _sub(tid, uid, endpoint, topics=None):
    s = appmod.PushSubscription(tenant_id=tid, user_id=uid,
                                subscription_info=json.dumps({"endpoint": endpoint, "keys": {}}),
                                topics=json.dumps(topics) if topics is not None else None)
    appmod.db.session.add(s)
    return s


def _capture(monkeypatch):
    sent = []
    monkeypatch.setattr(appmod, "VAPID_PRIVATE_KEY", "test-key")
    monkeypatch.setattr(appmod, "webpush", lambda **kw: sent.append(kw["subscription_info"]["endpoint"]))
    return sent


def test_targets_admins_of_tenant_with_topic(app, client, monkeypatch):
    make_tenant(client, "Biz Push", "push_admin")
    auth_headers(client, "push_collector", role="collector")
    make_tenant(client, "Biz Other", "other_admin")
    sent = _capture(monkeypatch)
    with app.app_context():
        admin = appmod.User.query.filter_by(username="push_admin").first()
        coll = appmod.User.query.filter_by(username="push_collector").first()
        other = appmod.User.query.filter_by(username="other_admin").first()
        # auth_headers(role=...) attaches to the most recent tenant; move the
        # collector into push_admin's tenant explicitly.
        coll.tenant_id = admin.tenant_id
        _sub(admin.tenant_id, admin.id, "https://e/admin")
        _sub(admin.tenant_id, admin.id, "https://e/admin-tickets-only", ["tickets"])
        _sub(admin.tenant_id, coll.id, "https://e/collector")
        _sub(other.tenant_id, other.id, "https://e/other-tenant")
        appmod.db.session.commit()
        n = appmod.send_push_notification({"title": "t", "body": "b"}, tenant_id=admin.tenant_id,
                                          roles=["admin"], topic="whatsapp_inbox")
    assert n == 1 and sent == ["https://e/admin"]


def test_legacy_call_inside_request_still_pushes_everyone(app, client, monkeypatch):
    hdr = make_tenant(client, "Biz Legacy", "legacy_admin")
    sent = _capture(monkeypatch)
    with app.app_context():
        u = appmod.User.query.filter_by(username="legacy_admin").first()
        _sub(u.tenant_id, u.id, "https://e/legacy")
        appmod.db.session.commit()
    r = client.post("/api/subscription_plans", headers=hdr, json={"name": "P", "price": 1, "billing_cycle": "monthly"})
    pid = r.get_json()["plan"]["id"]
    r = client.post("/api/customers", headers=hdr, json={"name": "C", "phone": "70111222", "address": "a",
                                                          "subscription_plan_id": pid, "subscription_start_date": "2026-01-01"})
    cid = r.get_json()["customer"]["id"] if "customer" in r.get_json() else r.get_json()["id"]
    client.post("/api/support-tickets", headers=hdr, json={"customer_id": cid, "title": "x", "description": "y", "priority": "low"})
    assert sent == ["https://e/legacy"]


def test_notify_conversation_throttles(app, client, monkeypatch):
    make_tenant(client, "Biz Thr", "thr_admin")
    calls = []
    monkeypatch.setattr(appmod, "send_push_notification", lambda payload, **kw: calls.append((payload, kw)) or 1)
    with app.app_context():
        tid = appmod.User.query.filter_by(username="thr_admin").first().tenant_id
        conv = wi.upsert_conversation(appmod, tid, "96170999888", "Nada")
        wi.flag_attention(conv, "ai_failed")
        conv.last_message_preview = "where is my internet"
        appmod.db.session.commit()
        now = datetime(2026, 9, 23, 12, 0)
        assert wi.notify_conversation(appmod, conv, now=now) is True
        assert wi.notify_conversation(appmod, conv, now=now + timedelta(seconds=60)) is False
        assert wi.notify_conversation(appmod, conv, now=now + timedelta(minutes=3)) is True
    payload, kw = calls[0]
    assert payload["title"] == "Nada"
    assert payload["body"] == "AI couldn't answer: where is my internet"
    assert payload["tag"] == f"wa-conv-{conv.id}" and payload["url"] == f"/?view=messaging&inbox={conv.id}"
    assert kw == {"tenant_id": tid, "roles": ["admin"], "topic": "whatsapp_inbox"}


def test_topics_routes_and_unsubscribe(app, client, monkeypatch):
    hdr = make_tenant(client, "Biz Top", "top_admin")
    sub = {"endpoint": "https://e/dev1", "keys": {"p256dh": "x", "auth": "y"}}
    assert client.post("/api/push-subscribe", headers=hdr, json={"subscription": sub}).status_code == 200
    # Re-subscribing the same endpoint must not duplicate the row.
    client.post("/api/push-subscribe", headers=hdr, json={"subscription": sub})
    with app.app_context():
        assert appmod.PushSubscription.query.count() == 1
    r = client.get("/api/push-subscription/topics", headers=hdr, query_string={"endpoint": "https://e/dev1"})
    assert r.get_json() == {"subscribed": True, "topics": ["tickets", "whatsapp_inbox"]}
    r = client.put("/api/push-subscription/topics", headers=hdr, json={"endpoint": "https://e/dev1", "topics": ["tickets"]})
    assert r.status_code == 200
    r = client.get("/api/push-subscription/topics", headers=hdr, query_string={"endpoint": "https://e/dev1"})
    assert r.get_json()["topics"] == ["tickets"]
    r = client.put("/api/push-subscription/topics", headers=hdr, json={"endpoint": "https://e/dev1", "topics": ["bogus"]})
    assert r.status_code == 400
    sent = _capture(monkeypatch)
    assert client.post("/api/push-test", headers=hdr, json={"endpoint": "https://e/dev1"}).status_code == 200
    assert sent == ["https://e/dev1"]
    assert client.post("/api/push-unsubscribe", headers=hdr, json={"endpoint": "https://e/dev1"}).status_code == 200
    r = client.get("/api/push-subscription/topics", headers=hdr, query_string={"endpoint": "https://e/dev1"})
    assert r.get_json() == {"subscribed": False, "topics": []}
```

In the legacy test, check the customer-create response shape (`grep -n "def add_customer" -A40 app.py`) and use the key it actually returns for the new id. Fix that single line and don't restructure the test.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_push_targeting.py -v`
Expected: FAIL (`send_push_notification() got an unexpected keyword argument 'tenant_id'`, 404 on the new routes).

- [ ] **Step 3: Implement the push refactor in `app.py`**

Next to the VAPID key loading (after line 76) add:

```python
VAPID_CLAIM_EMAIL = os.environ.get('VAPID_CLAIM_EMAIL', 'admin@example.com')
```

Replace `send_push_notification` and `push_subscribe` (lines 7237-7281) with:

```python
def _webpush_one(sub, payload_dict):
    """Send one push; prune the subscription when the push service says it's gone."""
    try:
        webpush(
            subscription_info=json.loads(sub.subscription_info),
            data=json.dumps(payload_dict),
            vapid_private_key=VAPID_PRIVATE_KEY,
            vapid_claims={"sub": f"mailto:{VAPID_CLAIM_EMAIL}"}
        )
        return True
    except Exception as e:
        print(f"Failed to send push to user {sub.user_id}:", e)
        if "410" in str(e) or "404" in str(e):
            db.session.delete(sub)
            db.session.commit()
        return False


def _user_has_any_role(user, roles):
    wanted = {r.lower() for r in roles}
    return bool(wanted & {r.strip().lower() for r in (user.role or '').split(',')})


def send_push_notification(payload_dict, tenant_id=None, roles=None, topic=None):
    """Push to a tenant's subscriptions. tenant_id defaults to the request's
    JWT tenant (so the webhook/scheduler, which have no JWT, pass it
    explicitly); roles limits to users holding any of them; topic limits to
    subscriptions that opted into it. Returns how many pushes went out."""
    if not VAPID_PRIVATE_KEY:
        print("Push notification failed: VAPID keys not configured.")
        return 0
    tid = tenant_id if tenant_id is not None else current_tenant_id()
    q = PushSubscription.query.filter_by(tenant_id=tid)
    if roles:
        user_ids = [u.id for u in User.query.filter_by(tenant_id=tid).all() if _user_has_any_role(u, roles)]
        if not user_ids:
            return 0
        q = q.filter(PushSubscription.user_id.in_(user_ids))
    sent = 0
    for sub in q.all():
        if topic and topic not in sub.topic_list():
            continue
        if _webpush_one(sub, payload_dict):
            sent += 1
    return sent


def _current_user():
    return User.query.filter_by(username=get_jwt_identity()).first()


def _my_subscription(user, endpoint):
    if not endpoint:
        return None
    for sub in tenant_query(PushSubscription).filter_by(user_id=user.id).all():
        if sub.endpoint() == endpoint:
            return sub
    return None


@app.route('/api/vapid-public-key', methods=['GET'])
def get_vapid_public_key():
    return jsonify({"public_key": VAPID_PUBLIC_KEY})


@app.route('/api/push-subscribe', methods=['POST'])
@jwt_required()
def push_subscribe():
    data = request.json or {}
    user = _current_user()
    if not user:
        return jsonify({"msg": "User not found"}), 404
    subscription = data.get('subscription') or {}
    existing = _my_subscription(user, subscription.get('endpoint'))
    if existing:
        existing.subscription_info = json.dumps(subscription)  # keys may rotate
    else:
        db.session.add(PushSubscription(user_id=user.id, subscription_info=json.dumps(subscription)))
    db.session.commit()
    return jsonify({"msg": "Subscribed successfully"}), 200


@app.route('/api/push-unsubscribe', methods=['POST'])
@jwt_required()
def push_unsubscribe():
    user = _current_user()
    sub = _my_subscription(user, (request.json or {}).get('endpoint')) if user else None
    if sub:
        db.session.delete(sub)
        db.session.commit()
    return jsonify({"msg": "Unsubscribed"}), 200


@app.route('/api/push-subscription/topics', methods=['GET', 'PUT'])
@jwt_required()
def push_subscription_topics():
    user = _current_user()
    if not user:
        return jsonify({"msg": "User not found"}), 404
    if request.method == 'GET':
        sub = _my_subscription(user, request.args.get('endpoint'))
        return jsonify({"subscribed": bool(sub), "topics": sub.topic_list() if sub else []})
    data = request.json or {}
    topics = data.get('topics')
    if not isinstance(topics, list) or any(t not in DEFAULT_PUSH_TOPICS for t in topics):
        return jsonify({"msg": f"topics must be a list drawn from {list(DEFAULT_PUSH_TOPICS)}"}), 400
    sub = _my_subscription(user, data.get('endpoint'))
    if not sub:
        return jsonify({"msg": "This device is not subscribed"}), 404
    sub.topics = json.dumps(topics)
    db.session.commit()
    return jsonify({"subscribed": True, "topics": sub.topic_list()})


@app.route('/api/push-test', methods=['POST'])
@jwt_required()
def push_test():
    user = _current_user()
    sub = _my_subscription(user, (request.json or {}).get('endpoint')) if user else None
    if not sub:
        return jsonify({"msg": "This device is not subscribed"}), 404
    if not VAPID_PRIVATE_KEY:
        return jsonify({"msg": "Push is not configured on the server"}), 503
    ok = _webpush_one(sub, {"title": "Test notification", "body": "Notifications are working on this device.",
                            "tag": "push-test", "url": "/?view=messaging"})
    return (jsonify({"msg": "Sent"}), 200) if ok else (jsonify({"msg": "Push service rejected the message"}), 502)
```

In `create_support_ticket` (around line 7320), change `send_push_notification(payload)` to:

```python
        send_push_notification(payload, topic='tickets')
```

- [ ] **Step 4: Add `notify_conversation` to `whatsapp_inbox.py`**

```python
def notify_conversation(appmod, conv, body=None, now=None):
    """Web-push this conversation to the tenant's admins, at most once per
    PUSH_THROTTLE per conversation. Commits last_push_at. Never raises."""
    now = now or datetime.utcnow()
    if conv.last_push_at and now - conv.last_push_at < PUSH_THROTTLE:
        return False
    conv.last_push_at = now
    appmod.db.session.commit()
    title = (conv.customer.name if conv.customer_id and conv.customer else None) or conv.contact_name or f"+{conv.wa_phone}"
    if body is None:
        label = REASON_LABELS.get(conv.attention_reason, 'New WhatsApp message')
        preview = conv.last_message_preview or ''
        body = f"{label}: {preview}" if preview else label
    payload = {'title': title, 'body': body[:180], 'tag': f"wa-conv-{conv.id}",
               'url': f"/?view=messaging&inbox={conv.id}", 'conversation_id': conv.id}
    try:
        appmod.send_push_notification(payload, tenant_id=conv.tenant_id, roles=['admin'], topic='whatsapp_inbox')
    except Exception:
        logging.exception("whatsapp_inbox push failed")
    return True
```

- [ ] **Step 5: Run the tests**

Run: `python -m pytest tests/test_push_targeting.py -v`, then `python -m pytest tests/ -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add app.py whatsapp_inbox.py tests/test_push_targeting.py
git commit -m "Target web push by tenant/role/topic; add per-device topic, unsubscribe and test routes"
```

---

### Task 6: Webhook persists messages, flags attention, gates the AI

**Files:**
- Modify: `app.py`, in `whatsapp_webhook` (lines 7876-8080). Add `import whatsapp_inbox` and `import functools` next to `import cs_agent_tools` (line 48).
- Modify: `whatsapp_inbox.py`: add `store_inbound_media`
- Test: `tests/test_whatsapp_inbox_webhook.py`

**Interfaces:**
- Consumes: everything from Task 4, plus `notify_conversation` from Task 5.
- Produces:
  - `whatsapp_inbox.store_inbound_media(appmod, message_id, access_token, api_version) -> None`, which commits.
  - `whatsapp_inbox.after_ai_reply(appmod, tenant_id, wa_phone, result) -> None`, which commits. Task 6 adds a stub; Task 7 completes it.
  - The webhook AI closure calls `whatsapp_inbox.after_ai_reply` after `cs_agent_tools.handle_whatsapp_cs_ai_reply`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_whatsapp_inbox_webhook.py
import pytest

import app as appmod
import cs_agent_tools
import storage
import whatsapp_inbox as wi
from tests.inbox_helpers import signed_post, seed_customer, setup_wa_tenant, wa_payload, FakeResponse


@pytest.fixture
def env(app, client, monkeypatch, tmp_path):
    monkeypatch.setattr(wi, "SYNC_BACKGROUND_TASKS", True)
    monkeypatch.setattr(storage, "UPLOAD_ROOT", str(tmp_path))
    monkeypatch.setattr(storage, "_backend", storage.LocalBackend())
    pushes, ai_calls = [], []
    monkeypatch.setattr(appmod, "send_push_notification", lambda payload, **kw: pushes.append(payload) or 1)
    monkeypatch.setattr(cs_agent_tools, "handle_whatsapp_cs_ai_reply",
                        lambda **kw: ai_calls.append(kw) or {"reply_text": "ok", "ai_source": "gemini", "gemini_configured": True})
    monkeypatch.setattr(cs_agent_tools, "handle_whatsapp_audio_transcription", lambda *a, **kw: "وين الانترنت")
    monkeypatch.setattr(cs_agent_tools, "download_meta_media", lambda *a, **kw: (b"\x89PNGfake", "image/png"))
    monkeypatch.setattr(appmod.requests, "post", lambda *a, **kw: FakeResponse())
    hdr, tid = setup_wa_tenant(app, client, "Biz WH", "wh_admin", "PNID_WH")
    return {"hdr": hdr, "tid": tid, "pushes": pushes, "ai_calls": ai_calls}


def _send(client, *messages, statuses=None):
    return signed_post(client, wa_payload("PNID_WH", messages=list(messages), statuses=statuses), "s3cret")


def _conv(app, tid):
    return appmod.WhatsAppConversation.query.filter_by(tenant_id=tid).first()


def test_text_from_known_customer_is_stored_and_ai_runs(app, client, env):
    seed_customer(client, env["hdr"], "70123456", name="Rami")
    r = _send(client, {"from": "96170123456", "id": "wamid.T1", "type": "text", "text": {"body": "hello"}})
    assert r.status_code == 200
    with app.app_context():
        conv = _conv(app, env["tid"])
        assert conv.customer_id is not None and conv.contact_name == "Cust"
        msgs = appmod.WhatsAppMessage.query.filter_by(conversation_id=conv.id).all()
        assert [(m.direction, m.msg_type, m.text) for m in msgs] == [("in", "text", "hello")]
        assert conv.needs_attention is False
    assert len(env["ai_calls"]) == 1 and env["pushes"] == []


def test_duplicate_delivery_is_ignored_entirely(app, client, env):
    seed_customer(client, env["hdr"], "70123456")
    m = {"from": "96170123456", "id": "wamid.DUP", "type": "text", "text": {"body": "hello"}}
    _send(client, m)
    _send(client, m)
    with app.app_context():
        assert appmod.WhatsAppMessage.query.filter_by(wa_message_id="wamid.DUP").count() == 1
    assert len(env["ai_calls"]) == 1


def test_unknown_sender_flagged_and_pushed(app, client, env):
    _send(client, {"from": "96179000000", "id": "wamid.U1", "type": "text", "text": {"body": "hi"}})
    with app.app_context():
        conv = _conv(app, env["tid"])
        assert conv.needs_attention and conv.attention_reason == "unknown_sender"
    assert len(env["pushes"]) == 1 and env["pushes"][0]["body"].startswith("Unknown sender")


def test_image_flags_media_and_is_downloaded(app, client, env):
    seed_customer(client, env["hdr"], "70123456")
    _send(client, {"from": "96170123456", "id": "wamid.I1", "type": "image", "image": {"id": "MEDIA_I", "caption": "receipt"}})
    with app.app_context():
        conv = _conv(app, env["tid"])
        assert conv.attention_reason == "media_received"
        msg = appmod.WhatsAppMessage.query.filter_by(wa_message_id="wamid.I1").first()
        assert msg.media_status == "stored" and storage.read_bytes(msg.media_key) == b"\x89PNGfake"


def test_sticker_is_not_flagged(app, client, env):
    seed_customer(client, env["hdr"], "70123456")
    _send(client, {"from": "96170123456", "id": "wamid.S1", "type": "sticker", "sticker": {"id": "MEDIA_S"}})
    with app.app_context():
        assert _conv(app, env["tid"]).needs_attention is False


def test_voice_note_transcript_saved(app, client, env):
    seed_customer(client, env["hdr"], "70123456")
    _send(client, {"from": "96170123456", "id": "wamid.V1", "type": "audio", "audio": {"id": "MEDIA_V"}})
    with app.app_context():
        msg = appmod.WhatsAppMessage.query.filter_by(wa_message_id="wamid.V1").first()
        assert msg.msg_type == "audio" and msg.transcript == "وين الانترنت"
    assert env["ai_calls"][0]["is_voice"] is True


def test_paused_conversation_skips_ai_and_flags_awaiting_admin(app, client, env):
    seed_customer(client, env["hdr"], "70123456")
    with app.app_context():
        conv = wi.upsert_conversation(appmod, env["tid"], "96170123456")
        conv.ai_paused = True
        from datetime import datetime
        conv.last_admin_reply_at = datetime.utcnow()
        appmod.db.session.commit()
    _send(client, {"from": "96170123456", "id": "wamid.P1", "type": "text", "text": {"body": "still down"}})
    with app.app_context():
        assert _conv(app, env["tid"]).attention_reason == "awaiting_admin"
    assert env["ai_calls"] == [] and len(env["pushes"]) == 1


def test_ai_inactive_flags_and_keeps_ticket(app, client, env):
    seed_customer(client, env["hdr"], "70123456")
    with app.app_context():
        appmod.db.session.add(appmod.CSAgentSettings(tenant_id=env["tid"], is_active=False))
        appmod.db.session.commit()
    _send(client, {"from": "96170123456", "id": "wamid.X1", "type": "text", "text": {"body": "hi"}})
    with app.app_context():
        assert _conv(app, env["tid"]).attention_reason == "ai_inactive"
        assert appmod.SupportTicket.query.filter_by(tenant_id=env["tid"]).count() == 1
        system_msgs = appmod.WhatsAppMessage.query.filter_by(sender="system").all()
        assert len(system_msgs) == 1 and system_msgs[0].wa_message_id == "wamid.OUT1"
    assert env["ai_calls"] == []


def test_forwarding_mobile_messages_not_persisted(app, client, env):
    with app.app_context():
        s = appmod.WhatsAppSettings.query.filter_by(tenant_id=env["tid"]).first()
        s.forwarding_mobile = "96176000000"
        appmod.db.session.commit()
    _send(client, {"from": "96176000000", "id": "wamid.F1", "type": "text", "text": {"body": "ok"}})
    with app.app_context():
        assert appmod.WhatsAppConversation.query.count() == 0


def test_status_callbacks_update_outbound(app, client, env):
    with app.app_context():
        conv = wi.upsert_conversation(appmod, env["tid"], "96170123456")
        wi.record_outbound(appmod, conv, sender="admin", msg_type="text", text="hi", wa_message_id="wamid.O9")
        appmod.db.session.commit()
    _send(client, statuses=[{"id": "wamid.O9", "status": "delivered", "recipient_id": "96170123456"}])
    with app.app_context():
        assert appmod.WhatsAppMessage.query.filter_by(wa_message_id="wamid.O9").first().status == "delivered"
    _send(client, statuses=[{"id": "wamid.O9", "status": "failed", "errors": [{"code": 131026, "title": "Undeliverable"}]}])
    with app.app_context():
        assert _conv(app, env["tid"]).attention_reason == "send_failed"
    assert len(env["pushes"]) == 1
```

`_send(client, statuses=...)` with no messages: `wa_payload` gets `messages=[]`, which is fine.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_whatsapp_inbox_webhook.py -v`
Expected: FAIL (no rows in `whatsapp_conversation`).

- [ ] **Step 3: Add `store_inbound_media` and an `after_ai_reply` stub to `whatsapp_inbox.py`**

```python
import mimetypes

import media_convert
import storage


def store_inbound_media(appmod, message_id, access_token, api_version):
    """Download an inbound message's media from Meta into storage (plus an mp3
    playback copy for audio). Commits. Never raises into the caller."""
    import cs_agent_tools  # lazy: cs_agent_tools imports this module lazily too
    msg = appmod.db.session.get(appmod.WhatsAppMessage, message_id)
    if msg is None or not msg.wa_media_id:
        return
    try:
        data, mime = cs_agent_tools.download_meta_media(access_token, msg.wa_media_id, api_version=api_version)
        if not data:
            msg.media_status = 'failed'
            appmod.db.session.commit()
            return
        mime = (mime or msg.media_mime or 'application/octet-stream').split(';')[0].strip()
        ext = mimetypes.guess_extension(mime) or '.bin'
        msg.media_key = storage.save_bytes(data, msg.tenant_id, f"wa-{msg.id}{ext}", mime)
        msg.media_mime = mime
        if msg.msg_type == 'audio':
            try:
                mp3 = media_convert.to_mp3(data)
                msg.media_playback_key = storage.save_bytes(mp3, msg.tenant_id, f"wa-{msg.id}.mp3", 'audio/mpeg')
            except media_convert.ConversionError as e:
                logging.warning(f"whatsapp_inbox: no mp3 playback copy for message {msg.id}: {e}")
        msg.media_status = 'stored'
        appmod.db.session.commit()
    except Exception:
        appmod.db.session.rollback()
        logging.exception(f"whatsapp_inbox: media download failed for message {message_id}")
        msg = appmod.db.session.get(appmod.WhatsAppMessage, message_id)
        if msg is not None:
            msg.media_status = 'failed'
            appmod.db.session.commit()


def after_ai_reply(appmod, tenant_id, wa_phone, result):
    """Placeholder until Task 7 -- flags ai_failed when the AI path returned nothing."""
    conv = find_conversation_by_phone(appmod, tenant_id, wa_phone)
    if conv is None:
        return
    if result is None:
        flag_attention(conv, 'ai_failed')
    appmod.db.session.commit()
    if conv.needs_attention:
        notify_conversation(appmod, conv)
```

Put the `import mimetypes`, `import media_convert` and `import storage` lines with the other imports at the top of the file.

- [ ] **Step 4: Modify the webhook in `app.py`**

Add these imports after `import cs_agent_tools` (line 48):

```python
import functools
import whatsapp_inbox
```

In `whatsapp_webhook`, replace the whole `for st in val.get('statuses', []):` loop with:

```python
                    _appmod = sys.modules[__name__]
                    for st in val.get('statuses', []):
                        st_status = st.get('status')
                        st_recipient = st.get('recipient_id')
                        if st_status == 'failed':
                            logging.warning(f"WhatsApp message to {st_recipient} FAILED: {st.get('errors', [])}")
                        else:
                            logging.info(f"WhatsApp message status update: to={st_recipient} status={st_status}")
                        try:
                            _st_msg, _st_flagged = whatsapp_inbox.apply_status(_appmod, resolved_tenant_id, st)
                            db.session.commit()
                            if _st_flagged is not None:
                                whatsapp_inbox.notify_conversation(_appmod, _st_flagged)
                        except Exception as ex_st:
                            db.session.rollback()
                            logging.warning(f"WhatsApp inbox: could not apply status {st.get('id')}: {ex_st}")
```

At the top of `for msg in messages:`, right after `msg_type = msg.get('type', '')`, insert:

```python
                        wamid = msg.get('id')
                        if wamid and whatsapp_inbox.is_duplicate(_appmod, resolved_tenant_id, wamid):
                            logging.info(f"WhatsApp webhook: duplicate delivery of {wamid}; skipping.")
                            continue
```

Directly after the `cust_obj = Customer.query...` / `cust_name = cust_obj.name` block, and **before** the transcription block, insert:

```python
                        is_forwarding_mobile_reply = bool(
                            settings and settings.forwarding_mobile and
                            normalize_whatsapp_phone(settings.forwarding_mobile) == sender_phone
                        )

                        # --- Admin inbox persistence (before the AI, so nothing is lost) ---
                        inbox_conv = inbox_msg = None
                        if not is_forwarding_mobile_reply:
                            try:
                                _profile = contacts[0].get('profile', {}).get('name') if contacts else None
                                inbox_conv = whatsapp_inbox.upsert_conversation(_appmod, resolved_tenant_id, sender_phone, _profile)
                                inbox_msg = whatsapp_inbox.record_inbound(
                                    _appmod, resolved_tenant_id, inbox_conv, whatsapp_inbox.parse_inbound(msg))
                                db.session.commit()
                            except Exception as ex_inbox:
                                db.session.rollback()
                                inbox_conv = inbox_msg = None
                                logging.error(f"WhatsApp inbox: could not persist inbound {wamid}: {ex_inbox}")
                        if inbox_msg is not None and inbox_msg.wa_media_id and settings.access_token:
                            whatsapp_inbox.run_background(app, functools.partial(
                                whatsapp_inbox.store_inbound_media, _appmod, inbox_msg.id,
                                settings.access_token, settings.api_version or 'v19.0'),
                                pool=whatsapp_inbox.media_pool())
```

Inside the transcription block, change

```python
                                    if transcript:
                                        msg_text = f"[رسالة صوتية]: {transcript}"
```

to

```python
                                    if transcript:
                                        msg_text = f"[رسالة صوتية]: {transcript}"
                                        if inbox_msg is not None:
                                            inbox_msg.transcript = transcript
                                            db.session.commit()
```

Delete the old `is_forwarding_mobile_reply = bool(...)` assignment further down; it moved up. Keep the `cs_agent_active` computation. Directly after it (before `ticket = None`) insert:

```python
                        ai_will_run = bool(cs_agent_active and getattr(settings, 'auto_reply_enabled', True))
                        ai_paused = False
                        if inbox_conv is not None:
                            try:
                                whatsapp_inbox.maybe_auto_resume(inbox_conv)
                                ai_paused = bool(inbox_conv.ai_paused)
                                reason = whatsapp_inbox.inbound_attention_reason(
                                    ai_paused=ai_paused, ai_will_run=ai_will_run,
                                    has_customer=cust_obj is not None,
                                    msg_type=inbox_msg.msg_type if inbox_msg is not None else 'text')
                                if reason:
                                    whatsapp_inbox.flag_attention(inbox_conv, reason)
                                db.session.commit()
                                if inbox_conv.needs_attention:
                                    whatsapp_inbox.notify_conversation(_appmod, inbox_conv)
                            except Exception as ex_flag:
                                db.session.rollback()
                                logging.error(f"WhatsApp inbox: could not flag conversation for {wamid}: {ex_flag}")
```

Change the AI branch condition from

```python
                        if cs_agent_active and getattr(settings, 'auto_reply_enabled', True) and not is_forwarding_mobile_reply:
```

to

```python
                        if ai_will_run and not is_forwarding_mobile_reply and not ai_paused:
```

Replace the `_run_ai_reply_with_ctx` closure and its spawn block (from `def _run_ai_reply_with_ctx(` through the `else: _run_ai_reply_with_ctx()`) with:

```python
                                def _run_ai_reply(
                                    appmod=_appmod, tenant_id=_tenant_id, sender=_sender,
                                    cust=_cust, text=_text, is_voice=_is_voice, stgs=_settings
                                ):
                                    try:
                                        result = cs_agent_tools.handle_whatsapp_cs_ai_reply(
                                            appmod=appmod, tenant_id=tenant_id, sender_phone=sender,
                                            customer=cust, incoming_text=text, is_voice=is_voice,
                                            settings=stgs, ticket=None)
                                    except Exception:
                                        logging.exception("CS AI reply crashed")
                                        result = None
                                    whatsapp_inbox.after_ai_reply(appmod, tenant_id, sender, result)

                                # Bounded pool (see AI_REPLY_GREENLET_POOL); run_background pushes the
                                # app context the greenlet needs, or runs inline under tests.
                                whatsapp_inbox.run_background(_flask_app, _run_ai_reply, pool=AI_REPLY_GREENLET_POOL)
```

Keep the `_flask_app = app`, `_appmod`, `_tenant_id`, … assignments above it. `_appmod` is now defined earlier, so the `_appmod = sys.modules[__name__]` line inside this block can stay or be deleted; the result is the same.

Change the inactive branch condition from `elif not is_forwarding_mobile_reply:` to:

```python
                        elif not is_forwarding_mobile_reply and not ai_paused:
```

In that branch's canned-reply send, after `if res_rep.ok: logging.info(...)`, record it:

```python
                                        if res_rep.ok:
                                            logging.info(f"Sent fallback canned reply to +{sender_phone}.")
                                            if inbox_conv is not None:
                                                try:
                                                    _w = ((res_rep.json() or {}).get('messages') or [{}])[0].get('id')
                                                    whatsapp_inbox.record_outbound(
                                                        _appmod, inbox_conv, sender='system', msg_type='text',
                                                        text=reply_text, wa_message_id=_w)
                                                    db.session.commit()
                                                except Exception as ex_rec:
                                                    db.session.rollback()
                                                    logging.warning(f"WhatsApp inbox: could not record canned reply: {ex_rec}")
```

- [ ] **Step 5: Run the tests**

Run: `python -m pytest tests/test_whatsapp_inbox_webhook.py tests/test_iso_webhook.py tests/test_cs_agent_tools.py -v`
Expected: all pass. The existing webhook tests must be unchanged. `test_webhook_resolves_tenant_by_phone_number_id` has no access token, so `cs_agent_active` is False and it still creates a ticket.

Run: `python -m pytest tests/ -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add app.py whatsapp_inbox.py tests/test_whatsapp_inbox_webhook.py
git commit -m "Persist WhatsApp messages to the inbox, dedupe retries, flag attention, pause AI"
```

---

### Task 7: AI replies recorded, AI failure and escalation detection

**Files:**
- Modify: `cs_agent_tools.py`:
  - `send_whatsapp_voice` (line 1170)
  - `handle_whatsapp_cs_ai_reply` (lines 2091-2330)
  - `escalate_to_human` (line 799, just before `return`)
- Modify: `whatsapp_inbox.py`: add `record_ai_reply`, `flag_attention_for_phone`, and the full `after_ai_reply`
- Test: `tests/test_whatsapp_inbox_ai.py`

**Interfaces:**
- Produces:
  - `send_whatsapp_voice(...)` returns the wamid string on success (`True` if Meta returned no id) or `False`. Existing callers only test truthiness.
  - `handle_whatsapp_cs_ai_reply` returns `ai_result` with the extra keys `ai_source` (`'gemini'` or `'rules'`), `gemini_configured` (bool) and `inbox_send_failed` (bool).
  - `whatsapp_inbox.record_ai_reply(appmod, tenant_id, wa_phone, *, reply_text, text_wamid, text_ok, text_error, voice_result) -> bool`. It returns True when the text send failed, commits, and never raises.
  - `whatsapp_inbox.flag_attention_for_phone(appmod, tenant_id, phone, reason) -> WhatsAppConversation | None`. It commits and pushes, and only touches existing conversations.
  - `whatsapp_inbox.after_ai_reply(appmod, tenant_id, wa_phone, result)` is the final version.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_whatsapp_inbox_ai.py
from unittest.mock import MagicMock

import app as appmod
import cs_agent_tools
import whatsapp_inbox as wi
from tests.conftest import make_tenant
from tests.inbox_helpers import FakeResponse, seed_customer


def _tenant(app, client, name, user):
    hdr = make_tenant(client, name, user)
    with app.app_context():
        return hdr, appmod.User.query.filter_by(username=user).first().tenant_id


def _settings():
    s = MagicMock()
    s.access_token = "tok"; s.phone_number_id = "PN"; s.api_version = "v19.0"
    return s


def test_ai_reply_is_recorded_as_ai_message(app, client, monkeypatch):
    hdr, tid = _tenant(app, client, "Biz AI", "ai_admin")
    seed_customer(client, hdr, "70777888", name="Salim")
    monkeypatch.setattr(cs_agent_tools.requests, "post", lambda *a, **kw: FakeResponse(body={"messages": [{"id": "wamid.AI1"}]}))
    with app.app_context():
        cust = appmod.Customer.query.filter_by(tenant_id=tid).first()
        wi.upsert_conversation(appmod, tid, "96170777888"); appmod.db.session.commit()
        res = cs_agent_tools.handle_whatsapp_cs_ai_reply(appmod, tid, "96170777888", cust, "رصيدي", settings=_settings())
        assert res["ai_source"] == "rules" and res["gemini_configured"] is False and res["inbox_send_failed"] is False
        ai = appmod.WhatsAppMessage.query.filter_by(sender="ai").all()
        assert len(ai) == 1 and ai[0].wa_message_id == "wamid.AI1" and ai[0].text == res["reply_text"]


def test_ai_text_send_failure_flags_send_failed(app, client, monkeypatch):
    hdr, tid = _tenant(app, client, "Biz AIF", "aif_admin")
    seed_customer(client, hdr, "70777889")
    monkeypatch.setattr(cs_agent_tools.requests, "post",
                        lambda *a, **kw: FakeResponse(ok=False, status_code=400, body={"error": {"code": 131047, "message": "Re-engagement"}}))
    with app.app_context():
        cust = appmod.Customer.query.filter_by(tenant_id=tid).first()
        res = cs_agent_tools.handle_whatsapp_cs_ai_reply(appmod, tid, "96170777889", cust, "hi", settings=_settings())
        assert res["inbox_send_failed"] is True
        conv = wi.find_conversation_by_phone(appmod, tid, "96170777889")
        assert conv.attention_reason == "send_failed"
        failed = appmod.WhatsAppMessage.query.filter_by(sender="ai").first()
        assert failed.status == "failed" and failed.error_code == "131047"


def test_after_ai_reply_flags(app, client, monkeypatch):
    _, tid = _tenant(app, client, "Biz After", "after_admin")
    pushes = []
    monkeypatch.setattr(appmod, "send_push_notification", lambda p, **kw: pushes.append(p) or 1)
    with app.app_context():
        conv = wi.upsert_conversation(appmod, tid, "96170100200"); appmod.db.session.commit()
        wi.after_ai_reply(appmod, tid, "96170100200", {"reply_text": "x", "ai_source": "gemini", "gemini_configured": True})
        assert conv.needs_attention is False
        wi.after_ai_reply(appmod, tid, "96170100200", {"reply_text": "x", "ai_source": "rules", "gemini_configured": False})
        assert conv.needs_attention is False  # tenant without Gemini relies on rules by design
        wi.after_ai_reply(appmod, tid, "96170100200", {"reply_text": "x", "ai_source": "rules", "gemini_configured": True})
        assert conv.attention_reason == "ai_failed"
        wi.clear_attention(conv); appmod.db.session.commit()
        wi.after_ai_reply(appmod, tid, "96170100200", None)
        assert conv.attention_reason == "ai_failed"
        wi.clear_attention(conv); appmod.db.session.commit()
        wi.after_ai_reply(appmod, tid, "96170100200", {"reply_text": "x", "ai_source": "rules", "gemini_configured": False, "escalate": True})
        assert conv.attention_reason == "escalated"
    assert len(pushes) >= 1


def test_escalate_to_human_flags_existing_conversation(app, client, monkeypatch):
    hdr, tid = _tenant(app, client, "Biz Esc", "esc_admin")
    seed_customer(client, hdr, "70555666")
    monkeypatch.setattr(appmod, "send_push_notification", lambda p, **kw: 1)
    with app.app_context():
        conv = wi.upsert_conversation(appmod, tid, "96170555666"); appmod.db.session.commit()
        cust = appmod.Customer.query.filter_by(tenant_id=tid).first()
        cs_agent_tools.escalate_to_human(appmod, tid, cust.id, "need human", "summary", phone="96170555666")
        assert conv.attention_reason == "escalated"
        # No conversation for this phone -> nothing created.
        cs_agent_tools.escalate_to_human(appmod, tid, None, "r", "s", phone="96171999999")
        assert wi.find_conversation_by_phone(appmod, tid, "96171999999") is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_whatsapp_inbox_ai.py -v`
Expected: FAIL with `KeyError: 'ai_source'`

- [ ] **Step 3: Add the inbox helpers (replacing the `after_ai_reply` stub) in `whatsapp_inbox.py`**

```python
def flag_attention_for_phone(appmod, tenant_id, phone, reason):
    """Flag an EXISTING conversation for this phone (never creates one -- an
    escalation from the phone channel must not invent a WhatsApp chat).
    Commits and pushes. Returns the conversation or None."""
    conv = find_conversation_by_phone(appmod, tenant_id, phone)
    if conv is None:
        return None
    flag_attention(conv, reason)
    appmod.db.session.commit()
    notify_conversation(appmod, conv)
    return conv


def record_ai_reply(appmod, tenant_id, wa_phone, *, reply_text, text_wamid, text_ok, text_error, voice_result):
    """Record what the AI actually sent. Returns True when the text send failed
    (the conversation is then flagged send_failed). Commits; never raises."""
    try:
        conv = upsert_conversation(appmod, tenant_id, wa_phone)
        record_outbound(appmod, conv, sender='ai', msg_type='text', text=reply_text,
                        wa_message_id=text_wamid if text_ok else None,
                        status='sent' if text_ok else 'failed',
                        error_code=(text_error or {}).get('code'), error_message=(text_error or {}).get('message'))
        if voice_result:
            record_outbound(appmod, conv, sender='ai', msg_type='audio', transcript=reply_text,
                            wa_message_id=voice_result if isinstance(voice_result, str) else None)
        if not text_ok:
            flag_attention(conv, 'send_failed')
        appmod.db.session.commit()
        return not text_ok
    except Exception:
        appmod.db.session.rollback()
        logging.exception("whatsapp_inbox: could not record AI reply")
        return not text_ok


def after_ai_reply(appmod, tenant_id, wa_phone, result):
    """Called after every AI attempt on an inbound message. Flags:
    - ai_failed: the AI raised / returned nothing, OR the tenant has a Gemini
      key but Gemini produced nothing and the rule-based fallback answered;
    - escalated: the (rule-based) AI decided to escalate.
    send_failed was already flagged by record_ai_reply. Commits and pushes."""
    try:
        conv = find_conversation_by_phone(appmod, tenant_id, wa_phone)
        if conv is None:
            return
        if result is None:
            flag_attention(conv, 'ai_failed')
        elif result.get('escalate'):
            flag_attention(conv, 'escalated')
        elif result.get('gemini_configured') and result.get('ai_source') == 'rules':
            flag_attention(conv, 'ai_failed')
        appmod.db.session.commit()
        if conv.needs_attention:
            notify_conversation(appmod, conv)
    except Exception:
        appmod.db.session.rollback()
        logging.exception("whatsapp_inbox: after_ai_reply failed")
```

- [ ] **Step 4: Modify `cs_agent_tools.py`**

In `send_whatsapp_voice`, replace the final two lines of the `try` block (`if not res_msg.ok: ...` / `return res_msg.ok`) with:

```python
        if not res_msg.ok:
            logging.warning(f"Failed to send WhatsApp audio message: {res_msg.status_code} {res_msg.text}")
            return False
        try:
            return ((res_msg.json() or {}).get('messages') or [{}])[0].get('id') or True
        except ValueError:
            return True
```

In `handle_whatsapp_cs_ai_reply`, change the fallback block

```python
    if not ai_result or not ai_result.get("reply_text"):
        ai_result = process_customer_message_ai(
```

to

```python
    ai_source = 'gemini' if (ai_result and ai_result.get("reply_text")) else 'rules'
    if not ai_result or not ai_result.get("reply_text"):
        ai_result = process_customer_message_ai(
```

and directly after that `if` block (before `reply_text = ai_result.get("reply_text")`) add:

```python
    ai_result = dict(ai_result or {})
    ai_result['ai_source'] = ai_source
    ai_result['gemini_configured'] = bool(gemini_key)
```

Replace the "1. Send WhatsApp Text message" `try` block with:

```python
    # 1. Send WhatsApp Text message
    text_ok, text_wamid, text_error = False, None, None
    try:
        payload_reply = {
            'messaging_product': 'whatsapp',
            'to': sender_phone,
            'type': 'text',
            'text': {'body': reply_text}
        }
        res_rep = requests.post(url_reply, json=payload_reply, headers=headers_reply, timeout=10)
        try:
            body = res_rep.json() or {}
        except ValueError:
            body = {}
        if res_rep.ok:
            text_ok = True
            text_wamid = (body.get('messages') or [{}])[0].get('id')
            logging.info(f"Sent CS AI reply text to +{sender_phone} (intent: {ai_result.get('intent')}).")
        else:
            err = body.get('error') or {}
            text_error = {'code': err.get('code') or res_rep.status_code, 'message': err.get('message') or res_rep.text}
            logging.warning(f"Could not send CS AI reply text to +{sender_phone}: {res_rep.text}")
    except Exception as e:
        text_error = {'code': 'exception', 'message': str(e)}
        logging.error(f"Error sending WhatsApp text reply: {e}")
```

In the voice block, add `voice_result = None` before `if is_voice:`, and change `sent_voice = send_whatsapp_voice(` so it assigns into `voice_result` too:

```python
    voice_result = None
    if is_voice:
        try:
            tts_audio = synthesize_speech_elevenlabs(reply_text, agent_id=target_agent_id)
            if tts_audio:
                sent_voice = voice_result = send_whatsapp_voice(
```

Leave the rest of the voice block unchanged.

Right before the final `return ai_result`, add:

```python
    # 5. Mirror what was actually sent into the admin inbox (see whatsapp_inbox.py).
    import whatsapp_inbox  # lazy: whatsapp_inbox imports this module lazily too
    ai_result['inbox_send_failed'] = whatsapp_inbox.record_ai_reply(
        appmod, tenant_id, sender_phone, reply_text=reply_text, text_wamid=text_wamid,
        text_ok=text_ok, text_error=text_error, voice_result=voice_result)
```

In `escalate_to_human`, right before the final `return {`, add:

```python
    # Surface this in the admin WhatsApp inbox, if a WhatsApp chat exists for them.
    try:
        import whatsapp_inbox
        flag_phone = phone or (customer.phone if customer else None)
        if flag_phone:
            whatsapp_inbox.flag_attention_for_phone(appmod, tenant_id, flag_phone, 'escalated')
    except Exception as ex_inbox:
        logging.warning(f"Could not flag inbox conversation for escalation: {ex_inbox}")
```

- [ ] **Step 5: Run the tests**

Run: `python -m pytest tests/test_whatsapp_inbox_ai.py tests/test_cs_agent_tools.py tests/test_cs_agent_gemini_brain.py -v`
Expected: all pass.

If an existing test asserts `send_whatsapp_voice(...) is True` or `== True`, change that assertion to `assert result` (truthy). That is the only permitted change to existing tests.

Run: `python -m pytest tests/ -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add cs_agent_tools.py whatsapp_inbox.py tests/test_whatsapp_inbox_ai.py
git commit -m "Record AI WhatsApp replies in the inbox; flag AI failure, escalation and send failures"
```

---

### Task 8: Admin send helpers (text, reaction, voice, sticker, template)

**Files:**
- Modify: `whatsapp_inbox.py`
- Test: `tests/test_whatsapp_inbox_send.py`

**Interfaces:**
- Produces:
  - `whatsapp_inbox.SendError(code, message, http_status=502)` and `whatsapp_inbox.WindowClosed(SendError)`, which has `code='window_closed'` and `http_status=409`.
  - `whatsapp_inbox.send_admin_message(appmod, conv, user_id, kind, *, text=None, reply_to=None, target=None, emoji=None, file_bytes=None, template_name=None, body_params=None, header_param=None) -> WhatsAppMessage`. `kind` is one of `text`, `reaction`, `voice`, `sticker`, `template`. It commits.
  - On success it sets `ai_paused=True`, `ai_paused_at`, `last_admin_reply_at`, clears attention and sets `unread_count=0`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_whatsapp_inbox_send.py
import io
import json
from datetime import datetime, timedelta

import pytest
from PIL import Image

import app as appmod
import media_convert
import storage
import whatsapp_inbox as wi
from tests.inbox_helpers import FakeResponse, setup_wa_tenant


@pytest.fixture
def conv_env(app, client, monkeypatch, tmp_path):
    monkeypatch.setattr(storage, "UPLOAD_ROOT", str(tmp_path))
    monkeypatch.setattr(storage, "_backend", storage.LocalBackend())
    calls = []

    def fake_post(url, **kw):
        calls.append({"url": url, **kw})
        if url.endswith("/media"):
            return FakeResponse(body={"id": "MEDIA_UP"})
        return FakeResponse(body={"messages": [{"id": f"wamid.SENT{len(calls)}"}]})

    monkeypatch.setattr(wi.requests, "post", fake_post)
    hdr, tid = setup_wa_tenant(app, client, "Biz Send", "send_admin", "PNID_S")
    with app.app_context():
        conv = wi.upsert_conversation(appmod, tid, "96170123456")
        conv.last_inbound_at = datetime.utcnow() - timedelta(hours=1)
        wi.flag_attention(conv, "ai_failed")
        conv.unread_count = 3
        appmod.db.session.commit()
        conv_id = conv.id
        uid = appmod.User.query.filter_by(username="send_admin").first().id
    return {"calls": calls, "conv_id": conv_id, "uid": uid, "tid": tid}


def _conv(conv_env):
    return appmod.db.session.get(appmod.WhatsAppConversation, conv_env["conv_id"])


def test_text_send_pauses_ai_and_clears_attention(app, conv_env):
    with app.app_context():
        conv = _conv(conv_env)
        m = wi.send_admin_message(appmod, conv, conv_env["uid"], "text", text="On it 👍", reply_to="wamid.IN1")
        body = conv_env["calls"][0]["json"]
        assert body == {"messaging_product": "whatsapp", "to": "96170123456", "type": "text",
                        "text": {"body": "On it 👍"}, "context": {"message_id": "wamid.IN1"}}
        assert m.sender == "admin" and m.sent_by_user_id == conv_env["uid"] and m.wa_message_id == "wamid.SENT1"
        assert conv.ai_paused and conv.last_admin_reply_at and not conv.needs_attention and conv.unread_count == 0


def test_reaction_send(app, conv_env):
    with app.app_context():
        m = wi.send_admin_message(appmod, _conv(conv_env), conv_env["uid"], "reaction", target="wamid.IN1", emoji="❤️")
        assert conv_env["calls"][0]["json"]["reaction"] == {"message_id": "wamid.IN1", "emoji": "❤️"}
        assert m.msg_type == "reaction" and m.reaction_target_wa_id == "wamid.IN1"


def test_sticker_png_is_converted_and_uploaded(app, conv_env):
    buf = io.BytesIO(); Image.new("RGBA", (300, 200), (0, 0, 255, 255)).save(buf, "PNG")
    with app.app_context():
        m = wi.send_admin_message(appmod, _conv(conv_env), conv_env["uid"], "sticker", file_bytes=buf.getvalue())
        upload = conv_env["calls"][0]
        assert upload["url"].endswith("/PNID_S/media")
        assert upload["files"]["file"][2] == "image/webp"
        assert conv_env["calls"][1]["json"]["sticker"] == {"id": "MEDIA_UP"}
        stored = Image.open(io.BytesIO(storage.read_bytes(m.media_key)))
        assert stored.size == (512, 512)


@pytest.mark.skipif(not media_convert.ffmpeg_available(), reason="ffmpeg not installed")
def test_voice_is_converted_to_ogg_and_sent(app, conv_env):
    from tests.test_media_convert import _wav_bytes
    with app.app_context():
        m = wi.send_admin_message(appmod, _conv(conv_env), conv_env["uid"], "voice", file_bytes=_wav_bytes())
        assert conv_env["calls"][0]["files"]["file"][2] == "audio/ogg"
        assert conv_env["calls"][1]["json"]["audio"] == {"id": "MEDIA_UP"}
        assert m.msg_type == "audio" and storage.read_bytes(m.media_key)[:4] == b"OggS"


def test_voice_without_ffmpeg_is_503(app, conv_env, monkeypatch):
    monkeypatch.setattr(media_convert, "ffmpeg_available", lambda: False)
    monkeypatch.setattr(media_convert.shutil, "which", lambda n: None)
    with app.app_context():
        with pytest.raises(wi.SendError) as e:
            wi.send_admin_message(appmod, _conv(conv_env), conv_env["uid"], "voice", file_bytes=b"x")
        assert e.value.code == "voice_unavailable" and e.value.http_status == 503


def test_window_closed_blocks_free_form_but_allows_template(app, conv_env, monkeypatch):
    monkeypatch.setattr(appmod, "build_meta_template_payload",
                        lambda **kw: {"name": kw["template_name"], "language": {"code": "en"}})
    with app.app_context():
        conv = _conv(conv_env)
        conv.last_inbound_at = datetime.utcnow() - timedelta(hours=30)
        appmod.db.session.commit()
        with pytest.raises(wi.WindowClosed):
            wi.send_admin_message(appmod, conv, conv_env["uid"], "text", text="hi")
        assert conv_env["calls"] == []
        m = wi.send_admin_message(appmod, conv, conv_env["uid"], "template", template_name="follow_up", body_params=["Rami"])
        assert conv_env["calls"][0]["json"]["type"] == "template"
        assert m.msg_type == "template" and m.text == "[Template: follow_up] Rami"


def test_meta_131047_maps_to_window_closed_and_records_failed_row(app, conv_env, monkeypatch):
    monkeypatch.setattr(wi.requests, "post", lambda url, **kw: FakeResponse(
        ok=False, status_code=400, body={"error": {"code": 131047, "message": "Re-engagement message"}}))
    with app.app_context():
        with pytest.raises(wi.WindowClosed):
            wi.send_admin_message(appmod, _conv(conv_env), conv_env["uid"], "text", text="hi")
        failed = appmod.WhatsAppMessage.query.filter_by(sender="admin").first()
        assert failed.status == "failed" and failed.error_code == "131047"
        assert _conv(conv_env).ai_paused is False


def test_empty_text_and_unknown_kind_rejected(app, conv_env):
    with app.app_context():
        with pytest.raises(wi.SendError) as e:
            wi.send_admin_message(appmod, _conv(conv_env), conv_env["uid"], "text", text="   ")
        assert e.value.http_status == 400
        with pytest.raises(wi.SendError):
            wi.send_admin_message(appmod, _conv(conv_env), conv_env["uid"], "gif")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_whatsapp_inbox_send.py -v`
Expected: FAIL with `AttributeError: module 'whatsapp_inbox' has no attribute 'send_admin_message'`

- [ ] **Step 3: Implement (append to `whatsapp_inbox.py`; add `import io` and `import requests` to the imports)**

```python
class SendError(Exception):
    def __init__(self, code, message, http_status=502):
        super().__init__(message)
        self.code = str(code)
        self.message = message
        self.http_status = http_status


class WindowClosed(SendError):
    def __init__(self, message="The 24-hour WhatsApp window has closed -- send an approved template instead."):
        super().__init__('window_closed', message, 409)


def _graph_base(settings):
    return f"https://graph.facebook.com/{settings.api_version or 'v19.0'}/{settings.phone_number_id}"


def _json(res):
    try:
        return res.json() or {}
    except ValueError:
        return {}


def _post_message(settings, payload):
    res = requests.post(f"{_graph_base(settings)}/messages",
                        json={'messaging_product': 'whatsapp', **payload},
                        headers={'Authorization': f'Bearer {settings.access_token}',
                                 'Content-Type': 'application/json'}, timeout=15)
    body = _json(res)
    if not res.ok:
        err = body.get('error') or {}
        code = str(err.get('code') or res.status_code)
        if code == '131047':
            raise WindowClosed()
        raise SendError(code, err.get('message') or res.text[:300])
    return ((body.get('messages') or [{}])[0]).get('id')


def _upload_media(settings, data, filename, mime):
    res = requests.post(f"{_graph_base(settings)}/media",
                        headers={'Authorization': f'Bearer {settings.access_token}'},
                        files={'file': (filename, io.BytesIO(data), mime)},
                        data={'messaging_product': 'whatsapp', 'type': mime}, timeout=30)
    body = _json(res)
    if not res.ok or not body.get('id'):
        err = body.get('error') or {}
        raise SendError(str(err.get('code') or res.status_code), err.get('message') or 'Media upload to WhatsApp failed')
    return body['id']


def send_admin_message(appmod, conv, user_id, kind, *, text=None, reply_to=None, target=None, emoji=None,
                       file_bytes=None, template_name=None, body_params=None, header_param=None):
    settings = appmod.WhatsAppSettings.query.filter_by(tenant_id=conv.tenant_id).first()
    if not settings or not settings.access_token or not settings.phone_number_id:
        raise SendError('not_configured', 'WhatsApp Cloud API is not configured for this business.', 400)
    if kind != 'template' and not window_open(conv):
        raise WindowClosed()

    to = conv.wa_phone
    rec = {'msg_type': kind, 'text': None}
    if kind == 'text':
        text = (text or '').strip()
        if not text:
            raise SendError('empty', 'Message is empty.', 400)
        payload = {'to': to, 'type': 'text', 'text': {'body': text[:4096]}}
        if reply_to:
            payload['context'] = {'message_id': reply_to}
        rec.update(text=text[:4096], reply_to_wa_message_id=reply_to)
    elif kind == 'reaction':
        if not target:
            raise SendError('missing_target', 'Pick a message to react to.', 400)
        payload = {'to': to, 'type': 'reaction', 'reaction': {'message_id': target, 'emoji': emoji or ''}}
        rec.update(reaction_emoji=emoji or '', reaction_target_wa_id=target)
    elif kind == 'voice':
        if not file_bytes:
            raise SendError('empty', 'No recording received.', 400)
        if len(file_bytes) > media_convert.VOICE_MAX_BYTES:
            raise SendError('too_large', 'Voice notes are limited to 16 MB.', 413)
        if not media_convert.ffmpeg_available():
            raise SendError('voice_unavailable', 'Voice notes are unavailable: ffmpeg is not installed on the server.', 503)
        try:
            ogg = media_convert.to_ogg_opus(file_bytes)
        except media_convert.ConversionError as e:
            raise SendError('bad_audio', f'Could not convert the recording: {e}', 400)
        media_id = _upload_media(settings, ogg, 'voice.ogg', 'audio/ogg')
        playback_key = None
        try:
            playback_key = storage.save_bytes(media_convert.to_mp3(ogg), conv.tenant_id, 'voice.mp3', 'audio/mpeg')
        except media_convert.ConversionError:
            pass
        payload = {'to': to, 'type': 'audio', 'audio': {'id': media_id}}
        rec.update(msg_type='audio', media_key=storage.save_bytes(ogg, conv.tenant_id, 'voice.ogg', 'audio/ogg'),
                   media_playback_key=playback_key, media_mime='audio/ogg')
    elif kind == 'sticker':
        if not file_bytes:
            raise SendError('empty', 'No sticker file received.', 400)
        if len(file_bytes) > media_convert.STICKER_SOURCE_MAX_BYTES:
            raise SendError('too_large', 'Sticker source images are limited to 5 MB.', 413)
        try:
            webp = media_convert.to_sticker_webp(file_bytes)
        except media_convert.ConversionError as e:
            raise SendError('bad_image', str(e), 400)
        media_id = _upload_media(settings, webp, 'sticker.webp', 'image/webp')
        payload = {'to': to, 'type': 'sticker', 'sticker': {'id': media_id}}
        rec.update(media_key=storage.save_bytes(webp, conv.tenant_id, 'sticker.webp', 'image/webp'), media_mime='image/webp')
    elif kind == 'template':
        if not template_name:
            raise SendError('missing_template', 'Pick a template.', 400)
        params = [str(p) for p in (body_params or []) if str(p).strip()]
        tpl = appmod.build_meta_template_payload(
            settings=settings, template_name=template_name,
            default_language=settings.template_language or 'en',
            user_body_params=params, user_header_params=header_param or None)
        payload = {'to': to, 'type': 'template', 'template': tpl}
        rec.update(text=f"[Template: {template_name}]" + (" " + " | ".join(params) if params else ""))
    else:
        raise SendError('bad_kind', f'Unsupported message type: {kind}', 400)

    try:
        wamid = _post_message(settings, payload)
    except SendError as e:
        record_outbound(appmod, conv, sender='admin', sent_by_user_id=user_id, status='failed',
                        error_code=e.code, error_message=e.message, **rec)
        appmod.db.session.commit()
        raise
    msg = record_outbound(appmod, conv, sender='admin', sent_by_user_id=user_id, wa_message_id=wamid,
                          status='sent', **rec)
    now = datetime.utcnow()
    conv.last_admin_reply_at = now
    conv.ai_paused = True
    conv.ai_paused_at = now
    conv.unread_count = 0
    clear_attention(conv)
    appmod.db.session.commit()
    return msg
```

`record_outbound` accepts `media_status` implicitly: it sets `stored` when `media_key` is given. The `rec` dict keys are all `record_outbound` keyword arguments.

- [ ] **Step 4: Run the tests**

Run: `python -m pytest tests/test_whatsapp_inbox_send.py -v`
Expected: all pass (the voice test is skipped on CI).

- [ ] **Step 5: Commit**

```bash
git add whatsapp_inbox.py tests/test_whatsapp_inbox_send.py
git commit -m "Add admin WhatsApp sends: text, reaction, voice, sticker, template with window enforcement"
```

---

### Task 9: Inbox admin API

**Files:**
- Create: `whatsapp_inbox_routes.py`
- Modify: `app.py`: register the routes right after the `whatsapp_webhook` function (after line 8080):
  ```python
  import whatsapp_inbox_routes
  whatsapp_inbox_routes.register_inbox_routes(app, sys.modules[__name__])
  ```
- Test: `tests/test_whatsapp_inbox_routes.py`

**Interfaces:**
- Consumes: `send_admin_message`, `SendError`, `window_open`, `clear_attention`, the models, `media_convert.ffmpeg_available`, `storage.read_bytes`.
- Produces (all under `/api/whatsapp/inbox`, JSON):
  - `GET /summary` → `{needs_attention:int, unread:int, voice_available:bool, push_configured:bool}`
  - `GET /conversations?filter=attention|unread|all&q=&offset=0` → `{conversations:[conv.to_dict()], has_more:bool}`
  - `GET /conversations/<id>/messages?before=<msg_id>` → `{conversation: conv.to_dict() + {window_open, customer}, messages:[...ascending], has_more}`, where `customer` is `{id, name, phone, status, balance, plan}` or null.
  - `POST /conversations/<id>/read`, `/resolve`, `/pause` → `{conversation}`
  - `POST /conversations/<id>/send`. JSON for `{type:'text'|'reaction'|'template', ...}`; multipart for `type=voice|sticker` with `file`. Returns 201 `{message}`. On `SendError` it returns `{error: code, msg: message}` with `http_status`.
  - `GET /media/<message_id>?variant=original|playback` → the bytes, with the correct mimetype and `Cache-Control: private, max-age=300`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_whatsapp_inbox_routes.py
import io
from datetime import datetime, timedelta

import pytest

import app as appmod
import storage
import whatsapp_inbox as wi
from tests.conftest import auth_headers
from tests.inbox_helpers import FakeResponse, setup_wa_tenant


@pytest.fixture
def two_tenants(app, client, monkeypatch, tmp_path):
    monkeypatch.setattr(storage, "UPLOAD_ROOT", str(tmp_path))
    monkeypatch.setattr(storage, "_backend", storage.LocalBackend())
    monkeypatch.setattr(wi.requests, "post", lambda url, **kw: FakeResponse(body={"messages": [{"id": "wamid.R1"}]}))
    a_hdr, a_tid = setup_wa_tenant(app, client, "Biz RA", "ra_admin", "PNID_RA")
    b_hdr, b_tid = setup_wa_tenant(app, client, "Biz RB", "rb_admin", "PNID_RB")
    with app.app_context():
        a1 = wi.upsert_conversation(appmod, a_tid, "96170000001", "Alpha")
        wi.record_inbound(appmod, a_tid, a1, wi.parse_inbound({"id": "wamid.A1", "type": "text", "text": {"body": "no internet"}}))
        wi.flag_attention(a1, "ai_failed")
        a2 = wi.upsert_conversation(appmod, a_tid, "96170000002", "Beta")
        img = wi.record_inbound(appmod, a_tid, a2, wi.parse_inbound({"id": "wamid.A2", "type": "image", "image": {"id": "M"}}))
        img.media_key = storage.save_bytes(b"IMGDATA", a_tid, "x.jpg", "image/jpeg")
        img.media_mime = "image/jpeg"; img.media_status = "stored"
        a2.unread_count = 0
        b1 = wi.upsert_conversation(appmod, b_tid, "96170000009", "Other")
        appmod.db.session.commit()
        ids = {"a1": a1.id, "a2": a2.id, "b1": b1.id, "img": img.id}
    return {"a": a_hdr, "b": b_hdr, **ids}


def test_summary_and_list_filters(client, two_tenants):
    r = client.get("/api/whatsapp/inbox/summary", headers=two_tenants["a"])
    assert r.status_code == 200 and r.get_json()["needs_attention"] == 1
    r = client.get("/api/whatsapp/inbox/conversations", headers=two_tenants["a"])  # default: attention
    assert [c["contact_name"] for c in r.get_json()["conversations"]] == ["Alpha"]
    r = client.get("/api/whatsapp/inbox/conversations?filter=all", headers=two_tenants["a"])
    assert {c["contact_name"] for c in r.get_json()["conversations"]} == {"Alpha", "Beta"}
    r = client.get("/api/whatsapp/inbox/conversations?filter=unread", headers=two_tenants["a"])
    assert [c["contact_name"] for c in r.get_json()["conversations"]] == ["Alpha"]
    r = client.get("/api/whatsapp/inbox/conversations?filter=all&q=beta", headers=two_tenants["a"])
    assert [c["contact_name"] for c in r.get_json()["conversations"]] == ["Beta"]
    r = client.get("/api/whatsapp/inbox/conversations?filter=all&q=0000002", headers=two_tenants["a"])
    assert [c["contact_name"] for c in r.get_json()["conversations"]] == ["Beta"]


def test_thread_read_resolve_pause(client, two_tenants):
    cid = two_tenants["a1"]
    r = client.get(f"/api/whatsapp/inbox/conversations/{cid}/messages", headers=two_tenants["a"])
    body = r.get_json()
    assert [m["text"] for m in body["messages"]] == ["no internet"]
    assert body["conversation"]["window_open"] is True and body["has_more"] is False
    assert client.post(f"/api/whatsapp/inbox/conversations/{cid}/read", headers=two_tenants["a"]).get_json()["conversation"]["unread_count"] == 0
    r = client.post(f"/api/whatsapp/inbox/conversations/{cid}/pause", headers=two_tenants["a"])
    assert r.get_json()["conversation"]["ai_paused"] is True
    r = client.post(f"/api/whatsapp/inbox/conversations/{cid}/resolve", headers=two_tenants["a"])
    conv = r.get_json()["conversation"]
    assert conv["ai_paused"] is False and conv["needs_attention"] is False


def test_send_text_json_and_errors(client, two_tenants, app):
    cid = two_tenants["a1"]
    r = client.post(f"/api/whatsapp/inbox/conversations/{cid}/send", headers=two_tenants["a"], json={"type": "text", "text": "hi 🙂"})
    assert r.status_code == 201 and r.get_json()["message"]["sender"] == "admin"
    r = client.post(f"/api/whatsapp/inbox/conversations/{cid}/send", headers=two_tenants["a"], json={"type": "text", "text": ""})
    assert r.status_code == 400 and r.get_json()["error"] == "empty"
    with app.app_context():
        c = appmod.db.session.get(appmod.WhatsAppConversation, cid)
        c.last_inbound_at = datetime.utcnow() - timedelta(hours=48)
        appmod.db.session.commit()
    r = client.post(f"/api/whatsapp/inbox/conversations/{cid}/send", headers=two_tenants["a"], json={"type": "text", "text": "late"})
    assert r.status_code == 409 and r.get_json()["error"] == "window_closed"


def test_send_sticker_multipart(client, two_tenants, monkeypatch):
    from PIL import Image
    monkeypatch.setattr(wi.requests, "post", lambda url, **kw: FakeResponse(
        body={"id": "MEDIA_UP"} if url.endswith("/media") else {"messages": [{"id": "wamid.ST"}]}))
    buf = io.BytesIO(); Image.new("RGBA", (64, 64), (1, 2, 3, 255)).save(buf, "PNG"); buf.seek(0)
    r = client.post(f"/api/whatsapp/inbox/conversations/{two_tenants['a1']}/send", headers=two_tenants["a"],
                    data={"type": "sticker", "file": (buf, "s.png")}, content_type="multipart/form-data")
    assert r.status_code == 201 and r.get_json()["message"]["msg_type"] == "sticker"


def test_media_streams_with_tenant_check(client, two_tenants):
    r = client.get(f"/api/whatsapp/inbox/media/{two_tenants['img']}", headers=two_tenants["a"])
    assert r.status_code == 200 and r.data == b"IMGDATA" and r.mimetype == "image/jpeg"
    assert client.get(f"/api/whatsapp/inbox/media/{two_tenants['img']}", headers=two_tenants["b"]).status_code == 404


def test_tenant_isolation_and_roles(client, two_tenants):
    b_conv = two_tenants["b1"]
    for path in (f"/conversations/{b_conv}/messages",):
        assert client.get("/api/whatsapp/inbox" + path, headers=two_tenants["a"]).status_code == 404
    for action in ("read", "resolve", "pause"):
        assert client.post(f"/api/whatsapp/inbox/conversations/{b_conv}/{action}", headers=two_tenants["a"]).status_code == 404
    assert client.post(f"/api/whatsapp/inbox/conversations/{b_conv}/send", headers=two_tenants["a"],
                       json={"type": "text", "text": "x"}).status_code == 404
    collector = auth_headers(client, "rb_collector", role="collector")
    assert client.get("/api/whatsapp/inbox/summary", headers=collector).status_code == 403
    assert client.get("/api/whatsapp/inbox/summary").status_code == 401
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_whatsapp_inbox_routes.py -v`
Expected: FAIL with 404 on `/api/whatsapp/inbox/summary`.

- [ ] **Step 3: Implement `whatsapp_inbox_routes.py`**

```python
"""Admin API for the WhatsApp inbox -- see
docs/superpowers/specs/2026-09-23-whatsapp-inbox-design.md. Registered from
app.py via register_inbox_routes(app, appmod) so app.py doesn't grow further."""
from functools import wraps

from flask import jsonify, request, Response
from flask_jwt_extended import verify_jwt_in_request, get_jwt_identity

import media_convert
import storage
import whatsapp_inbox as wi
from tenancy import tenant_query

PAGE_SIZE = 30
THREAD_PAGE = 50


def register_inbox_routes(app, appmod):
    db = appmod.db
    Conv = appmod.WhatsAppConversation
    Msg = appmod.WhatsAppMessage

    def inbox_admin(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            verify_jwt_in_request()
            if 'admin' not in appmod._jwt_roles():
                return jsonify(msg="Admins only!"), 403
            return fn(*args, **kwargs)
        return wrapper

    def _conv_or_404(conv_id):
        return tenant_query(Conv).filter_by(id=conv_id).first_or_404()

    def _customer_summary(conv):
        c = conv.customer
        if not c:
            return None
        plan = getattr(c, 'subscription_plan', None)
        return {'id': c.id, 'name': c.name, 'phone': c.phone,
                'status': 'active' if c.is_subscription_active else 'inactive',
                'balance': c.balance, 'plan': getattr(plan, 'name', None),
                'upstream_status': c.upstream_last_status}

    def _conv_payload(conv):
        d = conv.to_dict()
        d['window_open'] = wi.window_open(conv)
        return d

    @app.route('/api/whatsapp/inbox/summary', methods=['GET'])
    @inbox_admin
    def inbox_summary():
        base = tenant_query(Conv)
        return jsonify({
            'needs_attention': base.filter(Conv.needs_attention.is_(True)).count(),
            'unread': base.filter(Conv.unread_count > 0).count(),
            'voice_available': media_convert.ffmpeg_available(),
            'push_configured': bool(appmod.VAPID_PUBLIC_KEY and appmod.VAPID_PRIVATE_KEY),
        })

    @app.route('/api/whatsapp/inbox/conversations', methods=['GET'])
    @inbox_admin
    def inbox_conversations():
        flt = request.args.get('filter', 'attention')
        q = tenant_query(Conv)
        if flt == 'attention':
            q = q.filter(Conv.needs_attention.is_(True))
        elif flt == 'unread':
            q = q.filter(Conv.unread_count > 0)
        term = (request.args.get('q') or '').strip()
        if term:
            like = f"%{term}%"
            q = q.outerjoin(appmod.Customer, Conv.customer_id == appmod.Customer.id).filter(
                db.or_(Conv.contact_name.ilike(like), Conv.wa_phone.like(f"%{wi.digits(term) or term}%"),
                       appmod.Customer.name.ilike(like)))
        offset = max(int(request.args.get('offset', 0) or 0), 0)
        rows = q.order_by(Conv.last_message_at.desc()).offset(offset).limit(PAGE_SIZE + 1).all()
        return jsonify({'conversations': [c.to_dict() for c in rows[:PAGE_SIZE]], 'has_more': len(rows) > PAGE_SIZE})

    @app.route('/api/whatsapp/inbox/conversations/<int:conv_id>/messages', methods=['GET'])
    @inbox_admin
    def inbox_messages(conv_id):
        conv = _conv_or_404(conv_id)
        q = tenant_query(Msg).filter_by(conversation_id=conv.id)
        before = request.args.get('before', type=int)
        if before:
            q = q.filter(Msg.id < before)
        rows = q.order_by(Msg.id.desc()).limit(THREAD_PAGE + 1).all()
        page = list(reversed(rows[:THREAD_PAGE]))
        payload = _conv_payload(conv)
        payload['customer'] = _customer_summary(conv)
        return jsonify({'conversation': payload, 'messages': [m.to_dict() for m in page],
                        'has_more': len(rows) > THREAD_PAGE})

    @app.route('/api/whatsapp/inbox/conversations/<int:conv_id>/read', methods=['POST'])
    @inbox_admin
    def inbox_read(conv_id):
        conv = _conv_or_404(conv_id)
        conv.unread_count = 0
        db.session.commit()
        return jsonify({'conversation': _conv_payload(conv)})

    @app.route('/api/whatsapp/inbox/conversations/<int:conv_id>/resolve', methods=['POST'])
    @inbox_admin
    def inbox_resolve(conv_id):
        conv = _conv_or_404(conv_id)
        wi.clear_attention(conv)
        conv.ai_paused = False
        conv.ai_paused_at = None
        db.session.commit()
        return jsonify({'conversation': _conv_payload(conv)})

    @app.route('/api/whatsapp/inbox/conversations/<int:conv_id>/pause', methods=['POST'])
    @inbox_admin
    def inbox_pause(conv_id):
        from datetime import datetime
        conv = _conv_or_404(conv_id)
        conv.ai_paused = True
        conv.ai_paused_at = datetime.utcnow()
        conv.last_admin_reply_at = conv.ai_paused_at  # auto-resume clock starts now
        db.session.commit()
        return jsonify({'conversation': _conv_payload(conv)})

    @app.route('/api/whatsapp/inbox/conversations/<int:conv_id>/send', methods=['POST'])
    @inbox_admin
    def inbox_send(conv_id):
        conv = _conv_or_404(conv_id)
        user = appmod.User.query.filter_by(username=get_jwt_identity()).first()
        if request.files:
            kind = request.form.get('type')
            upload = request.files.get('file')
            kwargs = {'file_bytes': upload.read() if upload else None}
        else:
            data = request.get_json(silent=True) or {}
            kind = data.get('type')
            kwargs = {'text': data.get('text'), 'reply_to': data.get('reply_to'),
                      'target': data.get('target'), 'emoji': data.get('emoji'),
                      'template_name': data.get('template_name'), 'body_params': data.get('body_params'),
                      'header_param': data.get('header_param')}
        try:
            msg = wi.send_admin_message(appmod, conv, user.id if user else None, kind, **kwargs)
        except wi.SendError as e:
            return jsonify({'error': e.code, 'msg': e.message}), e.http_status
        return jsonify({'message': msg.to_dict()}), 201

    @app.route('/api/whatsapp/inbox/media/<int:message_id>', methods=['GET'])
    @inbox_admin
    def inbox_media(message_id):
        msg = tenant_query(Msg).filter_by(id=message_id).first_or_404()
        use_playback = request.args.get('variant') == 'playback' and msg.media_playback_key
        key = msg.media_playback_key if use_playback else msg.media_key
        if not key:
            return jsonify({'error': 'media_unavailable'}), 404
        try:
            data = storage.read_bytes(key)
        except FileNotFoundError:
            return jsonify({'error': 'media_unavailable'}), 404
        mime = 'audio/mpeg' if use_playback else (msg.media_mime or 'application/octet-stream')
        resp = Response(data, mimetype=mime)
        resp.headers['Cache-Control'] = 'private, max-age=300'
        return resp
```

Check that `Customer` has a `subscription_plan` relationship (`grep -n "subscription_plan = db.relationship" app.py`). The `getattr` above makes that safe either way.

- [ ] **Step 4: Register the routes in `app.py`**

Directly after the end of the `whatsapp_webhook` function (after its `return jsonify({'status': 'ok'}), 200`), add:

```python
import whatsapp_inbox_routes
whatsapp_inbox_routes.register_inbox_routes(app, sys.modules[__name__])
```

- [ ] **Step 5: Run the tests**

Run: `python -m pytest tests/test_whatsapp_inbox_routes.py -v`, then `python -m pytest tests/ -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add whatsapp_inbox_routes.py app.py tests/test_whatsapp_inbox_routes.py
git commit -m "Add WhatsApp inbox admin API: list, thread, read/resolve/pause, send, media"
```

---

### Task 10: PWA manifest + service worker

**Files:**
- Create: `frontend/public/manifest.json`
- Modify: `frontend/public/index.html`, `frontend/public/service-worker.js`

**Interfaces:**
- Produces: when a notification is clicked, the SW posts `{type: 'open-url', url, conversationId}` to an existing window, or opens `url` if there is none. Push payload fields used: `title`, `body`, `tag`, `url`, `conversation_id`.

- [ ] **Step 1: Create `frontend/public/manifest.json`**

```json
{
  "short_name": "servicesBills",
  "name": "servicesBills",
  "icons": [
    { "src": "favicon.ico", "sizes": "64x64 32x32 24x24 16x16", "type": "image/x-icon" },
    { "src": "logo192.png", "type": "image/png", "sizes": "192x192", "purpose": "any maskable" },
    { "src": "logo512.png", "type": "image/png", "sizes": "512x512", "purpose": "any maskable" }
  ],
  "start_url": "/",
  "scope": "/",
  "display": "standalone",
  "theme_color": "#1a1f3a",
  "background_color": "#ffffff"
}
```

- [ ] **Step 2: Add iOS PWA meta to `frontend/public/index.html`**

Next to the existing `<link rel="manifest" ...>` line, add:

```html
    <meta name="apple-mobile-web-app-capable" content="yes" />
    <meta name="mobile-web-app-capable" content="yes" />
    <meta name="apple-mobile-web-app-title" content="servicesBills" />
    <link rel="apple-touch-icon" href="%PUBLIC_URL%/logo192.png" />
```

Leave in place any existing `apple-touch-icon` or `theme-color` tag; don't add a duplicate.

- [ ] **Step 3: Update `frontend/public/service-worker.js`**

Replace the `push` and `notificationclick` listeners with:

```js
self.addEventListener('push', function(event) {
  if (!event.data) return;
  let data;
  try { data = event.data.json(); } catch (e) { data = { title: 'servicesBills', body: event.data.text() }; }
  const options = {
    body: data.body,
    icon: '/logo192.png',
    badge: '/logo192.png',
    vibrate: [100, 50, 100],
    data: {
      dateOfArrival: Date.now(),
      url: data.url || '/',
      conversationId: data.conversation_id || null
    }
  };
  // Same tag -> the new notification replaces the old one (one per WhatsApp
  // conversation) and renotify makes it buzz again instead of updating silently.
  if (data.tag) {
    options.tag = data.tag;
    options.renotify = true;
  }
  event.waitUntil(self.registration.showNotification(data.title || 'servicesBills', options));
});

self.addEventListener('notificationclick', function(event) {
  event.notification.close();
  const data = event.notification.data || {};
  const urlToOpen = new URL(data.url || '/', self.location.origin).href;

  event.waitUntil(
    clients.matchAll({ type: 'window', includeUncontrolled: true }).then((windowClients) => {
      // Reuse any open window of this app: tell it where to go (App.js listens)
      // instead of requiring an exact URL match like before.
      const client = windowClients.find(c => new URL(c.url).origin === self.location.origin);
      if (client) {
        client.postMessage({ type: 'open-url', url: urlToOpen, conversationId: data.conversationId || null });
        return client.focus();
      }
      return clients.openWindow(urlToOpen);
    })
  );
});
```

- [ ] **Step 4: Verify the build**

Run: `npm --prefix frontend run build`
Expected: `Compiled successfully` (or only pre-existing warnings), and `frontend/build/manifest.json` exists.

- [ ] **Step 5: Commit**

```bash
git add frontend/public/manifest.json frontend/public/index.html frontend/public/service-worker.js
git commit -m "Add PWA manifest; service worker tags notifications and routes clicks to the open app"
```

---

### Task 11: Frontend inbox (read side): API wrappers, pure helpers, list + thread, Messaging tab, nav badge, deep link

**Files:**
- Modify: `frontend/src/context/AppContext.js`
- Create: `frontend/src/components/inbox/inboxFormat.js`, `frontend/src/components/inbox/inboxFormat.test.js`
- Create: `frontend/src/components/inbox/InboxView.js`, `ConversationList.js`, `ChatThread.js`, `InboxMedia.js`
- Modify: `frontend/src/components/MessagingView.js`, `frontend/src/App.js`

**Interfaces:**
- Consumes: the Task 9 API and the Task 10 SW message.
- Produces:
  - `apiService` methods: `fetchInboxSummary`, `fetchInboxConversations(params)`, `fetchInboxMessages(id, before)`, `markInboxRead(id)`, `resolveInboxConversation(id)`, `pauseInboxConversation(id)`, `sendInboxMessage(id, payload)`, `sendInboxFile(id, type, file)`, `fetchInboxMedia(messageId, variant)`, `fetchPushTopics(endpoint)`, `setPushTopics(endpoint, topics)`, `unsubscribePush(endpoint)`, `sendTestPush(endpoint)`, `fetchVapidPublicKey()`, `pushSubscribe(subscription)`.
  - `inboxFormat` exports: `describeWindow(expiresAtStamp, nowMs) -> {open, label}`, `attachReactions(messages) -> messages` (reaction rows removed, `reactions: [{emoji, sender}]` added to their targets), `REASON_META` (`{label, color}` per reason), `templateParamCount(template) -> number`.
  - `<InboxView openConversationId={number|null} />`
  - Composer slot: in this task, `ChatThread` renders `children` below the thread. Task 12 passes `<Composer/>`.

- [ ] **Step 1: Add the API wrappers**

In `frontend/src/context/AppContext.js`, inside `rawApiService`, after `uploadWhatsAppTemplateSample`, add:

```js
    // WhatsApp Inbox (see docs/superpowers/specs/2026-09-23-whatsapp-inbox-design.md)
    fetchInboxSummary: () => api.get('/whatsapp/inbox/summary'),
    fetchInboxConversations: (params) => api.get('/whatsapp/inbox/conversations', { params }),
    fetchInboxMessages: (id, before) => api.get(`/whatsapp/inbox/conversations/${id}/messages`, { params: before ? { before } : {} }),
    markInboxRead: (id) => api.post(`/whatsapp/inbox/conversations/${id}/read`),
    resolveInboxConversation: (id) => api.post(`/whatsapp/inbox/conversations/${id}/resolve`),
    pauseInboxConversation: (id) => api.post(`/whatsapp/inbox/conversations/${id}/pause`),
    sendInboxMessage: (id, payload) => api.post(`/whatsapp/inbox/conversations/${id}/send`, payload),
    sendInboxFile: (id, type, file) => {
        const fd = new FormData();
        fd.append('type', type);
        fd.append('file', file);
        return api.post(`/whatsapp/inbox/conversations/${id}/send`, fd, { headers: { 'Content-Type': 'multipart/form-data' } });
    },
    fetchInboxMedia: (messageId, variant = 'original') => api.get(`/whatsapp/inbox/media/${messageId}`, { params: { variant }, responseType: 'blob' }),

    // Web push (per-device)
    fetchVapidPublicKey: () => api.get('/vapid-public-key'),
    pushSubscribe: (subscription) => api.post('/push-subscribe', { subscription }),
    fetchPushTopics: (endpoint) => api.get('/push-subscription/topics', { params: { endpoint } }),
    setPushTopics: (endpoint, topics) => api.put('/push-subscription/topics', { endpoint, topics }),
    unsubscribePush: (endpoint) => api.post('/push-unsubscribe', { endpoint }),
    sendTestPush: (endpoint) => api.post('/push-test', { endpoint }),
```

- [ ] **Step 2: Write the failing pure-helper tests**

```js
// frontend/src/components/inbox/inboxFormat.test.js
import { describeWindow, attachReactions, REASON_META, templateParamCount } from './inboxFormat';

const NOW = Date.parse('2026-09-23T12:00:00Z');

test('describeWindow open with hours and minutes', () => {
    expect(describeWindow('2026-09-23 17:12:00', NOW)).toEqual({ open: true, label: 'Window closes in 5h 12m' });
});

test('describeWindow under an hour', () => {
    expect(describeWindow('2026-09-23 12:30:00', NOW)).toEqual({ open: true, label: 'Window closes in 30m' });
});

test('describeWindow closed or missing', () => {
    expect(describeWindow('2026-09-23 11:59:00', NOW).open).toBe(false);
    expect(describeWindow(null, NOW)).toEqual({ open: false, label: 'No customer message yet — templates only' });
});

test('attachReactions folds reactions onto targets, latest per side wins, empty removes', () => {
    const msgs = [
        { id: 1, direction: 'out', msg_type: 'text', wa_message_id: 'w1' },
        { id: 2, direction: 'in', msg_type: 'reaction', reaction_target_wa_id: 'w1', reaction_emoji: '👍' },
        { id: 3, direction: 'in', msg_type: 'reaction', reaction_target_wa_id: 'w1', reaction_emoji: '❤️' },
        { id: 4, direction: 'in', msg_type: 'text', wa_message_id: 'w4' },
        { id: 5, direction: 'out', msg_type: 'reaction', reaction_target_wa_id: 'w4', reaction_emoji: '😂' },
        { id: 6, direction: 'out', msg_type: 'reaction', reaction_target_wa_id: 'w4', reaction_emoji: '' },
    ];
    const out = attachReactions(msgs);
    expect(out.map(m => m.id)).toEqual([1, 4]);
    expect(out[0].reactions).toEqual([{ emoji: '❤️', side: 'in' }]);
    expect(out[1].reactions).toEqual([]);
});

test('REASON_META covers every backend reason', () => {
    ['ai_failed', 'escalated', 'awaiting_admin', 'unknown_sender', 'ai_inactive', 'media_received', 'send_failed']
        .forEach(r => expect(REASON_META[r].label).toBeTruthy());
});

test('templateParamCount counts distinct BODY placeholders', () => {
    expect(templateParamCount({ components: [{ type: 'BODY', text: 'Hi {{1}}, your balance is {{2}} ({{1}})' }] })).toBe(2);
    expect(templateParamCount({ components: [{ type: 'HEADER', text: 'x' }] })).toBe(0);
    expect(templateParamCount({})).toBe(0);
});
```

Run: `set CI=true&& npm --prefix frontend test -- --watchAll=false inboxFormat` (PowerShell: `$env:CI='true'; npm --prefix frontend test -- --watchAll=false inboxFormat`)
Expected: FAIL with `Cannot find module './inboxFormat'`

- [ ] **Step 3: Implement `inboxFormat.js`**

```js
// frontend/src/components/inbox/inboxFormat.js
import { parseUtc } from '../formatStamp';

export const REASON_META = {
    ai_failed: { label: "AI couldn't answer", color: 'error' },
    escalated: { label: 'Escalated', color: 'error' },
    awaiting_admin: { label: 'Awaiting admin', color: 'warning' },
    unknown_sender: { label: 'Unknown sender', color: 'info' },
    ai_inactive: { label: 'AI off', color: 'default' },
    media_received: { label: 'Media received', color: 'info' },
    send_failed: { label: 'Send failed', color: 'error' },
};

export function describeWindow(expiresAtStamp, nowMs = Date.now()) {
    const expires = parseUtc(expiresAtStamp);
    if (Number.isNaN(expires)) return { open: false, label: 'No customer message yet — templates only' };
    const left = expires - nowMs;
    if (left <= 0) return { open: false, label: 'Window closed — send a template to reopen' };
    const totalMin = Math.floor(left / 60000);
    const h = Math.floor(totalMin / 60);
    const m = totalMin % 60;
    return { open: true, label: `Window closes in ${h > 0 ? `${h}h ${m}m` : `${m}m`}` };
}

/** Drop reaction rows and attach them to the message they target. The latest
 *  reaction per side ('in' customer / 'out' us) wins; an empty emoji removes it. */
export function attachReactions(messages) {
    const bySide = {};
    messages.filter(m => m.msg_type === 'reaction').forEach(r => {
        const key = r.reaction_target_wa_id;
        if (!key) return;
        bySide[key] = bySide[key] || {};
        bySide[key][r.direction] = r.reaction_emoji || '';
    });
    return messages.filter(m => m.msg_type !== 'reaction').map(m => {
        const sides = (m.wa_message_id && bySide[m.wa_message_id]) || {};
        const reactions = Object.entries(sides).filter(([, e]) => e).map(([side, emoji]) => ({ emoji, side }));
        return { ...m, reactions };
    });
}

export function templateParamCount(template) {
    const body = (template?.components || []).find(c => (c.type || '').toUpperCase() === 'BODY');
    if (!body?.text) return 0;
    return new Set(body.text.match(/\{\{\d+\}\}/g) || []).size;
}
```

Run the jest command from Step 2 again.
Expected: 6 passed.

- [ ] **Step 4: Create `InboxMedia.js`**

```js
// frontend/src/components/inbox/InboxMedia.js
import React, { useEffect, useState } from 'react';
import { Box, CircularProgress, Link, Typography, Dialog } from '@mui/material';
import { useAppContext } from '../../context/AppContext';

// Media needs the JWT header, so it's fetched as a blob and shown via an
// object URL (an <img src> straight at the API can't send Authorization).
const useMediaUrl = (messageId, variant, enabled) => {
    const { apiService } = useAppContext();
    const [url, setUrl] = useState(null);
    const [failed, setFailed] = useState(false);
    useEffect(() => {
        if (!enabled) return undefined;
        let objectUrl = null;
        let cancelled = false;
        apiService.fetchInboxMedia(messageId, variant)
            .then(res => { if (!cancelled) { objectUrl = URL.createObjectURL(res.data); setUrl(objectUrl); } })
            .catch(() => { if (!cancelled) setFailed(true); });
        return () => { cancelled = true; if (objectUrl) URL.revokeObjectURL(objectUrl); };
    }, [apiService, messageId, variant, enabled]);
    return { url, failed };
};

const InboxMedia = ({ message }) => {
    const [zoom, setZoom] = useState(false);
    const stored = message.media_status === 'stored';
    const variant = message.msg_type === 'audio' && message.has_playback ? 'playback' : 'original';
    const { url, failed } = useMediaUrl(message.id, variant, stored);

    if (message.media_status === 'pending') return <Typography variant="caption" color="text.secondary">Downloading media…</Typography>;
    if (!stored || failed) return <Typography variant="caption" color="text.secondary">Media unavailable</Typography>;
    if (!url) return <CircularProgress size={18} />;

    switch (message.msg_type) {
        case 'audio':
            return <audio controls src={url} style={{ maxWidth: 260 }} />;
        case 'image':
            return (
                <>
                    <Box component="img" src={url} alt="" onClick={() => setZoom(true)}
                        sx={{ maxWidth: 240, maxHeight: 240, borderRadius: 2, cursor: 'zoom-in', display: 'block' }} />
                    <Dialog open={zoom} onClose={() => setZoom(false)} maxWidth="lg">
                        <Box component="img" src={url} alt="" sx={{ maxWidth: '90vw', maxHeight: '90vh' }} />
                    </Dialog>
                </>
            );
        case 'sticker':
            return <Box component="img" src={url} alt="sticker" sx={{ width: 128, height: 128, display: 'block' }} />;
        case 'video':
            return <video controls src={url} style={{ maxWidth: 260, borderRadius: 8 }} />;
        default:
            return <Link href={url} download>Download {message.text || 'file'}</Link>;
    }
};

export default InboxMedia;
```

- [ ] **Step 5: Create `ConversationList.js`**

```js
// frontend/src/components/inbox/ConversationList.js
import React from 'react';
import {
    Box, List, ListItemButton, ListItemText, Typography, Chip, Badge, TextField,
    ToggleButtonGroup, ToggleButton, Stack, InputAdornment
} from '@mui/material';
import { Search as SearchIcon } from '@mui/icons-material';
import { REASON_META } from './inboxFormat';
import { describeAge } from '../NetworkTreeView.describeAge';

const ConversationList = ({ conversations, selectedId, onSelect, filter, onFilterChange, search, onSearchChange }) => (
    <Box sx={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
        <Stack spacing={1.5} sx={{ p: 2, borderBottom: 1, borderColor: 'divider' }}>
            <ToggleButtonGroup size="small" exclusive fullWidth value={filter} onChange={(e, v) => v && onFilterChange(v)}>
                <ToggleButton value="attention">Needs attention</ToggleButton>
                <ToggleButton value="unread">Unread</ToggleButton>
                <ToggleButton value="all">All</ToggleButton>
            </ToggleButtonGroup>
            <TextField size="small" placeholder="Search name or phone" value={search}
                onChange={e => onSearchChange(e.target.value)}
                InputProps={{ startAdornment: <InputAdornment position="start"><SearchIcon fontSize="small" /></InputAdornment> }} />
        </Stack>
        <List sx={{ flex: 1, overflowY: 'auto', py: 0 }}>
            {conversations.length === 0 && (
                <Typography sx={{ p: 3 }} color="text.secondary" align="center">
                    {filter === 'attention' ? 'Nothing needs attention 🎉' : 'No conversations'}
                </Typography>
            )}
            {conversations.map(c => (
                <ListItemButton key={c.id} selected={c.id === selectedId} onClick={() => onSelect(c.id)}
                    sx={{ borderBottom: 1, borderColor: 'divider', alignItems: 'flex-start' }}>
                    <ListItemText
                        primary={
                            <Stack direction="row" justifyContent="space-between" alignItems="center" spacing={1}>
                                <Typography fontWeight={c.unread_count ? 800 : 600} noWrap>
                                    {c.customer_name || c.contact_name || `+${c.wa_phone}`}
                                </Typography>
                                <Typography variant="caption" color="text.secondary" sx={{ flexShrink: 0 }}>
                                    {describeAge(c.last_message_at)}
                                </Typography>
                            </Stack>
                        }
                        secondary={
                            <Box component="span" sx={{ display: 'block' }}>
                                <Stack direction="row" alignItems="center" spacing={1} component="span">
                                    <Typography component="span" variant="body2" color="text.secondary" noWrap sx={{ flex: 1 }}>
                                        {c.last_message_preview}
                                    </Typography>
                                    {c.unread_count > 0 && <Badge color="primary" badgeContent={c.unread_count} sx={{ mr: 1 }} />}
                                </Stack>
                                <Stack direction="row" spacing={0.5} component="span" sx={{ mt: 0.5, display: 'flex' }}>
                                    {c.needs_attention && c.attention_reason && (
                                        <Chip size="small" color={REASON_META[c.attention_reason]?.color || 'default'}
                                            label={REASON_META[c.attention_reason]?.label || c.attention_reason} />
                                    )}
                                    {c.ai_paused && <Chip size="small" variant="outlined" label="AI paused" />}
                                </Stack>
                            </Box>
                        }
                    />
                </ListItemButton>
            ))}
        </List>
    </Box>
);

export default ConversationList;
```

Before writing this file, open `frontend/src/components/NetworkTreeView.describeAge.js` (it exists; it has a test file) and confirm its export name and argument format. If it isn't a named `describeAge(stamp)` export taking a UTC API stamp, use `formatStamp` from `../formatStamp` instead.

- [ ] **Step 6: Create `ChatThread.js`**

```js
// frontend/src/components/inbox/ChatThread.js
import React, { useEffect, useRef } from 'react';
import {
    Box, Stack, Typography, Button, Chip, IconButton, Tooltip, Paper, Collapse
} from '@mui/material';
import {
    ArrowBack as BackIcon, Done as SentIcon, DoneAll as DeliveredIcon, ErrorOutline as FailedIcon,
    SmartToy as AiIcon, Reply as ReplyIcon, AddReaction as ReactIcon, CheckCircle as ResolveIcon,
    PauseCircle as PauseIcon
} from '@mui/icons-material';
import InboxMedia from './InboxMedia';
import { REASON_META } from './inboxFormat';
import { formatStamp } from '../formatStamp';

const BUBBLE = {
    customer: { align: 'flex-start', bg: 'background.paper' },
    ai: { align: 'flex-end', bg: '#e3f2fd' },
    admin: { align: 'flex-end', bg: '#dcf8c6' },
    system: { align: 'flex-end', bg: '#f1f1f1' },
};

const StatusTick = ({ m }) => {
    if (m.direction !== 'out') return null;
    if (m.status === 'failed') return <Tooltip title={m.error_message || `Error ${m.error_code}`}><FailedIcon color="error" sx={{ fontSize: 14 }} /></Tooltip>;
    if (m.status === 'read') return <DeliveredIcon color="primary" sx={{ fontSize: 14 }} />;
    if (m.status === 'delivered') return <DeliveredIcon sx={{ fontSize: 14, color: 'text.secondary' }} />;
    return <SentIcon sx={{ fontSize: 14, color: 'text.secondary' }} />;
};

const Bubble = ({ m, byWamid, onReply, onReact }) => {
    const [showTranscript, setShowTranscript] = React.useState(false);
    const style = BUBBLE[m.sender] || BUBBLE.customer;
    const quoted = m.reply_to_wa_message_id ? byWamid[m.reply_to_wa_message_id] : null;
    const isSticker = m.msg_type === 'sticker';
    const canInteract = m.direction === 'in' && m.wa_message_id;
    return (
        <Box sx={{ display: 'flex', justifyContent: style.align, mb: 1, '&:hover .bubble-actions': { opacity: 1 } }}>
            <Box sx={{ maxWidth: '75%' }}>
                <Paper elevation={0} sx={{ p: isSticker ? 0 : 1.25, bgcolor: isSticker ? 'transparent' : style.bg, borderRadius: 2, border: isSticker ? 0 : 1, borderColor: 'divider' }}>
                    {m.sender !== 'customer' && (
                        <Typography variant="caption" color="text.secondary" sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
                            {m.sender === 'ai' ? <><AiIcon sx={{ fontSize: 14 }} /> AI</> : m.sender === 'admin' ? (m.sent_by || 'Admin') : 'Auto-reply'}
                        </Typography>
                    )}
                    {quoted && (
                        <Box sx={{ borderLeft: 3, borderColor: 'primary.main', pl: 1, mb: 0.5, opacity: 0.8 }}>
                            <Typography variant="caption" noWrap display="block">{quoted.text || quoted.msg_type}</Typography>
                        </Box>
                    )}
                    {m.media_status !== 'none' && <InboxMedia message={m} />}
                    {m.text && m.msg_type !== 'sticker' && (
                        <Typography variant="body2" sx={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
                            {m.msg_type === 'location'
                                ? <a href={`https://maps.google.com/?q=${encodeURIComponent(m.text.split(' ')[0])}`} target="_blank" rel="noreferrer">📍 {m.text}</a>
                                : m.text}
                        </Typography>
                    )}
                    {m.transcript && (
                        <>
                            <Button size="small" onClick={() => setShowTranscript(s => !s)} sx={{ p: 0, minWidth: 0, textTransform: 'none' }}>
                                {showTranscript ? 'Hide transcript' : 'Show transcript'}
                            </Button>
                            <Collapse in={showTranscript}><Typography variant="body2" color="text.secondary" dir="auto">{m.transcript}</Typography></Collapse>
                        </>
                    )}
                    <Stack direction="row" spacing={0.5} alignItems="center" justifyContent="flex-end">
                        <Typography variant="caption" color="text.secondary">{formatStamp(m.created_at)}</Typography>
                        <StatusTick m={m} />
                    </Stack>
                </Paper>
                <Stack direction="row" spacing={0.5} justifyContent={style.align}>
                    {m.reactions?.map(r => <Chip key={r.side} size="small" label={r.emoji} sx={{ mt: -1, height: 22 }} />)}
                    {canInteract && (
                        <Box className="bubble-actions" sx={{ opacity: { xs: 1, md: 0 }, transition: 'opacity .15s' }}>
                            <IconButton size="small" onClick={() => onReply(m)}><ReplyIcon sx={{ fontSize: 16 }} /></IconButton>
                            <IconButton size="small" onClick={(e) => onReact(m, e.currentTarget)}><ReactIcon sx={{ fontSize: 16 }} /></IconButton>
                        </Box>
                    )}
                </Stack>
            </Box>
        </Box>
    );
};

const ChatThread = ({ conversation, messages, hasMore, onLoadOlder, onBack, onResolve, onPause, onReply, onReact, children }) => {
    const bottomRef = useRef(null);
    const lastId = messages.length ? messages[messages.length - 1].id : null;
    useEffect(() => { bottomRef.current?.scrollIntoView({ block: 'end' }); }, [lastId]);
    const byWamid = Object.fromEntries(messages.filter(m => m.wa_message_id).map(m => [m.wa_message_id, m]));
    const cust = conversation.customer;
    return (
        <Box sx={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
            <Stack direction="row" alignItems="center" spacing={1} sx={{ p: 1.5, borderBottom: 1, borderColor: 'divider' }}>
                {onBack && <IconButton onClick={onBack}><BackIcon /></IconButton>}
                <Box sx={{ flex: 1, minWidth: 0 }}>
                    <Typography fontWeight={800} noWrap>{cust?.name || conversation.contact_name || `+${conversation.wa_phone}`}</Typography>
                    <Typography variant="caption" color="text.secondary" noWrap display="block">
                        +{conversation.wa_phone}{cust ? ` · ${cust.plan || ''} · ${cust.status} · balance ${cust.balance}` : ' · not linked to a customer'}
                    </Typography>
                </Box>
                {conversation.needs_attention && conversation.attention_reason && (
                    <Chip size="small" color={REASON_META[conversation.attention_reason]?.color} label={REASON_META[conversation.attention_reason]?.label} />
                )}
                {!conversation.ai_paused && <Button size="small" startIcon={<PauseIcon />} onClick={onPause}>Pause AI</Button>}
                {(conversation.needs_attention || conversation.ai_paused) && (
                    <Button size="small" variant="contained" startIcon={<ResolveIcon />} onClick={onResolve}>Resolve</Button>
                )}
            </Stack>
            <Box sx={{ flex: 1, overflowY: 'auto', p: 2, bgcolor: '#efeae2' }}>
                {hasMore && <Box sx={{ textAlign: 'center', mb: 1 }}><Button size="small" onClick={onLoadOlder}>Load older</Button></Box>}
                {messages.map(m => <Bubble key={m.id} m={m} byWamid={byWamid} onReply={onReply} onReact={onReact} />)}
                <div ref={bottomRef} />
            </Box>
            {conversation.ai_paused && (
                <Typography variant="caption" sx={{ px: 2, py: 0.5, bgcolor: 'warning.light' }}>
                    AI is paused for this chat — press Resolve to hand it back to the AI.
                </Typography>
            )}
            {children}
        </Box>
    );
};

export default ChatThread;
```

- [ ] **Step 7: Create `InboxView.js`**

```js
// frontend/src/components/inbox/InboxView.js
import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Box, Paper, Typography, useMediaQuery, useTheme, Stack } from '@mui/material';
import { useAppContext } from '../../context/AppContext';
import ConversationList from './ConversationList';
import ChatThread from './ChatThread';
import { attachReactions } from './inboxFormat';

const LIST_POLL_MS = 20000;
const THREAD_POLL_MS = 5000;

const InboxView = ({ openConversationId = null, renderComposer = null, headerExtra = null }) => {
    const { apiService, setSnackbar } = useAppContext();
    const theme = useTheme();
    const isMobile = useMediaQuery(theme.breakpoints.down('md'));
    const [filter, setFilter] = useState(openConversationId ? 'all' : 'attention');
    const [search, setSearch] = useState('');
    const [conversations, setConversations] = useState([]);
    const [selectedId, setSelectedId] = useState(openConversationId);
    const [thread, setThread] = useState(null); // { conversation, messages, has_more }
    const [replyTo, setReplyTo] = useState(null);
    const [reactTarget, setReactTarget] = useState(null); // { message, anchorEl }
    const selectedRef = useRef(selectedId);
    selectedRef.current = selectedId;

    useEffect(() => { if (openConversationId) { setSelectedId(openConversationId); setFilter('all'); } }, [openConversationId]);

    const loadList = useCallback(async () => {
        try {
            const res = await apiService.fetchInboxConversations({ filter, q: search || undefined });
            setConversations(res.data.conversations);
        } catch (e) { /* polling: stay quiet */ }
    }, [apiService, filter, search]);

    const loadThread = useCallback(async (id, { markRead = false } = {}) => {
        if (!id) return;
        try {
            const res = await apiService.fetchInboxMessages(id);
            if (selectedRef.current !== id) return;
            setThread(res.data);
            if (markRead && res.data.conversation.unread_count > 0) {
                await apiService.markInboxRead(id);
                loadList();
            }
        } catch (e) {
            if (e.response?.status === 404) { setSelectedId(null); setThread(null); }
        }
    }, [apiService, loadList]);

    useEffect(() => {
        const t = setTimeout(loadList, search ? 300 : 0); // debounce typing
        const i = setInterval(loadList, LIST_POLL_MS);
        return () => { clearTimeout(t); clearInterval(i); };
    }, [loadList, search]);

    useEffect(() => {
        setThread(null); setReplyTo(null);
        if (!selectedId) return undefined;
        loadThread(selectedId, { markRead: true });
        const i = setInterval(() => loadThread(selectedId, { markRead: true }), THREAD_POLL_MS);
        return () => clearInterval(i);
    }, [selectedId, loadThread]);

    const act = async (fn, okMsg) => {
        try {
            await fn();
            if (okMsg) setSnackbar({ open: true, message: okMsg, severity: 'success' });
            await Promise.all([loadThread(selectedId), loadList()]);
        } catch (e) {
            setSnackbar({ open: true, message: e.response?.data?.msg || 'Action failed', severity: 'error' });
        }
    };

    const refresh = useCallback(() => Promise.all([loadThread(selectedId), loadList()]), [loadThread, loadList, selectedId]);

    const listPane = (
        <ConversationList conversations={conversations} selectedId={selectedId} onSelect={setSelectedId}
            filter={filter} onFilterChange={setFilter} search={search} onSearchChange={setSearch} />
    );
    const threadPane = thread ? (
        <ChatThread
            conversation={thread.conversation}
            messages={attachReactions(thread.messages)}
            hasMore={thread.has_more}
            onLoadOlder={async () => {
                const oldest = thread.messages[0]?.id;
                const res = await apiService.fetchInboxMessages(selectedId, oldest);
                setThread(t => ({ ...t, messages: [...res.data.messages, ...t.messages], has_more: res.data.has_more }));
            }}
            onBack={isMobile ? () => setSelectedId(null) : null}
            onResolve={() => act(() => apiService.resolveInboxConversation(selectedId), 'Handed back to the AI')}
            onPause={() => act(() => apiService.pauseInboxConversation(selectedId), 'AI paused for this chat')}
            onReply={setReplyTo}
            onReact={(message, anchorEl) => setReactTarget({ message, anchorEl })}
        >
            {renderComposer && renderComposer({
                conversation: thread.conversation, replyTo, clearReply: () => setReplyTo(null),
                reactTarget, clearReactTarget: () => setReactTarget(null), onSent: refresh,
            })}
        </ChatThread>
    ) : (
        <Box sx={{ height: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
            <Typography color="text.secondary">{selectedId ? 'Loading…' : 'Select a conversation'}</Typography>
        </Box>
    );

    return (
        <Box>
            {headerExtra && <Stack direction="row" justifyContent="flex-end" sx={{ px: 2, pt: 2 }}>{headerExtra}</Stack>}
            <Paper elevation={0} sx={{ m: 2, border: 1, borderColor: 'divider', borderRadius: 3, overflow: 'hidden', height: { xs: 'calc(100vh - 220px)', md: 640 } }}>
                {isMobile ? (selectedId ? threadPane : listPane) : (
                    <Box sx={{ display: 'grid', gridTemplateColumns: '340px 1fr', height: '100%' }}>
                        <Box sx={{ borderRight: 1, borderColor: 'divider', minHeight: 0 }}>{listPane}</Box>
                        <Box sx={{ minHeight: 0 }}>{threadPane}</Box>
                    </Box>
                )}
            </Paper>
        </Box>
    );
};

export default InboxView;
```

- [ ] **Step 8: Wire `MessagingView.js`**

Add the import:

```js
import InboxView from './inbox/InboxView';
```

Change the component signature and the tab state:

```js
const MessagingView = ({ openConversationId = null }) => {
    const { apiService, setSnackbar } = useAppContext();
    const [loading, setLoading] = useState(false);
    const [activeTab, setActiveTab] = useState(0);
    useEffect(() => { if (openConversationId) setActiveTab(0); }, [openConversationId]);
```

Replace the two `<Tab .../>` lines with three:

```js
                    <Tab label="Inbox" sx={{ fontWeight: 700, fontSize: '0.95rem' }} />
                    <Tab label="Customer Notifications" sx={{ fontWeight: 700, fontSize: '0.95rem' }} />
                    <Tab label="Marketing Campaign (Custom / Non-Customers)" icon={<WhatsAppIcon sx={{ fontSize: 18 }} />} iconPosition="start" sx={{ fontWeight: 700, fontSize: '0.95rem' }} />
```

Wrap the existing `<Box sx={{ p: 4 }}> ... </Box>` (the one right after `</Tabs>`) like this, and inside it change `activeTab === 0 ?` to `activeTab === 1 ?`:

```js
                {activeTab === 0 ? (
                    <InboxView openConversationId={openConversationId} />
                ) : (
                <Box sx={{ p: 4 }}>
                    {activeTab === 1 ? (
                        /* ...existing Customer Notifications content, unchanged... */
                    ) : (
                        /* ...existing Marketing Campaign content, unchanged... */
                    )}
                </Box>
                )}
```

Task 12 replaces `<InboxView openConversationId={openConversationId} />` with the composer-enabled version, and Task 13 adds `headerExtra`.

- [ ] **Step 9: Wire `App.js` (badge + deep link + SW message)**

Add `Badge` to the `@mui/material` import list.

Inside `MainApp`, after the `const [drawerOpen, ...]` line, add:

```js
    const [inboxOpenId, setInboxOpenId] = useState(() => {
        const v = new URLSearchParams(window.location.search).get('inbox');
        return v ? Number(v) : null;
    });
    const [inboxAttention, setInboxAttention] = useState(0);

    useEffect(() => {
        if (!hasRole('admin')) return undefined;
        let cancelled = false;
        const poll = () => apiService.fetchInboxSummary()
            .then(r => { if (!cancelled) setInboxAttention(r.data.needs_attention || 0); })
            .catch(() => {});
        poll();
        const i = setInterval(poll, 20000);
        return () => { cancelled = true; clearInterval(i); };
    }, []); // eslint-disable-line react-hooks/exhaustive-deps

    useEffect(() => {
        if (!('serviceWorker' in navigator)) return undefined;
        const onMessage = (event) => {
            if (event.data?.type !== 'open-url') return;
            const url = new URL(event.data.url, window.location.origin);
            const view = url.searchParams.get('view');
            if (view) setCurrentView(view);
            if (event.data.conversationId) setInboxOpenId(Number(event.data.conversationId));
            window.history.replaceState(null, '', url.pathname + url.search);
        };
        navigator.serviceWorker.addEventListener('message', onMessage);
        return () => navigator.serviceWorker.removeEventListener('message', onMessage);
    }, []);
```

If `apiService` isn't already in scope in `MainApp`, add `import { apiService } from './context/AppContext';`. `AppContext.js` exports `apiService` as a named export, so check the existing imports at the top of `App.js` first.

Change the messaging case in `renderView`:

```js
            case 'messaging': return hasRole('admin') ? <MessagingView openConversationId={inboxOpenId} /> : <Typography>Access Denied</Typography>;
```

In `DesktopNav`, change `{item.label}` to:

```js
                    {item.key === 'messaging' && inboxAttention > 0
                        ? <Badge color="error" badgeContent={inboxAttention} sx={{ '& .MuiBadge-badge': { right: -10 } }}>{item.label}</Badge>
                        : item.label}
```

In `MobileDrawer`, change `primary={item.label}` to:

```js
                                                    primary={item.key === 'messaging' && inboxAttention > 0 ? `${item.label} (${inboxAttention})` : item.label}
```

- [ ] **Step 10: Build and run the jest tests**

Run: `$env:CI='true'; npm --prefix frontend test -- --watchAll=false inbox` then `npm --prefix frontend run build`
Expected: tests pass; the build compiles with no new errors. Fix any lint errors the build reports in the new files.

- [ ] **Step 11: Commit**

```bash
git add frontend/src/context/AppContext.js frontend/src/components/inbox frontend/src/components/MessagingView.js frontend/src/App.js
git commit -m "Add Messaging > Inbox: conversation list, chat thread, media playback, nav badge, deep link"
```

---

### Task 12: Composer: text + emoji, reply, react, sticker, voice, template

**Files:**
- Create: `frontend/src/components/inbox/Composer.js`, `frontend/src/components/inbox/VoiceRecorder.js`
- Modify: `frontend/src/components/MessagingView.js`: pass `renderComposer`
- Modify: `frontend/package.json`, via the install below

**Interfaces:**
- Consumes: the `renderComposer({conversation, replyTo, clearReply, reactTarget, clearReactTarget, onSent})` contract from Task 11, `describeWindow`, `templateParamCount`, and `apiService.sendInboxMessage`, `sendInboxFile`, `fetchWhatsAppTemplates`, `fetchInboxSummary`.

- [ ] **Step 1: Install the emoji picker**

Run: `npm --prefix frontend install emoji-picker-react@^4`
Expected: `package.json` and `package-lock.json` are updated.

- [ ] **Step 2: Create `VoiceRecorder.js`**

```js
// frontend/src/components/inbox/VoiceRecorder.js
import React, { useEffect, useRef, useState } from 'react';
import { IconButton, Stack, Typography, Tooltip } from '@mui/material';
import { Mic as MicIcon, Stop as StopIcon, Send as SendIcon, Delete as DeleteIcon } from '@mui/icons-material';

const MAX_SECONDS = 300;
const pickMime = () => {
    if (typeof MediaRecorder === 'undefined') return null;
    return ['audio/webm;codecs=opus', 'audio/ogg;codecs=opus', 'audio/mp4'].find(t => MediaRecorder.isTypeSupported(t)) || '';
};

const VoiceRecorder = ({ disabled, onSend, onError }) => {
    const [state, setState] = useState('idle'); // idle | recording | review
    const [seconds, setSeconds] = useState(0);
    const [blob, setBlob] = useState(null);
    const [previewUrl, setPreviewUrl] = useState(null);
    const recRef = useRef(null);
    const timerRef = useRef(null);

    useEffect(() => () => { clearInterval(timerRef.current); if (previewUrl) URL.revokeObjectURL(previewUrl); }, [previewUrl]);

    const start = async () => {
        const mime = pickMime();
        if (mime === null || !navigator.mediaDevices?.getUserMedia) { onError('This browser cannot record audio.'); return; }
        let stream;
        try { stream = await navigator.mediaDevices.getUserMedia({ audio: true }); }
        catch (e) { onError('Microphone permission was denied.'); return; }
        const chunks = [];
        const rec = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined);
        rec.ondataavailable = e => e.data.size && chunks.push(e.data);
        rec.onstop = () => {
            stream.getTracks().forEach(t => t.stop());
            const b = new Blob(chunks, { type: rec.mimeType || 'audio/webm' });
            setBlob(b); setPreviewUrl(URL.createObjectURL(b)); setState('review');
        };
        recRef.current = rec;
        rec.start();
        setSeconds(0); setState('recording');
        timerRef.current = setInterval(() => setSeconds(s => {
            if (s + 1 >= MAX_SECONDS) stop();
            return s + 1;
        }), 1000);
    };
    const stop = () => { clearInterval(timerRef.current); recRef.current?.state === 'recording' && recRef.current.stop(); };
    const discard = () => { if (previewUrl) URL.revokeObjectURL(previewUrl); setBlob(null); setPreviewUrl(null); setState('idle'); };
    const send = async () => {
        const ext = (blob.type.includes('mp4') ? 'm4a' : blob.type.includes('ogg') ? 'ogg' : 'webm');
        await onSend(new File([blob], `voice.${ext}`, { type: blob.type }));
        discard();
    };

    if (state === 'recording') return (
        <Stack direction="row" alignItems="center" spacing={1}>
            <Typography color="error" variant="body2">● {Math.floor(seconds / 60)}:{String(seconds % 60).padStart(2, '0')}</Typography>
            <IconButton color="error" onClick={stop}><StopIcon /></IconButton>
        </Stack>
    );
    if (state === 'review') return (
        <Stack direction="row" alignItems="center" spacing={1}>
            <audio controls src={previewUrl} style={{ height: 36, maxWidth: 220 }} />
            <IconButton onClick={discard}><DeleteIcon /></IconButton>
            <IconButton color="primary" onClick={send}><SendIcon /></IconButton>
        </Stack>
    );
    return (
        <Tooltip title={disabled ? 'Voice notes unavailable on this server' : 'Record voice note'}>
            <span><IconButton disabled={disabled} onClick={start}><MicIcon /></IconButton></span>
        </Tooltip>
    );
};

export default VoiceRecorder;
```

- [ ] **Step 3: Create `Composer.js`**

```js
// frontend/src/components/inbox/Composer.js
import React, { useEffect, useRef, useState } from 'react';
import {
    Box, Stack, TextField, IconButton, Popover, Chip, Typography, Tooltip, MenuItem, Button, Select, FormControl, InputLabel
} from '@mui/material';
import { Send as SendIcon, EmojiEmotions as EmojiIcon, Image as StickerIcon, Close as CloseIcon } from '@mui/icons-material';
import EmojiPicker from 'emoji-picker-react';
import { useAppContext } from '../../context/AppContext';
import VoiceRecorder from './VoiceRecorder';
import { describeWindow, templateParamCount } from './inboxFormat';

const QUICK_REACTIONS = ['👍', '❤️', '😂', '😮', '😢', '🙏'];

const TemplateSender = ({ conversationId, onSent, notify }) => {
    const { apiService } = useAppContext();
    const [templates, setTemplates] = useState([]);
    const [name, setName] = useState('');
    const [params, setParams] = useState([]);
    useEffect(() => {
        apiService.fetchWhatsAppTemplates()
            .then(r => setTemplates((r.data.templates || []).filter(t => (t.status || '').toUpperCase() === 'APPROVED')))
            .catch(() => {});
    }, [apiService]);
    const selected = templates.find(t => t.name === name);
    const count = templateParamCount(selected);
    useEffect(() => setParams(Array(count).fill('')), [name, count]);
    const send = async () => {
        try {
            await apiService.sendInboxMessage(conversationId, { type: 'template', template_name: name, body_params: params });
            setName(''); onSent();
        } catch (e) { notify(e.response?.data?.msg || 'Template send failed'); }
    };
    return (
        <Stack spacing={1}>
            <FormControl size="small" fullWidth>
                <InputLabel>Approved template</InputLabel>
                <Select label="Approved template" value={name} onChange={e => setName(e.target.value)}>
                    {templates.map(t => <MenuItem key={`${t.name}-${t.language}`} value={t.name}>{t.name} ({t.language})</MenuItem>)}
                </Select>
            </FormControl>
            {params.map((p, i) => (
                <TextField key={i} size="small" label={`{{${i + 1}}}`} value={p}
                    onChange={e => setParams(ps => ps.map((x, j) => (j === i ? e.target.value : x)))} />
            ))}
            <Button variant="contained" disabled={!name || params.some(p => !p.trim())} onClick={send}>Send template</Button>
        </Stack>
    );
};

const Composer = ({ conversation, replyTo, clearReply, reactTarget, clearReactTarget, onSent }) => {
    const { apiService, setSnackbar } = useAppContext();
    const [text, setText] = useState('');
    const [sending, setSending] = useState(false);
    const [emojiAnchor, setEmojiAnchor] = useState(null);
    const [fullReactPicker, setFullReactPicker] = useState(false);
    const [voiceAvailable, setVoiceAvailable] = useState(true);
    const [, forceTick] = useState(0);
    const fileRef = useRef(null);
    const notify = (message) => setSnackbar({ open: true, message, severity: 'error' });

    useEffect(() => { apiService.fetchInboxSummary().then(r => setVoiceAvailable(!!r.data.voice_available)).catch(() => {}); }, [apiService]);
    useEffect(() => { const i = setInterval(() => forceTick(t => t + 1), 30000); return () => clearInterval(i); }, []);
    useEffect(() => { setText(''); }, [conversation.id]);

    const windowInfo = describeWindow(conversation.window_expires_at);
    const run = async (fn) => {
        setSending(true);
        try { await fn(); await onSent(); }
        catch (e) { notify(e.response?.data?.msg || 'Send failed'); if (e.response?.data?.error === 'window_closed') await onSent(); }
        finally { setSending(false); }
    };
    const sendText = () => text.trim() && run(async () => {
        await apiService.sendInboxMessage(conversation.id, { type: 'text', text, reply_to: replyTo?.wa_message_id });
        setText(''); clearReply();
    });
    const sendReaction = (emoji) => {
        const target = reactTarget?.message;
        clearReactTarget(); setFullReactPicker(false);
        if (target) run(() => apiService.sendInboxMessage(conversation.id, { type: 'reaction', target: target.wa_message_id, emoji }));
    };
    const sendSticker = (file) => file && run(() => apiService.sendInboxFile(conversation.id, 'sticker', file));
    const sendVoice = (file) => run(() => apiService.sendInboxFile(conversation.id, 'voice', file));

    const reactionPopover = (
        <Popover open={!!reactTarget} anchorEl={reactTarget?.anchorEl} onClose={() => { clearReactTarget(); setFullReactPicker(false); }}
            anchorOrigin={{ vertical: 'top', horizontal: 'center' }} transformOrigin={{ vertical: 'bottom', horizontal: 'center' }}>
            {fullReactPicker
                ? <EmojiPicker onEmojiClick={(d) => sendReaction(d.emoji)} lazyLoadEmojis />
                : (
                    <Stack direction="row" sx={{ p: 0.5 }}>
                        {QUICK_REACTIONS.map(e => <IconButton key={e} onClick={() => sendReaction(e)} sx={{ fontSize: 22 }}>{e}</IconButton>)}
                        <IconButton onClick={() => setFullReactPicker(true)}>＋</IconButton>
                    </Stack>
                )}
        </Popover>
    );

    if (!windowInfo.open) return (
        <Box sx={{ p: 2, borderTop: 1, borderColor: 'divider' }}>
            {reactionPopover}
            <Typography variant="body2" color="warning.main" sx={{ mb: 1 }}>{windowInfo.label}</Typography>
            <TemplateSender conversationId={conversation.id} onSent={onSent} notify={notify} />
        </Box>
    );

    return (
        <Box sx={{ p: 1.5, borderTop: 1, borderColor: 'divider' }}>
            {reactionPopover}
            <Stack direction="row" justifyContent="space-between" alignItems="center" sx={{ mb: 0.5 }}>
                {replyTo ? (
                    <Chip size="small" onDelete={clearReply} deleteIcon={<CloseIcon />}
                        label={`Replying to: ${(replyTo.text || replyTo.msg_type).slice(0, 40)}`} />
                ) : <span />}
                <Typography variant="caption" color="text.secondary">{windowInfo.label}</Typography>
            </Stack>
            <Stack direction="row" spacing={0.5} alignItems="flex-end">
                <IconButton onClick={e => setEmojiAnchor(e.currentTarget)}><EmojiIcon /></IconButton>
                <Popover open={!!emojiAnchor} anchorEl={emojiAnchor} onClose={() => setEmojiAnchor(null)}
                    anchorOrigin={{ vertical: 'top', horizontal: 'left' }} transformOrigin={{ vertical: 'bottom', horizontal: 'left' }}>
                    <EmojiPicker onEmojiClick={(d) => setText(t => t + d.emoji)} lazyLoadEmojis />
                </Popover>
                <Tooltip title="Send sticker (.webp, or PNG/JPG converted to 512×512)">
                    <IconButton onClick={() => fileRef.current?.click()} disabled={sending}><StickerIcon /></IconButton>
                </Tooltip>
                <input ref={fileRef} type="file" accept="image/webp,image/png,image/jpeg" hidden
                    onChange={e => { sendSticker(e.target.files?.[0]); e.target.value = ''; }} />
                <TextField fullWidth multiline maxRows={5} size="small" placeholder="Type a message" value={text} dir="auto"
                    onChange={e => setText(e.target.value)}
                    onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendText(); } }} />
                {text.trim()
                    ? <IconButton color="primary" disabled={sending} onClick={sendText}><SendIcon /></IconButton>
                    : <VoiceRecorder disabled={!voiceAvailable || sending} onSend={sendVoice} onError={notify} />}
            </Stack>
        </Box>
    );
};

export default Composer;
```

- [ ] **Step 4: Plug the composer into `MessagingView.js`**

Add the import `import Composer from './inbox/Composer';` and change the inbox branch to:

```js
                    <InboxView openConversationId={openConversationId}
                        renderComposer={(props) => <Composer {...props} />} />
```

- [ ] **Step 5: Build**

Run: `npm --prefix frontend run build`
Expected: compiles with no new errors. Fix lint errors in the new files; for example, move the `stop` definition above `start` in `VoiceRecorder` if the linter complains about use before define.

- [ ] **Step 6: Commit**

```bash
git add frontend/package.json frontend/package-lock.json frontend/src/components/inbox/Composer.js frontend/src/components/inbox/VoiceRecorder.js frontend/src/components/MessagingView.js
git commit -m "Add inbox composer: text/emoji, reply, reactions, sticker upload, voice notes, templates"
```

---

### Task 13: Notifications control in the Inbox header

**Files:**
- Create: `frontend/src/components/inbox/InboxNotificationsControl.js`
- Modify: `frontend/src/components/MessagingView.js`: pass `headerExtra`

**Interfaces:**
- Consumes: `serviceWorkerRegistration.subscribeUserToPush(vapidPublicKey)` (the existing `frontend/src/serviceWorkerRegistration.js`) and the push wrappers from Task 11.

- [ ] **Step 1: Create the control**

```js
// frontend/src/components/inbox/InboxNotificationsControl.js
import React, { useCallback, useEffect, useState } from 'react';
import { Button, Menu, MenuItem, Tooltip, Typography } from '@mui/material';
import { NotificationsActive as OnIcon, NotificationsOff as OffIcon, NotificationsNone as NoneIcon } from '@mui/icons-material';
import { useAppContext } from '../../context/AppContext';
import * as swReg from '../../serviceWorkerRegistration';

const TOPIC = 'whatsapp_inbox';
const isIos = () => /iphone|ipad|ipod/i.test(navigator.userAgent);
const isStandalone = () => window.matchMedia?.('(display-mode: standalone)').matches || window.navigator.standalone === true;

const InboxNotificationsControl = () => {
    const { apiService, setSnackbar } = useAppContext();
    const [state, setState] = useState('loading'); // loading|unsupported|ios_install|not_configured|blocked|off|on
    const [anchor, setAnchor] = useState(null);
    const toast = (message, severity = 'info') => setSnackbar({ open: true, message, severity });

    const currentSub = async () => {
        const reg = await navigator.serviceWorker.ready;
        return reg.pushManager.getSubscription();
    };

    const refresh = useCallback(async () => {
        if (isIos() && !isStandalone()) return setState('ios_install');
        if (!('serviceWorker' in navigator) || !('PushManager' in window) || !('Notification' in window)) return setState('unsupported');
        if (Notification.permission === 'denied') return setState('blocked');
        try {
            const { data } = await apiService.fetchVapidPublicKey();
            if (!data.public_key) return setState('not_configured');
            const sub = await currentSub();
            if (!sub) return setState('off');
            const topics = await apiService.fetchPushTopics(sub.endpoint);
            setState(topics.data.subscribed && topics.data.topics.includes(TOPIC) ? 'on' : 'off');
        } catch (e) { setState('off'); }
    }, [apiService]);

    useEffect(() => { refresh(); }, [refresh]);

    const enable = async () => {
        try {
            const { data } = await apiService.fetchVapidPublicKey();
            let sub = await currentSub();
            if (!sub) sub = await swReg.subscribeUserToPush(data.public_key);
            await apiService.pushSubscribe(sub.toJSON ? sub.toJSON() : sub);
            const { data: t } = await apiService.fetchPushTopics(sub.endpoint);
            const topics = Array.from(new Set([...(t.topics || []), TOPIC]));
            await apiService.setPushTopics(sub.endpoint, topics);
            toast('Inbox notifications enabled on this device', 'success');
        } catch (e) {
            toast(`Could not enable notifications: ${e.response?.data?.msg || e.message}`, 'error');
        }
        refresh();
    };
    const disable = async () => {
        setAnchor(null);
        const sub = await currentSub();
        if (sub) {
            const { data: t } = await apiService.fetchPushTopics(sub.endpoint);
            await apiService.setPushTopics(sub.endpoint, (t.topics || []).filter(x => x !== TOPIC));
        }
        refresh();
    };
    const test = async () => {
        setAnchor(null);
        try { const sub = await currentSub(); await apiService.sendTestPush(sub.endpoint); toast('Test notification sent', 'success'); }
        catch (e) { toast(e.response?.data?.msg || 'Test failed', 'error'); }
    };

    if (state === 'loading') return null;
    if (state === 'ios_install') return <Typography variant="caption" color="text.secondary">On iPhone: Share → Add to Home Screen, then open the app to enable notifications.</Typography>;
    if (state === 'unsupported') return <Typography variant="caption" color="text.secondary">This browser doesn't support notifications.</Typography>;
    if (state === 'not_configured') return <Typography variant="caption" color="text.secondary">Notifications are not configured on the server.</Typography>;
    if (state === 'blocked') return <Tooltip title="Allow notifications for this site in your browser settings"><Button size="small" startIcon={<OffIcon />} disabled>Notifications blocked</Button></Tooltip>;
    if (state === 'off') return <Button size="small" variant="outlined" startIcon={<NoneIcon />} onClick={enable}>Enable notifications</Button>;
    return (
        <>
            <Button size="small" color="success" startIcon={<OnIcon />} onClick={e => setAnchor(e.currentTarget)}>Notifications on</Button>
            <Menu open={!!anchor} anchorEl={anchor} onClose={() => setAnchor(null)}>
                <MenuItem onClick={test}>Send test notification</MenuItem>
                <MenuItem onClick={disable}>Turn off on this device</MenuItem>
            </Menu>
        </>
    );
};

export default InboxNotificationsControl;
```

- [ ] **Step 2: Plug it in**

In `MessagingView.js`, add the import `import InboxNotificationsControl from './inbox/InboxNotificationsControl';` and add the `headerExtra` prop:

```js
                    <InboxView openConversationId={openConversationId}
                        headerExtra={<InboxNotificationsControl />}
                        renderComposer={(props) => <Composer {...props} />} />
```

- [ ] **Step 3: Build**

Run: `npm --prefix frontend run build`
Expected: compiles with no new errors.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/inbox/InboxNotificationsControl.js frontend/src/components/MessagingView.js
git commit -m "Add per-device inbox notification control (enable/disable/test, iOS install hint)"
```

---

### Task 14: End-to-end verification

**Files:** none new, apart from any fixes found.

- [ ] **Step 1: Full backend suite**

Run: `python -m pytest tests/ -q`
Expected: all pass, 0 failures.

- [ ] **Step 2: Frontend tests + build**

Run: `$env:CI='true'; npm --prefix frontend test -- --watchAll=false` then `npm --prefix frontend run build`
Expected: all pass; build compiles.

- [ ] **Step 3: Browser verification against a throwaway DB**

Follow the `feedback_local_test_server_isolation` memory. `preview_start` reads the main checkout's `.claude/launch.json`, so run the backend with an explicit throwaway DB, for example `DATABASE_PATH=<scratchpad>/inbox_e2e.db` and no `DATABASE_URL`. Never use the user's real dev database. Then:

1. Register a test tenant, add a customer with phone `70123456`, and add `WhatsAppSettings` (fake token, `phone_number_id=PNID_E2E`, `app_secret=e2e`).
2. POST signed webhook payloads using the same HMAC as `tests/inbox_helpers.signed_post`. The script lives in the scratchpad. Send:
   - a text from an unknown number;
   - an image;
   - a sticker;
   - a reaction targeting one of the stored messages.
   Media downloads will fail against the fake token. That is expected, and "Media unavailable" should render.
3. Open Messaging → Inbox and verify:
   - the "Needs attention" list shows the unknown sender and the media conversations, with the right chips;
   - the nav badge count matches;
   - the thread renders bubbles, reaction chips and the window countdown;
   - on a conversation with `last_inbound_at` older than 24h (edit it via SQL), the composer shows the template picker.
4. Check `read_console_messages` for errors, and screenshot the inbox.
5. Resize to mobile (375×812) and confirm list → thread → back navigation. Reset to desktop afterwards.

Sending can't reach Meta with a fake token. Verify that a send attempt shows the Meta error title on a failed bubble, which exercises the error path end to end.

- [ ] **Step 4: Deploy checklist (report to the user; do not change production yourself)**

- Render env has `VAPID_PUBLIC_KEY` and `VAPID_PRIVATE_KEY`. Optionally set `VAPID_CLAIM_EMAIL`.
- The Docker image now installs ffmpeg. After deploy, `GET /api/whatsapp/inbox/summary` should report `voice_available: true`.
- The migration `a7c3e9d1b2f4` runs on startup via `flask db upgrade`.
- Live test, following the `feedback_iterate_against_live_production_logs` pattern: the user sends a real WhatsApp text, a voice note, a sticker and an image to the business number. Then check that they appear in the inbox, that the voice note plays and has a transcript, that the admin reply arrives on the phone and the AI stays paused, and that a push arrives on an enabled device.

- [ ] **Step 5: Commit any fixes**

```bash
git add -A -- ':!graphify-out' ':!tests/test_stale_agent_job_sweep.py'
git commit -m "Fix issues found in inbox end-to-end verification"
```

Only commit if Step 3 turned up fixes. Never stage the user's unrelated WIP (`app.py` changes present before this branch, `graphify-out/`, `tests/test_stale_agent_job_sweep.py`). Stage specific files instead of `-A` if in doubt.

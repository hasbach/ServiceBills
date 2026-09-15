# CS Agent Gemini Brain + Tenant Memory Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the ElevenLabs Conversational AI brain on the WhatsApp CS agent path with a self-orchestrated, per-tenant Gemini tool-calling loop (free tier), and add a tenant-curated "knowledge" memory the brain draws on.

**Architecture:** `handle_whatsapp_cs_ai_reply()` in `cs_agent_tools.py` calls a new `query_gemini_agent()` instead of `query_elevenlabs_conversational_ai()`. `query_gemini_agent()` runs its own manual function-calling loop against the Google GenAI Python SDK, dispatching to the same five tool functions the HTTP `/api/cs-agent/tools/*` endpoints already call, with a Flash → Flash-Lite → existing rule-based fallback chain. A new `cs_agent_knowledge_entry` table holds tenant-curated Q&A pairs, keyword-matched into the system prompt. STT/TTS (ElevenLabs) and the CS agent tool HTTP endpoints are unchanged.

**Tech Stack:** Flask, SQLAlchemy, Alembic (hand-written migrations), pytest, React + MUI (frontend), `google-genai` (new dependency).

**Design spec:** `docs/superpowers/specs/2026-09-16-cs-agent-gemini-brain-design.md`

## Global Constraints

- No fine-tuning, no vector/embedding retrieval (keyword `ILIKE` search only) — per spec Non-Goals.
- STT/TTS stay on ElevenLabs; only the WhatsApp conversational brain changes.
- A tenant with no `gemini_api_key` must never call Gemini — falls straight to `process_customer_message_ai()`.
- Every new DB table/column is tenant-scoped; memory entries never cross tenants.
- `gemini_api_key` is stored via the existing `EncryptedString` column type (same as `access_token`, `portal_password`).
- Package to add: `google-genai` (current SDK; import as `from google import genai`, `from google.genai import types, errors`).
- Gemini models: primary `gemini-flash-latest` (Google's self-updating alias — see Task 5 note), fallback `gemini-2.5-flash-lite` (concrete stable model with a free tier).
- Tool-call loop cap: 6 round-trips (`GEMINI_MAX_TOOL_ROUNDTRIPS`), matching the two-sequential-tool diagnostic chain from the original CS agent spec with headroom.

---

### Task 1: Data model — `CSAgentSettings` Gemini columns + `CSAgentKnowledgeEntry` table

**Files:**
- Modify: `app.py:1569-1589` (`CSAgentSettings` class)
- Modify: `app.py` (add new `CSAgentKnowledgeEntry` class directly after `CSAgentMessageLog`, i.e. after line 1642)
- Create: `migrations/versions/<new_revision>_add_gemini_brain_and_knowledge.py`
- Test: `tests/test_cs_agent_tools.py`

**Interfaces:**
- Produces: `CSAgentSettings.gemini_api_key` (str|None), `CSAgentSettings.gemini_model` (str|None), `CSAgentSettings.to_dict()` including both. `CSAgentKnowledgeEntry` model with `tenant_id, question_text, answer_text, source, source_log_id, created_by_id, is_active, created_at, updated_at` and `.to_dict()`. Both consumed by later tasks.

- [ ] **Step 1: Add the two Gemini columns to `CSAgentSettings`**

In `app.py`, inside the `CSAgentSettings` class (around line 1574), add after `elevenlabs_agent_id`:

```python
    gemini_api_key = db.Column(EncryptedString, nullable=True)   # tenant's own free-tier key; encrypted at rest
    gemini_model = db.Column(db.String(50), nullable=True)       # optional override; None -> default Flash/Flash-Lite chain
```

Update `to_dict()` (around line 1580) to:

```python
    def to_dict(self):
        return {
            'id': self.id,
            'tenant_id': self.tenant_id,
            'elevenlabs_agent_id': self.elevenlabs_agent_id or '',
            'admin_mobile_number': self.admin_mobile_number or '',
            'gemini_api_key': self.gemini_api_key or '',
            'gemini_model': self.gemini_model or '',
            'is_active': self.is_active,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.strftime('%Y-%m-%d %H:%M:%S') if self.updated_at else None,
        }
```

- [ ] **Step 2: Add the `CSAgentKnowledgeEntry` model**

In `app.py`, directly after the `CSAgentMessageLog` class (after its closing `to_dict()`, i.e. after line 1642), add:

```python
class CSAgentKnowledgeEntry(db.Model):
    """Tenant-curated question/answer pairs the Gemini brain draws on for that
    tenant's recurring questions. Only admin-added or admin-approved entries
    land here -- never raw, unreviewed conversation logs (see
    docs/superpowers/specs/2026-09-16-cs-agent-gemini-brain-design.md)."""
    __tablename__ = "cs_agent_knowledge_entry"
    __table_args__ = (
        db.Index('ix_cs_agent_knowledge_entry_tenant_active', 'tenant_id', 'is_active'),
    )
    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey('tenant.id'), nullable=False, index=True)
    question_text = db.Column(db.Text, nullable=False)
    answer_text = db.Column(db.Text, nullable=False)
    source = db.Column(db.String(20), nullable=False, default='manual')  # 'manual' | 'conversation_log'
    source_log_id = db.Column(db.Integer, db.ForeignKey('cs_agent_message_log.id'), nullable=True)
    created_by_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'tenant_id': self.tenant_id,
            'question_text': self.question_text,
            'answer_text': self.answer_text,
            'source': self.source,
            'source_log_id': self.source_log_id,
            'is_active': self.is_active,
            'created_at': self.created_at.strftime('%Y-%m-%d %H:%M:%S') if self.created_at else None,
            'updated_at': self.updated_at.strftime('%Y-%m-%d %H:%M:%S') if self.updated_at else None,
        }
```

- [ ] **Step 3: Determine the correct migration `down_revision`**

This repo's migration history currently has more than one head (a known, pre-existing drift issue — see the "Schema migration drift risk" note in project memory). Run:

```bash
python -c "
import os, re
files = [f for f in os.listdir('migrations/versions') if f.endswith('.py')]
revs = {}
for f in files:
    content = open(os.path.join('migrations/versions', f), encoding='utf-8').read()
    rev = re.search(r\"^revision = '([^']+)'\", content, re.M)
    down = re.search(r\"^down_revision = '?([^'\n]+)'?\", content, re.M)
    if rev:
        revs[rev.group(1)] = down.group(1) if down else None
downs = set(v for v in revs.values() if v and v != 'None')
heads = [r for r in revs if r not in downs]
print('heads:', heads)
"
```

If this prints more than one head, **stop and ask the user which head matches the production database** (check with `flask db current` against the production `DATABASE_URL`, or ask directly) before writing the migration — do not guess. Use the confirmed production head as `down_revision` below.

- [ ] **Step 4: Write the migration**

Create `migrations/versions/<new_revision>_add_gemini_brain_and_knowledge.py` (pick a fresh unique revision id, e.g. `f1a2b3c4d5e6`, and use the `down_revision` confirmed in Step 3):

```python
"""add gemini brain columns and cs_agent_knowledge_entry table

Revision ID: f1a2b3c4d5e6
Revises: <CONFIRMED_HEAD_FROM_STEP_3>
Create Date: 2026-09-16 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'f1a2b3c4d5e6'
down_revision = '<CONFIRMED_HEAD_FROM_STEP_3>'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('cs_agent_settings', schema=None) as batch_op:
        batch_op.add_column(sa.Column('gemini_api_key', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('gemini_model', sa.String(length=50), nullable=True))

    op.create_table(
        'cs_agent_knowledge_entry',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('tenant_id', sa.Integer(), nullable=False),
        sa.Column('question_text', sa.Text(), nullable=False),
        sa.Column('answer_text', sa.Text(), nullable=False),
        sa.Column('source', sa.String(length=20), nullable=False, server_default='manual'),
        sa.Column('source_log_id', sa.Integer(), nullable=True),
        sa.Column('created_by_id', sa.Integer(), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='1'),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id'], name=op.f('fk_cs_agent_knowledge_entry_tenant_id_tenant')),
        sa.ForeignKeyConstraint(['source_log_id'], ['cs_agent_message_log.id'], name=op.f('fk_cs_agent_knowledge_entry_source_log_id_cs_agent_message_log')),
        sa.ForeignKeyConstraint(['created_by_id'], ['user.id'], name=op.f('fk_cs_agent_knowledge_entry_created_by_id_user')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_cs_agent_knowledge_entry'))
    )
    with op.batch_alter_table('cs_agent_knowledge_entry', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_cs_agent_knowledge_entry_tenant_id'), ['tenant_id'], unique=False)
        batch_op.create_index('ix_cs_agent_knowledge_entry_tenant_active', ['tenant_id', 'is_active'], unique=False)


def downgrade():
    with op.batch_alter_table('cs_agent_knowledge_entry', schema=None) as batch_op:
        batch_op.drop_index('ix_cs_agent_knowledge_entry_tenant_active')
        batch_op.drop_index(batch_op.f('ix_cs_agent_knowledge_entry_tenant_id'))
    op.drop_table('cs_agent_knowledge_entry')

    with op.batch_alter_table('cs_agent_settings', schema=None) as batch_op:
        batch_op.drop_column('gemini_model')
        batch_op.drop_column('gemini_api_key')
```

- [ ] **Step 5: Write the failing test**

Add to `tests/test_cs_agent_tools.py`:

```python
def test_cs_agent_settings_gemini_fields_round_trip(app, client):
    """CSAgentSettings persists and returns gemini_api_key / gemini_model."""
    auth_headers(client, "admin_gemini_fields", "pw123")
    with app.app_context():
        tenant = appmod.Tenant.query.order_by(appmod.Tenant.id.desc()).first()
        settings = appmod.CSAgentSettings(tenant_id=tenant.id)
        settings.gemini_api_key = 'AIzaTestKey123'
        settings.gemini_model = 'gemini-2.5-flash-lite'
        appmod.db.session.add(settings)
        appmod.db.session.commit()

        reloaded = appmod.CSAgentSettings.query.filter_by(tenant_id=tenant.id).first()
        assert reloaded.gemini_api_key == 'AIzaTestKey123'
        assert reloaded.to_dict()['gemini_model'] == 'gemini-2.5-flash-lite'


def test_cs_agent_knowledge_entry_tenant_scoped(app, client):
    """A CSAgentKnowledgeEntry belongs to exactly one tenant and round-trips."""
    auth_headers(client, "admin_knowledge_scoped", "pw123")
    with app.app_context():
        tenant = appmod.Tenant.query.order_by(appmod.Tenant.id.desc()).first()
        entry = appmod.CSAgentKnowledgeEntry(
            tenant_id=tenant.id,
            question_text='شو بواقي اشتراكي؟',
            answer_text='بواقيك 25 دولار، تنتهي بعد 20 يوم.',
            source='manual'
        )
        appmod.db.session.add(entry)
        appmod.db.session.commit()

        reloaded = appmod.CSAgentKnowledgeEntry.query.filter_by(tenant_id=tenant.id).first()
        assert reloaded.question_text == 'شو بواقي اشتراكي؟'
        assert reloaded.is_active is True
        assert reloaded.to_dict()['source'] == 'manual'
```

This test needs no route yet since `app` fixture builds schema via `db.create_all()`, which will already pick up the new model once Step 2 is done — this step confirms that.

- [ ] **Step 6: Run the tests, verify they fail before Steps 1-2, pass after**

Run:
```bash
pytest tests/test_cs_agent_tools.py -k "gemini_fields_round_trip or knowledge_entry_tenant_scoped" -v
```
Expected before Steps 1-2 exist: `AttributeError` (no `gemini_api_key` / no `CSAgentKnowledgeEntry`). After Steps 1-2: both PASS.

- [ ] **Step 7: Commit**

```bash
git add app.py migrations/versions tests/test_cs_agent_tools.py
git commit -m "Add gemini_api_key/gemini_model columns and cs_agent_knowledge_entry table"
```

---

### Task 2: Config endpoint — expose and save Gemini settings

**Files:**
- Modify: `app.py:12147-12198` (`cs_agent_config` route)
- Test: `tests/test_cs_agent_tools.py`

**Interfaces:**
- Consumes: `CSAgentSettings.gemini_api_key`, `.gemini_model` (Task 1)
- Produces: `GET /api/cs-agent/config` returns `gemini_api_key`, `gemini_model`, `has_gemini_key` (bool); `POST /api/cs-agent/config` accepts and saves the same two fields.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_cs_agent_tools.py`:

```python
def test_cs_agent_config_saves_gemini_key(app, client):
    """POST /api/cs-agent/config saves a tenant's Gemini API key and model, and GET returns them."""
    headers = auth_headers(client, "admin_gemini_config", "pw123")

    save_res = client.post(
        "/api/cs-agent/config",
        json={
            "elevenlabs_agent_id": "agent_keep_existing",
            "gemini_api_key": "AIzaSyTestKeyForTenant",
            "gemini_model": "gemini-2.5-flash-lite"
        },
        headers=headers
    )
    assert save_res.status_code == 200
    assert save_res.get_json()["settings"]["gemini_api_key"] == "AIzaSyTestKeyForTenant"

    get_res = client.get("/api/cs-agent/config", headers=headers)
    assert get_res.status_code == 200
    data = get_res.get_json()
    assert data["gemini_api_key"] == "AIzaSyTestKeyForTenant"
    assert data["gemini_model"] == "gemini-2.5-flash-lite"
    assert data["has_gemini_key"] is True


def test_cs_agent_config_no_gemini_key_by_default(app, client):
    """A tenant that never set a Gemini key gets has_gemini_key: False, never another tenant's key."""
    headers = auth_headers(client, "admin_no_gemini", "pw123")
    get_res = client.get("/api/cs-agent/config", headers=headers)
    data = get_res.get_json()
    assert data["gemini_api_key"] == ""
    assert data["has_gemini_key"] is False
```

- [ ] **Step 2: Run to verify failure**

```bash
pytest tests/test_cs_agent_tools.py -k "gemini_key" -v
```
Expected: FAIL — `KeyError: 'gemini_api_key'` (route doesn't return/accept it yet).

- [ ] **Step 3: Update the route**

In `app.py`, replace the `cs_agent_config` POST handling (lines 12168-12171) with:

```python
            if 'elevenlabs_agent_id' in data:
                settings.elevenlabs_agent_id = (data.get('elevenlabs_agent_id') or '').strip()
            if 'admin_mobile_number' in data:
                settings.admin_mobile_number = (data.get('admin_mobile_number') or '').strip()
            if 'gemini_api_key' in data:
                settings.gemini_api_key = (data.get('gemini_api_key') or '').strip()
            if 'gemini_model' in data:
                settings.gemini_model = (data.get('gemini_model') or '').strip()
```

And replace the GET response section (lines 12178-12198) with:

```python
    agent_id = ''
    admin_mobile_number = ''
    gemini_api_key = ''
    gemini_model = ''
    if tenant_id:
        try:
            settings = CSAgentSettings.query.filter_by(tenant_id=tenant_id).first()
            if settings:
                agent_id = settings.elevenlabs_agent_id or ''
                admin_mobile_number = settings.admin_mobile_number or ''
                gemini_api_key = settings.gemini_api_key or ''
                gemini_model = settings.gemini_model or ''
        except Exception:
            db.session.rollback()

    if not agent_id:
        agent_id = app.config.get('ELEVENLABS_AGENT_ID', '')

    return jsonify({
        'status': 'ok',
        'elevenlabs_agent_id': agent_id,
        'admin_mobile_number': admin_mobile_number,
        'gemini_api_key': gemini_api_key,
        'gemini_model': gemini_model,
        'has_gemini_key': bool(gemini_api_key),
        'has_agent_id': bool(agent_id),
        'ws_url': f"wss://api.elevenlabs.io/v1/convai/conversation?agent_id={agent_id}" if agent_id else None
    }), 200
```

Note: no env-var fallback for `gemini_api_key` (unlike `elevenlabs_agent_id`) — per the design's cost-isolation decision, a tenant with no key gets nothing, never a shared/platform key.

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_cs_agent_tools.py -k "gemini_key" -v
```
Expected: both PASS.

- [ ] **Step 5: Commit**

```bash
git add app.py tests/test_cs_agent_tools.py
git commit -m "Expose and save per-tenant Gemini API key/model on CS agent config endpoint"
```

---

### Task 3: Knowledge search + CRUD functions in `cs_agent_tools.py`

**Files:**
- Modify: `cs_agent_tools.py` (add functions near the top, after `normalize_lebanese_phone`, i.e. after line ~76)
- Test: `tests/test_cs_agent_tools.py`

**Interfaces:**
- Consumes: `CSAgentKnowledgeEntry` (Task 1)
- Produces: `search_knowledge_entries(appmod, tenant_id, query_text, limit=5) -> list[CSAgentKnowledgeEntry]`, `add_knowledge_entry(appmod, tenant_id, question_text, answer_text, source='manual', source_log_id=None, created_by_id=None) -> CSAgentKnowledgeEntry`, `list_knowledge_entries(appmod, tenant_id) -> list[CSAgentKnowledgeEntry]`, `set_knowledge_entry_active(appmod, tenant_id, entry_id, is_active) -> bool`, `delete_knowledge_entry(appmod, tenant_id, entry_id) -> bool`. Task 4 (HTTP endpoints) and Task 5 (Gemini brain) both call these.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_cs_agent_tools.py`:

```python
def test_search_knowledge_entries_keyword_match_and_tenant_isolation(app, client):
    """Keyword search finds a relevant entry for the right tenant, and never for another tenant."""
    import cs_agent_tools

    auth_headers(client, "admin_knowledge_t1", "pw123")
    with app.app_context():
        t1_id = appmod.Tenant.query.order_by(appmod.Tenant.id.desc()).first().id

    auth_headers(client, "admin_knowledge_t2", "pw123")
    with app.app_context():
        t2_id = appmod.Tenant.query.order_by(appmod.Tenant.id.desc()).first().id
        assert t2_id != t1_id

        appmod.db.session.add(appmod.CSAgentKnowledgeEntry(
            tenant_id=t1_id, question_text="كيف بدي جدد اشتراكي؟", answer_text="ابعتلك رابط دفع فوراً."
        ))
        appmod.db.session.add(appmod.CSAgentKnowledgeEntry(
            tenant_id=t2_id, question_text="كيف بدي جدد اشتراكي؟", answer_text="جواب تينانت تاني ما لازم يظهر."
        ))
        appmod.db.session.commit()

        results = cs_agent_tools.search_knowledge_entries(appmod, t1_id, "بدي جدد اشتراكي شو بعمل", limit=5)
        assert len(results) == 1
        assert results[0].answer_text == "ابعتلك رابط دفع فوراً."


def test_search_knowledge_entries_no_match_returns_empty(app, client):
    """An unrelated question, or a tenant with zero entries, returns an empty list -- not an error."""
    import cs_agent_tools
    auth_headers(client, "admin_knowledge_empty", "pw123")
    with app.app_context():
        tenant = appmod.Tenant.query.order_by(appmod.Tenant.id.desc()).first()
        results = cs_agent_tools.search_knowledge_entries(appmod, tenant.id, "شي غير موجود إطلاقاً", limit=5)
        assert results == []


def test_add_list_and_deactivate_knowledge_entry(app, client):
    """Manual add, list, and deactivate round-trip correctly."""
    import cs_agent_tools
    auth_headers(client, "admin_knowledge_crud", "pw123")
    with app.app_context():
        tenant = appmod.Tenant.query.order_by(appmod.Tenant.id.desc()).first()
        entry = cs_agent_tools.add_knowledge_entry(
            appmod, tenant.id, "شو أوقات الدعم الفني؟", "من 9 الصبح لـ 9 الليل كل يوم."
        )
        assert entry.id is not None

        entries = cs_agent_tools.list_knowledge_entries(appmod, tenant.id)
        assert any(e.id == entry.id for e in entries)

        ok = cs_agent_tools.set_knowledge_entry_active(appmod, tenant.id, entry.id, False)
        assert ok is True
        reloaded = appmod.CSAgentKnowledgeEntry.query.get(entry.id)
        assert reloaded.is_active is False

        # Deactivated entries never come back from search
        results = cs_agent_tools.search_knowledge_entries(appmod, tenant.id, "شو أوقات الدعم الفني", limit=5)
        assert results == []
```

- [ ] **Step 2: Run to verify failure**

```bash
pytest tests/test_cs_agent_tools.py -k "knowledge_entries or knowledge_entry" -v
```
Expected: FAIL — `AttributeError: module 'cs_agent_tools' has no attribute 'search_knowledge_entries'`.

- [ ] **Step 3: Implement the functions**

In `cs_agent_tools.py`, add after `normalize_lebanese_phone` (after its closing, before `_poll_sleep` or right after — place near the top, around line 76):

```python
def search_knowledge_entries(appmod, tenant_id, query_text, limit=5):
    """Keyword-matches active CSAgentKnowledgeEntry rows for this tenant against
    query_text, ranked by number of matched words. No embeddings/vector store --
    see docs/superpowers/specs/2026-09-16-cs-agent-gemini-brain-design.md for why.
    """
    words = [w for w in re.findall(r'\w+', (query_text or ''), re.UNICODE) if len(w) >= 3]
    if not words:
        return []

    entry_model = appmod.CSAgentKnowledgeEntry
    conditions = [entry_model.question_text.ilike(f'%{w}%') for w in words[:10]]
    candidates = entry_model.query.filter_by(tenant_id=tenant_id, is_active=True).filter(
        appmod.db.or_(*conditions)
    ).limit(limit * 3).all()

    lowered_words = [w.lower() for w in words]

    def _score(entry):
        qt = (entry.question_text or '').lower()
        return sum(1 for w in lowered_words if w in qt)

    candidates.sort(key=_score, reverse=True)
    return candidates[:limit]


def add_knowledge_entry(appmod, tenant_id, question_text, answer_text, source='manual', source_log_id=None, created_by_id=None):
    """Creates a CSAgentKnowledgeEntry row. Used by both the manual-add form and
    the promote-from-conversation-log flow."""
    entry = appmod.CSAgentKnowledgeEntry(
        tenant_id=tenant_id,
        question_text=(question_text or '').strip(),
        answer_text=(answer_text or '').strip(),
        source=source,
        source_log_id=source_log_id,
        created_by_id=created_by_id,
    )
    appmod.db.session.add(entry)
    appmod.db.session.commit()
    return entry


def list_knowledge_entries(appmod, tenant_id):
    """All entries (active and inactive) for a tenant, newest first."""
    return appmod.CSAgentKnowledgeEntry.query.filter_by(
        tenant_id=tenant_id
    ).order_by(appmod.CSAgentKnowledgeEntry.id.desc()).all()


def set_knowledge_entry_active(appmod, tenant_id, entry_id, is_active):
    """Activates/deactivates an entry. Returns False if it doesn't belong to this tenant."""
    entry = appmod.CSAgentKnowledgeEntry.query.filter_by(id=entry_id, tenant_id=tenant_id).first()
    if not entry:
        return False
    entry.is_active = bool(is_active)
    appmod.db.session.commit()
    return True


def delete_knowledge_entry(appmod, tenant_id, entry_id):
    """Permanently deletes an entry. Returns False if it doesn't belong to this tenant."""
    entry = appmod.CSAgentKnowledgeEntry.query.filter_by(id=entry_id, tenant_id=tenant_id).first()
    if not entry:
        return False
    appmod.db.session.delete(entry)
    appmod.db.session.commit()
    return True
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_cs_agent_tools.py -k "knowledge_entries or knowledge_entry" -v
```
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add cs_agent_tools.py tests/test_cs_agent_tools.py
git commit -m "Add tenant-scoped knowledge entry search and CRUD helpers"
```

---

### Task 4: Memory HTTP endpoints (manual add/list/deactivate/delete + recent-logs for promotion)

**Files:**
- Modify: `app.py` (add new routes directly after `cs_get_recent_tickets`, i.e. after the block starting at line 12330)
- Test: `tests/test_cs_agent_tools.py`

**Interfaces:**
- Consumes: `cs_agent_tools.add_knowledge_entry`, `.list_knowledge_entries`, `.set_knowledge_entry_active`, `.delete_knowledge_entry` (Task 3)
- Produces: `GET/POST /api/cs-agent/memory`, `PUT/DELETE /api/cs-agent/memory/<int:entry_id>`, `GET /api/cs-agent/memory/recent-logs` — consumed by Task 8 (frontend).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_cs_agent_tools.py`:

```python
def test_memory_endpoints_crud(app, client):
    """POST creates, GET lists, PUT deactivates, DELETE removes -- all tenant-scoped."""
    headers = auth_headers(client, "admin_memory_crud", "pw123")

    create_res = client.post(
        "/api/cs-agent/memory",
        json={"question_text": "شو بدل التركيب؟", "answer_text": "50 دولار تركيب لمرة وحدة."},
        headers=headers
    )
    assert create_res.status_code == 200
    entry_id = create_res.get_json()["entry"]["id"]
    assert create_res.get_json()["entry"]["source"] == "manual"

    list_res = client.get("/api/cs-agent/memory", headers=headers)
    assert list_res.status_code == 200
    assert any(e["id"] == entry_id for e in list_res.get_json()["entries"])

    deactivate_res = client.put(
        f"/api/cs-agent/memory/{entry_id}", json={"is_active": False}, headers=headers
    )
    assert deactivate_res.status_code == 200

    delete_res = client.delete(f"/api/cs-agent/memory/{entry_id}", headers=headers)
    assert delete_res.status_code == 200

    list_after = client.get("/api/cs-agent/memory", headers=headers)
    assert not any(e["id"] == entry_id for e in list_after.get_json()["entries"])


def test_memory_endpoints_are_tenant_isolated(app, client):
    """A tenant can't see, edit, or delete another tenant's memory entries."""
    headers_a = auth_headers(client, "admin_memory_a", "pw123")
    create_res = client.post(
        "/api/cs-agent/memory",
        json={"question_text": "سؤال تينانت A", "answer_text": "جواب تينانت A"},
        headers=headers_a
    )
    entry_id = create_res.get_json()["entry"]["id"]

    headers_b = auth_headers(client, "admin_memory_b", "pw123")
    list_res_b = client.get("/api/cs-agent/memory", headers=headers_b)
    assert not any(e["id"] == entry_id for e in list_res_b.get_json()["entries"])

    # Tenant B can't deactivate or delete tenant A's entry
    put_res = client.put(f"/api/cs-agent/memory/{entry_id}", json={"is_active": False}, headers=headers_b)
    assert put_res.status_code == 404
    delete_res = client.delete(f"/api/cs-agent/memory/{entry_id}", headers=headers_b)
    assert delete_res.status_code == 404


def test_memory_recent_logs_endpoint(app, client):
    """GET /api/cs-agent/memory/recent-logs returns this tenant's recent CS agent message logs."""
    headers = auth_headers(client, "admin_recent_logs", "pw123")
    with app.app_context():
        tenant = appmod.Tenant.query.order_by(appmod.Tenant.id.desc()).first()
        appmod.db.session.add(appmod.CSAgentMessageLog(
            tenant_id=tenant.id, direction='in', transcript='شو رصيدي؟'
        ))
        appmod.db.session.add(appmod.CSAgentMessageLog(
            tenant_id=tenant.id, direction='out', transcript='رصيدك 25 دولار.'
        ))
        appmod.db.session.commit()

    res = client.get("/api/cs-agent/memory/recent-logs", headers=headers)
    assert res.status_code == 200
    logs = res.get_json()["logs"]
    assert len(logs) >= 2
    assert any(l["transcript"] == 'شو رصيدي؟' for l in logs)
```

- [ ] **Step 2: Run to verify failure**

```bash
pytest tests/test_cs_agent_tools.py -k "memory_endpoints or memory_recent_logs" -v
```
Expected: FAIL — 404 (routes don't exist yet).

- [ ] **Step 3: Implement the routes**

In `app.py`, add directly after the `cs_get_recent_tickets` route body (after its closing, following the block that starts at line 12330):

```python
@app.route('/api/cs-agent/memory', methods=['GET', 'POST'])
def cs_agent_memory():
    appmod = sys.modules[__name__]
    tenant_id, is_jwt = cs_agent_tools.resolve_tenant_id(appmod)
    if not tenant_id:
        return jsonify(error="Unauthorized or tenant_id required"), 401

    if request.method == 'POST':
        data = request.get_json(silent=True) or {}
        question_text = (data.get('question_text') or '').strip()
        answer_text = (data.get('answer_text') or '').strip()
        if not question_text or not answer_text:
            return jsonify(error="question_text and answer_text are required"), 400

        created_by_id = None
        try:
            verify_jwt_in_request(optional=True)
            claims = get_jwt()
            if claims and claims.get('user_id'):
                created_by_id = int(claims['user_id'])
        except Exception:
            pass

        source = data.get('source') or 'manual'
        source_log_id = data.get('source_log_id')
        entry = cs_agent_tools.add_knowledge_entry(
            appmod, tenant_id, question_text, answer_text,
            source=source, source_log_id=source_log_id, created_by_id=created_by_id
        )
        return jsonify(status='ok', entry=entry.to_dict()), 200

    entries = cs_agent_tools.list_knowledge_entries(appmod, tenant_id)
    return jsonify(status='ok', entries=[e.to_dict() for e in entries]), 200


@app.route('/api/cs-agent/memory/recent-logs', methods=['GET'])
def cs_agent_memory_recent_logs():
    appmod = sys.modules[__name__]
    tenant_id, is_jwt = cs_agent_tools.resolve_tenant_id(appmod)
    if not tenant_id:
        return jsonify(error="Unauthorized or tenant_id required"), 401

    limit = min(int(request.args.get('limit', 50)), 100)
    logs = CSAgentMessageLog.query.filter_by(tenant_id=tenant_id).order_by(
        CSAgentMessageLog.id.desc()
    ).limit(limit).all()
    return jsonify(status='ok', logs=[l.to_dict() for l in logs]), 200


@app.route('/api/cs-agent/memory/<int:entry_id>', methods=['PUT'])
def cs_agent_memory_update(entry_id):
    appmod = sys.modules[__name__]
    tenant_id, is_jwt = cs_agent_tools.resolve_tenant_id(appmod)
    if not tenant_id:
        return jsonify(error="Unauthorized or tenant_id required"), 401

    data = request.get_json(silent=True) or {}
    if 'is_active' not in data:
        return jsonify(error="is_active is required"), 400

    ok = cs_agent_tools.set_knowledge_entry_active(appmod, tenant_id, entry_id, bool(data['is_active']))
    if not ok:
        return jsonify(error="Entry not found"), 404
    return jsonify(status='ok'), 200


@app.route('/api/cs-agent/memory/<int:entry_id>', methods=['DELETE'])
def cs_agent_memory_delete(entry_id):
    appmod = sys.modules[__name__]
    tenant_id, is_jwt = cs_agent_tools.resolve_tenant_id(appmod)
    if not tenant_id:
        return jsonify(error="Unauthorized or tenant_id required"), 401

    ok = cs_agent_tools.delete_knowledge_entry(appmod, tenant_id, entry_id)
    if not ok:
        return jsonify(error="Entry not found"), 404
    return jsonify(status='ok'), 200
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_cs_agent_tools.py -k "memory_endpoints or memory_recent_logs" -v
```
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add app.py tests/test_cs_agent_tools.py
git commit -m "Add CS agent memory CRUD and recent-logs HTTP endpoints"
```

---

### Task 5: The Gemini brain — `query_gemini_agent()`

**Files:**
- Modify: `requirements.txt` (add `google-genai`)
- Modify: `cs_agent_tools.py` (add near `query_elevenlabs_conversational_ai`, i.e. before line 1153)
- Create: `tests/test_cs_agent_gemini_brain.py`

**Interfaces:**
- Consumes: `search_knowledge_entries` (Task 3), `lookup_customer`, `get_customer_status`, `network_diagnostic`, `send_payment_link`, `escalate_to_human` (all pre-existing in `cs_agent_tools.py`), `clean_speech_tags` (pre-existing)
- Produces: `query_gemini_agent(appmod, tenant_id, api_key, incoming_text, sender_phone, customer=None, recent_history=None, model=None, is_admin=False) -> dict | None` with shape `{"intent": "gemini_agent", "reply_text": str, "ticket_tag": str, "escalate": bool}` — consumed by Task 6.

**Model choice note:** `gemini-flash-latest` is Google's own self-updating alias (hot-swapped to their current recommended Flash model, per Gemini API docs' "Model version name patterns" — 2-week breaking-change notice via email). There is no equivalent `-lite-latest` alias today, so the fallback is pinned to the concrete stable `gemini-2.5-flash-lite`, which has a documented free tier. Both are overridable per-tenant via `CSAgentSettings.gemini_model`.

- [ ] **Step 1: Add the dependency**

In `requirements.txt`, add a new line:
```
google-genai
```

Run:
```bash
pip install google-genai
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_cs_agent_gemini_brain.py`:

```python
"""Tests for the self-orchestrated Gemini CS agent brain (query_gemini_agent)."""
from unittest.mock import patch, MagicMock
import pytest
import app as appmod
import cs_agent_tools


def _text_only_response(text):
    resp = MagicMock()
    resp.function_calls = None
    resp.text = text
    return resp


def _fake_api_error(code, message):
    """Builds a genuine errors.APIError instance without depending on its
    real __init__ signature (undocumented/unstable across SDK versions) --
    query_gemini_agent's `except errors.APIError as e` only needs isinstance()
    to hold and `.code`/`.message` to be readable, both set directly here."""
    from google.genai import errors
    err = errors.APIError.__new__(errors.APIError)
    err.code = code
    err.message = message
    return err


def test_query_gemini_agent_returns_none_without_api_key():
    result = cs_agent_tools.query_gemini_agent(
        appmod, tenant_id=1, api_key=None, incoming_text="شو رصيدي؟", sender_phone="70123456"
    )
    assert result is None


def test_query_gemini_agent_returns_none_without_incoming_text():
    result = cs_agent_tools.query_gemini_agent(
        appmod, tenant_id=1, api_key="fake-key", incoming_text="", sender_phone="70123456"
    )
    assert result is None


@patch("cs_agent_tools.search_knowledge_entries", return_value=[])
def test_query_gemini_agent_simple_text_reply(mock_search):
    """No tool calls -- Gemini answers directly, and we return the same dict
    shape query_elevenlabs_conversational_ai used to return."""
    with patch("google.genai.Client") as MockClient:
        instance = MockClient.return_value
        instance.models.generate_content.return_value = _text_only_response("أهلاً! رصيدك صفر.")

        result = cs_agent_tools.query_gemini_agent(
            appmod, tenant_id=1, api_key="fake-key",
            incoming_text="شو رصيدي؟", sender_phone="70123456"
        )

    assert result is not None
    assert result["reply_text"] == "أهلاً! رصيدك صفر."
    assert result["intent"] == "gemini_agent"
    assert result["escalate"] is False


@patch("cs_agent_tools.search_knowledge_entries", return_value=[])
@patch("cs_agent_tools.get_customer_status")
def test_query_gemini_agent_dispatches_tool_call_then_returns_final_text(mock_get_status, mock_search):
    """A single function-call turn: Gemini asks for get_customer_status, we
    call the real tool dispatcher, feed the result back, and Gemini's second
    response (no more function calls) becomes the final reply."""
    mock_get_status.return_value = {"found": True, "balance_due": 25, "expiry_date": "2026-10-01"}

    fake_function_call = MagicMock()
    fake_function_call.name = "get_customer_status"
    fake_function_call.args = {"customer_id": 42}

    first_response = MagicMock()
    first_response.function_calls = [fake_function_call]
    first_response.candidates = [MagicMock(content="model-turn-with-function-call")]

    second_response = _text_only_response("رصيدك المتبقي 25 دولار وبينتهي بـ 2026-10-01.")

    with patch("google.genai.Client") as MockClient:
        instance = MockClient.return_value
        instance.models.generate_content.side_effect = [first_response, second_response]

        result = cs_agent_tools.query_gemini_agent(
            appmod, tenant_id=7, api_key="fake-key",
            incoming_text="شو باقي علي؟", sender_phone="70123456",
            customer=MagicMock(id=42, name="Georges")
        )

    assert result["reply_text"] == "رصيدك المتبقي 25 دولار وبينتهي بـ 2026-10-01."
    mock_get_status.assert_called_once_with(appmod, 7, 42)


@patch("cs_agent_tools.search_knowledge_entries", return_value=[])
def test_query_gemini_agent_falls_back_to_lite_model_on_rate_limit(mock_search):
    """A 429 on the primary model retries once on the fallback model instead
    of giving up immediately."""
    rate_limit_error = _fake_api_error(429, "rate limited")

    with patch("google.genai.Client") as MockClient:
        instance = MockClient.return_value
        instance.models.generate_content.side_effect = [
            rate_limit_error,
            _text_only_response("جواب من الموديل الاحتياطي."),
        ]

        result = cs_agent_tools.query_gemini_agent(
            appmod, tenant_id=1, api_key="fake-key",
            incoming_text="شو رصيدي؟", sender_phone="70123456"
        )

    assert result["reply_text"] == "جواب من الموديل الاحتياطي."
    assert instance.models.generate_content.call_count == 2
    second_call_model = instance.models.generate_content.call_args_list[1].kwargs.get("model")
    assert second_call_model == cs_agent_tools.GEMINI_MODEL_FALLBACK


@patch("cs_agent_tools.search_knowledge_entries", return_value=[])
def test_query_gemini_agent_returns_none_on_non_rate_limit_error(mock_search):
    """Any other API error (not 429) returns None immediately -- caller falls
    back to the rule-based processor, no retry on the fallback model."""
    server_error = _fake_api_error(500, "internal error")

    with patch("google.genai.Client") as MockClient:
        instance = MockClient.return_value
        instance.models.generate_content.side_effect = [server_error]

        result = cs_agent_tools.query_gemini_agent(
            appmod, tenant_id=1, api_key="fake-key",
            incoming_text="شو رصيدي؟", sender_phone="70123456"
        )

    assert result is None
    assert instance.models.generate_content.call_count == 1
```

- [ ] **Step 3: Run to verify failure**

```bash
pytest tests/test_cs_agent_gemini_brain.py -v
```
Expected: FAIL — `AttributeError: module 'cs_agent_tools' has no attribute 'query_gemini_agent'`.

- [ ] **Step 4: Implement `query_gemini_agent()` and its helpers**

In `cs_agent_tools.py`, add before `query_elevenlabs_conversational_ai` (before line 1153):

```python
# gemini-flash-latest is Google's own self-updating alias -- always hot-swapped
# to their current recommended Flash model (2-week notice on breaking changes).
# There is no "-lite-latest" equivalent today, so the fallback is pinned to a
# concrete stable model with a documented free tier instead of guessing at a
# future lite alias name.
GEMINI_MODEL_PRIMARY = "gemini-flash-latest"
GEMINI_MODEL_FALLBACK = "gemini-2.5-flash-lite"

# Covers the two-sequential-tool diagnostic chain (lookup_customer then
# network_diagnostic) from the original CS agent spec, with headroom.
GEMINI_MAX_TOOL_ROUNDTRIPS = 6


def _gemini_tools():
    from google.genai import types
    return [types.Tool(function_declarations=[
        types.FunctionDeclaration(
            name='lookup_customer',
            description="Find a customer by their phone number. Use this when you don't yet know who you're speaking with.",
            parameters_json_schema={
                'type': 'object',
                'properties': {
                    'phone': {'type': 'string', 'description': 'The customer phone number, any format.'}
                },
                'required': ['phone'],
            },
        ),
        types.FunctionDeclaration(
            name='get_customer_status',
            description="Get a specific customer's subscription status, balance, expiry date, and ONU status.",
            parameters_json_schema={
                'type': 'object',
                'properties': {
                    'customer_id': {'type': 'integer', 'description': 'The customer ID, from lookup_customer.'}
                },
                'required': ['customer_id'],
            },
        ),
        types.FunctionDeclaration(
            name='network_diagnostic',
            description="Check a customer's live network/connectivity status (ONU online, PON outage, etc). Can take several seconds.",
            parameters_json_schema={
                'type': 'object',
                'properties': {
                    'customer_id': {'type': 'integer', 'description': 'The customer ID to diagnose.'}
                },
                'required': ['customer_id'],
            },
        ),
        types.FunctionDeclaration(
            name='send_payment_link',
            description='Send the customer a self-serve payment link for their balance due.',
            parameters_json_schema={
                'type': 'object',
                'properties': {
                    'customer_id': {'type': 'integer', 'description': 'The customer ID to send a payment link for.'}
                },
                'required': ['customer_id'],
            },
        ),
        types.FunctionDeclaration(
            name='escalate_to_human',
            description='Flag this conversation for a human to follow up, when you cannot resolve the issue yourself.',
            parameters_json_schema={
                'type': 'object',
                'properties': {
                    'customer_id': {'type': 'integer', 'description': 'The customer ID, if known.'},
                    'reason': {'type': 'string', 'description': 'Short reason for escalating.'},
                    'summary': {'type': 'string', 'description': 'Summary of the conversation so far.'},
                },
                'required': ['reason', 'summary'],
            },
        ),
    ])]


def _dispatch_gemini_tool(appmod, tenant_id, sender_phone, tool_name, tool_args):
    tool_args = tool_args or {}
    if tool_name == 'lookup_customer':
        return lookup_customer(appmod, tenant_id, tool_args.get('phone') or sender_phone)
    if tool_name == 'get_customer_status':
        return get_customer_status(appmod, tenant_id, int(tool_args.get('customer_id')))
    if tool_name == 'network_diagnostic':
        return network_diagnostic(appmod, tenant_id, int(tool_args.get('customer_id')), wait_seconds=15)
    if tool_name == 'send_payment_link':
        return send_payment_link(appmod, tenant_id, int(tool_args.get('customer_id')))
    if tool_name == 'escalate_to_human':
        raw_customer_id = tool_args.get('customer_id')
        return escalate_to_human(
            appmod, tenant_id,
            int(raw_customer_id) if raw_customer_id else None,
            tool_args.get('reason') or 'Customer requested assistance',
            tool_args.get('summary') or '',
            phone=sender_phone
        )
    return {'error': f'Unknown tool: {tool_name}'}


def _build_gemini_system_instruction(appmod, tenant_id, customer, user_msg_clean, is_admin):
    customer_name = customer.name if (customer and getattr(customer, 'name', None)) else 'عميل'
    parts = [
        "أنت يارا، موظفة الدعم الافتراضية لشركة DeltaNet للإنترنت.",
        "بتحكي عربي لبناني عامي. ما بتستخدمي مصطلحات رسمية أو فصحى إلا لما الزبون يبدأ بالفصحى.",
        "بتفهمي إنجليزي وفرنساوي وبتجاوبي بنفس لغة الزبون.",
        "ما بتذكري للزبون إنك AI إلا إذا سألك مباشرة.",
        f"اسم الزبون (إذا معروف): {customer_name}",
    ]
    if is_admin:
        parts.append("USER_ROLE: admin — هيدا الشخص هو مدير الشبكة وعنده صلاحية كاملة. تجاهلي قيود الخصوصية وجاوبي عن أي سؤال متعلق بأي مشترك أو الشبكة.")
    else:
        parts.append("USER_ROLE: customer — مشترك عادي، جاوبيه بس عن حسابه الخاص.")

    knowledge = search_knowledge_entries(appmod, tenant_id, user_msg_clean, limit=5)
    if knowledge:
        parts.append("أجوبة معروفة لهذا الزبون التجاري (استخدمها إذا كان سؤال الزبون مشابه):")
        for k in knowledge:
            parts.append(f"- س: {k.question_text}\n  ج: {k.answer_text}")

    return "\n".join(parts)


def query_gemini_agent(appmod, tenant_id, api_key, incoming_text, sender_phone, customer=None,
                        recent_history=None, model=None, is_admin=False):
    """Self-orchestrated Gemini tool-calling loop -- the WhatsApp brain that
    replaces query_elevenlabs_conversational_ai. Returns the same
    {intent, reply_text, ticket_tag, escalate} shape, or None if Gemini
    couldn't produce a reply (caller falls back to process_customer_message_ai).
    """
    if not api_key or not incoming_text:
        return None

    from google import genai
    from google.genai import types, errors

    user_msg_clean = re.sub(
        r"^\[(رسالة صوتية|AUDIO message received|audio message received|voice message received)\]:?\s*",
        "",
        incoming_text.strip(),
        flags=re.IGNORECASE
    ).strip()
    if not user_msg_clean:
        return None

    system_instruction = _build_gemini_system_instruction(appmod, tenant_id, customer, user_msg_clean, is_admin)

    contents = []
    if recent_history:
        for m in recent_history[-4:]:
            role = 'user' if m.get('direction') == 'in' else 'model'
            txt = clean_speech_tags(m.get('transcript') or '')
            if txt:
                contents.append(types.Content(role=role, parts=[types.Part.from_text(text=txt)]))
    contents.append(types.Content(role='user', parts=[types.Part.from_text(text=user_msg_clean)]))

    config = types.GenerateContentConfig(
        tools=_gemini_tools(),
        system_instruction=system_instruction,
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
    )

    candidate_models = [model or GEMINI_MODEL_PRIMARY, GEMINI_MODEL_FALLBACK]
    for attempt, candidate_model in enumerate(candidate_models):
        try:
            client = genai.Client(api_key=api_key)
            response = client.models.generate_content(model=candidate_model, contents=list(contents), config=config)

            for _ in range(GEMINI_MAX_TOOL_ROUNDTRIPS):
                if not response.function_calls:
                    break
                contents.append(response.candidates[0].content)
                response_parts = []
                for fc in response.function_calls:
                    result = _dispatch_gemini_tool(appmod, tenant_id, sender_phone, fc.name, dict(fc.args or {}))
                    response_parts.append(types.Part.from_function_response(name=fc.name, response=result))
                contents.append(types.Content(role='tool', parts=response_parts))
                response = client.models.generate_content(model=candidate_model, contents=list(contents), config=config)

            final_text = (response.text or '').strip()
            if final_text:
                logging.info(f"Gemini ({candidate_model}) reply: {final_text[:80]}")
                return {
                    "intent": "gemini_agent",
                    "reply_text": final_text,
                    "ticket_tag": "محادثة ذكاء اصطناعي",
                    "escalate": False
                }
            return None  # exhausted tool loop with no final text -- fall back to rule-based
        except errors.APIError as e:
            if e.code == 429 and attempt < len(candidate_models) - 1:
                logging.warning(f"Gemini rate-limited on {candidate_model}, retrying on {candidate_models[attempt + 1]}")
                continue
            logging.warning(f"Gemini API error ({e.code}): {getattr(e, 'message', e)}")
            return None
        except Exception as ex:
            logging.warning(f"Error communicating with Gemini: {ex}")
            return None

    return None
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
pytest tests/test_cs_agent_gemini_brain.py -v
```
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add requirements.txt cs_agent_tools.py tests/test_cs_agent_gemini_brain.py
git commit -m "Add self-orchestrated Gemini tool-calling brain for the CS agent"
```

---

### Task 6: Wire `query_gemini_agent` into `handle_whatsapp_cs_ai_reply`

**Files:**
- Modify: `cs_agent_tools.py:1701-1727` (inside `handle_whatsapp_cs_ai_reply`)
- Test: `tests/test_cs_agent_tools.py`

**Interfaces:**
- Consumes: `query_gemini_agent` (Task 5), `CSAgentSettings.gemini_api_key`/`.gemini_model` (Task 1)
- Produces: `handle_whatsapp_cs_ai_reply` now tries Gemini before the rule-based fallback, instead of ElevenLabs ConvAI. TTS voice-reply behavior (still keyed off `target_agent_id` for ElevenLabs) is unchanged.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_cs_agent_tools.py`:

```python
def test_whatsapp_reply_uses_gemini_when_tenant_key_configured(app, client):
    """handle_whatsapp_cs_ai_reply calls query_gemini_agent (not ElevenLabs
    ConvAI) when the tenant has a gemini_api_key configured."""
    import cs_agent_tools
    from unittest.mock import patch, MagicMock

    auth_headers(client, "admin_wa_gemini", "pw123")
    with app.app_context():
        tenant = appmod.Tenant.query.order_by(appmod.Tenant.id.desc()).first()
        settings = appmod.CSAgentSettings(tenant_id=tenant.id, gemini_api_key="fake-key")
        appmod.db.session.add(settings)

        wa_settings = MagicMock()
        wa_settings.access_token = "wa-token"
        wa_settings.phone_number_id = "123"
        wa_settings.api_version = "v19.0"
        appmod.db.session.commit()

        with patch("cs_agent_tools.query_gemini_agent") as mock_gemini, \
             patch("cs_agent_tools.query_elevenlabs_conversational_ai") as mock_convai, \
             patch("requests.post"):
            mock_gemini.return_value = {
                "intent": "gemini_agent", "reply_text": "جواب من جيميناي",
                "ticket_tag": "محادثة ذكاء اصطناعي", "escalate": False
            }
            cs_agent_tools.handle_whatsapp_cs_ai_reply(
                appmod, tenant.id, "70123456", None, "شو رصيدي؟",
                is_voice=False, settings=wa_settings
            )

        mock_gemini.assert_called_once()
        mock_convai.assert_not_called()


def test_whatsapp_reply_falls_back_to_rule_based_without_gemini_key(app, client):
    """With no gemini_api_key configured, query_gemini_agent is never called
    and the rule-based processor answers instead."""
    import cs_agent_tools
    from unittest.mock import patch, MagicMock

    auth_headers(client, "admin_wa_no_gemini", "pw123")
    with app.app_context():
        tenant = appmod.Tenant.query.order_by(appmod.Tenant.id.desc()).first()

        wa_settings = MagicMock()
        wa_settings.access_token = "wa-token"
        wa_settings.phone_number_id = "123"
        wa_settings.api_version = "v19.0"

        with patch("cs_agent_tools.query_gemini_agent") as mock_gemini, \
             patch("requests.post"):
            cs_agent_tools.handle_whatsapp_cs_ai_reply(
                appmod, tenant.id, "70123456", None, "مرحبا",
                is_voice=False, settings=wa_settings
            )

        mock_gemini.assert_not_called()
```

- [ ] **Step 2: Run to verify failure**

```bash
pytest tests/test_cs_agent_tools.py -k "uses_gemini_when_tenant_key or falls_back_to_rule_based_without_gemini_key" -v
```
Expected: FAIL — `mock_convai.assert_not_called()` fails (ElevenLabs ConvAI is still called), since the wiring hasn't changed yet.

- [ ] **Step 3: Update `handle_whatsapp_cs_ai_reply`**

In `cs_agent_tools.py`, replace lines 1664-1721 (from `# Resolve target ElevenLabs agent ID and admin permissions` through the end of the `except Exception as ex_conv:` block) with:

```python
    # Resolve target ElevenLabs agent ID (still used for TTS voice selection)
    # and admin permissions, and this tenant's own Gemini key (the brain).
    target_agent_id = getattr(settings, 'elevenlabs_agent_id', None)
    is_admin = False
    cs_settings = None

    try:
        cs_settings = appmod.CSAgentSettings.query.filter_by(tenant_id=tenant_id).first()
        if cs_settings:
            if cs_settings.elevenlabs_agent_id:
                target_agent_id = cs_settings.elevenlabs_agent_id
            if cs_settings.admin_mobile_number:
                admin_phone_clean = re.sub(r'\D', '', str(cs_settings.admin_mobile_number))
                if phone_digits and admin_phone_clean and (phone_digits == admin_phone_clean or phone_digits.endswith(admin_phone_clean) or admin_phone_clean.endswith(phone_digits)):
                    is_admin = True
    except Exception:
        pass

    if not target_agent_id:
        try:
            target_agent_id = current_app.config.get('ELEVENLABS_AGENT_ID') or os.environ.get('ELEVENLABS_AGENT_ID')
        except Exception:
            target_agent_id = os.environ.get('ELEVENLABS_AGENT_ID')

    # Fetch recent conversation history from active session if available
    recent_history = []
    if session:
        try:
            recent_logs = appmod.CSAgentMessageLog.query.filter_by(
                session_id=session.id
            ).order_by(appmod.CSAgentMessageLog.id.desc()).limit(4).all()
            for rl in reversed(recent_logs):
                recent_history.append({
                    "direction": rl.direction,
                    "transcript": rl.transcript
                })
        except Exception as ex_hist:
            logging.warning(f"Error fetching recent session history: {ex_hist}")

    ai_result = None
    gemini_key = getattr(cs_settings, 'gemini_api_key', None) if cs_settings else None
    if gemini_key:
        try:
            ai_result = query_gemini_agent(
                appmod, tenant_id, gemini_key, incoming_text, sender_phone,
                customer=customer, recent_history=recent_history,
                model=getattr(cs_settings, 'gemini_model', None), is_admin=is_admin
            )
        except Exception as ex_gemini:
            logging.warning(f"Gemini agent call failed, will fallback: {ex_gemini}")
```

This removes the `query_elevenlabs_conversational_ai(...)` call entirely (the function itself stays defined in the file, just no longer called from the WhatsApp path) and the duplicate `recent_history` fetch that used to sit below it. The existing lines immediately after (the `# Fallback to local rule-based AI processor...` block through the rest of the function) are unchanged.

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_cs_agent_tools.py -k "uses_gemini_when_tenant_key or falls_back_to_rule_based_without_gemini_key" -v
```
Expected: both PASS.

- [ ] **Step 5: Run the full CS agent test suite to check for regressions**

```bash
pytest tests/test_cs_agent_tools.py tests/test_cs_agent_gemini_brain.py -v
```
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add cs_agent_tools.py tests/test_cs_agent_tools.py
git commit -m "Wire Gemini brain into handle_whatsapp_cs_ai_reply, replacing ElevenLabs ConvAI"
```

---

### Task 7: Frontend — Gemini API key field in CS Agent settings

**Files:**
- Modify: `frontend/src/components/CSAgentVoiceTest.js`

**Interfaces:**
- Consumes: `GET /api/cs-agent/config` (now returns `gemini_api_key`, `gemini_model`), `POST /api/cs-agent/config` (Task 2)

- [ ] **Step 1: Add state and fetch handling**

In `CSAgentVoiceTest.js`, after the `adminMobile`/`isEditingAdminMobile` state declarations (around line 19-21), add:

```javascript
    const [geminiApiKey, setGeminiApiKey] = useState('');
    const [isEditingGeminiKey, setIsEditingGeminiKey] = useState(false);
```

Update the config-fetch `useEffect` (around line 48-62) to also read `gemini_api_key`:

```javascript
    useEffect(() => {
        const token = localStorage.getItem('token');
        axios.get('/api/cs-agent/config', {
            headers: token ? { Authorization: `Bearer ${token}` } : {}
        })
            .then(res => {
                if (res.data) {
                    if (res.data.elevenlabs_agent_id) setAgentId(res.data.elevenlabs_agent_id);
                    if (res.data.admin_mobile_number) setAdminMobile(res.data.admin_mobile_number);
                    if (res.data.gemini_api_key) setGeminiApiKey(res.data.gemini_api_key);
                }
            })
            .catch(() => {
                // Ignore config fetch error in dev
            });
    }, []);
```

- [ ] **Step 2: Include it in the save call**

Update `handleSaveAgentId` (around line 78-101) to also send `gemini_api_key`:

```javascript
    const handleSaveAgentId = async () => {
        if (!agentId.trim()) {
            setErrorMessage('يجب إدخال ElevenLabs Agent ID لحفظ الإعدادات.');
            return;
        }
        setSavingConfig(true);
        setSaveSuccess('');
        setErrorMessage('');
        try {
            const token = localStorage.getItem('token');
            await axios.post('/api/cs-agent/config', {
                elevenlabs_agent_id: agentId.trim(),
                admin_mobile_number: adminMobile.trim(),
                gemini_api_key: geminiApiKey.trim()
            }, { headers: { Authorization: `Bearer ${token}` } });

            setSaveSuccess('تم حفظ إعدادات الـ Agent بنجاح!');
            setIsEditingAgentId(false);
            setIsEditingAdminMobile(false);
            setIsEditingGeminiKey(false);
        } catch (err) {
            setErrorMessage(err.response?.data?.error || 'حدث خطأ أثناء الحفظ.');
        } finally {
            setSavingConfig(false);
        }
    };
```

- [ ] **Step 3: Add the field to the UI**

In the render, directly after the "Network Admin Mobile" `Box` (after line 535, the closing `</Box>` of the admin mobile field block), add:

```jsx
                            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5, mb: 4, flexWrap: 'wrap' }}>
                                <TextField
                                    size="small"
                                    label="Gemini API Key (free tier)"
                                    value={geminiApiKey}
                                    onChange={(e) => setGeminiApiKey(e.target.value)}
                                    placeholder="AIza..."
                                    disabled={!isEditingGeminiKey || (status !== 'idle' && status !== 'error')}
                                    helperText={
                                        <span>
                                            بيحل محل ElevenLabs كـ"دماغ" الرد على الواتساب مجاناً.{' '}
                                            <a href="https://aistudio.google.com/apikey" target="_blank" rel="noopener noreferrer">
                                                احصل على مفتاح مجاني من هون
                                            </a>
                                        </span>
                                    }
                                    sx={{ minWidth: 260, flexGrow: 1 }}
                                    InputProps={{
                                        endAdornment: (
                                            <InputAdornment position="end">
                                                <IconButton
                                                    onClick={() => setIsEditingGeminiKey(true)}
                                                    disabled={status !== 'idle' && status !== 'error'}
                                                    edge="end"
                                                >
                                                    <EditIcon fontSize="small" />
                                                </IconButton>
                                            </InputAdornment>
                                        )
                                    }}
                                />
                            </Box>
```

- [ ] **Step 4: Manually verify in the browser**

Start the dev server (per this project's launch config), open the CS Agent settings tab, confirm the "Gemini API Key" field appears, that typing a value and clicking the save button (پbutton reused from `handleSaveAgentId`) persists it, and that reloading the page shows the saved value.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/CSAgentVoiceTest.js
git commit -m "Add Gemini API key field to CS Agent settings UI"
```

---

### Task 8: Frontend — Agent Memory manager (manual add + list + promote-from-log)

**Files:**
- Create: `frontend/src/components/AgentMemoryManager.js`
- Modify: `frontend/src/components/CSAgentVoiceTest.js` (mount the new component)

**Interfaces:**
- Consumes: `GET/POST /api/cs-agent/memory`, `PUT/DELETE /api/cs-agent/memory/<id>`, `GET /api/cs-agent/memory/recent-logs` (Task 4)

- [ ] **Step 1: Create the component**

Create `frontend/src/components/AgentMemoryManager.js`:

```javascript
import React, { useState, useEffect, useCallback } from 'react';
import {
    Box, Card, CardContent, Typography, TextField, Button,
    Stack, IconButton, CircularProgress, Alert, Paper,
    Table, TableBody, TableCell, TableHead, TableRow, Switch, Tabs, Tab
} from '@mui/material';
import { Add as AddIcon, Delete as DeleteIcon } from '@mui/icons-material';
import axios from 'axios';

function authHeaders() {
    const token = localStorage.getItem('token');
    return token ? { Authorization: `Bearer ${token}` } : {};
}

export default function AgentMemoryManager() {
    const [tab, setTab] = useState(0); // 0 = manual add / list, 1 = promote from conversation
    const [question, setQuestion] = useState('');
    const [answer, setAnswer] = useState('');
    const [entries, setEntries] = useState([]);
    const [recentLogs, setRecentLogs] = useState([]);
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState('');
    const [success, setSuccess] = useState('');

    const loadEntries = useCallback(() => {
        axios.get('/api/cs-agent/memory', { headers: authHeaders() })
            .then(res => setEntries(res.data.entries || []))
            .catch(() => {});
    }, []);

    const loadRecentLogs = useCallback(() => {
        axios.get('/api/cs-agent/memory/recent-logs', { headers: authHeaders() })
            .then(res => setRecentLogs(res.data.logs || []))
            .catch(() => {});
    }, []);

    useEffect(() => {
        loadEntries();
    }, [loadEntries]);

    useEffect(() => {
        if (tab === 1) loadRecentLogs();
    }, [tab, loadRecentLogs]);

    const handleAdd = async () => {
        if (!question.trim() || !answer.trim()) {
            setError('لازم تكتب السؤال والجواب.');
            return;
        }
        setSaving(true);
        setError('');
        setSuccess('');
        try {
            await axios.post('/api/cs-agent/memory', {
                question_text: question.trim(),
                answer_text: answer.trim()
            }, { headers: authHeaders() });
            setQuestion('');
            setAnswer('');
            setSuccess('تمت إضافة الجواب لذاكرة المساعد.');
            loadEntries();
        } catch (err) {
            setError(err.response?.data?.error || 'حدث خطأ أثناء الحفظ.');
        } finally {
            setSaving(false);
        }
    };

    const handleUseLogLine = (transcript, target) => {
        if (target === 'question') setQuestion(transcript);
        else setAnswer(transcript);
        setTab(0);
    };

    const handleToggleActive = async (entry) => {
        try {
            await axios.put(`/api/cs-agent/memory/${entry.id}`, {
                is_active: !entry.is_active
            }, { headers: authHeaders() });
            loadEntries();
        } catch (err) {
            setError(err.response?.data?.error || 'حدث خطأ أثناء التحديث.');
        }
    };

    const handleDelete = async (entry) => {
        try {
            await axios.delete(`/api/cs-agent/memory/${entry.id}`, { headers: authHeaders() });
            loadEntries();
        } catch (err) {
            setError(err.response?.data?.error || 'حدث خطأ أثناء الحذف.');
        }
    };

    return (
        <Card elevation={1} sx={{ borderRadius: '16px', mt: 3 }}>
            <CardContent sx={{ p: 3 }}>
                <Typography variant="h6" fontWeight={700} gutterBottom>
                    ذاكرة المساعد (Agent Memory)
                </Typography>
                <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
                    أسئلة وأجوبة موثوقة يستخدمها المساعد ليجاوب بشكل أدق على أسئلة زبائنك المتكررة.
                </Typography>

                {error && <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError('')}>{error}</Alert>}
                {success && <Alert severity="success" sx={{ mb: 2 }} onClose={() => setSuccess('')}>{success}</Alert>}

                <Tabs value={tab} onChange={(e, v) => setTab(v)} sx={{ mb: 2 }}>
                    <Tab label="إضافة يدوية / القائمة" />
                    <Tab label="من محادثة سابقة" />
                </Tabs>

                {tab === 0 && (
                    <>
                        <Stack spacing={2} sx={{ mb: 3 }}>
                            <TextField
                                label="السؤال" value={question} onChange={(e) => setQuestion(e.target.value)}
                                fullWidth multiline minRows={2}
                            />
                            <TextField
                                label="الجواب" value={answer} onChange={(e) => setAnswer(e.target.value)}
                                fullWidth multiline minRows={2}
                            />
                            <Box>
                                <Button
                                    variant="contained" startIcon={saving ? <CircularProgress size={16} /> : <AddIcon />}
                                    onClick={handleAdd} disabled={saving}
                                >
                                    إضافة للذاكرة
                                </Button>
                            </Box>
                        </Stack>

                        <Paper variant="outlined" sx={{ borderRadius: '12px' }}>
                            <Table size="small">
                                <TableHead>
                                    <TableRow>
                                        <TableCell>السؤال</TableCell>
                                        <TableCell>الجواب</TableCell>
                                        <TableCell align="center">مفعّل</TableCell>
                                        <TableCell align="center">حذف</TableCell>
                                    </TableRow>
                                </TableHead>
                                <TableBody>
                                    {entries.length === 0 ? (
                                        <TableRow>
                                            <TableCell colSpan={4} align="center">
                                                <Typography variant="body2" color="text.secondary">لا يوجد أي إدخال بعد.</Typography>
                                            </TableCell>
                                        </TableRow>
                                    ) : entries.map(entry => (
                                        <TableRow key={entry.id}>
                                            <TableCell sx={{ maxWidth: 260 }}>{entry.question_text}</TableCell>
                                            <TableCell sx={{ maxWidth: 260 }}>{entry.answer_text}</TableCell>
                                            <TableCell align="center">
                                                <Switch checked={entry.is_active} onChange={() => handleToggleActive(entry)} size="small" />
                                            </TableCell>
                                            <TableCell align="center">
                                                <IconButton size="small" color="error" onClick={() => handleDelete(entry)}>
                                                    <DeleteIcon fontSize="small" />
                                                </IconButton>
                                            </TableCell>
                                        </TableRow>
                                    ))}
                                </TableBody>
                            </Table>
                        </Paper>
                    </>
                )}

                {tab === 1 && (
                    <Paper variant="outlined" sx={{ borderRadius: '12px', maxHeight: 360, overflowY: 'auto' }}>
                        <Table size="small">
                            <TableHead>
                                <TableRow>
                                    <TableCell>الاتجاه</TableCell>
                                    <TableCell>النص</TableCell>
                                    <TableCell align="center">استخدم كسؤال</TableCell>
                                    <TableCell align="center">استخدم كجواب</TableCell>
                                </TableRow>
                            </TableHead>
                            <TableBody>
                                {recentLogs.length === 0 ? (
                                    <TableRow>
                                        <TableCell colSpan={4} align="center">
                                            <Typography variant="body2" color="text.secondary">لا يوجد محادثات بعد.</Typography>
                                        </TableCell>
                                    </TableRow>
                                ) : recentLogs.map(log => (
                                    <TableRow key={log.id}>
                                        <TableCell>{log.direction === 'in' ? 'الزبون' : 'المساعد'}</TableCell>
                                        <TableCell sx={{ maxWidth: 300 }}>{log.transcript}</TableCell>
                                        <TableCell align="center">
                                            <Button size="small" onClick={() => handleUseLogLine(log.transcript, 'question')}>سؤال</Button>
                                        </TableCell>
                                        <TableCell align="center">
                                            <Button size="small" onClick={() => handleUseLogLine(log.transcript, 'answer')}>جواب</Button>
                                        </TableCell>
                                    </TableRow>
                                ))}
                            </TableBody>
                        </Table>
                    </Paper>
                )}
            </CardContent>
        </Card>
    );
}
```

- [ ] **Step 2: Mount it in the settings page**

In `CSAgentVoiceTest.js`, add the import near the top (after the `axios` import, around line 15):

```javascript
import AgentMemoryManager from './AgentMemoryManager';
```

Then mount it at the end of the main returned `Box`, directly after the closing `</Grid>` of the two-column layout (after line 867, before the final `</Box>` at line 868):

```jsx
            <AgentMemoryManager />
```

- [ ] **Step 3: Manually verify in the browser**

Start the dev server, open the CS Agent settings tab, confirm:
- The "ذاكرة المساعد" card renders below the existing voice console
- Adding a question/answer through the form succeeds and appears in the table below
- Toggling the switch and deleting a row both work and reflect immediately
- Switching to the "من محادثة سابقة" tab loads recent log lines (add a couple of `CSAgentMessageLog` rows via the backend if none exist yet), and clicking "سؤال"/"جواب" pre-fills the form on the first tab

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/AgentMemoryManager.js frontend/src/components/CSAgentVoiceTest.js
git commit -m "Add Agent Memory manager UI (manual add, list, promote from conversation)"
```

---

## Self-Review Notes

- **Spec coverage**: Task 1-2 cover multi-tenant Gemini config; Task 3-4 cover the memory table, search, and CRUD/HTTP surface (manual add + promote-from-log); Task 5-6 cover the Gemini brain and its Flash → Flash-Lite → rule-based fallback chain, wired to replace ElevenLabs ConvAI on WhatsApp; Task 7-8 cover both frontend pieces. STT/TTS and the tool HTTP endpoints are untouched throughout, per spec Non-Goals.
- **Type consistency checked**: `query_gemini_agent(appmod, tenant_id, api_key, incoming_text, sender_phone, customer=None, recent_history=None, model=None, is_admin=False)` signature is identical between its definition (Task 5) and every call site (Task 5 tests, Task 6 wiring and tests). `CSAgentKnowledgeEntry` field names (`question_text`, `answer_text`, `source`, `source_log_id`, `is_active`) match across the model (Task 1), the helper functions (Task 3), the HTTP responses (Task 4), and the frontend's expected JSON keys (Task 8).
- **Migration risk flagged explicitly** in Task 1 Step 3 rather than guessed at, given this repo's known multi-head migration drift.

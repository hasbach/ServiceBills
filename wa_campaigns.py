"""Background WhatsApp campaigns (Messaging -> bulk sends).

A campaign is saved with one recipient row per number *before* anything is
sent, then a background greenlet works through the pending rows one at a
time. Every row is committed as it finishes, so:

  * the admin can close the page -- nothing depends on the HTTP request;
  * one failed number is recorded and the next one is sent;
  * a restart/deploy mid-campaign loses at most the row in flight: the
    campaign's heartbeat goes stale and resume_stale() picks it up again,
    sending only rows that are still pending (never a number twice).

`appmod` is the app module (models, db, senders), passed in like
whatsapp_inbox does, so this module has no import cycle with app.py.
"""
import functools
import logging
from datetime import datetime, timedelta

import whatsapp_inbox

# A running campaign whose sender has not touched it for this long has lost
# its worker. One send times out after 10s, so a live worker beats far sooner.
STALE_AFTER = timedelta(minutes=3)

INTERRUPTED_ERROR = ('Interrupted by a server restart while sending -- it may or may not '
                     'have reached the customer, so it was not resent automatically.')

# Meta's receipts only ever move forward; a late 'sent' must not undo 'read'.
_DELIVERY_RANK = {'sent': 1, 'delivered': 2, 'read': 3}


def _now():
    return datetime.utcnow()


def _iso(dt):
    return dt.strftime('%Y-%m-%dT%H:%M:%SZ') if dt else None


# --------------------------------------------------------------------------
# Create / start
# --------------------------------------------------------------------------

def create_campaign(appmod, tenant_id, kind, title, params, recipients, created_by=None):
    """Save the campaign and all its recipient rows (status 'pending').
    `recipients` is a list of dicts: name, phone, customer_id (optional),
    and optionally status/error for rows already known to be bad."""
    db = appmod.db
    campaign = appmod.WhatsAppCampaign(
        tenant_id=tenant_id, kind=kind, title=title[:200], params=params or {},
        status='queued', total=len(recipients), created_by=created_by, created_at=_now())
    db.session.add(campaign)
    db.session.flush()
    now = _now()
    for r in recipients:
        db.session.add(appmod.WhatsAppCampaignRecipient(
            tenant_id=tenant_id, campaign_id=campaign.id,
            customer_id=r.get('customer_id'), name=(r.get('name') or '')[:200] or None,
            phone=(str(r.get('phone') or '')[:40]) or None,
            status=r.get('status', 'pending'), error=r.get('error'), updated_at=now))
    db.session.commit()
    return campaign


def start(appmod, campaign_id):
    """Send the campaign off the request, in the bulk-notification pool."""
    whatsapp_inbox.run_background(
        appmod.app, functools.partial(run_campaign, appmod, campaign_id),
        pool=getattr(appmod, 'BULK_NOTIFY_GREENLET_POOL', None))


# --------------------------------------------------------------------------
# The sender
# --------------------------------------------------------------------------

def _claim(appmod, campaign_id):
    """Atomically take ownership of a campaign: queued, or running with a
    stale heartbeat. Returns True when this worker now owns it, so two
    workers (or a resume racing the original) never send the same campaign."""
    C = appmod.WhatsAppCampaign
    now = _now()
    stale = now - STALE_AFTER
    claimed = (C.query.filter(C.id == campaign_id)
               .filter((C.status == 'queued') |
                       ((C.status == 'running') & ((C.heartbeat_at.is_(None)) | (C.heartbeat_at < stale))))
               .update({C.status: 'running', C.heartbeat_at: now}, synchronize_session=False))
    appmod.db.session.commit()
    return claimed == 1


def run_campaign(appmod, campaign_id):
    db = appmod.db
    C, R = appmod.WhatsAppCampaign, appmod.WhatsAppCampaignRecipient
    if not _claim(appmod, campaign_id):
        return
    campaign = db.session.get(C, campaign_id)
    if campaign.started_at is None:
        campaign.started_at = _now()

    # A row left in 'sending' means the previous worker died mid-call to
    # Meta: we can't know whether it went out, so don't risk a duplicate.
    R.query.filter_by(campaign_id=campaign_id, status='sending').update(
        {R.status: 'failed', R.error: INTERRUPTED_ERROR, R.updated_at: _now()},
        synchronize_session=False)
    db.session.commit()

    while True:
        db.session.refresh(campaign)
        if campaign.status != 'running':  # cancelled from the UI
            break
        row = (R.query.filter_by(campaign_id=campaign_id, status='pending')
               .order_by(R.id).first())
        if row is None:
            break
        took = (R.query.filter_by(id=row.id, status='pending')
                .update({R.status: 'sending', R.updated_at: _now()}, synchronize_session=False))
        db.session.commit()
        if took != 1:
            continue
        try:
            result = _send_one(appmod, campaign, row)
        except Exception as exc:  # never let one number stop the campaign
            logging.exception("WhatsApp campaign %s: recipient %s crashed", campaign_id, row.id)
            db.session.rollback()
            result = {'status': 'failed', 'error': f'Error: {exc}'}
        row = db.session.get(R, row.id)
        row.status = result['status']
        row.error = result.get('error')
        if result.get('message_id'):
            row.wa_message_id = result['message_id']
        if result['status'] == 'sent':
            row.sent_at = _now()
        row.updated_at = _now()
        campaign.heartbeat_at = _now()
        db.session.commit()

    db.session.refresh(campaign)
    if campaign.status == 'running':
        campaign.status = 'completed'
    if campaign.status == 'cancelled':
        R.query.filter_by(campaign_id=campaign_id, status='pending').update(
            {R.status: 'cancelled', R.updated_at: _now()}, synchronize_session=False)
    campaign.finished_at = _now()
    db.session.commit()


def _map_result(res):
    """send_whatsapp_message-style dict -> recipient row outcome."""
    if not isinstance(res, dict):
        return {'status': 'sent'}
    if res.get('success'):
        out = {'status': 'sent', 'message_id': res.get('message_id')}
        if 'Simulated' in str(res.get('status', '')):
            out['error'] = res.get('details')  # shown as a note, not a failure
        return out
    if res.get('status') == 'Skipped':
        return {'status': 'skipped', 'error': res.get('error') or res.get('details')}
    return {'status': 'failed', 'error': res.get('error') or res.get('details') or 'Failed'}


def _send_one(appmod, campaign, row):
    params = campaign.params or {}
    if campaign.kind == 'customers':
        customer = appmod.db.session.get(appmod.Customer, row.customer_id) if row.customer_id else None
        if customer is None or customer.tenant_id != campaign.tenant_id:
            return {'status': 'skipped', 'error': 'Customer no longer exists'}
        res = appmod.send_whatsapp_message(
            customer, f"bulk_{params.get('event_type')}", dict(params.get('variables') or {}))
        return _map_result(res)
    res = appmod.send_custom_wa_template(
        tenant_id=campaign.tenant_id, phone_raw=row.phone,
        template_name=params.get('template'), language=params.get('language'),
        body_params=params.get('variables') or [], header_url=params.get('header_url'))
    return _map_result(res)


# --------------------------------------------------------------------------
# Resume, retry, cancel
# --------------------------------------------------------------------------

def resume_stale(appmod, tenant_id=None):
    """Restart campaigns whose worker died (or that never got one).
    Returns how many were restarted."""
    C = appmod.WhatsAppCampaign
    stale = _now() - STALE_AFTER
    q = C.query.filter(
        ((C.status == 'running') & ((C.heartbeat_at.is_(None)) | (C.heartbeat_at < stale))) |
        ((C.status == 'queued') & (C.created_at < stale)))
    if tenant_id is not None:
        q = q.filter(C.tenant_id == tenant_id)
    ids = [c.id for c in q.all()]
    for cid in ids:
        logging.info("WhatsApp campaign %s: resuming stale campaign", cid)
        start(appmod, cid)
    return len(ids)


def retry_failed(appmod, campaign):
    """Put failed rows back to pending and send them again."""
    R = appmod.WhatsAppCampaignRecipient
    n = (R.query.filter_by(campaign_id=campaign.id, status='failed')
         .update({R.status: 'pending', R.error: None, R.delivery_status: None,
                  R.wa_message_id: None, R.updated_at: _now()}, synchronize_session=False))
    if n:
        campaign.status = 'queued'
        campaign.finished_at = None
    appmod.db.session.commit()
    if n:
        start(appmod, campaign.id)
    return n


def cancel(appmod, campaign):
    """Stop sending; rows already sent stay sent. The worker notices between
    numbers and marks what is left as cancelled."""
    R = appmod.WhatsAppCampaignRecipient
    if campaign.status not in ('queued', 'running'):
        return False
    was_queued = campaign.status == 'queued'
    campaign.status = 'cancelled'
    if was_queued:  # no worker will come along to tidy up
        R.query.filter_by(campaign_id=campaign.id, status='pending').update(
            {R.status: 'cancelled', R.updated_at: _now()}, synchronize_session=False)
        campaign.finished_at = _now()
    appmod.db.session.commit()
    return True


# --------------------------------------------------------------------------
# Meta delivery receipts
# --------------------------------------------------------------------------

def apply_status(appmod, tenant_id, st):
    """Record a webhook status (sent/delivered/read/failed) on the campaign
    row that sent this wamid. Returns True when a row was updated. Caller
    commits."""
    wamid = st.get('id')
    status = st.get('status')
    if not wamid or not status:
        return False
    R = appmod.WhatsAppCampaignRecipient
    row = R.query.filter_by(tenant_id=tenant_id, wa_message_id=wamid).first()
    if row is None:
        return False
    if status == 'failed':
        errs = st.get('errors') or []
        detail = '; '.join(
            str(e.get('error_data', {}).get('details') or e.get('message') or e.get('title') or e.get('code'))
            for e in errs) or 'Meta reported the message as undelivered'
        row.delivery_status = 'failed'
        row.status = 'failed'
        row.error = f'Not delivered: {detail}'
    elif status in _DELIVERY_RANK:
        if row.delivery_status == 'failed':
            return False
        if _DELIVERY_RANK[status] <= _DELIVERY_RANK.get(row.delivery_status, 0):
            return False
        row.delivery_status = status
    else:
        return False
    row.updated_at = _now()
    return True


# --------------------------------------------------------------------------
# Serialisation
# --------------------------------------------------------------------------

def counts(appmod, campaign_ids):
    """{campaign_id: {status: n, 'delivered': n, 'read': n}} in two queries."""
    db, R = appmod.db, appmod.WhatsAppCampaignRecipient
    out = {cid: {} for cid in campaign_ids}
    if not campaign_ids:
        return out
    for cid, status, n in (db.session.query(R.campaign_id, R.status, db.func.count(R.id))
                           .filter(R.campaign_id.in_(campaign_ids))
                           .group_by(R.campaign_id, R.status)):
        out[cid][status] = n
    for cid, dstatus, n in (db.session.query(R.campaign_id, R.delivery_status, db.func.count(R.id))
                            .filter(R.campaign_id.in_(campaign_ids), R.delivery_status.isnot(None))
                            .group_by(R.campaign_id, R.delivery_status)):
        out[cid]['delivery_' + dstatus] = n
    return out


def campaign_dict(campaign, c):
    sent = c.get('sent', 0)
    read = c.get('delivery_read', 0)
    return {
        'id': campaign.id,
        'kind': campaign.kind,
        'title': campaign.title,
        'status': campaign.status,
        'total': campaign.total,
        'pending': c.get('pending', 0) + c.get('sending', 0),
        'sent': sent,
        'failed': c.get('failed', 0),
        'skipped': c.get('skipped', 0),
        'cancelled': c.get('cancelled', 0),
        # read implies delivered
        'delivered': c.get('delivery_delivered', 0) + read,
        'read': read,
        'created_by': campaign.created_by,
        'created_at': _iso(campaign.created_at),
        'started_at': _iso(campaign.started_at),
        'finished_at': _iso(campaign.finished_at),
        'params': campaign.params or {},
    }


def recipient_dict(r):
    return {
        'id': r.id,
        'customer_id': r.customer_id,
        'name': r.name,
        'phone': r.phone,
        'status': r.status,
        'delivery_status': r.delivery_status,
        'error': r.error,
        'wa_message_id': r.wa_message_id,
        'sent_at': _iso(r.sent_at),
        'updated_at': _iso(r.updated_at),
    }

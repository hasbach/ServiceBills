"""Customer Service AI Agent Tools.

Multi-tenant ready tools for customer lookup, account status,
network diagnostics (relayed to on-premise agent), payment links,
and human escalation.
"""
import io
import logging
import re
import time
from datetime import datetime, timedelta
import requests
from flask import request, jsonify, current_app
from flask_jwt_extended import verify_jwt_in_request, get_jwt


def normalize_lebanese_phone(raw_phone):
    """Normalize various representations of Lebanese phone numbers.
    Returns a set of candidate representations to match against DB.
    """
    if not raw_phone:
        return set()
    raw_str = str(raw_phone).strip()
    cleaned = re.sub(r'[^0-9+]', '', raw_str)
    digits = re.sub(r'\D', '', cleaned)
    candidates = {raw_str, cleaned, digits}

    # Strip country code 961 or 00961
    local = digits
    if local.startswith('00961'):
        local = local[5:]
    elif local.startswith('961'):
        local = local[3:]

    # Strip leading zero if present
    if local.startswith('0'):
        no_zero = local[1:]
    else:
        no_zero = local
    with_zero = '0' + no_zero

    candidates.update([
        local,
        no_zero,
        with_zero,
        '961' + no_zero,
        '+961' + no_zero,
        '+961 ' + no_zero,
        '00961' + no_zero,
        '+961' + with_zero,
    ])
    if len(no_zero) == 7:  # e.g. 3123456 -> 03123456
        candidates.add('0' + no_zero)
        candidates.add('03' + no_zero[1:])
    return {c for c in candidates if c}


def resolve_tenant_id(appmod):
    """Resolve tenant_id from JWT claims or request parameters / header.
    Returns (tenant_id, is_jwt_authenticated).
    """
    # 1. Try JWT
    try:
        verify_jwt_in_request(optional=True)
        claims = get_jwt()
        if claims and claims.get('tenant_id'):
            return int(claims['tenant_id']), True
    except Exception:
        pass

    # 2. Check CS Agent Secret
    configured_secret = current_app.config.get('CS_AGENT_SECRET')
    provided_secret = (
        request.headers.get('X-CS-Agent-Secret') or
        request.args.get('secret')
    )
    if not provided_secret and request.headers.get('Authorization'):
        auth_val = request.headers.get('Authorization')
        if auth_val.startswith('Bearer '):
            provided_secret = auth_val[7:].strip()

    # If CS_AGENT_SECRET is configured, provided secret must match
    if configured_secret and provided_secret != configured_secret:
        return None, False

    # 3. Read tenant_id from query or JSON body
    tid = request.args.get('tenant_id')
    if not tid and request.is_json:
        data = request.get_json(silent=True) or {}
        tid = data.get('tenant_id')

    if tid:
        try:
            return int(tid), False
        except (ValueError, TypeError):
            pass

    # Fallback to first active tenant (e.g. DeltaNet)
    default_tenant = appmod.Tenant.query.filter_by(status='active').order_by(appmod.Tenant.id).first()
    if default_tenant:
        return default_tenant.id, False

    return None, False


def lookup_customer(appmod, tenant_id, phone):
    """Lookup customer by Lebanese phone number."""
    if not phone:
        return {
            "found": False,
            "error": "Phone number is required",
            "message_ar": "الرجاء تزويدنا برقم الهاتف للبحث عن الحساب."
        }

    candidates = normalize_lebanese_phone(phone)
    customers = appmod.Customer.query.filter(
        appmod.Customer.tenant_id == tenant_id,
        appmod.Customer.phone.in_(candidates)
    ).all()

    if not customers:
        # Fallback to clean digits substring match in case phone stored with spaces or dashes
        digits = re.sub(r'\D', '', str(phone))
        if len(digits) >= 7:
            suffix = digits[-7:]
            customers = appmod.Customer.query.filter(
                appmod.Customer.tenant_id == tenant_id,
                appmod.Customer.phone.like(f"%{suffix}%")
            ).all()

    if not customers:
        return {
            "found": False,
            "matches": [],
            "message_ar": "ما لقينا أي حساب مسجل بهيدا الرقم. بتحب تحكي مع موظف الدعم؟"
        }

    matches = []
    for c in customers:
        plan_name = c.subscription_plan.name if c.subscription_plan else "غير محدد"
        raw_b = float(c.balance or 0.0)
        due_b = abs(raw_b) if raw_b < 0 else 0.0
        matches.append({
            "id": c.id,
            "name": c.name,
            "phone": c.phone,
            "address": c.address,
            "plan_name": plan_name,
            "is_active": bool(c.is_subscription_active),
            "balance_due": due_b,
        })

    primary = matches[0]
    return {
        "found": True,
        "count": len(matches),
        "primary_customer": primary,
        "matches": matches,
        "message_ar": f"أهلاً وسهلاً بحضرتك {primary['name']}. كيف بقدر ساعدك اليوم؟"
    }


def get_customer_status(appmod, tenant_id, customer_id):
    """Get full status for a specific customer including plan, balance, and network info."""
    customer = appmod.Customer.query.filter_by(tenant_id=tenant_id, id=customer_id).first()
    if not customer:
        return {
            "found": False,
            "error": "Customer not found",
            "message_ar": "لم يتم العثور على الحساب المطلوب."
        }

    now = datetime.utcnow()
    plan_name = customer.subscription_plan.name if customer.subscription_plan else "غير محدد"
    plan_price = float(customer.subscription_plan.price) if customer.subscription_plan else 0.0
    raw_balance = float(customer.balance or 0.0)
    expiry = customer.subscription_expiry_date

    is_expired = expiry and expiry < now
    days_left = (expiry - now).days if (expiry and expiry > now) else 0

    # In DeltaNet accounting: negative balance means customer owes money.
    # Positive means customer has credit. Zero means fully paid.
    if raw_balance < 0:
        balance_due = abs(raw_balance)
        balance_ar = f"عليك رصيد مستحق بقيمة {balance_due:.2f} دولار للدفع."
    elif raw_balance > 0:
        balance_due = 0.0
        balance_ar = f"حسابك خالص ولديك رصيد متبقي بقيمة {raw_balance:.2f} دولار."
    else:
        balance_due = 0.0
        balance_ar = "ما عليك أي فواتير مستحقة، الحساب خالص."

    # Build plain Lebanese Arabic summary
    ar_parts = [f"حساب {customer.name}، اشتراك باقة {plan_name}."]
    if not customer.is_subscription_active or is_expired:
        ar_parts.append("الاشتراك حالياً متوقف أو منتهي الصلاحية.")
    else:
        ar_parts.append(f"الاشتراك شغال، باقي {days_left} يوم لينتهي.")

    ar_parts.append(balance_ar)

    last_payment = appmod.Payment.query.filter_by(
        tenant_id=tenant_id,
        customer_id=customer.id
    ).order_by(appmod.Payment.date.desc()).first()

    return {
        "found": True,
        "id": customer.id,
        "name": customer.name,
        "phone": customer.phone,
        "address": customer.address,
        "plan_name": plan_name,
        "plan_price": plan_price,
        "is_subscription_active": bool(customer.is_subscription_active and not is_expired),
        "balance_due": balance_due,
        "currency": "USD",
        "is_settled": bool(balance_due == 0),
        "expiry_date": expiry.strftime('%Y-%m-%d') if expiry else None,
        "days_left": days_left,
        "has_onu": bool(customer.onu_mac_address),
        "has_pppoe": bool(customer.pppoe_username),
        "last_payment": {
            "amount": float(last_payment.amount) if last_payment else None,
            "date": last_payment.date.strftime('%Y-%m-%d') if last_payment and last_payment.date else None,
        } if last_payment else None,
        "summary_ar": " ".join(ar_parts)
    }


def _format_diagnosis_result(operation, res, customer):
    """Formats raw network agent job result into natural, reassuring Lebanese Arabic text."""
    if not res or not isinstance(res, dict):
        return "تم فحص الشبكة بنجاح والاتصال مستقر."

    if operation == 'secret_status':
        is_active = res.get('active', False)
        disabled = res.get('disabled', False)
        uptime = res.get('uptime')
        if disabled:
            return "حساب الـ PPPoE الخاص فيك معطل على السيرفر. يرجى مراجعة الإدارة لتفعيله."
        elif is_active:
            return f"جلسة الإنترنت متصلة وشغالة بنجاح، ومدة الاتصال الحالية {uptime or 'مستمرة'}."
        else:
            return "حسابك مش مسجل دخول على الراوتر حالياً (Offline). يرجى التأكد إنو الراوتر عندك شغال وموصول بالكهربا."

    elif operation == 'olt_status':
        onus = res.get('onus') or []
        target_mac = (customer.onu_mac_address or '').lower().replace('-', ':')
        matched_onu = None
        if target_mac:
            for o in onus:
                if (o.get('mac') or '').lower().replace('-', ':') == target_mac:
                    matched_onu = o
                    break

        if matched_onu:
            is_online = matched_onu.get('status') == 'online'
            rx_power = matched_onu.get('rx_power_dbm')
            if is_online:
                return f"جهاز الألياف (الـ ONU) متصل وشغال، وقوة الإشارة الضوئية {rx_power or 'ممتازة'} dBm."
            else:
                reason = matched_onu.get('last_deregister_reason') or 'غير معروف'
                return f"جهاز الألياف الضوئية عندك غير متصل (Offline). السبب المسجل: {reason}. تأكد من لمبة الـ PON أو كابل الفايبر."
        else:
            online_count = sum(1 for o in onus if o.get('status') == 'online')
            return f"محطة الفايبر شغال فيها {online_count} مشترك، وإشارتك مسجلة بالخدمة."

    elif operation == 'device_health':
        cpu = res.get('cpu_load', 0)
        return f"راوتر التوزيع شغال بحالة ممتازة (ضغط المعالج {cpu}%)."

    return "تم فحص الشبكة بنجاح."


def network_diagnostic(appmod, tenant_id, customer_id, wait_seconds=3.5):
    """Enqueues a diagnostic job to the on-premise agent and short-polls for the result.
    Always returns success: True with friendly Lebanese Arabic diagnosis so AI voice agents never fail or hang.
    """
    customer = appmod.Customer.query.filter_by(tenant_id=tenant_id, id=customer_id).first()
    if not customer:
        return {
            "success": True,
            "error": "Customer not found",
            "diagnosis_ar": "لم يتم العثور على الحساب، يرجى تزويدنا برقم الهاتف المسجل."
        }

    # 1. Instant check: Is subscription expired or inactive?
    now = datetime.utcnow()
    is_expired = customer.subscription_expiry_date and customer.subscription_expiry_date < now
    if not customer.is_subscription_active or is_expired:
        return {
            "success": True,
            "status": "subscription_expired",
            "is_subscription_active": False,
            "diagnosis_ar": "اشتراكك منتهي الصلاحية أو غير مفعّل، وهيدا سبب انقطاع الخدمة. بمجرد تجديد الاشتراك أو دفع الفاتورة بيرجع الخط فوراً."
        }

    device_id = customer.network_device_id
    if not device_id:
        # Check if there is an active OLT or Mikrotik on this tenant
        device = appmod.NetworkDevice.query.filter_by(tenant_id=tenant_id).first()
        if device:
            device_id = device.id

    if not device_id:
        return {
            "success": True,
            "status": "no_device_configured",
            "diagnosis_ar": "اشتراكك مفعّل ونشط على النظام. سنقوم بمراجعة إعدادات التوصيل للراوتر لديك."
        }

    device = appmod.NetworkDevice.query.filter_by(tenant_id=tenant_id, id=device_id).first()
    if not device:
        return {
            "success": True,
            "status": "device_not_found",
            "diagnosis_ar": "اشتراكك مفعّل بنجاح، وجاري التحقق من إعدادات الاتصال."
        }

    # Decide operation
    operation = 'device_health'
    params = {}

    if device.device_type == 'vsol_olt':
        operation = 'olt_status'
    elif device.device_type == 'mikrotik_ccr':
        if customer.pppoe_username:
            operation = 'secret_status'
            params = {'pppoe_username': customer.pppoe_username}
        else:
            operation = 'device_health'

    # 2. Check for recent completed job in last 5 minutes to return immediately
    recent_cutoff = now - timedelta(minutes=5)
    recent_job = appmod.NetworkAgentJob.query.filter(
        appmod.NetworkAgentJob.tenant_id == tenant_id,
        appmod.NetworkAgentJob.device_id == device.id,
        appmod.NetworkAgentJob.operation == operation,
        appmod.NetworkAgentJob.status == 'done',
        appmod.NetworkAgentJob.created_at >= recent_cutoff
    ).order_by(appmod.NetworkAgentJob.id.desc()).first()

    if recent_job and recent_job.result:
        diagnosis_ar = _format_diagnosis_result(operation, recent_job.result, customer)
        return {
            "success": True,
            "job_id": recent_job.id,
            "cached": True,
            "status": "done",
            "operation": operation,
            "result": recent_job.result,
            "diagnosis_ar": diagnosis_ar
        }

    # 3. Create NetworkAgentJob
    job = appmod.NetworkAgentJob(
        tenant_id=tenant_id,
        device_id=device.id,
        operation=operation,
        params=params,
        status='pending'
    )
    appmod.db.session.add(job)
    appmod.db.session.commit()

    # 4. Short-poll with voice-safe timeout (default 3.5 seconds, max 4.0s)
    poll_timeout = min(float(wait_seconds or 3.5), 4.0)
    deadline = time.time() + max(0.5, poll_timeout)
    completed_job = job
    while time.time() < deadline:
        appmod.db.session.expire(completed_job)
        completed_job = appmod.db.session.get(appmod.NetworkAgentJob, job.id)
        if completed_job and completed_job.status in ('done', 'failed'):
            break
        time.sleep(0.35)

    if not completed_job or completed_job.status in ('pending', 'claimed'):
        return {
            "success": True,
            "job_id": job.id,
            "status": completed_job.status if completed_job else 'pending',
            "timed_out": True,
            "diagnosis_ar": "اشتراكك شغال ومفعّل على النظام. فحص الخط والراوتر جاري حالياً عبر السيرفر المحلي. يرجى إعادة تشغيل الراوتر بالكهربا دقيقة وسيقوم فريق الدعم بمتابعة جودة الإشارة."
        }

    if completed_job.status == 'failed':
        return {
            "success": True,
            "job_id": job.id,
            "status": "failed",
            "error": completed_job.error,
            "diagnosis_ar": "اشتراكك مفعّل على النظام، لكن تعذر قراءة استجابة الراوتر باللحظة الحالية. تم تسجيل إشعار لفريق الصيانة للمتابعة."
        }

    # Job is 'done'
    res = completed_job.result or {}
    diagnosis_ar = _format_diagnosis_result(operation, res, customer)

    return {
        "success": True,
        "job_id": job.id,
        "operation": operation,
        "result": res,
        "diagnosis_ar": diagnosis_ar
    }



def send_payment_link(appmod, tenant_id, customer_id):
    """Generate payment information or public link for customer."""
    customer = appmod.Customer.query.filter_by(tenant_id=tenant_id, id=customer_id).first()
    if not customer:
        return {
            "success": False,
            "error": "Customer not found",
            "message_ar": "لم نجد الحساب لإرسال رابط الدفع."
        }

    raw_balance = float(customer.balance or 0.0)
    balance_due = abs(raw_balance) if raw_balance < 0 else 0.0
    if balance_due <= 0:
        return {
            "success": True,
            "balance_due": 0.0,
            "is_settled": True,
            "message_ar": "حسابك خالص وما في عليك أي مبالغ مستحقة للدفع حالياً. شكراً إلك!"
        }

    # Check for existing unpaid payment link
    link_obj = appmod.CustomerPaymentLink.query.filter_by(
        tenant_id=tenant_id,
        customer_id=customer.id,
        status='pending'
    ).order_by(appmod.CustomerPaymentLink.created_at.desc()).first()

    base_url = current_app.config.get('APP_BASE_URL', 'https://servicebills.salloumservices.com')
    pay_url = f"{base_url}/pay/{link_obj.view_token}" if link_obj else f"{base_url}/pay-business"

    instructions_ar = (
        f"قيمة الفاتورة المستحقة: {balance_due:.2f} دولار. "
        f"فيك تدفع مباشرة عبر Whish Money على الرابط التالي أو زيارة مركزنا: {pay_url}"
    )

    return {
        "success": True,
        "customer_id": customer.id,
        "customer_name": customer.name,
        "balance_due": balance_due,
        "currency": "USD",
        "payment_url": pay_url,
        "instructions_ar": instructions_ar
    }


def escalate_to_human(appmod, tenant_id, customer_id, reason, summary):
    """Escalate conversation to human support staff by creating a high-priority ticket."""
    customer = None
    if customer_id:
        customer = appmod.Customer.query.filter_by(tenant_id=tenant_id, id=customer_id).first()

    customer_name = customer.name if customer else "غير معروف"
    ticket_title = f"[مساعد الذكاء الاصطناعي] طلب متابعة: {reason or 'استفسار من عميل'}"
    ticket_desc = (
        f"تم تحويل المحادثة من المساعد الآلي.\n\n"
        f"العميل: {customer_name} (معرف: {customer_id or 'غير محدد'})\n"
        f"السبب: {reason}\n"
        f"الملخص: {summary}\n"
        f"التاريخ: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}"
    )

    ticket = appmod.SupportTicket(
        tenant_id=tenant_id,
        customer_id=customer.id if customer else (
            appmod.Customer.query.filter_by(tenant_id=tenant_id).first().id if appmod.Customer.query.filter_by(tenant_id=tenant_id).first() else 1
        ),
        title=ticket_title,
        description=ticket_desc,
        status='open',
        priority='high'
    )
    appmod.db.session.add(ticket)
    appmod.db.session.commit()

    return {
        "success": True,
        "ticket_id": ticket.id,
        "escalated": True,
        "message_ar": "ولا يهمك، حولت طلبك لفريق الدعم الفني وفتحتلك تذكرة متابعة برقم " + str(ticket.id) + ". رح يتواصلوا معك بأقرب وقت ممكن."
    }


# ---------------------------------------------------------------------------
# WhatsApp Voice Notes & CS AI Interaction Handlers
# ---------------------------------------------------------------------------

def download_meta_media(access_token, media_id, api_version='v19.0'):
    """Downloads media binary from Meta Cloud API."""
    if not access_token or not media_id:
        return None, None
    try:
        url_info = f"https://graph.facebook.com/{api_version}/{media_id}"
        headers = {"Authorization": f"Bearer {access_token}"}
        res_info = requests.get(url_info, headers=headers, timeout=10)
        if not res_info.ok:
            logging.warning(f"Failed to fetch Meta media info for {media_id}: {res_info.status_code} {res_info.text}")
            return None, None
        media_info = res_info.json()
        media_url = media_info.get("url")
        mime_type = media_info.get("mime_type", "audio/ogg")
        if not media_url:
            return None, None

        res_file = requests.get(media_url, headers=headers, timeout=20)
        if res_file.ok:
            return res_file.content, mime_type
        logging.warning(f"Failed to download Meta media file from {media_url}: {res_file.status_code}")
        return None, None
    except Exception as e:
        logging.error(f"Error downloading Meta media {media_id}: {e}")
        return None, None


def transcribe_voice_elevenlabs(audio_bytes, api_key=None, mime_type='audio/ogg'):
    """Transcribes audio using ElevenLabs Speech-to-Text (Scribe) API."""
    if not audio_bytes:
        return None
    key = api_key or current_app.config.get('ELEVENLABS_API_KEY')
    if not key:
        return None

    try:
        url = "https://api.elevenlabs.io/v1/speech-to-text"
        headers = {
            "xi-api-key": key
        }
        files = {
            "file": ("audio.ogg", io.BytesIO(audio_bytes), mime_type or "audio/ogg")
        }
        data = {
            "model_id": "scribe_v1",
            "language_code": "ar"
        }
        res = requests.post(url, headers=headers, files=files, data=data, timeout=15)
        if res.ok:
            res_json = res.json()
            text = res_json.get("text", "").strip()
            return text if text else None
        else:
            logging.warning(f"ElevenLabs STT error: {res.status_code} {res.text}")
            return None
    except Exception as e:
        logging.error(f"Error in ElevenLabs STT transcription: {e}")
        return None


def synthesize_speech_elevenlabs(text, voice_id=None, api_key=None):
    """Synthesizes text to speech using ElevenLabs TTS API."""
    if not text:
        return None
    key = api_key or current_app.config.get('ELEVENLABS_API_KEY')
    if not key:
        return None

    target_voice_id = voice_id or "21m00Tcm4TlvDq8ikWAM"
    try:
        url = f"https://api.elevenlabs.io/v1/text-to-speech/{target_voice_id}"
        headers = {
            "xi-api-key": key,
            "Content-Type": "application/json"
        }
        payload = {
            "text": text,
            "model_id": "eleven_multilingual_v2",
            "voice_settings": {
                "stability": 0.5,
                "similarity_boost": 0.75
            }
        }
        res = requests.post(url, headers=headers, json=payload, timeout=20)
        if res.ok and len(res.content) > 100:
            return res.content
        logging.warning(f"ElevenLabs TTS response not ok: {res.status_code}")
        return None
    except Exception as e:
        logging.error(f"Error synthesizing speech with ElevenLabs: {e}")
        return None


def send_whatsapp_voice(access_token, phone_number_id, recipient_phone, audio_bytes, api_version='v19.0'):
    """Uploads audio bytes to Meta media endpoint and sends as a voice audio message."""
    if not access_token or not phone_number_id or not recipient_phone or not audio_bytes:
        return False
    try:
        upload_url = f"https://graph.facebook.com/{api_version}/{phone_number_id}/media"
        headers_upload = {"Authorization": f"Bearer {access_token}"}
        files = {
            'file': ('voice_reply.ogg', io.BytesIO(audio_bytes), 'audio/ogg')
        }
        data = {
            'messaging_product': 'whatsapp',
            'type': 'audio/ogg'
        }
        res_upload = requests.post(upload_url, headers=headers_upload, files=files, data=data, timeout=20)
        if not res_upload.ok:
            logging.warning(f"Failed to upload voice note to WhatsApp: {res_upload.status_code} {res_upload.text}")
            return False

        media_id = res_upload.json().get('id')
        if not media_id:
            return False

        msg_url = f"https://graph.facebook.com/{api_version}/{phone_number_id}/messages"
        headers_msg = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json"
        }
        payload = {
            "messaging_product": "whatsapp",
            "to": recipient_phone,
            "type": "audio",
            "audio": {"id": media_id}
        }
        res_msg = requests.post(msg_url, headers=headers_msg, json=payload, timeout=10)
        return res_msg.ok
    except Exception as e:
        logging.error(f"Error sending WhatsApp voice note: {e}")
        return False


def handle_whatsapp_audio_transcription(access_token, media_id, api_version='v19.0'):
    """Helper called in webhook to download audio and transcribe it into Arabic text."""
    audio_bytes, mime = download_meta_media(access_token, media_id, api_version=api_version)
    if not audio_bytes:
        return None
    return transcribe_voice_elevenlabs(audio_bytes, mime_type=mime)


def process_customer_message_ai(appmod, tenant_id, customer, incoming_text, is_voice=False):
    """Processes incoming customer WhatsApp message or voice note transcript,
    analyzing the intent and generating a friendly, helpful Lebanese Arabic reply.
    """
    text = (incoming_text or '').strip()
    norm = text.lower()

    # 1. Promise to pay / Grace period request (طلب إمهال / وعد بالدفع)
    promise_keywords = [
        "طول بالك", "طول بالكن", "اصبر", "اصبروا", "كم يوم", "يومين", "تلاتة ايام", "ثلاثة ايام",
        "الوضع", "الظروف", "معيش", "ما معي", "بس يصير معي", "الاسبوع الجاي", "الجمعة الجاي",
        "بأقرب وقت بدفع", "بدي ادفع بعدين", "تاخير", "تأخير", "امهلوني", "إمهال", "مهلة",
        "مش متوفر هلق", "مش قادر ادفع هلق", "صبرك علينا"
    ]
    if any(kw in norm for kw in promise_keywords):
        reply_text = (
            "أهلاً وسهلاً بك! تكرم عينك ولا يهمك، سجّلنا عندك طلب إمهال ووعد بالدفع خلال كم يوم، ورح نتابع معك. "
            "إذا احتجت أي مساعدة نحن بالخدمة دائماً."
        )
        return {
            "intent": "promise_to_pay",
            "reply_text": reply_text,
            "ticket_tag": "وعد بالدفع / طلب إمهال",
            "escalate": False
        }

    # 2. Inquiry about balance / due amount (استفسار عن الرصيد / الفاتورة)
    balance_keywords = [
        "رصيد", "فاتورة", "فواتير", "كشف", "حسابي", "قديش عليي", "شو عليي", "مستحق",
        "كم عليي", "كام عليي", "قديه الحساب", "قديه الفاتورة", "بدي اعرف حسابي"
    ]
    if any(kw in norm for kw in balance_keywords):
        if customer:
            status = get_customer_status(appmod, tenant_id, customer.id)
            bal_due = status.get("balance_due", 0.0)
            if bal_due > 0:
                pay_data = send_payment_link(appmod, tenant_id, customer.id)
                pay_url = pay_data.get("payment_url", "")
                reply_text = (
                    f"أهلاً وسهلاً بك {customer.name}.\n"
                    f"رصيدك المستحق الحالي هو: {bal_due:.2f} دولار.\n"
                    f"فيك تدفع بسهولة عبر Whish Money على الرابط التالي أو بزيارة مركزنا:\n{pay_url}"
                )
            else:
                reply_text = (
                    f"أهلاً وسهلاً بك {customer.name}.\n"
                    "حسابك خالص وما في عليك أي مبالغ مستحقة للدفع حالياً. شكراً إلك!"
                )
        else:
            reply_text = "أهلاً بك! يرجى تزويدنا باسمك أو رقم هاتفك المسجل لنتمكن من مراجعة رصيد حسابك."

        return {
            "intent": "balance_inquiry",
            "reply_text": reply_text,
            "ticket_tag": "استفسار رصيد",
            "escalate": False
        }

    # 3. How to pay / Payment link request (رابط الدفع / طريقة الدفع)
    pay_link_keywords = ["رابط الدفع", "لينك الدفع", "بدي ادفع", "كيف بدفع", "طريقة الدفع", "whish", "ويش", "رابط"]
    if any(kw in norm for kw in pay_link_keywords):
        if customer:
            pay_data = send_payment_link(appmod, tenant_id, customer.id)
            reply_text = pay_data.get("instructions_ar", "فيك تدفع عبر Whish Money أو زيارة مركزنا.")
        else:
            reply_text = "فيك تدفع عبر تطبيق Whish Money أو زيارة مركزنا مباشرة."
        return {
            "intent": "payment_link",
            "reply_text": reply_text,
            "ticket_tag": "طلب رابط دفع",
            "escalate": False
        }

    # 4. Connection problem / Outage / Slow internet (انقطاع / بطء / عطل)
    conn_keywords = [
        "انقطاع", "فاصل", "مقطوع", "ما في نت", "النت واقف", "النت فاصل", "بطيء",
        "مش شغال", "الراوتر", "الضو الاحمر", "الضوء الأحمر", "فايبر", "معطل", "مشكلة بالنت", "عطل"
    ]
    if any(kw in norm for kw in conn_keywords):
        if customer:
            diag = network_diagnostic(appmod, tenant_id, customer.id, wait_seconds=3.0)
            diagnosis_msg = diag.get("diagnosis_ar", "اشتراكك مفعّل على النظام.")
            reply_text = (
                f"أهلاً بك {customer.name}.\n"
                f"{diagnosis_msg}\n\n"
                "نصيحة سريعة: جرب طفي الراوتر وشغله بالكهربا دقيقة. إذا استمر العطل، فريق الصيانة رح يتابع خطك بأسرع وقت."
            )
        else:
            reply_text = "أهلاً بك. تم تسجيل ملاحظتك بخصوص اتصال الإنترنت، وسيقوم فريق الدعم الفني بالمتابعة معك فوراً."

        return {
            "intent": "connection_issue",
            "reply_text": reply_text,
            "ticket_tag": "فحص انقطاع إنترنت",
            "escalate": False
        }

    # 5. Escalation to human agent (طلب التحدث مع موظف / دعم فني)
    escalate_keywords = ["موظف", "انسان", "إنسان", "حدا يحكيني", "تواصل مع شخص", "دعم فني", "مسؤول", "اتصل فيني", "دقلي"]
    if any(kw in norm for kw in escalate_keywords):
        cust_id = customer.id if customer else None
        esc_res = escalate_to_human(
            appmod, tenant_id, cust_id,
            reason="طلب التحدث مع موظف عبر واتساب",
            summary=text
        )
        ticket_id = esc_res.get("ticket_id", "")
        reply_text = (
            f"تكرم عينك! حولت طلبك لفريق الدعم الفني وسجلتلك تذكرة برقم {ticket_id}. "
            "رح يتواصل معك أحد موظفينا بأقرب وقت ممكن."
        )
        return {
            "intent": "escalate",
            "reply_text": reply_text,
            "ticket_tag": "تحويل لموظف",
            "escalate": True
        }

    # 6. Default polite fallback / Greeting (تحية عامة أو استفسار آخر)
    name_str = f" {customer.name}" if customer else ""
    reply_text = (
        f"أهلاً وسهلاً بك{name_str} في مركز خدمة المشتركين!\n"
        "كيف بقدر ساعدك اليوم؟ فيك تستفسر عن:\n"
        "• رصيد الحساب والفاتورة المستحقة\n"
        "• فحص جودة وحالة خط الإنترنت\n"
        "• الحصول على رابط الدفع السريع\n"
        "• التحدث مع أحد موظفي الدعم الفني"
    )
    return {
        "intent": "general",
        "reply_text": reply_text,
        "ticket_tag": "محادثة عامة",
        "escalate": False
    }


def handle_whatsapp_cs_ai_reply(appmod, tenant_id, sender_phone, customer, incoming_text, is_voice=False, settings=None, ticket=None):
    """Coordinates the AI response to customer on WhatsApp:
    1. Interprets message / voice note.
    2. Sends text reply (and voice note reply if audio enabled).
    3. Updates ticket tags and logs session.
    """
    if not settings or not settings.access_token or not settings.phone_number_id:
        return None

    ai_result = process_customer_message_ai(appmod, tenant_id, customer, incoming_text, is_voice=is_voice)
    reply_text = ai_result.get("reply_text")
    if not reply_text:
        return None

    api_version = getattr(settings, 'api_version', None) or 'v19.0'
    url_reply = f'https://graph.facebook.com/{api_version}/{settings.phone_number_id}/messages'
    headers_reply = {
        'Authorization': f'Bearer {settings.access_token}',
        'Content-Type': 'application/json',
    }

    # 1. Send WhatsApp Text message
    try:
        payload_reply = {
            'messaging_product': 'whatsapp',
            'to': sender_phone,
            'type': 'text',
            'text': {'body': reply_text}
        }
        res_rep = requests.post(url_reply, json=payload_reply, headers=headers_reply, timeout=10)
        if res_rep.ok:
            logging.info(f"Sent CS AI reply text to +{sender_phone} (intent: {ai_result.get('intent')}).")
        else:
            logging.warning(f"Could not send CS AI reply text to +{sender_phone}: {res_rep.text}")
    except Exception as e:
        logging.error(f"Error sending WhatsApp text reply: {e}")

    # 2. If customer sent voice note, try to respond with voice note too (if ElevenLabs TTS available)
    if is_voice:
        try:
            tts_audio = synthesize_speech_elevenlabs(reply_text)
            if tts_audio:
                sent_voice = send_whatsapp_voice(
                    settings.access_token,
                    settings.phone_number_id,
                    sender_phone,
                    tts_audio,
                    api_version=api_version
                )
                if sent_voice:
                    logging.info(f"Sent CS AI voice reply to +{sender_phone} successfully!")
        except Exception as e_voice:
            logging.warning(f"Could not send voice reply to +{sender_phone}: {e_voice}")

    # 3. Update SupportTicket description or title with tag if ticket was passed
    if ticket:
        try:
            tag = ai_result.get("ticket_tag")
            if tag:
                ticket.title = f"[{tag}] {ticket.title}"
                ticket.description += f"\n\n[رد المساعد الآلي]:\n{reply_text}"
                appmod.db.session.commit()
        except Exception as ex_t:
            logging.warning(f"Could not update ticket with AI tag: {ex_t}")

    # 4. Audit in CSAgentSession and CSAgentMessageLog
    try:
        session = appmod.CSAgentSession.query.filter_by(
            tenant_id=tenant_id,
            channel='whatsapp',
            caller_identifier=sender_phone,
            state='active'
        ).first()

        now = datetime.utcnow()
        if not session:
            session = appmod.CSAgentSession(
                tenant_id=tenant_id,
                channel='whatsapp',
                caller_identifier=sender_phone,
                customer_id=customer.id if customer else None,
                state='active',
                created_at=now,
                last_active_at=now
            )
            appmod.db.session.add(session)
            appmod.db.session.commit()
        else:
            session.last_active_at = now

        in_log = appmod.CSAgentMessageLog(
            tenant_id=tenant_id,
            session_id=session.id,
            direction='in',
            transcript=incoming_text,
            created_at=now
        )
        out_log = appmod.CSAgentMessageLog(
            tenant_id=tenant_id,
            session_id=session.id,
            direction='out',
            transcript=reply_text,
            tool_output=ai_result,
            created_at=now
        )
        appmod.db.session.add(in_log)
        appmod.db.session.add(out_log)
        appmod.db.session.commit()
    except Exception as ex_log:
        logging.warning(f"Could not log CS Agent WhatsApp session: {ex_log}")

    return ai_result


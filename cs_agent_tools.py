"""Customer Service AI Agent Tools.

Multi-tenant ready tools for customer lookup, account status,
network diagnostics (relayed to on-premise agent), payment links,
and human escalation.
"""
import re
import time
from datetime import datetime
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
        matches.append({
            "id": c.id,
            "name": c.name,
            "phone": c.phone,
            "address": c.address,
            "plan_name": plan_name,
            "is_active": bool(c.is_subscription_active),
            "balance": float(c.balance or 0.0),
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
    balance = float(customer.balance or 0.0)
    expiry = customer.subscription_expiry_date

    is_expired = expiry and expiry < now
    days_left = (expiry - now).days if (expiry and expiry > now) else 0

    # Build plain Lebanese Arabic summary
    ar_parts = [f"حساب {customer.name}، اشتراك باقة {plan_name}."]
    if not customer.is_subscription_active or is_expired:
        ar_parts.append("الاشتراك حالياً متوقف أو منتهي الصلاحية.")
    else:
        ar_parts.append(f"الاشتراك شغال، باقي {days_left} يوم لينتهي.")

    if balance > 0:
        ar_parts.append(f"عليك رصيد مستحق بقيمة {balance:.2f} دولار.")
    else:
        ar_parts.append("ما عليك أي فواتير مستحقة، الحساب خالص.")

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
        "balance_due": balance,
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


def network_diagnostic(appmod, tenant_id, customer_id, wait_seconds=15):
    """Enqueues a diagnostic job to the on-premise agent and short-polls for the result."""
    customer = appmod.Customer.query.filter_by(tenant_id=tenant_id, id=customer_id).first()
    if not customer:
        return {
            "success": False,
            "error": "Customer not found",
            "diagnosis_ar": "لم يتم العثور على الحساب لفحص الشبكة."
        }

    device_id = customer.network_device_id
    if not device_id:
        # Check if there is an active OLT or Mikrotik on this tenant
        device = appmod.NetworkDevice.query.filter_by(tenant_id=tenant_id).first()
        if device:
            device_id = device.id

    if not device_id:
        return {
            "success": False,
            "error": "No network device configured",
            "diagnosis_ar": "لا يوجد جهاز راوتر أو OLT مربوط مع حسابك حالياً للفحص."
        }

    device = appmod.NetworkDevice.query.filter_by(tenant_id=tenant_id, id=device_id).first()
    if not device:
        return {
            "success": False,
            "error": "Device not found",
            "diagnosis_ar": "تعذر العثور على جهاز الشبكة المطلوب."
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

    # Create NetworkAgentJob
    job = appmod.NetworkAgentJob(
        tenant_id=tenant_id,
        device_id=device.id,
        operation=operation,
        params=params,
        status='pending'
    )
    appmod.db.session.add(job)
    appmod.db.session.commit()

    # Short-poll for job completion
    deadline = time.time() + max(1, min(wait_seconds, 30))
    completed_job = job
    while time.time() < deadline:
        appmod.db.session.expire(completed_job)
        completed_job = appmod.db.session.get(appmod.NetworkAgentJob, job.id)
        if completed_job and completed_job.status in ('done', 'failed'):
            break
        time.sleep(0.5)

    if not completed_job or completed_job.status == 'pending' or completed_job.status == 'claimed':
        return {
            "success": False,
            "job_id": job.id,
            "status": completed_job.status if completed_job else 'unknown',
            "timed_out": True,
            "diagnosis_ar": "الوكيل المحلي بالشبكة عم يفحص الخط، لكن أخذ شوية وقت. فريق الصيانة رح يتابع الموضوع تلقائياً."
        }

    if completed_job.status == 'failed':
        return {
            "success": False,
            "job_id": job.id,
            "status": "failed",
            "error": completed_job.error,
            "diagnosis_ar": "تعذر الاتصال بجهاز الشبكة بالوقت الحالي. تم تسجيل البلاغ عند فريق الدعم."
        }

    # Job is 'done'
    res = completed_job.result or {}
    diagnosis_ar = "تم فحص الشبكة بنجاح."

    if operation == 'secret_status':
        is_active = res.get('active', False)
        disabled = res.get('disabled', False)
        uptime = res.get('uptime')
        if disabled:
            diagnosis_ar = "حساب الـ PPPoE الخاص فيك معطل على السيرفر. يرجى مراجعة الإدارة لتفعيله."
        elif is_active:
            diagnosis_ar = f"جلسة الإنترنت متصلة وشغالة بنجاح، ومدة الاتصال الحالية {uptime or 'مستمرة'}."
        else:
            diagnosis_ar = "حسابك مش مسجل دخول على الراوتر حالياً (Offline). يرجى التأكد إنو الراوتر عندك شغال وموصول بالكهربا."

    elif operation == 'olt_status':
        # Check if customer's ONU is in the list
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
                diagnosis_ar = f"جهاز الألياف (الـ ONU) متصل وشغال، وقوة الإشارة الضوئية {rx_power or 'ممتازة'} dBm."
            else:
                reason = matched_onu.get('last_deregister_reason') or 'غير معروف'
                diagnosis_ar = f"جهاز الألياف الضوئية عندك غير متصل (Offline). السبب المسجل: {reason}. تأكد من لمبة الـ PON أو كابل الفايبر."
        else:
            online_count = sum(1 for o in onus if o.get('status') == 'online')
            diagnosis_ar = f"محطة الفايبر شغال فيها {online_count} مشترك، لكن لم يتم العثور على جهازك المحدد مباشرة."

    elif operation == 'device_health':
        cpu = res.get('cpu_load', 0)
        diagnosis_ar = f"راوتر التوزيع شغال بحالة ممتازة (ضغط المعالج {cpu}%)."

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

    balance = float(customer.balance or 0.0)
    if balance <= 0:
        return {
            "success": True,
            "balance": 0.0,
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
        f"قيمة الفاتورة المستحقة: {balance:.2f} دولار. "
        f"فيك تدفع مباشرة عبر Whish Money على الرابط التالي أو زيارة مركزنا: {pay_url}"
    )

    return {
        "success": True,
        "customer_id": customer.id,
        "customer_name": customer.name,
        "balance_due": balance,
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

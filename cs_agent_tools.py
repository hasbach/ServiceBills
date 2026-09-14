"""Customer Service AI Agent Tools.

Multi-tenant ready tools for customer lookup, account status,
network diagnostics (relayed to on-premise agent), payment links,
and human escalation.
"""
import io
import json
import logging
import os
import re
import time
from datetime import datetime, timedelta
import requests
from flask import request, jsonify, current_app
from flask_jwt_extended import verify_jwt_in_request, get_jwt

# Hard ceiling (seconds) on total network_diagnostic latency -- the olt_status/
# secret_status job wait PLUS the cpe_locations follow-up combined. ElevenLabs'
# own tool-webhook timeout is well under a minute; a diagnosis that arrives
# after the platform has already given up is worse than a fast, slightly less
# precise fallback answer. Keep this comfortably below that timeout.
NETWORK_DIAGNOSTIC_LATENCY_CEILING = 20.0


def _poll_sleep(elapsed_iterations):
    """Backoff schedule for job-status poll loops: start responsive, then back
    off so a long wait doesn't hammer the DB with a query every 0.35s (a 15s
    wait was previously ~43 round trips; this cuts that by roughly half)."""
    if elapsed_iterations < 4:
        return 0.35
    if elapsed_iterations < 10:
        return 0.6
    return 1.0


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


def _normalize_mac(mac):
    """Normalise a MAC address to lowercase colon-separated form for comparison."""
    if not mac:
        return ''
    return mac.lower().replace('-', ':').replace('.', ':')


def _format_diagnosis_result(operation, res, customer):
    """Formats raw network agent job result into natural Lebanese Arabic text.

    Returns a dict:
        {
            'diagnosis_ar': str,
            'escalate': bool,       # True → ONU offline, trigger escalate_to_human
            'home_problem': bool,   # True → ONU up but customer CPE not visible
        }
    """
    base = {'escalate': False, 'home_problem': False}

    if not res or not isinstance(res, dict):
        return {**base, 'diagnosis_ar': 'تم فحص الشبكة بنجاح والاتصال مستقر.'}

    if operation == 'secret_status':
        is_active = res.get('active', False)
        disabled  = res.get('disabled', False)
        uptime    = res.get('uptime')
        if disabled:
            return {**base,
                    'escalate': True,
                    'diagnosis_ar': 'حساب الـ PPPoE الخاص فيك معطل على السيرفر. جاري تحويل الطلب لفريق الدعم.'}
        elif is_active:
            return {**base,
                    'diagnosis_ar': f'جلسة الإنترنت متصلة وشغالة بنجاح، ومدة الاتصال الحالية {uptime or "مستمرة"}.'}
        else:
            return {**base,
                    'home_problem': True,
                    'diagnosis_ar': 'حسابك مش مسجل دخول على الراوتر حالياً (Offline). '
                                    'يرجى التأكد إنو الراوتر عندك شغال وموصول بالكهربا.'}

    elif operation == 'olt_status':
        # The result from the on-premise agent is either a list (old format from
        # agent versions that returned onus directly) or a dict with 'onus' key.
        if isinstance(res, list):
            onus = res
        else:
            onus = res.get('onus') or []

        onu_mac  = _normalize_mac(customer.onu_mac_address)
        cpe_mac  = _normalize_mac(customer.cpe_mac_address)

        # --- Step 1: Find the customer's ONU in the OLT status list ---
        matched_onu = None
        if onu_mac:
            for o in onus:
                candidate = _normalize_mac(o.get('mac') or o.get('mac_address') or '')
                if candidate == onu_mac:
                    matched_onu = o
                    break

        # --- Step 2: Determine ONU state ---
        if matched_onu:
            onu_online = matched_onu.get('status') == 'online'
            rx_power   = matched_onu.get('rx_power_dbm')
            onu_id     = matched_onu.get('onu_id', '')
        else:
            # ONU MAC not linked → fall back to summary
            onu_online = None

        # --- Step 3: ONU is OFFLINE → infrastructure fault → escalate ---
        if matched_onu and not onu_online:
            reason = matched_onu.get('last_deregister_reason') or 'غير معروف'
            return {
                'escalate': True,
                'home_problem': False,
                'diagnosis_ar': (
                    f'جهاز الألياف الضوئية (ONU) الخاص بك غير متصل حالياً. '
                    f'السبب المسجل: {reason}. '
                    'هيدي مشكلة بالبنية التحتية وليست بالمنزل، جاري تحويل الطلب لفريق الصيانة.'
                )
            }

        # --- Step 4: ONU is ONLINE → check CPE visibility ---
        if matched_onu and onu_online:
            # Check CPE using the cpe_locations data embedded in result (if present)
            # or fall through to ONU-only diagnosis
            cpe_seen = res.get('cpe_seen')  # set by network_diagnostic when it has CPE data

            if cpe_mac:
                if cpe_seen is True:
                    return {**base,
                            'diagnosis_ar': (
                                f'جهاز الألياف (ONU {onu_id}) متصل وشغال'
                                f'{(" وقوة الإشارة " + str(rx_power) + " dBm") if rx_power else ""}. '
                                'والراوتر عندك مرئي على الشبكة — الاتصال شغال بشكل طبيعي.'
                            )}
                elif cpe_seen is False:
                    return {
                        'escalate': False,
                        'home_problem': True,
                        'diagnosis_ar': (
                            f'جهاز الألياف (ONU {onu_id}) متصل وشغال'
                            f'{(" بإشارة " + str(rx_power) + " dBm") if rx_power else ""}، '
                            'لكن الراوتر عندك غير مرئي على الشبكة. المشكلة على الأرجح '
                            'داخل المنزل: تأكد من توصيل كابل الشبكة بين الـ ONU والراوتر، '
                            'وأعد تشغيل الراوتر من الكهربا دقيقة.'
                        )
                    }
            # ONU online, no CPE data → positive result
            return {**base,
                    'diagnosis_ar': (
                        f'جهاز الألياف (ONU {onu_id}) متصل وشغال'
                        f'{(" وقوة الإشارة " + str(rx_power) + " dBm") if rx_power else ""}. '
                        'الاتصال من جهة الشبكة سليم.'
                    )}

        # ONU MAC not linked → summary
        online_count = sum(1 for o in onus if o.get('status') == 'online')
        return {**base,
                'diagnosis_ar': f'محطة الفايبر شغال فيها {online_count} مشترك متصل. '
                                 'اشتراكك مفعّل على النظام.'}

    elif operation == 'device_health':
        cpu = res.get('cpu_load', 0)
        return {**base,
                'diagnosis_ar': f'راوتر التوزيع شغال بحالة ممتازة (ضغط المعالج {cpu}%).'}

    return {**base, 'diagnosis_ar': 'تم فحص الشبكة بنجاح.'}


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

    # 2. Upstream bridge check — takes priority over Mikrotik/OLT device queries.
    # If the customer is managed via an upstream RADIUS portal (e.g. Krypton), we read
    # the last-synced upstream status directly from the DB. No router query needed.
    # IMPORTANT: We always use ServiceBills subscription_expiry_date for the expiry date
    # shown to the customer — NOT the upstream expiry, which may differ.
    if getattr(customer, 'upstream_username', None):
        upstream_status = getattr(customer, 'upstream_last_status', None)  # 'online'|'offline'|'expired'|None
        upstream_synced_at = getattr(customer, 'upstream_last_synced_at', None)

        # ServiceBills expiry date (source of truth for customer-facing expiry)
        sb_expiry = customer.subscription_expiry_date
        expiry_str = sb_expiry.strftime('%Y-%m-%d') if sb_expiry else None
        expiry_ar = f" الاشتراك مسجل لغاية {expiry_str}." if expiry_str else ""

        # How fresh is the upstream sync? (warn if stale > 60 min)
        stale_note = ""
        if upstream_synced_at:
            age_min = (now - upstream_synced_at).total_seconds() / 60
            if age_min > 60:
                stale_note = f" (آخر مزامنة منذ {int(age_min)} دقيقة)"

        if upstream_status == 'online':
            return {
                "success": True,
                "status": "online",
                "source": "upstream",
                "expiry_date": expiry_str,
                "diagnosis_ar": f"خطك شغال وأونلاين على الشبكة.{expiry_ar}{stale_note}"
            }
        elif upstream_status == 'offline':
            return {
                "success": True,
                "status": "offline",
                "source": "upstream",
                "expiry_date": expiry_str,
                "diagnosis_ar": (
                    f"خطك مطفي حالياً على شبكة المزوّد.{expiry_ar}{stale_note} "
                    f"اشتراكك مفعّل من جهتنا — المشكلة على الأرجح بجهاز الراوتر أو الكيبل. "
                    f"جرب تعيد تشغيل الراوتر بالكهربا دقيقتين، وإذا ما رجع تواصل مع الدعم الفني."
                ),
                "escalate": True
            }
        elif upstream_status == 'expired':
            return {
                "success": True,
                "status": "expired",
                "source": "upstream",
                "expiry_date": expiry_str,
                "diagnosis_ar": (
                    f"خطك منتهي الصلاحية على شبكة المزوّد.{expiry_ar}{stale_note} "
                    f"بمجرد تجديد الاشتراك بيرجع الخط فوراً."
                )
            }
        else:
            # upstream_status is None — never synced or unknown
            return {
                "success": True,
                "status": "unknown",
                "source": "upstream",
                "expiry_date": expiry_str,
                "diagnosis_ar": (
                    f"اشتراكك مفعّل من جهتنا.{expiry_ar} "
                    f"ما في معلومات حديثة عن حالة الخط على شبكة المزوّد. "
                    f"تواصل مع الدعم الفني إذا الإنترنت مش شغال."
                )
            }

    # 3. No upstream username — fall through to Mikrotik / OLT device diagnostic
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
        diag = _format_diagnosis_result(operation, recent_job.result, customer)
        return {
            "success": True,
            "job_id": recent_job.id,
            "cached": True,
            "status": "done",
            "operation": operation,
            "result": recent_job.result,
            "diagnosis_ar": diag['diagnosis_ar'],
            "escalate": diag.get('escalate', False),
            "home_problem": diag.get('home_problem', False),
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

    # 4. Short-poll waiting for the on-premise agent to claim and complete the job.
    # Timeline: agent polls every DEFAULT_POLL_SECONDS=2s → up to 2s before claim,
    # then 3-8s for the hardware query (Mikrotik RouterOS API / OLT SNMP).
    # `overall_deadline` is the total budget for THIS call, including the
    # cpe_locations follow-up below -- capped at NETWORK_DIAGNOSTIC_LATENCY_CEILING
    # so we never return later than ElevenLabs' own tool-call timeout allows.
    poll_timeout = min(float(wait_seconds or NETWORK_DIAGNOSTIC_LATENCY_CEILING), NETWORK_DIAGNOSTIC_LATENCY_CEILING)
    overall_deadline = time.time() + max(0.5, poll_timeout)
    completed_job = job
    _iter = 0
    while time.time() < overall_deadline:
        appmod.db.session.expire(completed_job)
        completed_job = appmod.db.session.get(appmod.NetworkAgentJob, job.id)
        if completed_job and completed_job.status in ('done', 'failed'):
            break
        time.sleep(_poll_sleep(_iter))
        _iter += 1

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

    # 5. For OLT: if ONU is online and customer has a CPE mac, run a cpe_locations job
    #    to determine whether the customer's home router is visible behind the ONU.
    #    This distinguishes "ONU up, home router down" (home problem) from "all good".
    if operation == 'olt_status' and customer.cpe_mac_address:
        onu_mac_norm = _normalize_mac(customer.onu_mac_address)
        cpe_mac_norm = _normalize_mac(customer.cpe_mac_address)

        # Find the customer's ONU in the olt_status result
        onus = res if isinstance(res, list) else (res.get('onus') or [])
        onu_online = False
        for o in onus:
            candidate = _normalize_mac(o.get('mac') or o.get('mac_address') or '')
            if onu_mac_norm and candidate == onu_mac_norm:
                onu_online = o.get('status') == 'online'
                break

        if onu_online:
            # Check for a recent cpe_locations job first
            recent_cpe_job = appmod.NetworkAgentJob.query.filter(
                appmod.NetworkAgentJob.tenant_id == tenant_id,
                appmod.NetworkAgentJob.device_id == device.id,
                appmod.NetworkAgentJob.operation == 'cpe_locations',
                appmod.NetworkAgentJob.status == 'done',
                appmod.NetworkAgentJob.created_at >= now - timedelta(minutes=5)
            ).order_by(appmod.NetworkAgentJob.id.desc()).first()

            cpe_result = None
            if recent_cpe_job and recent_cpe_job.result:
                cpe_result = recent_cpe_job.result
            else:
                # Enqueue a fresh cpe_locations job and wait for it, but only with
                # whatever's left of the SAME overall_deadline the olt_status job
                # drew from -- never a fresh budget on top of it. If there's
                # nothing meaningful left, skip the follow-up: an ONU-only answer
                # that arrives on time beats a more precise one that arrives after
                # ElevenLabs has already given up on the tool call.
                cpe_budget = overall_deadline - time.time()
                if cpe_budget >= 1.5:
                    cpe_job = appmod.NetworkAgentJob(
                        tenant_id=tenant_id,
                        device_id=device.id,
                        operation='cpe_locations',
                        params={},
                        status='pending'
                    )
                    appmod.db.session.add(cpe_job)
                    appmod.db.session.commit()

                    cpe_deadline = time.time() + cpe_budget
                    _cpe_iter = 0
                    while time.time() < cpe_deadline:
                        appmod.db.session.expire(cpe_job)
                        cpe_job = appmod.db.session.get(appmod.NetworkAgentJob, cpe_job.id)
                        if cpe_job and cpe_job.status in ('done', 'failed'):
                            break
                        time.sleep(_poll_sleep(_cpe_iter))
                        _cpe_iter += 1

                    if cpe_job and cpe_job.status == 'done' and cpe_job.result:
                        cpe_result = cpe_job.result

            if cpe_result and isinstance(cpe_result, dict):
                # cpe_locations result: {cpe_mac: {onu_id, onu_mac, pon_port}}
                # Normalize all keys for comparison
                cpe_seen = any(
                    _normalize_mac(k) == cpe_mac_norm
                    for k in cpe_result.keys()
                )
                # Embed cpe_seen into res for _format_diagnosis_result to pick up
                if isinstance(res, list):
                    res = {'onus': res, 'cpe_seen': cpe_seen}
                else:
                    res = dict(res)
                    res['cpe_seen'] = cpe_seen

    diag = _format_diagnosis_result(operation, res, customer)

    return {
        "success": True,
        "job_id": job.id,
        "operation": operation,
        "result": res,
        "diagnosis_ar": diag['diagnosis_ar'],
        "escalate": diag.get('escalate', False),
        "home_problem": diag.get('home_problem', False),
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


def escalate_to_human(appmod, tenant_id, customer_id, reason, summary, phone=None):
    """Escalate conversation to human support staff by creating a high-priority ticket
    and sending the Meta WhatsApp template 'customer_reply_alert' to the tenant's forwarding_mobile.
    """
    customer = None
    if customer_id:
        customer = appmod.Customer.query.filter_by(tenant_id=tenant_id, id=customer_id).first()
    if not customer and phone:
        candidates = normalize_lebanese_phone(phone)
        for cand in candidates:
            customer = appmod.Customer.query.filter_by(tenant_id=tenant_id).filter(
                (appmod.Customer.phone == cand) | (appmod.Customer.phone.like(f"%{cand}%"))
            ).first()
            if customer:
                break

    customer_name = customer.name if customer else "عميل"
    customer_phone = customer.phone if customer else (str(phone or '').strip())
    ticket_title = f"[مساعد الذكاء الاصطناعي] طلب متابعة: {reason or 'استفسار من عميل'}"
    ticket_desc = (
        f"تم تحويل المحادثة من المساعد الآلي.\n\n"
        f"العميل: {customer_name} (معرف: {customer.id if customer else (customer_id or 'غير محدد')})\n"
        f"الهاتف: {customer_phone or 'غير محدد'}\n"
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

    # Dispatch WhatsApp template alert ('customer_reply_alert') to forwarding_mobile
    alert_sent = False
    fwd_phone = None
    try:
        settings = None
        if hasattr(appmod, 'WhatsAppSettings'):
            settings = appmod.WhatsAppSettings.query.filter_by(tenant_id=tenant_id).first()
        elif hasattr(appmod, 'TenantWhatsAppSettings'):
            settings = appmod.TenantWhatsAppSettings.query.filter_by(tenant_id=tenant_id).first()

        if settings and settings.access_token and settings.phone_number_id and settings.forwarding_mobile:
            if hasattr(appmod, 'normalize_whatsapp_phone'):
                fwd_phone = appmod.normalize_whatsapp_phone(settings.forwarding_mobile)
            if not fwd_phone:
                fwd_digits = re.sub(r'\D', '', str(settings.forwarding_mobile or ''))
                if fwd_digits.startswith('0') and len(fwd_digits) <= 9:
                    fwd_phone = '961' + fwd_digits[1:]
                elif len(fwd_digits) <= 8 and not (fwd_digits.startswith('961') or fwd_digits.startswith('20') or fwd_digits.startswith('966')):
                    fwd_phone = '961' + fwd_digits
                else:
                    fwd_phone = fwd_digits

            if fwd_phone:
                tmpl_name = getattr(settings, 'template_forward_alert', None) or 'customer_reply_alert'
                lang_code = getattr(settings, 'template_language', None) or 'ar'
                api_version = getattr(settings, 'api_version', None) or 'v19.0'
                url = f"https://graph.facebook.com/{api_version}/{settings.phone_number_id}/messages"
                headers = {
                    'Authorization': f"Bearer {settings.access_token}",
                    'Content-Type': 'application/json'
                }

                from_str = f"{customer_name} ({customer_phone})" if customer_phone else customer_name
                msg_str = f"طلب تحويل للدعم: {reason}"
                if summary:
                    msg_str += f" - {summary}"
                msg_str = msg_str[:160]

                def _send_template(lang, params):
                    payload = {
                        'messaging_product': 'whatsapp',
                        'to': fwd_phone,
                        'type': 'template',
                        'template': {
                            'name': tmpl_name,
                            'language': {'code': lang}
                        }
                    }
                    if params:
                        payload['template']['components'] = [{
                            'type': 'body',
                            'parameters': [{'type': 'text', 'text': str(p)} for p in params]
                        }]
                    return requests.post(url, json=payload, headers=headers, timeout=10)

                attempts = [
                    (lang_code, [from_str, msg_str]),
                    ('en' if lang_code == 'ar' else 'ar', [from_str, msg_str]),
                    (lang_code, [customer_name, f"+{customer_phone}" if customer_phone else "+961", msg_str]),
                    (lang_code, [f"{from_str}: {msg_str}"]),
                    (lang_code, None)
                ]

                for target_lang, param_list in attempts:
                    try:
                        res_tpl = _send_template(target_lang, param_list)
                        if res_tpl.ok:
                            alert_sent = True
                            logging.info(
                                f"Sent escalation alert template '{tmpl_name}' ({target_lang}) to forwarding_mobile (+{fwd_phone}) for ticket #{ticket.id}."
                            )
                            break
                        else:
                            logging.warning(
                                f"Escalation alert template '{tmpl_name}' ({target_lang}) send failed: {res_tpl.status_code} {res_tpl.text}"
                            )
                    except Exception as ex_call:
                        logging.warning(f"Error calling Meta API for escalation alert: {ex_call}")
                        break
    except Exception as ex_fwd:
        logging.error(f"Error preparing escalation template alert to forwarding_mobile: {ex_fwd}")

    return {
        "success": True,
        "ticket_id": ticket.id,
        "escalated": True,
        "alert_sent": alert_sent,
        "forwarded_to": fwd_phone if alert_sent else None,
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
    key = api_key or current_app.config.get('ELEVENLABS_API_KEY') or os.environ.get('ELEVENLABS_API_KEY')
    if not key:
        logging.warning("transcribe_voice_elevenlabs: No ELEVENLABS_API_KEY available.")
        return None

    try:
        url = "https://api.elevenlabs.io/v1/speech-to-text"
        headers = {
            "xi-api-key": key
        }
        clean_mime = (mime_type or "audio/ogg").split(';')[0].strip()
        filename = "voice_note.ogg" if "ogg" in clean_mime else "voice_note.mp3"
        files = {
            "file": (filename, io.BytesIO(audio_bytes), clean_mime)
        }
        data = {
            "model_id": "scribe_v1",
            "language_code": "ar"
        }
        res = requests.post(url, headers=headers, files=files, data=data, timeout=20)
        if res.ok:
            res_json = res.json()
            text = res_json.get("text", "").strip()
            logging.info(f"ElevenLabs STT transcription successful: '{text}'")
            return text if text else None
        else:
            logging.warning(f"ElevenLabs STT error: {res.status_code} {res.text}")
            return None
    except Exception as e:
        logging.error(f"Error in ElevenLabs STT transcription: {e}")
        return None


def clean_speech_tags(text):
    """Strips TTS prompt emotion tags like [warmly], [friendly], [ودود], etc. from text."""
    if not text:
        return ""
    cleaned = re.sub(r'\[[\w\s_-]+\]\s*', '', text, flags=re.UNICODE)
    return cleaned.strip()


_CACHED_AGENT_CONFIG = {}
_CACHED_AGENT_CONFIG_TIME = 0

def get_elevenlabs_agent_config(api_key=None, agent_id=None):
    """Fetches the agent configuration directly from ElevenLabs Conversational AI API.
    Extracts the agent's name, voice_id, model_id, and first_message.
    Caches results for 300 seconds to minimize latency and avoid API rate limits.
    """
    global _CACHED_AGENT_CONFIG, _CACHED_AGENT_CONFIG_TIME
    import time
    now = time.time()
    if _CACHED_AGENT_CONFIG and (now - _CACHED_AGENT_CONFIG_TIME < 300):
        return _CACHED_AGENT_CONFIG

    target_agent_id = agent_id
    if not target_agent_id:
        try:
            target_agent_id = current_app.config.get('ELEVENLABS_AGENT_ID') or os.environ.get('ELEVENLABS_AGENT_ID')
        except Exception:
            target_agent_id = os.environ.get('ELEVENLABS_AGENT_ID')

    key = api_key
    if not key:
        try:
            key = current_app.config.get('ELEVENLABS_API_KEY') or os.environ.get('ELEVENLABS_API_KEY')
        except Exception:
            key = os.environ.get('ELEVENLABS_API_KEY')

    if not target_agent_id or not key:
        return _CACHED_AGENT_CONFIG or {}

    try:
        url = f"https://api.elevenlabs.io/v1/convai/agents/{target_agent_id}"
        res = requests.get(url, headers={"xi-api-key": key}, timeout=6)
        if res.ok:
            data = res.json()
            conv_cfg = data.get("conversation_config", {})
            tts_cfg = conv_cfg.get("tts", {})
            agent_cfg = conv_cfg.get("agent", {})

            voice_id = tts_cfg.get("voice_id") or data.get("tts", {}).get("voice_id") or ""
            model_id = tts_cfg.get("model_id") or "eleven_multilingual_v2"
            first_msg = (agent_cfg.get("first_message") or "").strip()
            name = (data.get("name") or "يارا").strip()

            _CACHED_AGENT_CONFIG = {
                "agent_id": target_agent_id,
                "name": name,
                "voice_id": voice_id,
                "model_id": model_id,
                "first_message": first_msg
            }
            _CACHED_AGENT_CONFIG_TIME = now
            logging.info(
                f"Synced ElevenLabs agent '{name}' ({target_agent_id}): voice_id={voice_id}, model={model_id}, first_message='{first_msg}'"
            )
            return _CACHED_AGENT_CONFIG
        else:
            logging.warning(f"Could not fetch ElevenLabs agent config ({res.status_code}): {res.text}")
    except Exception as e:
        logging.warning(f"Error fetching ElevenLabs agent config: {e}")

    return _CACHED_AGENT_CONFIG or {}


def get_effective_elevenlabs_voice_id(api_key=None, agent_id=None):
    """Finds the effective ElevenLabs voice ID.
    Priority:
    1. Voice configured on the ElevenLabs agent itself (via ConvAI agent config)
    2. Explicit ELEVENLABS_VOICE_ID from config / environment variable
    3. User's account voices via /v1/voices
    Note: Never hardcodes a voice ID and never defaults to a male voice.
    """
    key = api_key
    if not key:
        try:
            key = current_app.config.get('ELEVENLABS_API_KEY') or os.environ.get('ELEVENLABS_API_KEY')
        except Exception:
            key = os.environ.get('ELEVENLABS_API_KEY')

    # 1. Fetch the default voice assigned in the ElevenLabs agent's configuration
    agent_cfg = get_elevenlabs_agent_config(api_key=key, agent_id=agent_id)
    if agent_cfg.get("voice_id"):
        return agent_cfg["voice_id"]

    # 2. Check if explicitly set in config/env (without hardcoded defaults)
    try:
        configured = (current_app.config.get('ELEVENLABS_VOICE_ID') or os.environ.get('ELEVENLABS_VOICE_ID') or "").strip()
    except Exception:
        configured = (os.environ.get('ELEVENLABS_VOICE_ID') or "").strip()

    if configured:
        return configured

    # 3. Query /v1/voices to find an authorized voice on the account
    if key:
        try:
            res = requests.get("https://api.elevenlabs.io/v1/voices", headers={"xi-api-key": key}, timeout=5)
            if res.ok:
                voices = res.json().get("voices", [])
                if voices:
                    return voices[0]["voice_id"]
        except Exception as e:
            logging.warning(f"Could not query /v1/voices: {e}")

    return None


def synthesize_speech_elevenlabs(text, voice_id=None, api_key=None, agent_id=None):
    """Synthesizes text to speech using ElevenLabs TTS API with the agent's configured voice."""
    if not text:
        return None
    key = api_key
    if not key:
        try:
            key = current_app.config.get('ELEVENLABS_API_KEY') or os.environ.get('ELEVENLABS_API_KEY')
        except Exception:
            key = os.environ.get('ELEVENLABS_API_KEY')
    if not key:
        logging.warning("synthesize_speech_elevenlabs: No ELEVENLABS_API_KEY available.")
        return None

    agent_cfg = get_elevenlabs_agent_config(api_key=key, agent_id=agent_id)
    target_voice_id = voice_id or agent_cfg.get("voice_id") or get_effective_elevenlabs_voice_id(key, agent_id=agent_id)
    if not target_voice_id:
        logging.warning("synthesize_speech_elevenlabs: No voice ID available from ElevenLabs agent configuration.")
        return None

    clean_text = clean_speech_tags(text)
    if not clean_text:
        return None

    model_id = agent_cfg.get("model_id") or "eleven_multilingual_v2"
    if "multilingual" not in model_id and "turbo" not in model_id and "flash" not in model_id:
        model_id = "eleven_multilingual_v2"

    payload = {
        "text": clean_text,
        "model_id": model_id,
        "voice_settings": {
            "stability": 0.5,
            "similarity_boost": 0.75
        }
    }
    headers = {
        "xi-api-key": key,
        "Content-Type": "application/json"
    }

    try:
        url = f"https://api.elevenlabs.io/v1/text-to-speech/{target_voice_id}?output_format=mp3_44100_128"
        res = requests.post(url, headers=headers, json=payload, timeout=25)
        if res.ok and len(res.content) > 100:
            return res.content

        logging.warning(f"ElevenLabs TTS response error with voice {target_voice_id}: {res.status_code} {res.text}")
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
            'file': ('voice_reply.mp3', io.BytesIO(audio_bytes), 'audio/mpeg')
        }
        data = {
            'messaging_product': 'whatsapp',
            'type': 'audio/mpeg'
        }
        res_upload = requests.post(upload_url, headers=headers_upload, files=files, data=data, timeout=25)
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
        res_msg = requests.post(msg_url, headers=headers_msg, json=payload, timeout=15)
        if not res_msg.ok:
            logging.warning(f"Failed to send WhatsApp audio message: {res_msg.status_code} {res_msg.text}")
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


def query_elevenlabs_conversational_ai(agent_id, incoming_text, sender_phone, customer=None, recent_history=None, timeout=18, is_admin=False):
    """Interacts directly with the ElevenLabs Conversational AI Agent WebSocket.
    Allows Yara's LLM in ElevenLabs to handle the customer conversation dynamically,
    execute webhook tools (lookup-customer, customer-status, etc.), and craft the reply.
    """
    if not agent_id or not incoming_text:
        return None

    try:
        import websocket
    except ImportError:
        logging.warning("query_elevenlabs_conversational_ai: websocket-client package is not installed.")
        return None

    phone_digits = re.sub(r'\D', '', str(sender_phone or ''))
    phone_8 = phone_digits[-8:] if len(phone_digits) >= 8 else phone_digits
    customer_name = customer.name if (customer and getattr(customer, 'name', None)) else 'عميل'
    customer_id = str(customer.id) if (customer and getattr(customer, 'id', None)) else ''

    # Clean incoming message text
    user_msg_clean = re.sub(
        r"^\[(رسالة صوتية|AUDIO message received|audio message received|voice message received)\]:?\s*",
        "",
        incoming_text.strip(),
        flags=re.IGNORECASE
    ).strip()
    if not user_msg_clean:
        return None

    ws = None
    try:
        ws_url = f"wss://api.elevenlabs.io/v1/convai/conversation?agent_id={agent_id}"
        ws = websocket.create_connection(ws_url, timeout=10)

        # 1. Send conversation initiation client data with dynamic variables
        init_payload = {
            "type": "conversation_initiation_client_data",
            "conversation_initiation_client_data_event": {
                "dynamic_variables": {
                    "caller_id": sender_phone or phone_8,
                    "phone": phone_8,
                    "phone_number": sender_phone or phone_8,
                    "customer_name": customer_name,
                    "user_role": "admin" if is_admin else "customer"
                    # NOTE: customer_id intentionally NOT sent here.
                    # Sending it caused ElevenLabs to auto-fire customer-status/network-diagnostic
                    # on every single message. Tools should only fire when the user's message demands it.
                }
            }
        }
        ws.send(json.dumps(init_payload))

        # 2. Consume the initial greeting turn generated on connection.
        # ElevenLabs sends agent_response for the first_message on every new WebSocket session.
        # We must fully consume this before sending the user's turn, otherwise
        # ElevenLabs will process the greeting turn THEN our message, causing double-greeting.
        # Increased to 5s window with 2s per-recv; cold starts can push greeting to ~4s.
        greeting_received = False
        t_init = time.time()
        while time.time() - t_init < 5.0:
            try:
                ws.settimeout(2.0)
                raw = ws.recv()
                if not raw:
                    break
                data = json.loads(raw)
                m_type = data.get("type")
                if m_type == "agent_response":
                    greeting_received = True
                    break
                elif m_type == "ping":
                    p_id = data.get("ping_event", {}).get("event_id")
                    if p_id is not None:
                        ws.send(json.dumps({"type": "pong", "event_id": p_id}))
            except websocket.WebSocketTimeoutException:
                if greeting_received:
                    break
                continue
            except Exception:
                break

        # 3. Format prompt with caller context and recent conversation history
        prompt_parts = []
        # Inject role + permissions directly into the message — more reliable than
        # relying on ElevenLabs {{dynamic_variable}} substitution in the system prompt.
        # NOTE: We do NOT include the phone number here. Including it caused ElevenLabs
        # to auto-trigger lookup-customer on every message. The AI should only call tools
        # when the user's actual message requires it.
        if is_admin:
            prompt_parts.append(
                f"[SYSTEM - CALLER IDENTITY]: "
                f"Name: {customer_name}, "
                f"USER_ROLE: admin — This caller is the NETWORK ADMINISTRATOR with FULL ACCESS. "
                f"Ignore all customer privacy restrictions. Answer any question about any subscriber or the network."
            )
        else:
            prompt_parts.append(
                f"[SYSTEM - CALLER IDENTITY]: "
                f"Name: {customer_name}, "
                f"USER_ROLE: customer — Normal subscriber, only answer about their own account."
            )
        if recent_history:
            prompt_parts.append("[مقتطف من المحادثة السابقة بين العميل ويارا]:")
            for m in recent_history[-4:]:
                role = "العميل" if m.get("direction") == "in" else "يارا"
                txt = clean_speech_tags(m.get("transcript") or "")
                if txt:
                    prompt_parts.append(f"{role}: {txt}")
            prompt_parts.append("[رسالة العميل الحالية]:")
        prompt_parts.append(user_msg_clean)
        full_prompt = "\n".join(prompt_parts)

        # 4. Send the user message to ElevenLabs
        ws.send(json.dumps({"type": "user_message", "text": full_prompt}))

        # 5. Wait for final agent response
        # ElevenLabs flow when agent uses a tool:
        #   a) Sends intermediate agent_response ("just a second...")
        #   b) Makes HTTP tool call to our webhook (can take 5-15s)
        #   c) Sends FINAL agent_response with the actual tool result
        # We must NOT stop after the first reply. We keep listening until:
        #   - conversation_ended event is received, OR
        #   - 10s of silence AFTER getting at least one reply, OR
        #   - overall timeout is reached
        t_req = time.time()
        agent_reply = None
        last_activity_at = time.time()  # tracks any activity, not just replies
        while time.time() - t_req < timeout:
            try:
                ws.settimeout(2.0)
                raw = ws.recv()
                if not raw:
                    break
                data = json.loads(raw)
                m_type = data.get("type")
                last_activity_at = time.time()  # reset on ANY message received
                if m_type == "agent_response":
                    resp = data.get("agent_response_event", {}).get("agent_response")
                    if resp:
                        agent_reply = resp   # keep updating - we want the LAST one
                        logging.info(f"ElevenLabs intermediate/final reply: {resp[:80]}")
                elif m_type == "conversation_ended":
                    break  # ElevenLabs closed the session - use last reply we have
                elif m_type == "ping":
                    p_id = data.get("ping_event", {}).get("event_id")
                    if p_id is not None:
                        ws.send(json.dumps({"type": "pong", "event_id": p_id}))
            except websocket.WebSocketTimeoutException:
                # 2s of silence. If we have a reply AND 10s have passed since last
                # activity (meaning the tool call + final answer should be done), stop.
                if agent_reply and (time.time() - last_activity_at > 10.0):
                    break
                # Otherwise keep waiting - tool call may still be in progress
                continue
            except Exception as ex_loop:
                logging.warning(f"Exception during ElevenLabs ConvAI message wait: {ex_loop}")
                break

        if agent_reply:
            cleaned = clean_speech_tags(agent_reply)
            if cleaned:
                logging.info(f"ElevenLabs ConvAI reply received ({round(time.time() - t_req, 2)}s): {cleaned}")
                return {
                    "intent": "elevenlabs_convai",
                    "reply_text": cleaned,
                    "ticket_tag": "محادثة ذكاء اصطناعي",
                    "escalate": False
                }
    except Exception as e:
        logging.warning(f"Error communicating with ElevenLabs ConvAI: {e}")
    finally:
        if ws:
            try:
                ws.close()
            except Exception:
                pass

    return None


def process_customer_message_ai(appmod, tenant_id, customer, incoming_text, is_voice=False, is_new_session=True):
    """Processes incoming customer WhatsApp message or voice note transcript,
    analyzing the intent and generating a friendly, accurate Lebanese Arabic reply.
    """
    raw_text = (incoming_text or '').strip()
    # Strip voice note audio prefixes and formatting
    clean_text = re.sub(
        r"^\[(رسالة صوتية|AUDIO message received|audio message received|voice message received)\]:?\s*",
        "",
        raw_text,
        flags=re.IGNORECASE
    ).strip()
    norm = clean_text.lower()

    # 1. Greetings & courtesy (شكرا، يعطيك العافية، مرحبا)
    courtesy_thanks = ["شكرا", "شكرن", "يسلمو", "تسلم", "الله يعطيك العافية", "يعطيكم العافية", "مشكور", "ما قصرت"]
    if any(kw in norm for kw in courtesy_thanks) and len(norm) < 35:
        reply_text = "تكرم عينك والله يعافيك! معك يارا من الدعم الفني، إذا احتجت أي مساعدة أنا بالخدمة دائماً."
        return {
            "intent": "courtesy",
            "reply_text": reply_text,
            "ticket_tag": "شكر وتحية",
            "escalate": False
        }

    # 2. Direct greetings (مرحبا، أهلاً، سلام، هاي، صباح الخير، الو)
    greeting_keywords = [
        "مرحبا", "مرحب", "مرمرحبا", "هاي", "أهلاً", "اهلاً", "اهلين", "أهلين",
        "السلام عليكم", "سلام عليكم", "صباح الخير", "مساء الخير", "صباحو", "مساء النور",
        "الو", "ألو", "alo", "hello", "hi", "hey"
    ]
    if any(kw in norm for kw in greeting_keywords) and len(norm) < 40:
        agent_cfg = get_elevenlabs_agent_config()
        first_msg = clean_speech_tags(agent_cfg.get("first_message"))
        name_str = f" {customer.name}" if customer else ""
        if is_new_session:
            reply_text = first_msg if first_msg else f"أهلاً وسهلاً بك{name_str}! معك يارا من الدعم الفني، كيف بقدر ساعدك اليوم؟"
        else:
            reply_text = f"أهلاً وسهلاً فيك{name_str}! معك يارا، تفضل كيف بقدر كفي مساعدتك؟"
        return {
            "intent": "greeting",
            "reply_text": reply_text,
            "ticket_tag": "تحية واستقبال",
            "escalate": False
        }

    # 2b. Pleasantries / Wellbeing (كيفك، شو اخبارك، شلونك، كيف صحتك)
    wellbeing_keywords = ["كيفك", "كيفيك", "شو الاخبار", "شو اخبارك", "شلونك", "كيف صحتك", "كيف الصحه", "كيف الحال"]
    if any(kw in norm for kw in wellbeing_keywords) and len(norm) < 35:
        name_str = f" {customer.name}" if customer else ""
        reply_text = f"الحمدلله تمام الله يسلمك{name_str}! معك يارا، كيف بقدر ساعدك اليوم بخصوص خطك أو اشتراكك؟"
        return {
            "intent": "wellbeing",
            "reply_text": reply_text,
            "ticket_tag": "تحية واستقبال",
            "escalate": False
        }

    # 2c. Identity / Persona questions (مين انتي، شو اسمك، مين معي، مع مين عم بحكي)
    identity_keywords = ["مين انتي", "مين انت", "شو اسمك", "مين معي", "مع مين عم بحكي", "انت روبوت", "انت ذكاء اصطناعي", "انت انسان"]
    if any(kw in norm for kw in identity_keywords) and len(norm) < 40:
        reply_text = "أنا يارا، المساعدة الآلية لخدمة عملاء DeltaNet. بخدمتك لأي استفسار عن اشتراكك، فواتيرك، أو مشاكل النت."
        return {
            "intent": "identity",
            "reply_text": reply_text,
            "ticket_tag": "استفسار عن الهوية",
            "escalate": False
        }

    # 2. Promise to pay / Grace period request (طلب إمهال / وعد بالدفع)
    promise_keywords = [
        "طول بالك", "طول بالكن", "اصبر", "اصبروا", "كم يوم", "يومين", "تلاتة ايام", "ثلاثة ايام",
        "الوضع", "الظروف", "معيش", "معييش", "ما معي", "بس يصير معي", "الاسبوع الجاي", "الجمعة الجاي",
        "بأقرب وقت بدفع", "بدي ادفع بعدين", "تاخير", "تأخير", "امهلوني", "إمهال", "مهلة",
        "مش متوفر هلق", "مش قادر ادفع هلق", "صبرك علينا", "طولو بالكن"
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

    # 3. Expiry date / Renewal inquiry (تاريخ التجديد / انتهاء الاشتراك / إيمتى بيخلص)
    expiry_keywords = [
        "امتى بيخلص", "ايمتى بيخلص", "تاريخ التجديد", "تاريخ الانتهاء", "باقي للاشتراك",
        "كم يوم باقي", "متى بيخلص", "اي ساعة بيخلص", "تجديد الاشتراك", "تجديد باقتي",
        "كم يوم ضايل", "قديش باقي", "ايمتى بينتهي", "امتى بينتهي"
    ]
    if any(kw in norm for kw in expiry_keywords):
        if customer:
            status = get_customer_status(appmod, tenant_id, customer.id)
            exp = status.get("expiry_date")
            days = status.get("days_left", 0)
            plan = status.get("plan_name", "غير محدد")
            is_active = status.get("is_subscription_active", False)
            if not is_active or days <= 0:
                reply_text = f"أهلاً بك {customer.name}! معك يارا، اشتراكك منتهي الصلاحية حالياً. فيك تجدده فوراً عبر Whish أو تزورنا بالمركز ليرجع الخط شغال."
            else:
                reply_text = f"أهلاً بك {customer.name}! معك يارا، اشتراكك بباقة ({plan}) ساري المفعول، باقي عليه {days} يوم وتاريخ الانتهاء هو {exp}. في شي تاني بقدر ساعدك فيه؟"
        else:
            reply_text = "أهلاً بك! معك يارا من الدعم الفني، يرجى تزويدنا برقم هاتفك المسجل لمراجعة موعد تجديد اشتراكك."
        return {
            "intent": "expiry_inquiry",
            "reply_text": reply_text,
            "ticket_tag": "استفسار عن موعد التجديد",
            "escalate": False
        }

    # 4. Plan details / Speed / Price inquiry (تفاصيل الباقة / السرعة / السعر)
    plan_keywords = [
        "شو باقتي", "شو سرعتي", "كم ميغا", "باقة النت", "سعر الباقة", "تفاصيل الباقة",
        "نوع الاشتراك", "اي باقة", "سرعة الخط", "قديش السرعة", "كم السرعة"
    ]
    if any(kw in norm for kw in plan_keywords):
        if customer:
            status = get_customer_status(appmod, tenant_id, customer.id)
            plan = status.get("plan_name", "غير محدد")
            price = status.get("plan_price", 0.0)
            reply_text = f"أهلاً بك {customer.name}! معك يارا، باقتك الحالية هي {plan} وقيمتها {price:.2f} دولار شهرياً."
        else:
            reply_text = "أهلاً بك! معك يارا من الدعم الفني، يرجى تزويدنا برقم هاتفك المسجل لمعرفة تفاصيل باقتك الحالية."
        return {
            "intent": "plan_inquiry",
            "reply_text": reply_text,
            "ticket_tag": "استفسار عن الباقة والسرعة",
            "escalate": False
        }

    # 5. Inquiry about balance / due amount (استفسار عن الرصيد / الفاتورة)
    balance_keywords = [
        "رصيد", "فاتورة", "فواتير", "كشف", "حسابي", "قديش عليي", "شو عليي", "مستحق",
        "كم عليي", "كام عليي", "قديه الحساب", "قديه الفاتورة", "بدي اعرف حسابي",
        "قديه بدي ادفع", "المبلغ المطلوب"
    ]
    if any(kw in norm for kw in balance_keywords):
        if customer:
            status = get_customer_status(appmod, tenant_id, customer.id)
            bal_due = status.get("balance_due", 0.0)
            if bal_due > 0:
                pay_data = send_payment_link(appmod, tenant_id, customer.id)
                pay_url = pay_data.get("payment_url", "")
                reply_text = (
                    f"أهلاً وسهلاً بك {customer.name}! معك يارا،\n"
                    f"رصيدك المستحق الحالي هو: {bal_due:.2f} دولار.\n"
                    f"فيك تدفع بسهولة عبر Whish Money على الرابط التالي أو بزيارة مركزنا:\n{pay_url}"
                )
            else:
                reply_text = (
                    f"أهلاً وسهلاً بك {customer.name}! معك يارا، "
                    "حسابك خالص وما في عليك أي مبالغ مستحقة للدفع حالياً، شكراً إلك!"
                )
        else:
            reply_text = "أهلاً بك! معك يارا من الدعم الفني، يرجى تزويدنا برقم هاتفك المسجل لنتمكن من مراجعة رصيد حسابك."

        return {
            "intent": "balance_inquiry",
            "reply_text": reply_text,
            "ticket_tag": "استفسار رصيد",
            "escalate": False
        }

    # 6. How to pay / Payment link request (رابط الدفع / طريقة الدفع)
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

    # 7. Connection problem / Outage / Slow internet (انقطاع / بطء / عطل)
    conn_keywords = [
        "انقطاع", "فاصل", "مقطوع", "ما في نت", "النت واقف", "النت فاصل", "بطيء", "بطيئ",
        "مش شغال", "الراوتر", "الضو الاحمر", "الضوء الأحمر", "فايبر", "معطل", "مشكلة بالنت",
        "عطل", "تقطيع", "عم يقطع", "ضعيف", "تقيل", "ping", "بنغ"
    ]
    if any(kw in norm for kw in conn_keywords):
        if customer:
            diag = network_diagnostic(appmod, tenant_id, customer.id, wait_seconds=3.0)
            diagnosis_msg = diag.get("diagnosis_ar", "اشتراكك مفعّل على النظام.")
            reply_text = (
                f"أهلاً بك {customer.name}! معك يارا، فحصتلك الخط:\n"
                f"{diagnosis_msg}\n\n"
                "نصيحة سريعة: جرب طفي الراوتر وشغله بالكهربا دقيقة. إذا استمر العطل، فريق الصيانة رح يتابع خطك بأسرع وقت."
            )
        else:
            reply_text = "أهلاً بك! معك يارا، تم تسجيل ملاحظتك بخصوص اتصال الإنترنت، وسيقوم فريق الدعم الفني بالمتابعة معك فوراً."

        return {
            "intent": "connection_issue",
            "reply_text": reply_text,
            "ticket_tag": "فحص انقطاع إنترنت",
            "escalate": False
        }

    # 8. Location & Office hours (العنوان وساعات العمل)
    office_keywords = [
        "وين مركزكن", "وين المحل", "عنوانكن", "أوقات الدوام", "امتى بتفتحوا", "ساعات العمل",
        "موقعكم", "وين موجودين", "موقع المحل"
    ]
    if any(kw in norm for kw in office_keywords):
        reply_text = (
            "أهلاً وسهلاً بك! معك يارا، مركز DeltaNet بخدمتكم يومياً من الساعة 9:00 صباحاً حتى 8:00 مساءً.\n"
            "لأي مساعدة فيك تتواصل معنا هون مباشرة على الواتساب أو تشرفنا بالمركز."
        )
        return {
            "intent": "office_info",
            "reply_text": reply_text,
            "ticket_tag": "استفسار عن المركز والدوام",
            "escalate": False
        }

    # 9. Escalation to human agent (طلب التحدث مع موظف / دعم فني)
    escalate_keywords = ["موظف", "انسان", "إنسان", "حدا يحكيني", "تواصل مع شخص", "دعم فني", "مسؤول", "اتصل فيني", "دقلي", "بدي احكي مع حدا"]
    if any(kw in norm for kw in escalate_keywords):
        cust_id = customer.id if customer else None
        esc_res = escalate_to_human(
            appmod, tenant_id, cust_id,
            reason="طلب التحدث مع موظف عبر واتساب",
            summary=clean_text,
            phone=getattr(customer, 'phone', None)
        )
        ticket_id = esc_res.get("ticket_id", "")
        reply_text = (
            f"تكرم عينك! معك يارا، حولت طلبك لفريق الدعم الفني وسجلتلك تذكرة برقم {ticket_id}. "
            "رح يتواصل معك أحد موظفينا بأقرب وقت ممكن."
        )
        return {
            "intent": "escalate",
            "reply_text": reply_text,
            "ticket_tag": "تحويل لموظف",
            "escalate": True
        }

    # 10. Default polite fallback (unrecognized customer question or inquiry)
    # NEVER repeat the introductory first_message greeting here under any circumstances!
    name_str = f" {customer.name}" if customer else ""
    if is_new_session:
        reply_text = (
            f"أهلاً وسهلاً بك{name_str}! معك يارا من الدعم الفني لـ DeltaNet. "
            "تكرم عينك، كيف بقدر ساعدك؟ فيك تسألني عن رصيد حسابك، موعد تجديد اشتراكك، تفاصيل باقتك، أو فحص حالة النت عندك."
        )
    else:
        reply_text = (
            f"تكرم عينك{name_str}! معك يارا، كيف بقدر ساعدك بالتفصيل؟ "
            "فيك تسألني عن رصيدك والفاتورة، تجديد الاشتراك، فحص سرعة وجودة النت، أو طلب التحدث مع الدعم الفني."
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

    # Check if there is already an active session with messages for this phone number
    now = datetime.utcnow()
    is_new_session = True
    session = None  # always defined; assigned inside try block below
    try:
        phone_digits = re.sub(r'\D', '', str(sender_phone or ''))
        phone_8 = phone_digits[-8:] if len(phone_digits) >= 8 else phone_digits

        session_q = appmod.CSAgentSession.query.filter_by(
            tenant_id=tenant_id,
            channel='whatsapp',
            state='active'
        )
        if customer and customer.id:
            session = session_q.filter(
                (appmod.CSAgentSession.customer_id == customer.id) |
                (appmod.CSAgentSession.caller_identifier == sender_phone) |
                (appmod.CSAgentSession.caller_identifier.like(f"%{phone_8}%"))
            ).order_by(appmod.CSAgentSession.id.desc()).first()
        elif phone_8:
            session = session_q.filter(
                (appmod.CSAgentSession.caller_identifier == sender_phone) |
                (appmod.CSAgentSession.caller_identifier.like(f"%{phone_8}%"))
            ).order_by(appmod.CSAgentSession.id.desc()).first()
        else:
            session = session_q.filter_by(caller_identifier=sender_phone).order_by(appmod.CSAgentSession.id.desc()).first()

        if session:
            sess_time = getattr(session, 'last_active_at', None) or getattr(session, 'created_at', None)
            if sess_time and (now - sess_time < timedelta(minutes=30)):
                has_logs = appmod.CSAgentMessageLog.query.filter_by(session_id=session.id).first() is not None
                if has_logs:
                    is_new_session = False
                elif customer and customer.id:
                    recent_ticket = appmod.SupportTicket.query.filter_by(
                        tenant_id=tenant_id,
                        customer_id=customer.id
                    ).filter(appmod.SupportTicket.created_at >= now - timedelta(minutes=30)).first()
                    if recent_ticket:
                        is_new_session = False
    except Exception as e_sess:
        logging.warning(f"Error checking active session: {e_sess}")

    # Resolve target ElevenLabs agent ID and admin permissions
    target_agent_id = getattr(settings, 'elevenlabs_agent_id', None)
    is_admin = False
    
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
    if target_agent_id:
        try:
            ai_result = query_elevenlabs_conversational_ai(
                agent_id=target_agent_id,
                incoming_text=incoming_text,
                sender_phone=sender_phone,
                customer=customer,
                recent_history=recent_history,
                timeout=35,
                is_admin=is_admin
            )
        except Exception as ex_conv:
            logging.warning(f"ElevenLabs ConvAI call failed, will fallback: {ex_conv}")

    # Fallback to local rule-based AI processor if ElevenLabs ConvAI did not return a response
    if not ai_result or not ai_result.get("reply_text"):
        ai_result = process_customer_message_ai(
            appmod, tenant_id, customer, incoming_text, is_voice=is_voice, is_new_session=is_new_session
        )

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
            tts_audio = synthesize_speech_elevenlabs(reply_text, agent_id=target_agent_id)
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
                else:
                    logging.warning(f"send_whatsapp_voice returned False for +{sender_phone}")
            else:
                logging.warning(f"synthesize_speech_elevenlabs returned None for +{sender_phone} (check ELEVENLABS_API_KEY).")
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
        phone_digits = re.sub(r'\D', '', str(sender_phone or ''))
        phone_8 = phone_digits[-8:] if len(phone_digits) >= 8 else phone_digits

        session_q = appmod.CSAgentSession.query.filter_by(
            tenant_id=tenant_id,
            channel='whatsapp',
            state='active'
        )
        if customer and customer.id:
            session = session_q.filter(
                (appmod.CSAgentSession.customer_id == customer.id) |
                (appmod.CSAgentSession.caller_identifier == sender_phone) |
                (appmod.CSAgentSession.caller_identifier.like(f"%{phone_8}%"))
            ).order_by(appmod.CSAgentSession.id.desc()).first()
        elif phone_8:
            session = session_q.filter(
                (appmod.CSAgentSession.caller_identifier == sender_phone) |
                (appmod.CSAgentSession.caller_identifier.like(f"%{phone_8}%"))
            ).order_by(appmod.CSAgentSession.id.desc()).first()
        else:
            session = session_q.filter_by(caller_identifier=sender_phone).order_by(appmod.CSAgentSession.id.desc()).first()

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
            if customer and not session.customer_id:
                session.customer_id = customer.id
            appmod.db.session.commit()

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


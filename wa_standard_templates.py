"""Standard WhatsApp message templates a tenant can submit to Meta in one go,
with wording they can edit while the variables the platform fills stay
enforced (FR-015..FR-015d). See specs/001-whatsapp-auto-connect/research.md R-8.

Text uses named placeholders ({customer_name}). On submit the body is
renumbered to Meta's positional {{1}}..{{n}} in order of appearance and the
resulting `param_map` (semantic key per position) is stored on the
WhatsAppTemplate row, so send_whatsapp_message can fill the right values
wherever the tenant moved them.

`variables` lists each template's body variables in the platform's legacy
send order -- tests/test_whatsapp_standard_templates.py pins them against
app._wa_semantic_params so the catalog can't drift from the send path.
"""
import re

BODY_MAX = 1024
HEADER_MAX = 60
FOOTER_MAX = 60
BUTTON_TEXT_MAX = 25

_PLACEHOLDER = re.compile(r'\{([a-z_]+)\}')

_LABELS = {
    'customer_name': 'Customer name',
    'amount': 'Amount',
    'balance': 'Balance',
    'plan': 'Plan',
    'expiry_date': 'Expiry date',
    'pay_token': 'Payment link',
    'customer': 'Customer',
    'message': 'Message',
    'location': 'Location',
    'eta': 'Estimated time',
}


def _vars(*keys):
    return [{'key': k, 'label': _LABELS[k]} for k in keys]


CATALOG = {
    'payment_paid': {
        'settings_field': 'template_payment_paid', 'default_name': 'payment_confirmation',
        'category': 'UTILITY',
        'body': 'Dear {customer_name}, we have received your payment of ${amount}. '
                'Your current balance is ${balance}. Thank you!',
        'variables': _vars('customer_name', 'amount', 'balance'),
        'sample_values': {'customer_name': 'Ali', 'amount': '25.00', 'balance': '0.00'},
    },
    'subscription_created': {
        'settings_field': 'template_subscription_created', 'default_name': 'subscription_created',
        'category': 'UTILITY',
        'body': 'Welcome {customer_name}! Your {plan} subscription is active until {expiry_date}. '
                'Current balance: ${balance}. Thank you for choosing us.',
        'variables': _vars('customer_name', 'plan', 'expiry_date', 'balance'),
        'sample_values': {'customer_name': 'Ali', 'plan': 'Fiber 50', 'expiry_date': '2026-12-01',
                          'balance': '25.00'},
    },
    'subscription_renewed': {
        'settings_field': 'template_subscription_renewed', 'default_name': 'subscription_renewal',
        'category': 'UTILITY',
        'body': 'Dear {customer_name}, your {plan} subscription has been renewed until {expiry_date}. '
                'Current balance: ${balance}. Thank you!',
        'variables': _vars('customer_name', 'plan', 'expiry_date', 'balance'),
        'sample_values': {'customer_name': 'Ali', 'plan': 'Fiber 50', 'expiry_date': '2026-12-01',
                          'balance': '0.00'},
    },
    'payment_reminder': {
        'settings_field': 'template_payment_reminder', 'default_name': 'payment_reminder',
        'category': 'UTILITY',
        'body': 'Dear {customer_name}, this is a friendly reminder that your subscription payment is due. '
                'Balance: ${balance}. Expiry date: {expiry_date}. Thank you!',
        'variables': _vars('customer_name', 'balance', 'expiry_date'),
        'sample_values': {'customer_name': 'Ali', 'balance': '25.00', 'expiry_date': '2026-12-01'},
    },
    'current_balance': {
        'settings_field': 'template_current_balance', 'default_name': 'current_balance',
        'category': 'UTILITY',
        'body': 'Dear {customer_name}, your current balance is ${balance}. '
                'Your subscription expires on {expiry_date}. Thank you!',
        'variables': _vars('customer_name', 'balance', 'expiry_date'),
        'sample_values': {'customer_name': 'Ali', 'balance': '25.00', 'expiry_date': '2026-12-01'},
    },
    'payment_link': {
        # The URL button's {{1}} is filled from the same first send param, so
        # the body must have no variables (see send_whatsapp_message).
        'settings_field': 'template_payment_link', 'default_name': 'payment_link',
        'category': 'UTILITY',
        'body': 'Hello, you can pay your subscription securely online with the button below. Thank you!',
        'variables': [],
        'button_variables': _vars('pay_token'),
        'buttons': [{'type': 'URL', 'text': 'Pay now'}],
        'sample_values': {'pay_token': 'sample-token'},
    },
    'forward_alert': {
        # Sent by cs_agent_tools.escalate_to_human with fixed-order params, so
        # the variables may not be reordered.
        'settings_field': 'template_forward_alert', 'default_name': 'customer_reply_alert',
        'category': 'UTILITY', 'fixed_order': True,
        'body': 'Customer {customer} needs help: {message}. Please reply from the inbox.',
        'variables': _vars('customer', 'message'),
        'sample_values': {'customer': 'Ali (96170123456)', 'message': 'No internet since morning'},
    },
    'forward_keepalive': {
        'settings_field': 'template_forward_keepalive', 'default_name': 'daily_checkin',
        'category': 'UTILITY',
        'body': 'Daily check-in from your customer service system. '
                'Please reply to keep notifications active.',
        'variables': [],
        'sample_values': {},
    },
    'bulk_outage': {
        'settings_field': 'template_bulk_outage', 'default_name': 'outage_alert',
        'category': 'UTILITY',
        'body': 'Service notice: {message}. We apologise for the inconvenience.',
        'variables': _vars('message'),
        'sample_values': {'message': 'There is an outage in your area, our team is working on it'},
    },
    'bulk_maintenance': {
        'settings_field': 'template_bulk_maintenance', 'default_name': 'maintenance_alert',
        'category': 'UTILITY',
        'body': 'Scheduled maintenance in {location}. Estimated time: {eta}. Thank you for your patience.',
        'variables': _vars('location', 'eta'),
        'sample_values': {'location': 'Hamra', 'eta': '2 hours'},
    },
    'bulk_feature': {
        'settings_field': 'template_bulk_feature', 'default_name': 'feature_update',
        'category': 'MARKETING',
        'body': 'New from us: {message}. Thank you for being our customer.',
        'variables': _vars('message'),
        'sample_values': {'message': 'faster speeds at the same price'},
    },
    'bulk_offer': {
        'settings_field': 'template_bulk_offer', 'default_name': 'special_offer',
        'category': 'MARKETING',
        'body': 'Special offer: {message}. Reply to this message to learn more.',
        'variables': _vars('message'),
        'sample_values': {'message': 'one month free on yearly plans'},
    },
}


def placeholders(text):
    return _PLACEHOLDER.findall(text or '')


def validate_item(key, header, body, footer, buttons):
    """Plain-language problems with an edited template; [] when it can be submitted."""
    entry = CATALOG.get(key)
    if entry is None:
        return ['Unknown standard template "{}".'.format(key)]
    errors = []
    body = body or ''
    required = [v['key'] for v in entry['variables']]
    found = placeholders(body)
    if not body.strip():
        errors.append('Body cannot be empty.')
    for k in required:
        if k not in found:
            errors.append('Missing variable {%s}.' % k)
    for k in sorted(set(found)):
        if k not in required:
            errors.append('Unknown variable {%s} -- this template can only use: %s.'
                          % (k, ', '.join('{%s}' % r for r in required) or 'no variables'))
        elif found.count(k) > 1:
            errors.append('{%s} is used more than once.' % k)
    stripped = body.strip()
    if _PLACEHOLDER.match(stripped):
        errors.append('The body cannot start with a variable -- add some words before it.')
    if re.search(r'\{[a-z_]+\}$', stripped):
        errors.append('The body cannot end with a variable -- add some words after it.')
    if entry.get('fixed_order') and set(found) == set(required) and found != required:
        errors.append('The variables of this template must stay in this order: %s.'
                      % ', '.join('{%s}' % r for r in required))
    if len(body) > BODY_MAX:
        errors.append('Body is too long ({} characters, max {}).'.format(len(body), BODY_MAX))
    for label, text, limit in (('Header', header, HEADER_MAX), ('Footer', footer, FOOTER_MAX)):
        if text:
            if len(text) > limit:
                errors.append('{} is too long ({} characters, max {}).'.format(label, len(text), limit))
            if placeholders(text):
                errors.append('{} cannot contain a variable.'.format(label))
    for btn in buttons or []:
        text = (btn or {}).get('text') or ''
        if not text.strip():
            errors.append('Button text cannot be empty.')
        elif len(text) > BUTTON_TEXT_MAX:
            errors.append('Button text is too long (max {} characters).'.format(BUTTON_TEXT_MAX))
    return errors


def to_meta_components(key, header, body, footer, buttons, *, base_url):
    """Meta template components + param_map for a validated item."""
    entry = CATALOG[key]
    samples = entry['sample_values']
    order = []

    def number(match):
        order.append(match.group(1))
        return '{{%d}}' % len(order)

    components = []
    if header:
        components.append({'type': 'HEADER', 'format': 'TEXT', 'text': header})
    body_comp = {'type': 'BODY', 'text': _PLACEHOLDER.sub(number, body)}
    if order:
        body_comp['example'] = {'body_text': [[samples[k] for k in order]]}
    components.append(body_comp)
    if footer:
        components.append({'type': 'FOOTER', 'text': footer})
    if entry.get('buttons'):
        meta_buttons = []
        for i, default in enumerate(entry['buttons']):
            edited = (buttons or [])[i] if i < len(buttons or []) else {}
            text = (edited or {}).get('text') or default['text']
            if default['type'] == 'URL':
                url = base_url.rstrip('/') + '/pay?token='
                meta_buttons.append({'type': 'URL', 'text': text, 'url': url + '{{1}}',
                                     'example': [url + samples['pay_token']]})
        components.append({'type': 'BUTTONS', 'buttons': meta_buttons})
    return components, order


def catalog_view():
    """Catalog as sent to the editor (no server-only fields)."""
    out = []
    for key, entry in CATALOG.items():
        out.append({
            'key': key,
            'name': entry['default_name'],
            'settings_field': entry['settings_field'],
            'category': entry['category'],
            'header': None,
            'body': entry['body'],
            'footer': None,
            'buttons': [{'type': b['type'], 'text': b['text']} for b in entry.get('buttons', [])],
            'variables': entry['variables'],
            'button_variables': entry.get('button_variables', []),
            'fixed_order': bool(entry.get('fixed_order')),
        })
    return out

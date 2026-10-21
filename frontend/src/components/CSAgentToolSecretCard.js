import React, { useState } from 'react';
import { Alert, Box, Button, Chip, CircularProgress, IconButton, Paper, Typography } from '@mui/material';
import {
    ContentCopy as ContentCopyIcon,
    Key as KeyIcon,
    CheckCircle as CheckCircleIcon
} from '@mui/icons-material';
import axios from 'axios';

const TOOL_PATHS = [
    '/api/cs-agent/tools/lookup-customer',
    '/api/cs-agent/tools/get-customer-status',
    '/api/cs-agent/tools/network-diagnostic',
    '/api/cs-agent/tools/send-payment-link',
    '/api/cs-agent/tools/escalate-to-human',
];

// This business's own secret for the ElevenLabs agent's server tools. The
// backend derives the business FROM this secret, so one business's agent can
// never read another's customers. Shown once on generation; only a hash is kept.
export default function CSAgentToolSecretCard({ hasSecret, createdAt, headerName, onChange }) {
    const [secret, setSecret] = useState('');
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState('');
    const [copied, setCopied] = useState(false);
    const header = headerName || 'X-CS-Agent-Secret';

    const generate = async () => {
        if (hasSecret && !window.confirm(
            'سيتم إلغاء المفتاح الحالي فوراً وسيتوقف الـ Agent عن العمل حتى تضع المفتاح الجديد في أدوات ElevenLabs. متابعة؟'
        )) return;
        setBusy(true);
        setError('');
        setCopied(false);
        try {
            const token = localStorage.getItem('token');
            const res = await axios.post('/api/cs-agent/tool-secret', {}, {
                headers: { Authorization: `Bearer ${token}` }
            });
            setSecret(res.data.secret);
            if (onChange) onChange({ hasSecret: true, createdAt: res.data.created_at });
        } catch (err) {
            setError(err.response?.data?.error || err.response?.data?.msg || 'تعذر إنشاء المفتاح.');
        } finally {
            setBusy(false);
        }
    };

    const copy = async () => {
        try {
            await navigator.clipboard.writeText(secret);
            setCopied(true);
        } catch (e) {
            setCopied(false);
        }
    };

    return (
        <Paper variant="outlined" sx={{ p: 2, mb: 4, borderRadius: '12px' }}>
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5, flexWrap: 'wrap', mb: 1 }}>
                <KeyIcon color="primary" />
                <Typography variant="subtitle2" fontWeight={700} sx={{ flexGrow: 1 }}>
                    مفتاح أدوات الـ Agent (Tool Secret)
                </Typography>
                {hasSecret ? (
                    <Chip icon={<CheckCircleIcon />} color="success" size="small"
                        label={`مفعّل${createdAt ? ` منذ ${createdAt.slice(0, 10)}` : ''}`} />
                ) : (
                    <Chip color="warning" size="small" label="غير مُنشأ" />
                )}
            </Box>
            <Typography variant="body2" color="text.secondary" sx={{ mb: 1.5 }}>
                مفتاح خاص بشركتك تضعه في أدوات الـ Server Tools داخل ElevenLabs. يُعرض مرة واحدة فقط.
            </Typography>

            {error && <Alert severity="error" sx={{ mb: 1.5 }} onClose={() => setError('')}>{error}</Alert>}

            {secret && (
                <Alert severity="info" sx={{ mb: 1.5 }} icon={false}>
                    <Typography variant="body2" fontWeight={700} gutterBottom>
                        انسخ المفتاح الآن — لن يظهر مرة أخرى.
                    </Typography>
                    <Typography variant="body2" gutterBottom>
                        في ElevenLabs، لكل أداة من أدوات الـ Server Tools أضف هذا الـ Header:
                    </Typography>
                    <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, direction: 'ltr', my: 1 }}>
                        <Box component="code" data-testid="tool-secret-header"
                            sx={{ fontFamily: 'monospace', fontSize: '0.8rem', wordBreak: 'break-all', flexGrow: 1 }}>
                            {header}: {secret}
                        </Box>
                        <IconButton size="small" onClick={copy} aria-label="copy secret">
                            <ContentCopyIcon fontSize="small" />
                        </IconButton>
                    </Box>
                    {copied && <Typography variant="caption" color="success.main">تم النسخ</Typography>}
                    <Typography variant="caption" component="div" sx={{ mt: 1 }}>
                        الأدوات المعنية (لم يعد هناك حاجة لإرسال tenant_id):
                    </Typography>
                    <Box component="ul" sx={{ direction: 'ltr', m: 0, pl: 3, fontFamily: 'monospace', fontSize: '0.75rem' }}>
                        {TOOL_PATHS.map((p) => <li key={p}>{window.location.origin}{p}</li>)}
                    </Box>
                </Alert>
            )}

            <Button
                variant={hasSecret ? 'outlined' : 'contained'}
                color={hasSecret ? 'warning' : 'primary'}
                size="small"
                onClick={generate}
                disabled={busy}
                startIcon={busy ? <CircularProgress size={14} /> : <KeyIcon />}
            >
                {hasSecret ? 'تدوير المفتاح (Rotate)' : 'إنشاء المفتاح'}
            </Button>
        </Paper>
    );
}

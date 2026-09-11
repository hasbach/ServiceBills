import React, { useState, useEffect, useRef } from 'react';
import {
    Box, Card, CardContent, Typography, TextField, Button,
    Chip, Stack, IconButton, CircularProgress,
    Alert, Paper, Grid, Switch, FormControlLabel, LinearProgress
} from '@mui/material';
import {
    Mic as MicIcon,
    MicOff as MicOffIcon,
    CallEnd as CallEndIcon,
    PhoneInTalk as PhoneInTalkIcon,
    Save as SaveIcon
} from '@mui/icons-material';
import axios from 'axios';

export default function CSAgentVoiceTest() {
    const [agentId, setAgentId] = useState('');
    const [status, setStatus] = useState('idle'); // idle | connecting | connected | speaking | listening | error
    const [errorMessage, setErrorMessage] = useState('');
    const [isMuted, setIsMuted] = useState(false);
    const [audioLevel, setAudioLevel] = useState(0);
    const [saveSuccess, setSaveSuccess] = useState('');
    const [savingConfig, setSavingConfig] = useState(false);
    const [transcripts, setTranscripts] = useState([]);
    const [eventLogs, setEventLogs] = useState([]);
    const [autoScroll, setAutoScroll] = useState(true);

    // Direct Tool Testing state
    const [toolPhone, setToolPhone] = useState('70123456');
    const [toolCustomerId, setToolCustomerId] = useState('1');
    const [toolResult, setToolResult] = useState(null);
    const [toolLoading, setToolLoading] = useState(false);

    // Audio & WebSocket refs
    const wsRef = useRef(null);
    const audioContextRef = useRef(null);
    const mediaStreamRef = useRef(null);
    const processorRef = useRef(null);
    const audioQueueRef = useRef([]);
    const isPlayingRef = useRef(false);
    const transcriptEndRef = useRef(null);

    // Fetch initial config from backend
    useEffect(() => {
        const token = localStorage.getItem('token');
        axios.get('/api/cs-agent/config', {
            headers: token ? { Authorization: `Bearer ${token}` } : {}
        })
            .then(res => {
                if (res.data && res.data.elevenlabs_agent_id) {
                    setAgentId(res.data.elevenlabs_agent_id);
                }
            })
            .catch(() => {
                // Ignore config fetch error in dev
            });
    }, []);

    // Auto-scroll transcript
    useEffect(() => {
        if (autoScroll && transcriptEndRef.current) {
            transcriptEndRef.current.scrollIntoView({ behavior: 'smooth' });
        }
    }, [transcripts, eventLogs, autoScroll]);

    const logEvent = (name, data) => {
        setEventLogs(prev => [
            ...prev.slice(-49),
            { time: new Date().toLocaleTimeString(), name, data }
        ]);
    };

    const handleSaveAgentId = async () => {
        if (!agentId.trim()) {
            setErrorMessage('يرجى إدخال ElevenLabs Agent ID قبل الحفظ.');
            return;
        }
        setSavingConfig(true);
        setSaveSuccess('');
        setErrorMessage('');
        try {
            const token = localStorage.getItem('token');
            await axios.post('/api/cs-agent/config', {
                elevenlabs_agent_id: agentId.trim()
            }, {
                headers: token ? { Authorization: `Bearer ${token}` } : {}
            });
            setSaveSuccess('تم حفظ معرف الوكيل بنجاح لهذا المشترك!');
            setTimeout(() => setSaveSuccess(''), 4000);
        } catch (err) {
            setErrorMessage('فشل حفظ إعدادات الوكيل: ' + (err.response?.data?.error || err.message));
        } finally {
            setSavingConfig(false);
        }
    };

    // Helper: Downsample audio Float32Array to 16,000 Hz (ElevenLabs standard)
    const downsampleTo16k = (buffer, inputSampleRate) => {
        if (!buffer || buffer.length === 0 || inputSampleRate === 16000) return buffer;
        const ratio = inputSampleRate / 16000;
        const newLength = Math.round(buffer.length / ratio);
        const result = new Float32Array(newLength);
        let offsetResult = 0;
        let offsetBuffer = 0;
        while (offsetResult < result.length) {
            const nextOffsetBuffer = Math.round((offsetResult + 1) * ratio);
            let accum = 0;
            let count = 0;
            for (let i = offsetBuffer; i < nextOffsetBuffer && i < buffer.length; i++) {
                accum += buffer[i];
                count++;
            }
            result[offsetResult] = count > 0 ? accum / count : 0;
            offsetResult++;
            offsetBuffer = nextOffsetBuffer;
        }
        return result;
    };

    // ── WebSocket Audio Pipeline ─────────────────────────────────────────────
    const startConversation = async () => {
        if (!agentId.trim()) {
            setErrorMessage('يرجى إدخال ElevenLabs Agent ID للبدء.');
            return;
        }

        setErrorMessage('');
        setStatus('connecting');
        logEvent('Connecting', `Agent ID: ${agentId}`);

        try {
            // 1. Microphone access
            const stream = await navigator.mediaDevices.getUserMedia({
                audio: {
                    channelCount: 1,
                    echoCancellation: true,
                    noiseSuppression: true,
                    autoGainControl: true
                }
            });
            mediaStreamRef.current = stream;

            // 2. AudioContext setup (try 16kHz context, fallback to system default)
            const AudioCtx = window.AudioContext || window.webkitAudioContext;
            let audioCtx;
            try {
                audioCtx = new AudioCtx({ sampleRate: 16000 });
            } catch (e) {
                audioCtx = new AudioCtx();
            }
            audioContextRef.current = audioCtx;

            if (audioCtx.state === 'suspended') {
                await audioCtx.resume();
            }

            const source = audioCtx.createMediaStreamSource(stream);
            // ScriptProcessorNode to buffer audio chunks
            const processor = audioCtx.createScriptProcessor(4096, 1, 1);
            processorRef.current = processor;

            // Muted gain node to prevent speaker loopback/echo while keeping processor running
            const muteGain = audioCtx.createGain();
            muteGain.gain.value = 0;

            // 3. Connect WebSocket
            const wsUrl = `wss://api.elevenlabs.io/v1/convai/conversation?agent_id=${encodeURIComponent(agentId.trim())}`;
            const ws = new WebSocket(wsUrl);
            wsRef.current = ws;

            ws.onopen = () => {
                setStatus('connected');
                logEvent('Connected', 'WebSocket connection established');

                // Send initial client handshake
                const initData = {
                    type: 'conversation_initiation_client_data',
                    dynamic_variables: {
                        platform: 'ServiceBills Admin Voice Console',
                        timestamp: new Date().toISOString()
                    }
                };
                ws.send(JSON.stringify(initData));

                // Start recording and sending audio
                source.connect(processor);
                processor.connect(muteGain);
                muteGain.connect(audioCtx.destination);

                let chunkCount = 0;
                processor.onaudioprocess = (e) => {
                    if (ws.readyState !== WebSocket.OPEN) return;
                    const inputData = e.inputBuffer.getChannelData(0);

                    // Calculate RMS volume for live VU meter
                    let sum = 0;
                    for (let i = 0; i < inputData.length; i++) {
                        sum += inputData[i] * inputData[i];
                    }
                    const rms = Math.sqrt(sum / inputData.length);
                    const level = Math.min(100, Math.round(rms * 450));
                    setAudioLevel(level);

                    if (isMuted) return;

                    // Ensure strictly 16,000 Hz PCM for ElevenLabs
                    const samples16k = downsampleTo16k(inputData, audioCtx.sampleRate);

                    // Convert Float32Array to 16-bit PCM
                    const pcm16 = new Int16Array(samples16k.length);
                    for (let i = 0; i < samples16k.length; i++) {
                        const s = Math.max(-1, Math.min(1, samples16k[i]));
                        pcm16[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
                    }

                    // Convert to base64
                    const bytes = new Uint8Array(pcm16.buffer);
                    let binary = '';
                    for (let i = 0; i < bytes.byteLength; i++) {
                        binary += String.fromCharCode(bytes[i]);
                    }
                    const base64Chunk = btoa(binary);

                    ws.send(JSON.stringify({
                        user_audio_chunk: base64Chunk
                    }));

                    chunkCount++;
                    if (chunkCount === 1) {
                        logEvent('Audio Streaming', `Mic active (${audioCtx.sampleRate}Hz -> 16kHz PCM). ElevenLabs is listening.`);
                    } else if (chunkCount % 100 === 0) {
                        logEvent('Streaming Stats', `Sent ${chunkCount} audio chunks (Current input level: ${level}%)`);
                    }
                };
            };

            ws.onmessage = async (event) => {
                try {
                    const msg = JSON.parse(event.data);
                    handleServerEvent(msg);
                } catch (err) {
                    logEvent('Error', 'Failed to parse incoming message');
                }
            };

            ws.onerror = (err) => {
                logEvent('Error', 'WebSocket encountered an error');
                setStatus('error');
                setErrorMessage('فشل الاتصال بمخدم ElevenLabs. تأكد من صحة الـ Agent ID والميكروفون.');
            };

            ws.onclose = () => {
                logEvent('Disconnected', 'Call ended');
                cleanupAudio();
                setStatus('idle');
            };

        } catch (err) {
            setStatus('error');
            setErrorMessage(`تعذر الوصول للميكروفون أو بدء الاتصال: ${err.message}`);
            cleanupAudio();
        }
    };

    const stopConversation = () => {
        if (wsRef.current) {
            wsRef.current.close();
        }
        cleanupAudio();
        setStatus('idle');
        logEvent('User Action', 'Call ended by user');
    };

    const cleanupAudio = () => {
        setAudioLevel(0);
        if (processorRef.current) {
            processorRef.current.disconnect();
            processorRef.current = null;
        }
        if (mediaStreamRef.current) {
            mediaStreamRef.current.getTracks().forEach(track => track.stop());
            mediaStreamRef.current = null;
        }
        if (audioContextRef.current) {
            audioContextRef.current.close();
            audioContextRef.current = null;
        }
        audioQueueRef.current = [];
        isPlayingRef.current = false;
    };

    const handleServerEvent = (msg) => {
        switch (msg.type) {
            case 'conversation_initiation_metadata':
                logEvent('Metadata', msg.conversation_initiation_metadata_event);
                break;

            case 'user_transcript': {
                const text = msg.user_transcription_event?.user_transcript;
                if (text) {
                    setTranscripts(prev => [...prev, { speaker: 'user', text, time: new Date().toLocaleTimeString() }]);
                    setStatus('listening');
                }
                break;
            }

            case 'agent_response': {
                const text = msg.agent_response_event?.agent_response;
                if (text) {
                    setTranscripts(prev => [...prev, { speaker: 'agent', text, time: new Date().toLocaleTimeString() }]);
                    setStatus('speaking');
                }
                break;
            }

            case 'audio': {
                const base64Audio = msg.audio_event?.audio_base_64;
                if (base64Audio) {
                    queueAndPlayAudio(base64Audio);
                }
                break;
            }

            case 'interruption':
                audioQueueRef.current = [];
                isPlayingRef.current = false;
                logEvent('Interrupted', 'Agent response interrupted by user');
                setStatus('listening');
                break;

            case 'client_tool_call': {
                const toolCall = msg.client_tool_call;
                logEvent('Tool Call', toolCall);
                // Execute backend tool automatically if needed
                handleClientToolCall(toolCall);
                break;
            }

            default:
                if (msg.type) logEvent(msg.type, msg);
        }
    };

    // Audio Playback Queue
    const queueAndPlayAudio = (base64Audio) => {
        try {
            const binaryString = atob(base64Audio);
            const bytes = new Uint8Array(binaryString.length);
            for (let i = 0; i < binaryString.length; i++) {
                bytes[i] = binaryString.charCodeAt(i);
            }
            const blob = new Blob([bytes.buffer], { type: 'audio/mp3' });
            const url = URL.createObjectURL(blob);
            const audio = new Audio(url);

            audioQueueRef.current.push(audio);
            if (!isPlayingRef.current) {
                playNextAudio();
            }
        } catch (e) {
            console.error('Audio playback error', e);
        }
    };

    const playNextAudio = () => {
        if (audioQueueRef.current.length === 0) {
            isPlayingRef.current = false;
            setStatus('connected');
            return;
        }

        isPlayingRef.current = true;
        const audio = audioQueueRef.current.shift();
        audio.onended = () => {
            playNextAudio();
        };
        audio.onerror = () => {
            playNextAudio();
        };
        audio.play().catch(() => playNextAudio());
    };

    // Client tool execution (if ElevenLabs agent uses client tools)
    const handleClientToolCall = async (toolCall) => {
        const token = localStorage.getItem('token');
        const headers = token ? { Authorization: `Bearer ${token}` } : {};

        try {
            let result = {};
            if (toolCall.tool_name === 'lookup_customer') {
                const res = await axios.get(`/api/cs-agent/tools/lookup-customer?phone=${toolCall.parameters?.phone || ''}`, { headers });
                result = res.data;
            } else if (toolCall.tool_name === 'get_customer_status') {
                const res = await axios.get(`/api/cs-agent/tools/customer-status?customer_id=${toolCall.parameters?.customer_id || ''}`, { headers });
                result = res.data;
            } else if (toolCall.tool_name === 'network_diagnostic') {
                const res = await axios.post('/api/cs-agent/tools/network-diagnostic', toolCall.parameters, { headers });
                result = res.data;
            }

            if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
                wsRef.current.send(JSON.stringify({
                    type: 'client_tool_result',
                    tool_call_id: toolCall.tool_call_id,
                    result: JSON.stringify(result),
                    is_error: false
                }));
            }
        } catch (err) {
            if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
                wsRef.current.send(JSON.stringify({
                    type: 'client_tool_result',
                    tool_call_id: toolCall.tool_call_id,
                    result: err.message,
                    is_error: true
                }));
            }
        }
    };

    // ── Direct Tool Execution Helpers ────────────────────────────────────────
    const executeDirectTool = async (endpoint, method = 'GET', payload = null) => {
        setToolLoading(true);
        setToolResult(null);
        const token = localStorage.getItem('token');
        const headers = token ? { Authorization: `Bearer ${token}` } : {};

        try {
            let res;
            if (method === 'GET') {
                res = await axios.get(endpoint, { headers });
            } else {
                res = await axios.post(endpoint, payload, { headers });
            }
            setToolResult({ success: true, status: res.status, data: res.data });
        } catch (err) {
            setToolResult({
                success: false,
                status: err.response?.status || 500,
                data: err.response?.data || { error: err.message }
            });
        } finally {
            setToolLoading(false);
        }
    };

    return (
        <Box sx={{ p: { xs: 2, sm: 3 }, maxWidth: 1400, mx: 'auto' }}>
            <Box sx={{ mb: 3 }}>
                <Typography variant="h5" fontWeight={700} gutterBottom>
                    مساعد خدمة العملاء الصوتي (ElevenLabs AI Agent)
                </Typography>
                <Typography variant="body2" color="text.secondary">
                    اختبار المحادثة الصوتية الحية باللهجة اللبنانية عبر الـ WebSocket، وفحص أدوات تشخيص الشبكة والفوترة مباشرة.
                </Typography>
            </Box>

            {errorMessage && (
                <Alert severity="error" sx={{ mb: 3 }} onClose={() => setErrorMessage('')}>
                    {errorMessage}
                </Alert>
            )}

            <Grid container spacing={3}>
                {/* Left Column: Live Voice Agent Console */}
                <Grid item xs={12} md={7}>
                    <Card elevation={1} sx={{ borderRadius: '16px', height: '100%', display: 'flex', flexDirection: 'column' }}>
                        <CardContent sx={{ p: 3, flexGrow: 1, display: 'flex', flexDirection: 'column' }}>
                            {/* Agent ID & Status Header */}
                            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5, mb: 2, flexWrap: 'wrap' }}>
                                <TextField
                                    size="small"
                                    label="ElevenLabs Agent ID"
                                    value={agentId}
                                    onChange={(e) => setAgentId(e.target.value)}
                                    placeholder="agent_..."
                                    disabled={status !== 'idle' && status !== 'error'}
                                    sx={{ minWidth: 260, flexGrow: 1 }}
                                />

                                <Button
                                    variant="outlined"
                                    size="medium"
                                    startIcon={savingConfig ? <CircularProgress size={16} /> : <SaveIcon />}
                                    onClick={handleSaveAgentId}
                                    disabled={savingConfig || !agentId.trim()}
                                    sx={{ whiteSpace: 'nowrap' }}
                                >
                                    حفظ للمشترك
                                </Button>

                                <Chip
                                    label={
                                        status === 'idle' ? 'جاهز' :
                                        status === 'connecting' ? 'جارٍ الاتصال...' :
                                        status === 'speaking' ? 'سلام يتحدث...' :
                                        status === 'listening' ? 'يستمع إليك...' :
                                        status === 'connected' ? 'متصل ومستعد' : 'خطأ'
                                    }
                                    color={
                                        status === 'speaking' || status === 'listening' ? 'success' :
                                        status === 'connected' ? 'primary' :
                                        status === 'connecting' ? 'warning' :
                                        status === 'error' ? 'error' : 'default'
                                    }
                                    variant="filled"
                                    sx={{ fontWeight: 600, px: 1 }}
                                />
                            </Box>

                            {saveSuccess && (
                                <Alert severity="success" sx={{ mb: 2 }} onClose={() => setSaveSuccess('')}>
                                    {saveSuccess}
                                </Alert>
                            )}

                            {/* Call Control Button */}
                            <Box sx={{ display: 'flex', justifyContent: 'center', my: 2 }}>
                                {status === 'idle' || status === 'error' ? (
                                    <Button
                                        variant="contained"
                                        size="large"
                                        color="primary"
                                        startIcon={<PhoneInTalkIcon />}
                                        onClick={startConversation}
                                        sx={{
                                            borderRadius: '50px',
                                            px: 4,
                                            py: 1.5,
                                            fontSize: '1.1rem',
                                            fontWeight: 700,
                                            boxShadow: '0 8px 24px rgba(25, 118, 210, 0.3)'
                                        }}
                                    >
                                        بدء المحادثة الصوتية
                                    </Button>
                                ) : (
                                    <Stack direction="row" spacing={2} alignItems="center">
                                        <IconButton
                                            color={isMuted ? 'error' : 'primary'}
                                            onClick={() => setIsMuted(!isMuted)}
                                            sx={{
                                                bgcolor: isMuted ? 'error.light' : 'action.hover',
                                                p: 2
                                            }}
                                        >
                                            {isMuted ? <MicOffIcon /> : <MicIcon />}
                                        </IconButton>

                                        <Button
                                            variant="contained"
                                            color="error"
                                            size="large"
                                            startIcon={<CallEndIcon />}
                                            onClick={stopConversation}
                                            sx={{
                                                borderRadius: '50px',
                                                px: 4,
                                                py: 1.5,
                                                fontWeight: 700,
                                                boxShadow: '0 8px 24px rgba(211, 47, 47, 0.3)'
                                            }}
                                        >
                                            إنهاء المكالمة
                                        </Button>
                                    </Stack>
                                )}
                            </Box>

                            {/* Microphone Live VU Meter */}
                            {status !== 'idle' && status !== 'error' && (
                                <Box sx={{ width: '100%', maxWidth: 360, mx: 'auto', mt: 1, mb: 2, textAlign: 'center' }}>
                                    <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 0.5 }}>
                                        <Typography variant="caption" color="text.secondary">
                                            مستوى التقاط الميكروفون (VU Meter)
                                        </Typography>
                                        <Typography variant="caption" fontWeight={700} color={audioLevel > 5 ? 'success.main' : 'text.disabled'}>
                                            {audioLevel > 5 ? 'عم يسمع صوتك' : 'ساكت أو بعيد'} ({audioLevel}%)
                                        </Typography>
                                    </Box>
                                    <LinearProgress
                                        variant="determinate"
                                        value={Math.min(100, audioLevel)}
                                        sx={{
                                            height: 10,
                                            borderRadius: 5,
                                            backgroundColor: 'action.hover',
                                            '& .MuiLinearProgress-bar': {
                                                backgroundColor: audioLevel > 15 ? '#2e7d32' : audioLevel > 5 ? '#1976d2' : '#9e9e9e',
                                                transition: 'transform 0.08s linear'
                                            }
                                        }}
                                    />
                                </Box>
                            )}

                            {/* Live Transcripts Dialogue */}
                            <Typography variant="subtitle2" fontWeight={700} sx={{ mt: 2, mb: 1 }}>
                                نص المحادثة المباشر (Live Transcript)
                            </Typography>
                            <Paper
                                variant="outlined"
                                sx={{
                                    p: 2,
                                    flexGrow: 1,
                                    minHeight: 280,
                                    maxHeight: 400,
                                    overflowY: 'auto',
                                    borderRadius: '12px',
                                    bgcolor: 'grey.50'
                                }}
                            >
                                {transcripts.length === 0 ? (
                                    <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%', color: 'text.secondary' }}>
                                        <Typography variant="body2">
                                            اضغط "بدء المحادثة الصوتية" وتحدث بالعربية اللبنانية لتجربة المساعد.
                                        </Typography>
                                    </Box>
                                ) : (
                                    <Stack spacing={1.5}>
                                        {transcripts.map((t, idx) => (
                                            <Box
                                                key={idx}
                                                sx={{
                                                    alignSelf: t.speaker === 'user' ? 'flex-end' : 'flex-start',
                                                    maxWidth: '85%',
                                                    bgcolor: t.speaker === 'user' ? 'primary.main' : 'white',
                                                    color: t.speaker === 'user' ? 'white' : 'text.primary',
                                                    p: 1.5,
                                                    borderRadius: '12px',
                                                    boxShadow: '0 1px 4px rgba(0,0,0,0.08)'
                                                }}
                                            >
                                                <Typography variant="caption" sx={{ opacity: 0.8, display: 'block', mb: 0.5 }}>
                                                    {t.speaker === 'user' ? 'أنت (You)' : 'سلام (AI Agent)'} • {t.time}
                                                </Typography>
                                                <Typography variant="body1" sx={{ direction: 'rtl', textAlign: 'right' }}>
                                                    {t.text}
                                                </Typography>
                                            </Box>
                                        ))}
                                        <div ref={transcriptEndRef} />
                                    </Stack>
                                )}
                            </Paper>
                        </CardContent>
                    </Card>
                </Grid>

                {/* Right Column: Direct Backend Tools Tester & Event Logs */}
                <Grid item xs={12} md={5}>
                    <Stack spacing={3}>
                        {/* Direct Tools Quick Test */}
                        <Card elevation={1} sx={{ borderRadius: '16px' }}>
                            <CardContent sx={{ p: 3 }}>
                                <Typography variant="h6" fontWeight={700} gutterBottom>
                                    فحص أدوات النظام المباشرة (Direct Tools Test)
                                </Typography>
                                <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
                                    يمكنك فحص استجابة الأدوات التي يستدعيها الذكاء الاصطناعي من قاعدة بياناتك.
                                </Typography>

                                <Stack spacing={2}>
                                    <Box sx={{ display: 'flex', gap: 1 }}>
                                        <TextField
                                            size="small"
                                            label="رقم الهاتف (لبناني)"
                                            value={toolPhone}
                                            onChange={(e) => setToolPhone(e.target.value)}
                                            fullWidth
                                        />
                                        <Button
                                            variant="outlined"
                                            onClick={() => executeDirectTool(`/api/cs-agent/tools/lookup-customer?phone=${encodeURIComponent(toolPhone)}`)}
                                            disabled={toolLoading}
                                            sx={{ minWidth: 100 }}
                                        >
                                            بحث
                                        </Button>
                                    </Box>

                                    <Box sx={{ display: 'flex', gap: 1 }}>
                                        <TextField
                                            size="small"
                                            label="معرف العميل (ID)"
                                            value={toolCustomerId}
                                            onChange={(e) => setToolCustomerId(e.target.value)}
                                            sx={{ width: 140 }}
                                        />
                                        <Button
                                            variant="outlined"
                                            onClick={() => executeDirectTool(`/api/cs-agent/tools/customer-status?customer_id=${toolCustomerId}`)}
                                            disabled={toolLoading}
                                            sx={{ flexGrow: 1 }}
                                        >
                                            حالة الحساب
                                        </Button>
                                        <Button
                                            variant="outlined"
                                            color="warning"
                                            onClick={() => executeDirectTool('/api/cs-agent/tools/network-diagnostic', 'POST', { customer_id: parseInt(toolCustomerId), wait_seconds: 5 })}
                                            disabled={toolLoading}
                                            sx={{ flexGrow: 1 }}
                                        >
                                            فحص الشبكة
                                        </Button>
                                    </Box>

                                    <Box sx={{ display: 'flex', gap: 1 }}>
                                        <Button
                                            size="small"
                                            variant="text"
                                            onClick={() => executeDirectTool('/api/cs-agent/tools/send-payment-link', 'POST', { customer_id: parseInt(toolCustomerId) })}
                                            disabled={toolLoading}
                                        >
                                            رابط الدفع
                                        </Button>
                                        <Button
                                            size="small"
                                            variant="text"
                                            color="error"
                                            onClick={() => executeDirectTool('/api/cs-agent/tools/escalate', 'POST', { customer_id: parseInt(toolCustomerId), reason: 'تجربة تحويل', summary: 'طلب فحص سريع' })}
                                            disabled={toolLoading}
                                        >
                                            تحويل للدعم
                                        </Button>
                                    </Box>
                                </Stack>

                                {/* Tool Result Output */}
                                {toolLoading && (
                                    <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5, mt: 2 }}>
                                        <CircularProgress size={20} />
                                        <Typography variant="caption">جارٍ استدعاء الأداة...</Typography>
                                    </Box>
                                )}

                                {toolResult && (
                                    <Paper
                                        variant="outlined"
                                        sx={{
                                            mt: 2,
                                            p: 1.5,
                                            maxHeight: 180,
                                            overflowY: 'auto',
                                            bgcolor: toolResult.success ? 'success.50' : 'error.50',
                                            borderRadius: '8px'
                                        }}
                                    >
                                        <Typography variant="caption" sx={{ fontWeight: 700, display: 'block', mb: 0.5 }}>
                                            الاستجابة (Status: {toolResult.status})
                                        </Typography>
                                        <Typography
                                            component="pre"
                                            sx={{
                                                fontSize: '0.75rem',
                                                fontFamily: 'monospace',
                                                m: 0,
                                                whiteSpace: 'pre-wrap',
                                                wordBreak: 'break-word'
                                            }}
                                        >
                                            {JSON.stringify(toolResult.data, null, 2)}
                                        </Typography>
                                    </Paper>
                                )}
                            </CardContent>
                        </Card>

                        {/* Real-time WebSocket Event Log */}
                        <Card elevation={1} sx={{ borderRadius: '16px' }}>
                            <CardContent sx={{ p: 3 }}>
                                <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', mb: 1 }}>
                                    <Typography variant="subtitle2" fontWeight={700}>
                                        سجل أحداث الـ WebSocket
                                    </Typography>
                                    <FormControlLabel
                                        control={
                                            <Switch
                                                size="small"
                                                checked={autoScroll}
                                                onChange={(e) => setAutoScroll(e.target.checked)}
                                            />
                                        }
                                        label={<Typography variant="caption">تمرير تلقائي</Typography>}
                                    />
                                </Box>
                                <Paper
                                    variant="outlined"
                                    sx={{
                                        p: 1.5,
                                        height: 160,
                                        overflowY: 'auto',
                                        bgcolor: 'grey.900',
                                        color: 'grey.100',
                                        borderRadius: '8px',
                                        fontFamily: 'monospace',
                                        fontSize: '0.75rem'
                                    }}
                                >
                                    {eventLogs.length === 0 ? (
                                        <Typography variant="caption" sx={{ color: 'grey.500' }}>
                                            لا توجد أحداث بعد.
                                        </Typography>
                                    ) : (
                                        eventLogs.map((ev, i) => (
                                            <Box key={i} sx={{ mb: 0.5 }}>
                                                <span style={{ color: '#90caf9' }}>[{ev.time}]</span>{' '}
                                                <span style={{ color: '#a5d6a7', fontWeight: 'bold' }}>{ev.name}:</span>{' '}
                                                <span style={{ color: '#e0e0e0' }}>
                                                    {typeof ev.data === 'object' ? JSON.stringify(ev.data) : ev.data}
                                                </span>
                                            </Box>
                                        ))
                                    )}
                                </Paper>
                            </CardContent>
                        </Card>
                    </Stack>
                </Grid>
            </Grid>
        </Box>
    );
}

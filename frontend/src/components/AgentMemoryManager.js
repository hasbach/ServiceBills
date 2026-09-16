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

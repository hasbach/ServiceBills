import React, { useState } from 'react';
import {
    Box, Typography, Paper, Button, TextField, Stepper, Step, StepLabel,
    RadioGroup, FormControlLabel, Radio, Alert, CircularProgress,
} from '@mui/material';
import { useAppContext } from '../context/AppContext.js';

const STEPS = ['Your business', 'License'];

const SetupWizardView = () => {
    const { apiService, login, refreshSystemInfo } = useAppContext();
    const [step, setStep] = useState(0);
    const [form, setForm] = useState({ business_name: '', username: '', password: '', confirm: '', owner_phone: '' });
    const [mode, setMode] = useState('trial');
    const [licenseKey, setLicenseKey] = useState('');
    const [licenseFile, setLicenseFile] = useState('');
    const [fileName, setFileName] = useState('');
    const [error, setError] = useState('');
    const [loading, setLoading] = useState(false);

    const set = (k) => (e) => setForm(f => ({ ...f, [k]: e.target.value }));

    const step1Error = () => {
        if (!form.business_name.trim()) return 'Enter your business name.';
        if (!form.username.trim()) return 'Choose an admin username.';
        if (form.password.length < 6) return 'Password must be at least 6 characters.';
        if (form.password !== form.confirm) return 'Passwords do not match.';
        if (!form.owner_phone.trim()) return 'Enter the owner phone number.';
        return '';
    };

    const next = () => {
        const msg = step1Error();
        setError(msg);
        if (!msg) setStep(1);
    };

    const onFile = (e) => {
        const f = e.target.files[0];
        if (!f) return;
        setFileName(f.name);
        const reader = new FileReader();
        reader.onload = () => setLicenseFile(String(reader.result));
        reader.readAsText(f);
    };

    const submit = async () => {
        setError('');
        if (mode === 'activate' && !licenseKey.trim()) { setError('Enter your license key.'); return; }
        if (mode === 'file' && !licenseFile) { setError('Choose your license file.'); return; }
        const body = {
            business_name: form.business_name.trim(),
            username: form.username.trim(),
            password: form.password,
            owner_phone: form.owner_phone.trim(),
            mode,
        };
        if (mode === 'activate') body.license_key = licenseKey.trim();
        if (mode === 'file') body.license_file = licenseFile;
        setLoading(true);
        try {
            await apiService.runSetup(body);
            await login({ username: body.username, password: body.password });
            await refreshSystemInfo();
        } catch (e) {
            const msg = e.response?.data?.msg;
            if (msg === 'trial_already_used') {
                setError('A trial was already used on this computer. Enter a license key or file.');
            } else {
                setError(msg || 'Setup failed. Please try again.');
            }
            setLoading(false);
        }
    };

    return (
        <Box sx={{ display: 'flex', justifyContent: 'center', alignItems: 'center', minHeight: '100vh', p: 2 }}>
            <Paper sx={{ p: 4, width: '100%', maxWidth: 520, borderRadius: '16px' }}>
                <Typography variant="h4" sx={{ mb: 1, textAlign: 'center', fontWeight: 'bold' }}>Welcome to ServiceBills</Typography>
                <Typography variant="body2" color="text.secondary" sx={{ mb: 3, textAlign: 'center' }}>
                    Let's set up this computer. It only takes a minute.
                </Typography>
                <Stepper activeStep={step} sx={{ mb: 3 }}>
                    {STEPS.map(s => <Step key={s}><StepLabel>{s}</StepLabel></Step>)}
                </Stepper>

                {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}

                {step === 0 && (
                    <Box>
                        <TextField fullWidth margin="normal" label="Business name" value={form.business_name} onChange={set('business_name')} />
                        <TextField fullWidth margin="normal" label="Admin username" value={form.username} onChange={set('username')} autoComplete="username" />
                        <TextField fullWidth margin="normal" label="Password" type="password" value={form.password} onChange={set('password')} autoComplete="new-password" helperText="At least 6 characters" />
                        <TextField fullWidth margin="normal" label="Confirm password" type="password" value={form.confirm} onChange={set('confirm')} autoComplete="new-password" />
                        <TextField fullWidth margin="normal" label="Owner phone" value={form.owner_phone} onChange={set('owner_phone')} />
                        <Button fullWidth variant="contained" sx={{ mt: 2, py: 1.5 }} onClick={next}>Next</Button>
                    </Box>
                )}

                {step === 1 && (
                    <Box>
                        <RadioGroup value={mode} onChange={e => { setMode(e.target.value); setError(''); }}>
                            <FormControlLabel value="trial" control={<Radio />} label="Start 30-day free trial (needs internet)" />
                            <FormControlLabel value="activate" control={<Radio />} label="I have a license key" />
                            {mode === 'activate' && (
                                <TextField fullWidth size="small" sx={{ mb: 1, ml: 4, width: 'calc(100% - 32px)' }} placeholder="SB-XXXX-XXXX-XXXX"
                                    value={licenseKey} onChange={e => setLicenseKey(e.target.value)} />
                            )}
                            <FormControlLabel value="file" control={<Radio />} label="Upload license file" />
                            {mode === 'file' && (
                                <Box sx={{ ml: 4, mb: 1 }}>
                                    <Button variant="outlined" size="small" component="label">
                                        Choose file
                                        <input type="file" hidden accept=".key,.txt" onChange={onFile} />
                                    </Button>
                                    {fileName && <Typography variant="body2" component="span" sx={{ ml: 1 }}>{fileName}</Typography>}
                                </Box>
                            )}
                        </RadioGroup>
                        <Box sx={{ display: 'flex', gap: 1.5, mt: 2 }}>
                            <Button variant="outlined" onClick={() => { setStep(0); setError(''); }} disabled={loading}>Back</Button>
                            <Button fullWidth variant="contained" sx={{ py: 1.5 }} onClick={submit} disabled={loading}>
                                {loading ? <CircularProgress size={24} /> : 'Finish setup'}
                            </Button>
                        </Box>
                    </Box>
                )}
            </Paper>
        </Box>
    );
};

export default SetupWizardView;

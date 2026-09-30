// Bulk customer import from the Excel template -- see
// docs/superpowers/specs/2026-09-30-customer-import-wizard-design.md.
import React, { useMemo, useState } from 'react';
import {
    Alert, Box, Button, Chip, CircularProgress, Dialog, DialogActions, DialogContent,
    DialogTitle, MenuItem, Stack, Step, StepLabel, Stepper, Table, TableBody, TableCell,
    TableContainer, TableHead, TableRow, TextField, Typography,
} from '@mui/material';
import DownloadIcon from '@mui/icons-material/Download';
import UploadFileIcon from '@mui/icons-material/UploadFile';
import { useAppContext } from '../context/AppContext';

const STEPS = ['Download template', 'Upload file', 'Review', 'Done'];
const STATUS_COLOR = { ok: 'success', warning: 'warning', error: 'error' };
const XLSX_TYPE = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet';

function saveBlob(blob, filename) {
    const url = window.URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    window.URL.revokeObjectURL(url);
}

async function errorText(err, fallback) {
    const data = err?.response?.data;
    if (data instanceof Blob) {
        try { return JSON.parse(await data.text()).error || fallback; } catch (e) { return fallback; }
    }
    return data?.error || data?.msg || fallback;
}

export default function CustomerImportWizard({ open, onClose, onImported }) {
    const { apiService } = useAppContext();
    const [step, setStep] = useState(0);
    const [file, setFile] = useState(null);
    const [preview, setPreview] = useState(null);
    const [planDefs, setPlanDefs] = useState({});
    const [filter, setFilter] = useState('all');
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState('');
    const [result, setResult] = useState(null);

    const reset = () => {
        setStep(0); setFile(null); setPreview(null); setPlanDefs({});
        setFilter('all'); setBusy(false); setError(''); setResult(null);
    };
    const handleClose = () => { reset(); onClose(); };

    const newPlans = useMemo(() => (preview?.summary.unknown_plans || []).map((name) => ({
        name,
        price: planDefs[name]?.price ?? '',
        cost: planDefs[name]?.cost ?? '',
        billing_cycle: planDefs[name]?.billing_cycle || 'monthly',
        currency: planDefs[name]?.currency || 'USD',
    })), [preview, planDefs]);
    const plansComplete = newPlans.every((p) => p.price !== '' && Number(p.price) >= 0);
    const setPlanField = (name, field, value) =>
        setPlanDefs((prev) => ({ ...prev, [name]: { ...prev[name], [field]: value } }));

    const handleDownloadTemplate = async () => {
        setError('');
        try {
            const res = await apiService.downloadImportTemplate();
            saveBlob(res.data, 'customer-import-template.xlsx');
            setStep(1);
        } catch (err) {
            setError(await errorText(err, 'Could not download the template.'));
        }
    };

    const handleValidate = async () => {
        if (!file) return;
        setBusy(true); setError('');
        try {
            const res = await apiService.validateCustomerImport(file);
            setPreview(res.data);
            setStep(2);
        } catch (err) {
            setError(await errorText(err, 'Could not read the file.'));
        } finally {
            setBusy(false);
        }
    };

    const handleCommit = async () => {
        setBusy(true); setError('');
        try {
            const payload = newPlans.map((p) => ({ ...p, price: Number(p.price), cost: Number(p.cost || 0) }));
            const res = await apiService.commitCustomerImport(file, payload);
            setResult(res.data);
            setStep(3);
            onImported();
        } catch (err) {
            setError(await errorText(err, 'Import failed. Nothing was saved.'));
        } finally {
            setBusy(false);
        }
    };

    const handleErrorReport = () => {
        const bytes = Uint8Array.from(atob(result.skipped_report), (c) => c.charCodeAt(0));
        saveBlob(new Blob([bytes], { type: XLSX_TYPE }), 'customer-import-skipped-rows.xlsx');
    };

    const rows = (preview?.rows || []).filter((r) => filter === 'all' || r.status === filter);
    const summary = preview?.summary;

    return (
        <Dialog open={open} onClose={busy ? undefined : handleClose} maxWidth="lg" fullWidth>
            <DialogTitle>Import customers</DialogTitle>
            <DialogContent dividers>
                <Stepper activeStep={step} sx={{ mb: 3 }}>
                    {STEPS.map((label) => <Step key={label}><StepLabel>{label}</StepLabel></Step>)}
                </Stepper>
                {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}

                {step === 0 && (
                    <Stack spacing={2} alignItems="flex-start">
                        <Typography>
                            Download the Excel template, fill in one row per customer, then upload it.
                            Nothing is billed and no WhatsApp message is sent: each customer keeps the
                            expiry date you enter, and billing continues from that date.
                        </Typography>
                        <Button variant="contained" startIcon={<DownloadIcon />} onClick={handleDownloadTemplate}>
                            Download template
                        </Button>
                        <Button onClick={() => setStep(1)}>I already have a filled template</Button>
                    </Stack>
                )}

                {step === 1 && (
                    <Stack spacing={2} alignItems="flex-start">
                        <Button variant="outlined" component="label" startIcon={<UploadFileIcon />}>
                            {file ? file.name : 'Choose .xlsx file'}
                            <input hidden type="file" accept=".xlsx"
                                onChange={(e) => { setFile(e.target.files?.[0] || null); e.target.value = ''; }} />
                        </Button>
                        <Typography variant="body2" color="text.secondary">Up to 5,000 customers, 5 MB.</Typography>
                    </Stack>
                )}

                {step === 2 && summary && (
                    <Stack spacing={2}>
                        <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap>
                            {[['all', `All ${summary.total}`], ['ok', `OK ${summary.ok}`],
                              ['warning', `Warnings ${summary.warning}`], ['error', `Errors ${summary.error}`]]
                                .map(([key, label]) => (
                                    <Chip key={key} label={label} color={STATUS_COLOR[key] || 'default'}
                                        variant={filter === key ? 'filled' : 'outlined'} onClick={() => setFilter(key)} />
                                ))}
                        </Stack>
                        <Alert severity={summary.importable ? 'info' : 'warning'}>
                            {summary.importable} of {summary.total} customers will be imported.
                            {summary.customer_limit != null &&
                                ` Your plan allows ${summary.customer_limit} customers (you have ${summary.existing_customers}).`}
                            {summary.new_sectors.length > 0 && ` New sectors: ${summary.new_sectors.join(', ')}.`}
                        </Alert>

                        {newPlans.length > 0 && (
                            <Box>
                                <Typography variant="subtitle1" sx={{ fontWeight: 600, mb: 1 }}>
                                    New plans. Set a price for each before importing.
                                </Typography>
                                {newPlans.map((p) => (
                                    <Stack key={p.name} direction={{ xs: 'column', sm: 'row' }} spacing={2} sx={{ mb: 1 }}>
                                        <TextField label="Plan" value={p.name} size="small" disabled />
                                        <TextField label="Price" type="number" size="small" value={p.price}
                                            onChange={(e) => setPlanField(p.name, 'price', e.target.value)} />
                                        <TextField label="Cost" type="number" size="small" value={p.cost}
                                            onChange={(e) => setPlanField(p.name, 'cost', e.target.value)} />
                                        <TextField select label="Cycle" size="small" value={p.billing_cycle}
                                            onChange={(e) => setPlanField(p.name, 'billing_cycle', e.target.value)}>
                                            <MenuItem value="monthly">Monthly</MenuItem>
                                            <MenuItem value="yearly">Yearly</MenuItem>
                                        </TextField>
                                        <TextField select label="Currency" size="small" value={p.currency}
                                            onChange={(e) => setPlanField(p.name, 'currency', e.target.value)}>
                                            <MenuItem value="USD">USD</MenuItem>
                                            <MenuItem value="LBP">LBP</MenuItem>
                                        </TextField>
                                    </Stack>
                                ))}
                            </Box>
                        )}

                        <TableContainer sx={{ maxHeight: 420 }}>
                            <Table size="small" stickyHeader>
                                <TableHead>
                                    <TableRow>
                                        <TableCell>Row</TableCell><TableCell>Status</TableCell>
                                        <TableCell>Name</TableCell><TableCell>Phone</TableCell>
                                        <TableCell>Plan</TableCell><TableCell>Expiry</TableCell>
                                        <TableCell>Messages</TableCell>
                                    </TableRow>
                                </TableHead>
                                <TableBody>
                                    {rows.map((r) => (
                                        <TableRow key={r.row}>
                                            <TableCell>{r.row}</TableCell>
                                            <TableCell>
                                                <Chip size="small" label={r.import ? r.status : `${r.status} (skip)`}
                                                    color={STATUS_COLOR[r.status]} />
                                            </TableCell>
                                            <TableCell>{r.data.name}</TableCell>
                                            <TableCell>{r.data.phone}</TableCell>
                                            <TableCell>{r.data.plan}</TableCell>
                                            <TableCell>{r.data.expiry_date}</TableCell>
                                            <TableCell>{r.messages.join(' · ')}</TableCell>
                                        </TableRow>
                                    ))}
                                </TableBody>
                            </Table>
                        </TableContainer>
                    </Stack>
                )}

                {step === 3 && result && (
                    <Stack spacing={2} alignItems="flex-start">
                        <Alert severity="success">
                            Imported {result.imported} customers
                            {result.plans_created > 0 && `, created ${result.plans_created} plans`}
                            {result.sectors_created > 0 && `, created ${result.sectors_created} sectors`}.
                        </Alert>
                        {result.skipped > 0 && result.skipped_report && (
                            <>
                                <Typography>{result.skipped} rows were skipped. Download them, fix them, and upload again.</Typography>
                                <Button variant="outlined" startIcon={<DownloadIcon />} onClick={handleErrorReport}>
                                    Download skipped rows
                                </Button>
                            </>
                        )}
                    </Stack>
                )}
            </DialogContent>
            <DialogActions>
                {step === 1 && <Button onClick={() => setStep(0)}>Back</Button>}
                {step === 2 && <Button onClick={() => { setStep(1); setPreview(null); }}>Back</Button>}
                <Button onClick={handleClose} disabled={busy}>{step === 3 ? 'Close' : 'Cancel'}</Button>
                {step === 1 && (
                    <Button variant="contained" onClick={handleValidate} disabled={!file || busy}>
                        {busy ? <CircularProgress size={20} /> : 'Check file'}
                    </Button>
                )}
                {step === 2 && (
                    <Button variant="contained" onClick={handleCommit}
                        disabled={busy || !summary?.importable || !plansComplete}>
                        {busy ? <CircularProgress size={20} /> : `Import ${summary?.importable || 0} customers`}
                    </Button>
                )}
            </DialogActions>
        </Dialog>
    );
}

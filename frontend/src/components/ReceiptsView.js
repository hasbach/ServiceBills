import React, { useState, useEffect, useCallback } from 'react';
import {
    Box, Typography, Paper, Button, Grid, TextField, MenuItem,
    Table, TableBody, TableCell, TableContainer, TableHead, TableRow,
    Checkbox, Dialog, DialogTitle, DialogContent, DialogActions,
    alpha, useTheme, InputAdornment, IconButton, Tooltip, Chip, useMediaQuery
} from '@mui/material';
import {
    Print as PrintIcon,
    Search as SearchIcon,
    Receipt as ReceiptIcon,
    Delete as DeleteIcon,
    DeleteSweep as DeleteSweepIcon,
    FilterList as FilterListIcon,
    Clear as ClearIcon
} from '@mui/icons-material';
import { useAppContext } from '../context/AppContext.js';
import { escapeHtml } from '../utils/escapeHtml.js';

const ReceiptsView = () => {
    const { apiService, setSnackbar } = useAppContext();
    const theme = useTheme();
    const isMobile = useMediaQuery(theme.breakpoints.down('sm'));
    const [receipts, setReceipts] = useState([]);
    const [loading, setLoading] = useState(true);
    const [searchQuery, setSearchQuery] = useState('');
            const [selected, setSelected] = useState([]);
    const [showGenerateDialog, setShowGenerateDialog] = useState(false);
    const [generationDate, setGenerationDate] = useState({
        month: new Date().getMonth() + 1,
        year: new Date().getFullYear()
    });


    // New date filter state
    const [dateFilters, setDateFilters] = useState({
        startDate: '',
        endDate: '',
        showPrinted: false,
        showUnprinted: true,
        sortBy: 'billing_date',
        sortDesc: true
    });


    const fetchReceipts = useCallback(async () => {
        setLoading(true);
        try {
            const response = await apiService.fetchReceiptLogs(searchQuery);
            setReceipts(response.data || []);
        } catch (error) {
            console.error("Error fetching receipts:", error);
            setSnackbar({ open: true, message: 'Failed to load receipts.', severity: 'error' });
        } finally {
            setLoading(false);
        }
    }, [apiService, setSnackbar, searchQuery]);

    useEffect(() => {
        fetchReceipts();
    }, [fetchReceipts]);



    // Filter receipts based on date and print status
    const filteredReceipts = receipts.filter(receipt => {
        const billingDate = new Date(receipt.billing_date);
        const startDate = dateFilters.startDate ? new Date(dateFilters.startDate) : null;
        const endDate = dateFilters.endDate ? new Date(dateFilters.endDate) : null;

        // Date range filter
        if (startDate && billingDate < startDate) return false;
        if (endDate && billingDate > endDate) return false;

        // Print status filter
        const isPrinted = receipt.print_count > 0;
        if (!dateFilters.showPrinted && isPrinted) return false;
        if (!dateFilters.showUnprinted && !isPrinted) return false;

        return true;
    }).sort((a, b) => {
        let valA = a[dateFilters.sortBy];
        let valB = b[dateFilters.sortBy];
        
        if (!valA) valA = '';
        if (!valB) valB = '';

        if (valA < valB) return dateFilters.sortDesc ? 1 : -1;
        if (valA > valB) return dateFilters.sortDesc ? -1 : 1;
        return 0;
    });

    const handleDateFilterChange = (field, value) => {
        setDateFilters(prev => ({ ...prev, [field]: value }));
        setSelected([]); // Clear selection when filters change
    };

    const clearDateFilters = () => {
        setDateFilters({
            startDate: '',
            endDate: '',
            showPrinted: true,
            showUnprinted: true
        });
        setSelected([]);
    };




    const handleSelectAllClick = (event) => {
        if (event.target.checked) {
            // --- FIX: Map over filteredReceipts, not all receipts ---
            const newSelecteds = filteredReceipts.map((n) => n.id);
            setSelected(newSelecteds);
            return;
        }
        setSelected([]);
    };

    const handleClick = (event, id) => {
        const selectedIndex = selected.indexOf(id);
        let newSelected = [];
        if (selectedIndex === -1) {
            newSelected = newSelected.concat(selected, id);
        } else if (selectedIndex === 0) {
            newSelected = newSelected.concat(selected.slice(1));
        } else if (selectedIndex === selected.length - 1) {
            newSelected = newSelected.concat(selected.slice(0, -1));
        } else if (selectedIndex > 0) {
            newSelected = newSelected.concat(
                selected.slice(0, selectedIndex),
                selected.slice(selectedIndex + 1),
            );
        }
        setSelected(newSelected);
    };

    const handleDeleteReceipt = async (receiptId) => {
        if (window.confirm('Are you sure you want to delete this receipt? This action cannot be undone.')) {
            try {
                await apiService.deleteReceipt(receiptId);
                setSnackbar({ open: true, message: 'Receipt deleted successfully!', severity: 'success' });
                fetchReceipts(); // Refresh the list
            } catch (error) {
                console.error("Error deleting receipt:", error);
                setSnackbar({
                    open: true,
                    message: 'Failed to delete receipt. ' + (error.response?.data?.error || error.message),
                    severity: 'error'
                });
            }
        }
    };

    const handleDeleteSelected = async () => {
        if (selected.length === 0) {
            setSnackbar({ open: true, message: 'No receipts selected to delete.', severity: 'warning' });
            return;
        }

        if (window.confirm(`Are you sure you want to delete ${selected.length} selected receipt(s)? This action cannot be undone.`)) {
            try {
                // Delete all selected receipts
                await Promise.all(selected.map(id => apiService.deleteReceipt(id)));
                setSnackbar({
                    open: true,
                    message: `${selected.length} receipt(s) deleted successfully!`,
                    severity: 'success'
                });
                setSelected([]); // Clear selection
                fetchReceipts(); // Refresh the list
            } catch (error) {
                console.error("Error deleting receipts:", error);
                setSnackbar({
                    open: true,
                    message: 'Failed to delete some receipts. ' + (error.response?.data?.error || error.message),
                    severity: 'error'
                });
            }
        }
    };


    const handleGenerateReceipts = async () => {
        try {
            const response = await apiService.generateReceipts(generationDate);
            setSnackbar({ open: true, message: response.data.message, severity: 'success' });
            setShowGenerateDialog(false);
            fetchReceipts();
        } catch (error) {
            setSnackbar({ open: true, message: 'Failed to generate receipts.', severity: 'error' });
        }
    };

    const handlePrintSelected = () => {
        const receiptsToPrint = receipts.filter(r => selected.includes(r.id));
        if (receiptsToPrint.length === 0) {
            setSnackbar({ open: true, message: 'No receipts selected to print.', severity: 'warning' });
            return;
        }

        const printWindow = window.open('', '', 'height=600,width=900');
        printWindow.document.write('<html><head><title>Print All Receipts</title>');
        printWindow.document.write(`
            <style>
                @page { 
                    size: 21cm 30cm; /* Custom size for 4 receipts of 7.5cm each */
                    margin: 0;
                }
                
                body { 
                    margin: 0; 
                    padding: 0;
                    font-family: Arial, sans-serif; 
                }
                
                .receipt-container {
                    width: 21cm; 
                    height: 7.5cm; 
                    display: flex !important;
                    direction: rtl; 
                    background-color: white;
                    padding-top: 40px;
                    box-sizing: border-box; 
                    page-break-inside: avoid;
                    page-break-after: auto;
                }
                
                /* Force page break after every 4 receipts */
                .receipt-container:nth-child(4n) {
                    page-break-after: always;
                }
                
                .main-part { 
                    width: 14.1cm; 
                    padding: 40px 50px 10px 15px; 
                    box-sizing: border-box; 
                }
                
                .mini-part { 
                    width: 6.9cm; 
                    padding: 40px 50px 10px 15px;
                    box-sizing: border-box; 
                    border-right: 1px dashed #000; 
                }
                
                .receipt-details span { 
                    display: block; 
                    font-size: 16px; 
                    margin-bottom: 2px; 
                    color: #000; 
                }
                
                @media print {
                    body {
                        margin: 0 !important;
                        padding: 0 !important;
                    }
                    .receipt-container {
                        break-inside: avoid;
                    }
                }
            </style>
        `);
        printWindow.document.write('</head><body>');

        receiptsToPrint.forEach(r => {
            const data = r.receipt_data;
            // Add Arabic months array
            const arabicMonths = [
                'كانون الثاني', 'شباط', 'آذار', 'نيسان', 'أيار', 'حزيران',
                'تموز', 'آب', 'أيلول', 'تشرين الأول', 'تشرين الثاني', 'كانون الأول'
            ];
            const date = new Date(data.payment_date);
            const month = date.getMonth();
            const year = date.getFullYear();
            const arabicMonthYear = `${arabicMonths[month]} ${year}`;
            const currentBalance = (parseFloat(data.customer_new_balance) || 0).toFixed(2);
            const previousBalance = ((parseFloat(data.customer_new_balance) || 0) + (parseFloat(data.amount_on_record) || 0)).toFixed(2);
            printWindow.document.write(`
                <div class="receipt-container">
                    <div class="main-part">
                        <div class="receipt-details">
                            <span>الإسم: ${escapeHtml(data.customer_name)}</span>
                            <span>العنوان: ${escapeHtml(data.customer_address)}</span>
                            <span>الهاتف: ${escapeHtml(data.customer_phone)}</span>
                            <span>تاريخ الإيصال: ${escapeHtml(data.payment_date)}</span>
                            <span>الدفعة الشهرية: ${escapeHtml(data.subscription_plan_details.price)}$ - الخدمة: ${escapeHtml(data.subscription_plan_details.name)}</span>
                            <span>عن شهر: ${arabicMonthYear}</span>
                            <span>الرصيد الحالي: ${currentBalance}$ - الرصيد السابق: ${previousBalance}$</span>
                        </div>
                    </div>
                    <div class="mini-part">
                        <div class="receipt-details">
                            <span>الإسم: ${escapeHtml(data.customer_name)}</span>
                            <span>العنوان: ${escapeHtml(data.customer_address)}</span>
                            <span>الهاتف: ${escapeHtml(data.customer_phone)}</span>
                            <span>الدفعة الشهرية: ${escapeHtml(data.subscription_plan_details.price)}$</span>
                            <span>عن شهر: ${arabicMonthYear}</span>
                            <span>الرصيد الحالي: ${currentBalance}$</span>
                            <span>الرصيد السابق: ${previousBalance}$</span>
                        </div>
                    </div>
                </div>
            `);
        });

        printWindow.document.write('</body></html>');
        printWindow.document.close();
        printWindow.focus();
        setTimeout(() => {
            printWindow.print();
            printWindow.close();
            apiService.logReceiptPrint({ receipt_ids: selected });
            fetchReceipts(); // Refresh to show updated print counts
        }, 500);
    };

    const isSelected = (id) => selected.indexOf(id) !== -1;

    return (
        <Box sx={{ p: { xs: 1.5, sm: 2, md: 3 } }}>

            {/* ── Gradient Hero ── */}
            <Paper elevation={0} sx={{ p: { xs: 2, sm: 3, md: 4 }, mb: 3, borderRadius: '24px', background: 'linear-gradient(135deg, #667eea 0%, #764ba2 100%)', color: 'white', position: 'relative', overflow: 'hidden' }}>
                {/* Decorative circle */}
                <Box sx={{ position: 'absolute', top: -50, right: -50, width: 200, height: 200, borderRadius: '50%', background: alpha('#ffffff', 0.1), filter: 'blur(1px)' }} />
                <Box sx={{ position: 'relative', zIndex: 1 }}>

                    {/* Title row */}
                    <Box sx={{ display: 'flex', flexDirection: { xs: 'column', sm: 'row' }, justifyContent: 'space-between', alignItems: 'flex-start', gap: { xs: 2, sm: 0 }, mb: 3 }}>
                        <Box>
                            <Typography variant="h4" sx={{ fontWeight: 700, mb: 0.5, fontSize: { xs: '1.3rem', sm: '1.75rem', md: '2.125rem' } }}>
                                Generated Receipts
                            </Typography>
                            <Typography variant="body1" sx={{ opacity: 0.9, fontSize: { xs: '0.85rem', sm: '1rem' } }}>
                                Print, manage and generate monthly receipts
                            </Typography>
                        </Box>

                        {/* Action buttons */}
                        <Box sx={{ display: 'flex', flexDirection: { xs: 'column', sm: 'row' }, gap: 1, width: { xs: '100%', sm: 'auto' } }}>
                            <Button
                                variant="contained"
                                startIcon={<PrintIcon />}
                                onClick={handlePrintSelected}
                                disabled={selected.length === 0}
                                sx={{ backgroundColor: 'rgba(255,255,255,0.2)', backdropFilter: 'blur(10px)', border: '1px solid rgba(255,255,255,0.3)', color: 'white', borderRadius: '14px', textTransform: 'none', fontWeight: 600, px: { xs: 2, sm: 2.5 }, py: { xs: 1, sm: 1.2 }, width: { xs: '100%', sm: 'auto' }, '&:hover': { backgroundColor: 'rgba(255,255,255,0.3)' }, '&.Mui-disabled': { color: 'rgba(255,255,255,0.45)', borderColor: 'rgba(255,255,255,0.2)' }, transition: 'all 0.25s ease' }}
                            >
                                Print Selected ({selected.length})
                            </Button>
                            <Button
                                variant="contained"
                                startIcon={<DeleteSweepIcon />}
                                onClick={handleDeleteSelected}
                                disabled={selected.length === 0}
                                sx={{ backgroundColor: 'rgba(239,68,68,0.3)', backdropFilter: 'blur(10px)', border: '1px solid rgba(255,255,255,0.25)', color: 'white', borderRadius: '14px', textTransform: 'none', fontWeight: 600, px: { xs: 2, sm: 2.5 }, py: { xs: 1, sm: 1.2 }, width: { xs: '100%', sm: 'auto' }, '&:hover': { backgroundColor: 'rgba(239,68,68,0.5)' }, '&.Mui-disabled': { color: 'rgba(255,255,255,0.45)', borderColor: 'rgba(255,255,255,0.2)' }, transition: 'all 0.25s ease' }}
                            >
                                Delete Selected ({selected.length})
                            </Button>
                            <Button
                                variant="contained"
                                startIcon={<ReceiptIcon />}
                                onClick={() => setShowGenerateDialog(true)}
                                sx={{ backgroundColor: 'rgba(255,255,255,0.2)', backdropFilter: 'blur(10px)', border: '1px solid rgba(255,255,255,0.3)', color: 'white', borderRadius: '14px', textTransform: 'none', fontWeight: 600, px: { xs: 2, sm: 2.5 }, py: { xs: 1, sm: 1.2 }, width: { xs: '100%', sm: 'auto' }, '&:hover': { backgroundColor: 'rgba(255,255,255,0.3)' }, transition: 'all 0.25s ease' }}
                            >
                                {isMobile ? 'Generate Monthly' : 'Generate Monthly Receipts'}
                            </Button>
                        </Box>
                    </Box>

                    {/* Stats bar */}
                    <Box sx={{ display: 'flex', gap: { xs: 2, sm: 3 }, flexWrap: 'wrap', alignItems: 'center' }}>
                        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                            <ReceiptIcon sx={{ fontSize: 20 }} />
                            <Box>
                                <Typography variant="caption" sx={{ opacity: 0.8, display: 'block' }}>Total Receipts</Typography>
                                <Typography variant="h6" sx={{ fontWeight: 700, lineHeight: 1 }}>{receipts.length}</Typography>
                            </Box>
                        </Box>
                        <Box sx={{ width: '1px', height: 32, bgcolor: 'rgba(255,255,255,0.3)', display: { xs: 'none', sm: 'block' } }} />
                        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                            <PrintIcon sx={{ fontSize: 20 }} />
                            <Box>
                                <Typography variant="caption" sx={{ opacity: 0.8, display: 'block' }}>Printed</Typography>
                                <Typography variant="h6" sx={{ fontWeight: 700, lineHeight: 1 }}>{receipts.filter(r => r.print_count > 0).length}</Typography>
                            </Box>
                        </Box>
                        <Box sx={{ width: '1px', height: 32, bgcolor: 'rgba(255,255,255,0.3)', display: { xs: 'none', sm: 'block' } }} />
                        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                            <FilterListIcon sx={{ fontSize: 20 }} />
                            <Box>
                                <Typography variant="caption" sx={{ opacity: 0.8, display: 'block' }}>Showing</Typography>
                                <Typography variant="h6" sx={{ fontWeight: 700, lineHeight: 1 }}>{filteredReceipts.length}</Typography>
                            </Box>
                        </Box>
                        {selected.length > 0 && (
                            <Chip label={`${selected.length} selected`} size="small" sx={{ bgcolor: 'rgba(255,255,255,0.2)', color: 'white', fontWeight: 700, border: '1px solid rgba(255,255,255,0.35)' }} />
                        )}
                    </Box>
                </Box>
            </Paper>

            {/* ── Search & Filters ── */}
            <Paper elevation={0} sx={{ p: { xs: 2, sm: 2.5 }, mb: 3, borderRadius: '16px', border: `1px solid ${alpha(theme.palette.divider, 0.12)}` }}>
                <TextField
                    fullWidth
                    label="Search by Customer Name"
                    value={searchQuery}
                    onChange={(e) => setSearchQuery(e.target.value)}
                    size="small"
                    InputProps={{ startAdornment: <InputAdornment position="start"><SearchIcon /></InputAdornment> }}
                    sx={{ mb: 2, '& .MuiOutlinedInput-root': { borderRadius: '12px' } }}
                />

                <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 2 }}>
                    <FilterListIcon sx={{ color: 'text.secondary', fontSize: 18 }} />
                    <Typography variant="subtitle2" sx={{ fontWeight: 700 }}>Filters</Typography>
                    <Button size="small" startIcon={<ClearIcon />} onClick={clearDateFilters} sx={{ ml: 'auto', textTransform: 'none', borderRadius: '8px' }}>Clear</Button>
                </Box>

                <Grid container spacing={2} alignItems="center">
                    <Grid item xs={12} sm={6} md={3}>
                        <TextField fullWidth type="date" label="Start Date" value={dateFilters.startDate}
                            onChange={(e) => handleDateFilterChange('startDate', e.target.value)}
                            InputLabelProps={{ shrink: true }} size="small"
                            sx={{ '& .MuiOutlinedInput-root': { borderRadius: '10px' } }} />
                    </Grid>
                    <Grid item xs={12} sm={6} md={3}>
                        <TextField fullWidth type="date" label="End Date" value={dateFilters.endDate}
                            onChange={(e) => handleDateFilterChange('endDate', e.target.value)}
                            InputLabelProps={{ shrink: true }} size="small"
                            sx={{ '& .MuiOutlinedInput-root': { borderRadius: '10px' } }} />
                    </Grid>
                    <Grid item xs={12} md={6}>
                        <Box sx={{ display: 'flex', gap: 1, flexWrap: 'wrap' }}>
                            <Chip label="Show Printed" variant={dateFilters.showPrinted ? 'filled' : 'outlined'}
                                color={dateFilters.showPrinted ? 'primary' : 'default'}
                                onClick={() => handleDateFilterChange('showPrinted', !dateFilters.showPrinted)} clickable />
                            <Chip label="Show Unprinted" variant={dateFilters.showUnprinted ? 'filled' : 'outlined'}
                                color={dateFilters.showUnprinted ? 'secondary' : 'default'}
                                onClick={() => handleDateFilterChange('showUnprinted', !dateFilters.showUnprinted)} clickable />
                        </Box>
                    </Grid>
                </Grid>

                {(dateFilters.startDate || dateFilters.endDate || !dateFilters.showPrinted || !dateFilters.showUnprinted) && (
                    <Box sx={{ mt: 2, p: 1.5, bgcolor: alpha(theme.palette.info.main, 0.08), borderRadius: '10px', border: `1px solid ${alpha(theme.palette.info.main, 0.15)}` }}>
                        <Typography variant="body2" color="info.main">
                            Showing {filteredReceipts.length} of {receipts.length} receipts
                            {dateFilters.startDate || dateFilters.endDate ? ` · ${dateFilters.startDate || '…'} → ${dateFilters.endDate || '…'}` : ''}
                            {(!dateFilters.showPrinted || !dateFilters.showUnprinted) ? ` · ${dateFilters.showPrinted ? 'printed only' : 'unprinted only'}` : ''}
                        </Typography>
                    </Box>
                )}
            </Paper>

            <TableContainer component={Paper}>
                <Table>
                    <TableHead>
                        <TableRow>
                            <TableCell padding="checkbox">
                                <Checkbox
                                    // --- FIX: Base logic on filteredReceipts ---
                                    indeterminate={selected.length > 0 && selected.length < filteredReceipts.length}
                                    checked={filteredReceipts.length > 0 && selected.length === filteredReceipts.length}
                                    onChange={handleSelectAllClick}
                                />
                            </TableCell>
                            <TableCell>Customer Name</TableCell>
                            <TableCell>Billing Date</TableCell>
                            <TableCell>Print Count</TableCell>
                            <TableCell>Last Printed</TableCell>
                            <TableCell align="center">Actions</TableCell>
                        </TableRow>
                    </TableHead>
                    <TableBody>
                        {filteredReceipts.length > 0 ? (
                            filteredReceipts.map((row) => {
                                const isItemSelected = isSelected(row.id);
                                const isPrinted = row.print_count > 0;
                                return (
                                    <TableRow
                                        hover
                                        onClick={(event) => handleClick(event, row.id)}
                                        role="checkbox"
                                        aria-checked={isItemSelected}
                                        tabIndex={-1}
                                        key={row.id}
                                        selected={isItemSelected}
                                    >
                                        <TableCell padding="checkbox">
                                            <Checkbox checked={isItemSelected} />
                                        </TableCell>
                                        <TableCell>{row.customer_name}</TableCell>
                                        <TableCell>{row.billing_date}</TableCell>
                                        <TableCell>{row.print_count}</TableCell>
                                        <TableCell>{row.last_printed_date}</TableCell>
                                        <TableCell align="center">
                                            <Tooltip title="Delete Receipt">
                                                <IconButton
                                                    color="error"
                                                    onClick={() => handleDeleteReceipt(row.id)}
                                                    size="small"
                                                >
                                                    <DeleteIcon />
                                                </IconButton>
                                            </Tooltip>
                                        </TableCell>
                                    </TableRow>
                                );
                            })
                        ) : (
                            <TableRow>
                                <TableCell colSpan={6} sx={{ textAlign: 'center', py: 4 }}>
                                    <Typography variant="body2" color="text.secondary">
                                        No receipts match the current filters
                                    </Typography>
                                </TableCell>
                            </TableRow>
                        )}
                    </TableBody>
                </Table>
            </TableContainer>

            <Dialog open={showGenerateDialog} onClose={() => setShowGenerateDialog(false)}>
                <DialogTitle>Generate Receipts for a Month</DialogTitle>
                <DialogContent>
                    <Grid container spacing={2} sx={{ mt: 1 }}>
                        <Grid item xs={6}>
                            <TextField
                                fullWidth
                                type="number"
                                label="Year"
                                value={generationDate.year}
                                onChange={(e) => setGenerationDate({ ...generationDate, year: e.target.value })}
                            />
                        </Grid>
                        <Grid item xs={6}>
                            <TextField
                                fullWidth
                                type="number"
                                label="Month"
                                value={generationDate.month}
                                onChange={(e) => setGenerationDate({ ...generationDate, month: e.target.value })}
                            />
                        </Grid>
                    </Grid>
                </DialogContent>
                <DialogActions>
                    <Button onClick={() => setShowGenerateDialog(false)}>Cancel</Button>
                    <Button onClick={handleGenerateReceipts} variant="contained">Generate</Button>
                </DialogActions>
            </Dialog>
        </Box>
    );
};

export default ReceiptsView;

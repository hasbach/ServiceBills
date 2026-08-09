import React, { useState, useEffect, useCallback, useMemo } from 'react';
import {
    Box,
    Typography,
    Paper,
    Button,
    CircularProgress,
    Dialog,
    Chip,
    IconButton,
    Fade,
    Divider,
    alpha,
    useTheme,
    TextField, FormControlLabel, Switch, Select, MenuItem, InputLabel, FormControl,
    Collapse,
    Table,
    TableBody,
    TableCell,
    TableContainer,
    TableHead,
    TableRow,
    Tooltip,
    LinearProgress
} from '@mui/material';
import {
    Add as AddIcon,
    Edit as EditIcon,
    Delete as DeleteIcon,
    Receipt as ReceiptIcon,
    TrendingUp as TrendingUpIcon,
    Category as CategoryIcon,
    CalendarToday as CalendarIcon,
    Refresh as RefreshIcon,
    ExpandMore as ExpandMoreIcon,
    ExpandLess as ExpandLessIcon,
    FolderOpen as FolderOpenIcon,
    Lock as LockIcon
} from '@mui/icons-material';
import { useAppContext } from '../context/AppContext.js';
import ExpenseForm from './ExpenseForm.js';

// Stable color palette for categories (deterministic by string hash)
const CATEGORY_PALETTE = [
    '#4F46E5', '#10B981', '#F59E0B', '#EF4444',
    '#8B5CF6', '#06B6D4', '#EC4899', '#14B8A6',
    '#F97316', '#6366F1'
];

const getCategoryColor = (category) => {
    if (!category) return '#6B7280';
    let hash = 0;
    for (let i = 0; i < category.length; i++) {
        hash = category.charCodeAt(i) + ((hash << 5) - hash);
    }
    return CATEGORY_PALETTE[Math.abs(hash) % CATEGORY_PALETTE.length];
};

// ─── Category Row (expandable) ──────────────────────────────────────────────
const CategorySection = ({ category, expenses, totalForCategory, grandTotal, onEdit, onDelete, defaultOpen }) => {
    const theme = useTheme();
    const [open, setOpen] = useState(defaultOpen ?? true);
    const color = getCategoryColor(category);
    const pct = grandTotal > 0 ? (totalForCategory / grandTotal) * 100 : 0;

    return (
        <Paper
            elevation={0}
            sx={{
                mb: 2,
                borderRadius: '16px',
                border: `1px solid ${alpha(color, 0.18)}`,
                overflow: 'hidden',
                transition: 'box-shadow 0.2s',
                '&:hover': { boxShadow: `0 4px 20px ${alpha(color, 0.12)}` }
            }}
        >
            {/* Category Header */}
            <Box
                onClick={() => setOpen(o => !o)}
                sx={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: 2,
                    px: 3,
                    py: 2,
                    cursor: 'pointer',
                    background: `linear-gradient(135deg, ${alpha(color, 0.08)} 0%, ${alpha(color, 0.03)} 100%)`,
                    borderBottom: open ? `1px solid ${alpha(color, 0.12)}` : 'none',
                    userSelect: 'none',
                }}
            >
                {/* Color dot */}
                <Box sx={{ width: 12, height: 12, borderRadius: '50%', bgcolor: color, flexShrink: 0 }} />

                {/* Category name */}
                <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, flex: 1, flexWrap: 'wrap' }}>
                    <Typography variant="subtitle1" sx={{ fontWeight: 700, color: color }}>
                        {category}
                    </Typography>
                    <Chip
                        label={`${expenses.length} expense${expenses.length !== 1 ? 's' : ''}`}
                        size="small"
                        sx={{ bgcolor: alpha(color, 0.12), color, fontWeight: 600, fontSize: '0.72rem', height: 22 }}
                    />
                </Box>

                {/* Total + progress */}
                <Box sx={{ display: 'flex', alignItems: 'center', gap: 2 }}>
                    <Box sx={{ minWidth: 100, display: { xs: 'none', sm: 'block' } }}>
                        <LinearProgress
                            variant="determinate"
                            value={pct}
                            sx={{
                                height: 6,
                                borderRadius: 3,
                                bgcolor: alpha(color, 0.15),
                                '& .MuiLinearProgress-bar': { bgcolor: color, borderRadius: 3 }
                            }}
                        />
                        <Typography variant="caption" sx={{ color: 'text.secondary', fontSize: '0.68rem' }}>
                            {pct.toFixed(1)}% of total
                        </Typography>
                    </Box>
                    <Typography variant="h6" sx={{ fontWeight: 800, color, whiteSpace: 'nowrap' }}>
                        ${totalForCategory.toFixed(2)}
                    </Typography>
                    <IconButton size="small" sx={{ color }}>
                        {open ? <ExpandLessIcon /> : <ExpandMoreIcon />}
                    </IconButton>
                </Box>
            </Box>

            {/* Collapsible expense table */}
            <Collapse in={open} timeout="auto" unmountOnExit>
                <TableContainer>
                    <Table size="small">
                        <TableHead>
                            <TableRow sx={{ bgcolor: alpha(color, 0.04) }}>
                                <TableCell sx={{ fontWeight: 700, color: 'text.secondary', fontSize: '0.75rem' }}>Date</TableCell>
                                <TableCell sx={{ fontWeight: 700, color: 'text.secondary', fontSize: '0.75rem' }}>Description</TableCell>
                                <TableCell sx={{ fontWeight: 700, color: 'text.secondary', fontSize: '0.75rem' }} align="right">Amount</TableCell>
                                <TableCell sx={{ fontWeight: 700, color: 'text.secondary', fontSize: '0.75rem', width: 96 }} align="center">Actions</TableCell>
                            </TableRow>
                        </TableHead>
                        <TableBody>
                            {expenses.map((expense, idx) => (
                                <TableRow
                                    key={expense.id}
                                    sx={{
                                        bgcolor: idx % 2 === 0 ? 'transparent' : alpha(color, 0.02),
                                        '&:hover': { bgcolor: alpha(color, 0.06) },
                                        transition: 'background 0.15s'
                                    }}
                                >
                                    <TableCell sx={{ color: 'text.secondary', fontSize: '0.82rem', whiteSpace: 'nowrap' }}>
                                        {new Date(expense.date).toLocaleDateString('en-US', { year: 'numeric', month: 'short', day: 'numeric' })}
                                    </TableCell>
                                    <TableCell sx={{ fontSize: '0.87rem', color: 'text.primary', maxWidth: 320 }}>
                                        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                                            <Typography
                                                variant="body2"
                                                sx={{
                                                    overflow: 'hidden',
                                                    textOverflow: 'ellipsis',
                                                    whiteSpace: 'nowrap',
                                                    maxWidth: 300
                                                }}
                                                title={expense.description}
                                            >
                                                {expense.description || <em style={{ color: '#9CA3AF' }}>No description</em>}
                                            </Typography>
                                            {expense.is_credit && (
                                                <Tooltip title="Bought on credit -- not yet paid, so it isn't counted in the totals below until you record the payment from the Supplier page">
                                                    <Chip label="On Credit" size="small" sx={{ height: 20, fontSize: '0.68rem', fontWeight: 600 }} />
                                                </Tooltip>
                                            )}
                                        </Box>
                                    </TableCell>
                                    <TableCell align="right" sx={{ fontWeight: 700, color: expense.is_credit ? 'text.disabled' : color, whiteSpace: 'nowrap' }}>
                                        ${parseFloat(expense.amount).toFixed(2)}
                                    </TableCell>
                                    <TableCell align="center" sx={{ whiteSpace: 'nowrap' }}>
                                        {expense.source && expense.source !== 'manual' ? (
                                            <Tooltip title="Tracked automatically from Payroll/Suppliers — not editable here">
                                                <LockIcon fontSize="small" sx={{ color: 'text.disabled' }} />
                                            </Tooltip>
                                        ) : (
                                            <>
                                                <Tooltip title="Edit">
                                                    <IconButton
                                                        size="small"
                                                        onClick={() => onEdit(expense)}
                                                        sx={{
                                                            color: theme.palette.primary.main,
                                                            '&:hover': { bgcolor: alpha(theme.palette.primary.main, 0.1) }
                                                        }}
                                                    >
                                                        <EditIcon fontSize="small" />
                                                    </IconButton>
                                                </Tooltip>
                                                <Tooltip title="Delete">
                                                    <IconButton
                                                        size="small"
                                                        onClick={() => onDelete(expense.id)}
                                                        sx={{
                                                            color: theme.palette.error.main,
                                                            '&:hover': { bgcolor: alpha(theme.palette.error.main, 0.1) }
                                                        }}
                                                    >
                                                        <DeleteIcon fontSize="small" />
                                                    </IconButton>
                                                </Tooltip>
                                            </>
                                        )}
                                    </TableCell>
                                </TableRow>
                            ))}
                            {/* Subtotal row */}
                            <TableRow sx={{ bgcolor: alpha(color, 0.06) }}>
                                <TableCell colSpan={2} sx={{ fontWeight: 700, fontSize: '0.82rem', color }}>
                                    Subtotal — {category}
                                </TableCell>
                                <TableCell align="right" sx={{ fontWeight: 800, color }}>
                                    ${totalForCategory.toFixed(2)}
                                </TableCell>
                                <TableCell />
                            </TableRow>
                        </TableBody>
                    </Table>
                </TableContainer>
            </Collapse>
        </Paper>
    );
};

// ─── Main View ───────────────────────────────────────────────────────────────
const ExpensesView = () => {
    const { apiService, setSnackbar } = useAppContext();
    const theme = useTheme();
    const [expenses, setExpenses] = useState([]);
    const [loading, setLoading] = useState(false);
    const [isFormOpen, setIsFormOpen] = useState(false);
    const [selectedExpense, setSelectedExpense] = useState(null);

    // Date filtering — default to current month
    const getCurrentMonthDates = () => {
        const now = new Date();
        return {
            start: new Date(now.getFullYear(), now.getMonth(), 1).toISOString().split('T')[0],
            end: new Date(now.getFullYear(), now.getMonth() + 1, 0).toISOString().split('T')[0]
        };
    };
    const [startDate, setStartDate] = useState(getCurrentMonthDates().start);
    const [endDate, setEndDate] = useState(getCurrentMonthDates().end);

    const fetchExpenses = useCallback(async () => {
        setLoading(true);
        try {
            const response = await apiService.fetchExpenses(startDate, endDate);
            setExpenses(response.data);
        } catch (error) {
            console.error('Error fetching expenses:', error);
            setSnackbar({ open: true, message: 'Failed to fetch expenses.', severity: 'error' });
        } finally {
            setLoading(false);
        }
    }, [apiService, setSnackbar, startDate, endDate]);

    useEffect(() => { fetchExpenses(); }, [fetchExpenses]);

    const handleAddExpense    = () => { setSelectedExpense(null); setIsFormOpen(true); };
    const handleEditExpense   = (expense) => { setSelectedExpense(expense); setIsFormOpen(true); };
    const handleFormClose     = useCallback(() => { setIsFormOpen(false); setSelectedExpense(null); }, []);
    const handleFormSave      = useCallback(() => { fetchExpenses(); handleFormClose(); }, [fetchExpenses, handleFormClose]);

    const handleDeleteExpense = async (expenseId) => {
        if (!window.confirm('Delete this expense? This action cannot be undone.')) return;
        setLoading(true);
        try {
            await apiService.deleteExpense(expenseId);
            setSnackbar({ open: true, message: 'Expense deleted successfully!', severity: 'success' });
            fetchExpenses();
        } catch (error) {
            setSnackbar({ open: true, message: 'Failed to delete expense. ' + (error.response?.data?.error || error.message), severity: 'error' });
        } finally {
            setLoading(false);
        }
    };

    // Group expenses by category. Credit purchases (is_credit) aren't cash out
    // the door yet -- the supplier hasn't been paid -- so they're excluded from
    // every dollar total here, same as the backend's financial reports already
    // exclude them. They still appear in the row list (marked "On Credit"),
    // just not counted until the actual SupplierPayment happens.
    const grouped = useMemo(() => {
        const map = {};
        (expenses || []).forEach(exp => {
            const cat = exp.category || 'Uncategorized';
            if (!map[cat]) map[cat] = [];
            map[cat].push(exp);
        });
        // Sort categories by total descending
        return Object.entries(map)
            .map(([cat, items]) => ({
                category: cat,
                expenses: items.sort((a, b) => new Date(b.date) - new Date(a.date)),
                total: items.reduce((s, e) => s + (e.is_credit ? 0 : parseFloat(e.amount)), 0)
            }))
            .sort((a, b) => b.total - a.total);
    }, [expenses]);

    const totalExpenses = useMemo(
        () => expenses.reduce((s, e) => s + (e.is_credit ? 0 : parseFloat(e.amount)), 0),
        [expenses]
    );

    // Shared date-field style (white-on-gradient header)
    const dateFieldSx = {
        minWidth: 180,
        '& .MuiOutlinedInput-root': {
            color: 'white',
            backgroundColor: 'rgba(255,255,255,0.15)',
            borderRadius: '12px',
            '& fieldset': { borderColor: 'rgba(255,255,255,0.3)' },
            '&:hover fieldset': { borderColor: 'rgba(255,255,255,0.5)' },
            '&.Mui-focused fieldset': { borderColor: 'rgba(255,255,255,0.8)' },
        },
        '& .MuiInputBase-input': { color: 'white' }
    };

    return (
        <Box sx={{ p: { xs: 1.5, sm: 2, md: 3 }, background: 'linear-gradient(135deg, #f6f9fc 0%, #ffffff 100%)', minHeight: '100vh' }}>

            {/* ── Header ── */}
            <Paper
                elevation={0}
                sx={{ p: { xs: 2, sm: 3, md: 4 }, mb: 4, borderRadius: '24px', background: 'linear-gradient(135deg, #667eea 0%, #764ba2 100%)', color: 'white', position: 'relative', overflow: 'hidden' }}
            >
                <Box sx={{ position: 'absolute', top: -50, right: -50, width: 200, height: 200, borderRadius: '50%', background: alpha('#ffffff', 0.1), filter: 'blur(1px)' }} />
                <Box sx={{ position: 'relative', zIndex: 1 }}>
                    <Box sx={{ display: 'flex', flexDirection: { xs: 'column', sm: 'row' }, justifyContent: 'space-between', alignItems: 'flex-start', gap: { xs: 2, sm: 0 }, mb: 3 }}>
                        <Box>
                            <Typography variant="h4" sx={{ fontWeight: 700, mb: 1, fontSize: { xs: '1.3rem', sm: '1.75rem', md: '2.125rem' } }}>Expenses Management</Typography>
                            <Typography variant="body1" sx={{ opacity: 0.9, fontSize: { xs: '0.85rem', sm: '1rem' } }}>Track and manage your business expenses by category</Typography>
                        </Box>
                        <Button
                            variant="contained"
                            startIcon={<AddIcon />}
                            onClick={handleAddExpense}
                            sx={{ backgroundColor: 'rgba(255,255,255,0.2)', backdropFilter: 'blur(10px)', border: '1px solid rgba(255,255,255,0.3)', color: 'white', borderRadius: '16px', textTransform: 'none', fontWeight: 600, px: { xs: 2, sm: 3 }, py: { xs: 1, sm: 1.5 }, width: { xs: '100%', sm: 'auto' }, '&:hover': { backgroundColor: 'rgba(255,255,255,0.3)', transform: 'translateY(-2px)', boxShadow: '0 8px 20px rgba(0,0,0,0.2)' }, transition: 'all 0.3s ease' }}
                        >
                            Add New Expense
                        </Button>
                    </Box>

                    {/* Stats */}
                    <Box sx={{ display: 'flex', gap: { xs: 2, sm: 3 }, alignItems: 'center', flexWrap: 'wrap' }}>
                        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                            <TrendingUpIcon sx={{ fontSize: 20 }} />
                            <Box>
                                <Typography variant="caption" sx={{ opacity: 0.8, display: 'block' }}>Total Expenses</Typography>
                                <Typography variant="h6" sx={{ fontWeight: 700 }}>${totalExpenses.toFixed(2)}</Typography>
                            </Box>
                        </Box>
                        <Divider orientation="vertical" flexItem sx={{ bgcolor: 'rgba(255,255,255,0.3)' }} />
                        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                            <ReceiptIcon sx={{ fontSize: 20 }} />
                            <Box>
                                <Typography variant="caption" sx={{ opacity: 0.8, display: 'block' }}>Total Records</Typography>
                                <Typography variant="h6" sx={{ fontWeight: 700 }}>{expenses.length}</Typography>
                            </Box>
                        </Box>
                        <Divider orientation="vertical" flexItem sx={{ bgcolor: 'rgba(255,255,255,0.3)' }} />
                        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                            <CategoryIcon sx={{ fontSize: 20 }} />
                            <Box>
                                <Typography variant="caption" sx={{ opacity: 0.8, display: 'block' }}>Categories</Typography>
                                <Typography variant="h6" sx={{ fontWeight: 700 }}>{grouped.length}</Typography>
                            </Box>
                        </Box>
                    </Box>

                    {/* Date Filters */}
                    <Box sx={{ mt: 3, pt: 3, borderTop: '1px solid rgba(255,255,255,0.2)' }}>
                        <Box sx={{ display: 'flex', flexDirection: { xs: 'column', sm: 'row' }, gap: 2, alignItems: { xs: 'stretch', sm: 'center' }, flexWrap: 'wrap' }}>
                            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                                <CalendarIcon sx={{ fontSize: 18, opacity: 0.9 }} />
                                <Typography variant="body2" sx={{ fontWeight: 600, opacity: 0.9 }}>Filter by Date:</Typography>
                            </Box>
                            <TextField type="date" label="Start Date" value={startDate} onChange={e => setStartDate(e.target.value)} InputLabelProps={{ shrink: true, sx: { color: 'rgba(255,255,255,0.9)' } }} sx={{ ...dateFieldSx, minWidth: { xs: '100%', sm: 180 } }} />
                            <TextField type="date" label="End Date"   value={endDate}   onChange={e => setEndDate(e.target.value)}   InputLabelProps={{ shrink: true, sx: { color: 'rgba(255,255,255,0.9)' } }} sx={{ ...dateFieldSx, minWidth: { xs: '100%', sm: 180 } }} />
                            <Button variant="outlined" startIcon={<RefreshIcon />} onClick={() => { const d = getCurrentMonthDates(); setStartDate(d.start); setEndDate(d.end); }}
                                sx={{ borderColor: 'rgba(255,255,255,0.4)', color: 'white', borderRadius: '12px', textTransform: 'none', fontWeight: 600, px: 2, width: { xs: '100%', sm: 'auto' }, '&:hover': { borderColor: 'rgba(255,255,255,0.7)', backgroundColor: 'rgba(255,255,255,0.1)' } }}>
                                Current Month
                            </Button>
                        </Box>
                    </Box>
                </Box>
            </Paper>

            {/* ── Content ── */}
            {loading ? (
                <Box sx={{ display: 'flex', justifyContent: 'center', py: 8 }}>
                    <CircularProgress size={40} thickness={4} />
                </Box>
            ) : grouped.length > 0 ? (
                <Box>
                    {/* Category summary chips */}
                    <Box sx={{ display: 'flex', gap: 1, flexWrap: 'wrap', mb: 3 }}>
                        {grouped.map(({ category, total, expenses: items }) => (
                            <Chip
                                key={category}
                                icon={<CategoryIcon style={{ color: getCategoryColor(category) }} />}
                                label={`${category} · $${total.toFixed(2)} (${items.length})`}
                                size="small"
                                sx={{
                                    bgcolor: alpha(getCategoryColor(category), 0.1),
                                    color: getCategoryColor(category),
                                    fontWeight: 600,
                                    border: `1px solid ${alpha(getCategoryColor(category), 0.25)}`,
                                    fontSize: '0.78rem'
                                }}
                            />
                        ))}
                    </Box>

                    {/* One expandable section per category */}
                    {grouped.map(({ category, expenses: items, total }, idx) => (
                        <CategorySection
                            key={category}
                            category={category}
                            expenses={items}
                            totalForCategory={total}
                            grandTotal={totalExpenses}
                            onEdit={handleEditExpense}
                            onDelete={handleDeleteExpense}
                            defaultOpen={idx === 0}   // first category open by default
                        />
                    ))}

                    {/* Grand total footer */}
                    <Paper elevation={0} sx={{ p: 2, borderRadius: '14px', background: 'linear-gradient(135deg, #667eea22 0%, #764ba222 100%)', border: '1px solid #667eea33', mt: 1 }}>
                        <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                            <Typography variant="subtitle1" sx={{ fontWeight: 700, color: 'text.secondary' }}>Grand Total ({expenses.length} expenses across {grouped.length} categories)</Typography>
                            <Typography variant="h5" sx={{ fontWeight: 800, background: 'linear-gradient(135deg, #667eea, #764ba2)', WebkitBackgroundClip: 'text', WebkitTextFillColor: 'transparent', backgroundClip: 'text' }}>
                                ${totalExpenses.toFixed(2)}
                            </Typography>
                        </Box>
                        {expenses.some(e => e.is_credit) && (
                            <Typography variant="caption" sx={{ color: 'text.secondary', display: 'block', mt: 0.5 }}>
                                Rows marked "On Credit" aren't counted above until paid — record the payment from the Supplier page.
                            </Typography>
                        )}
                    </Paper>
                </Box>
            ) : (
                <Fade in timeout={800}>
                    <Box sx={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', py: 8, textAlign: 'center' }}>
                        <Box sx={{ width: 120, height: 120, borderRadius: '50%', background: `linear-gradient(135deg, ${alpha(theme.palette.primary.main, 0.1)}, ${alpha(theme.palette.secondary.main, 0.1)})`, display: 'flex', alignItems: 'center', justifyContent: 'center', mb: 3 }}>
                            <FolderOpenIcon sx={{ fontSize: 48, color: theme.palette.primary.main, opacity: 0.7 }} />
                        </Box>
                        <Typography variant="h6" sx={{ color: 'text.secondary', mb: 1 }}>No expenses recorded yet</Typography>
                        <Typography variant="body2" sx={{ color: 'text.disabled', mb: 3 }}>Start tracking your business expenses to get better insights</Typography>
                        <Button variant="contained" startIcon={<AddIcon />} onClick={handleAddExpense} sx={{ borderRadius: '12px', textTransform: 'none', fontWeight: 600, px: 3, py: 1.5 }}>
                            Add Your First Expense
                        </Button>
                    </Box>
                </Fade>
            )}

            {/* ── Add / Edit Dialog ── */}
            <Dialog open={isFormOpen} onClose={handleFormClose} maxWidth="sm" fullWidth PaperProps={{ sx: { borderRadius: '20px', boxShadow: '0 20px 40px rgba(0,0,0,0.1)', background: 'linear-gradient(135deg, #ffffff 0%, #f8fafc 100%)' } }}>
                <ExpenseForm expense={selectedExpense} onSave={handleFormSave} onCancel={handleFormClose} />
            </Dialog>
        </Box>
    );
};

export default ExpensesView;
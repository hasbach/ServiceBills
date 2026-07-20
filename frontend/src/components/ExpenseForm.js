import React, { useState, useEffect } from 'react';
import { Box, TextField, Button, Typography, CircularProgress, InputAdornment, FormControl, FormControlLabel, Switch, InputLabel, Select, MenuItem } from '@mui/material';
import { useAppContext } from '../context/AppContext.js';

function ExpenseForm({ expense, onSave, onCancel }) {
    const { apiService, setSnackbar } = useAppContext();
    const [categories, setCategories] = useState([]);
    const [suppliers, setSuppliers] = useState([]);
  const [formData, setFormData] = useState({
    category: '',
    amount: '',
    description: '',
    date: new Date().toISOString().split('T')[0],
    is_credit: false,
    supplier_id: ''
  });
  const [loading, setLoading] = useState(false);


    useEffect(() => {
        const fetchCategories = async () => {
            try {
                const response = await apiService.fetchExpenseCategories();
                const supRes = await apiService.fetchSuppliers();
                setSuppliers(supRes.data);
                setCategories(response.data);
            } catch (error) {
                setSnackbar({ open: true, message: 'Could not load expense categories.', severity: 'error' });
            }
        };
        fetchCategories();
    }, [apiService, setSnackbar]);

  useEffect(() => {
    if (expense) {
      setFormData({
        category: expense.category || '',
        amount: expense.amount || '',
        description: expense.description || '',
        date: expense.date || new Date().toISOString().split('T')[0],
        is_credit: expense.is_credit || false,
        supplier_id: expense.supplier_id || ''
      });
    } else {
      setFormData({ // Reset for new expense
        category: '',
        amount: '',
        description: '',
        date: new Date().toISOString().split('T')[0],
        is_credit: false,
        supplier_id: ''
      });
    }
  }, [expense]);

  const handleChange = (e) => {
    const { name, value } = e.target;
    setFormData(prev => ({ ...prev, [name]: value }));
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    setLoading(true);
    try {
      const dataToSend = {
        ...formData,
        amount: parseFloat(formData.amount) || 0.0,
      };

      if (expense) {
        await apiService.updateExpense(expense.id, dataToSend);
        setSnackbar({ open: true, message: 'Expense updated successfully!', severity: 'success' });
      } else {
        await apiService.addExpense(dataToSend);
        setSnackbar({ open: true, message: 'Expense added successfully!', severity: 'success' });
      }
      
      if (typeof onSave === 'function') {
        onSave(); // Callback to refresh list in parent (ExpensesView)
      } else {
        console.warn("onSave prop is not a function in ExpenseForm.");
      }

    } catch (error) {
      console.error('Error saving expense:', error);
      setSnackbar({ open: true, message: 'Failed to save expense: ' + (error.response?.data?.error || error.message), severity: 'error' });
    } finally {
      setLoading(false);
    }
  };

  return (
    <Box component="form" onSubmit={handleSubmit} sx={{ p: 3 }}>
      <Typography variant="h5" sx={{ mb: 3 }} className="font-bold text-gray-800">
        {expense ? 'Edit Expense' : 'Add New Expense'}
      </Typography>
          <FormControl fullWidth margin="normal" required variant="outlined" size="small">
              <InputLabel>Category</InputLabel>
              <Select
                  name="category"
                  value={formData.category}
                  onChange={handleChange}
                  label="Category"
              >
                  <MenuItem value="" disabled><em>Select a category</em></MenuItem>
                  {categories.map((category) => (
                      <MenuItem key={category.id} value={category.name}>{category.name}</MenuItem>
                  ))}
              </Select>
          </FormControl>
      <TextField
        label="Amount"
        name="amount"
        type="number"
        value={formData.amount}
        onChange={handleChange}
        fullWidth
        margin="normal"
        required
        variant="outlined"
        size="small"
        className="rounded-md"
        InputProps={{
          startAdornment: <InputAdornment position="start">$</InputAdornment>,
        }}
      />
      <TextField
        label="Date"
        name="date"
        type="date"
        value={formData.date}
        onChange={handleChange}
        fullWidth
        margin="normal"
        required
        variant="outlined"
        size="small"
        InputLabelProps={{ shrink: true }}
        className="rounded-md"
      />
      <TextField
        label="Description"
        name="description"
        value={formData.description}
        onChange={handleChange}
        fullWidth
        margin="normal"
        multiline
        rows={3}
        variant="outlined"
        size="small"
        className="rounded-md"
      />
      <FormControlLabel
        control={
            <Switch
                checked={formData.is_credit}
                onChange={(e) => setFormData({...formData, is_credit: e.target.checked, supplier_id: e.target.checked ? formData.supplier_id : ''})}
                color="primary"
            />
        }
        label="Purchase on Credit?"
        sx={{ mt: 1, display: 'block' }}
      />

      {formData.is_credit && (
        <FormControl fullWidth margin="normal" required size="small">
            <InputLabel>Supplier</InputLabel>
            <Select
                value={formData.supplier_id}
                label="Supplier"
                name="supplier_id"
                onChange={handleChange}
            >
                <MenuItem value=""><em>Select a Supplier</em></MenuItem>
                {suppliers.map((s) => (
                    <MenuItem key={s.id} value={s.id}>{s.name}</MenuItem>
                ))}
            </Select>
        </FormControl>
      )}
      <Box sx={{ mt: 3, display: 'flex', justifyContent: 'flex-end', gap: 2 }}>
        <Button variant="outlined" onClick={onCancel} disabled={loading} className="rounded-lg">
          Cancel
        </Button>
        <Button type="submit" variant="contained" color="primary" disabled={loading} className="rounded-lg">
          {loading ? <CircularProgress size={24} /> : 'Save Expense'}
        </Button>
      </Box>
    </Box>
  );
}

export default ExpenseForm;

import React, { useState, useEffect } from 'react';
import { Box, TextField, Button, Typography, Select, MenuItem, FormControl, InputLabel, CircularProgress, InputAdornment } from '@mui/material';
import { useAppContext } from '../context/AppContext.js'; // This path is correct for src/components/ to src/context/

function SubscriptionPlanForm({ plan, onSave, onCancel }) {
  const { apiService, setSnackbar } = useAppContext(); // Access apiService and setSnackbar
  const [formData, setFormData] = useState({
    name: '',
    price: '',
    cost: '',
    billing_cycle: 'monthly', // Default value
    status: 'active'
  });
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (plan) {
      setFormData({
        name: plan.name || '',
        price: plan.price || '',
        cost: plan.cost || '',
        billing_cycle: plan.billing_cycle || 'monthly',
        status: plan.status || 'active'
      });
    } else {
      setFormData({ // Reset for new plan
        name: '',
        price: '',
        cost: '',
        billing_cycle: 'monthly',
        status: 'active'
      });
    }
  }, [plan]);

  const handleChange = (e) => {
    const { name, value } = e.target;
    setFormData(prev => ({ ...prev, [name]: value }));
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (loading) return; // guard against a double-click firing before the disabled state re-renders
    setLoading(true); // Start loading
    try {
      const dataToSend = {
        ...formData,
        // Ensure price/cost are always valid numbers, default to 0.0 if empty/NaN
        price: parseFloat(formData.price) || 0.0,
        cost: parseFloat(formData.cost) || 0.0,
      };

      if (plan) {
        await apiService.updateSubscriptionPlan(plan.id, dataToSend);
        setSnackbar({ open: true, message: 'Subscription plan updated successfully!', severity: 'success' });
      } else {
        await apiService.addSubscriptionPlan(dataToSend);
        setSnackbar({ open: true, message: 'Subscription plan added successfully!', severity: 'success' });
      }
      
      // Ensure onSave is a function before calling it
      if (typeof onSave === 'function') {
        onSave(); // Callback to refresh list in parent (SubscriptionPlansView)
      } else {
        console.warn("onSave prop is not a function in SubscriptionPlanForm.");
      }

    } catch (error) {
      console.error('Error saving subscription plan:', error);
      setSnackbar({ open: true, message: 'Failed to save plan: ' + (error.response?.data?.error || error.message), severity: 'error' });
    } finally {
      setLoading(false); // End loading
    }
  };

  return (
    <Box component="form" onSubmit={handleSubmit} sx={{ p: 3 }}>
      <Typography variant="h5" sx={{ mb: 3 }}>{plan ? 'Edit Subscription Plan' : 'Add New Subscription Plan'}</Typography>
      <TextField
        label="Plan Name"
        name="name"
        value={formData.name}
        onChange={handleChange}
        fullWidth
        margin="normal"
        required
      />
      {/* Removed Description TextField */}
      <TextField
        label="Price"
        name="price"
        type="number"
        value={formData.price}
        onChange={handleChange}
        fullWidth
        margin="normal"
        required
        InputProps={{
          startAdornment: <InputAdornment position="start">$</InputAdornment>,
        }}
      />
      <TextField
        label="Cost"
        name="cost"
        type="number"
        value={formData.cost}
        onChange={handleChange}
        fullWidth
        margin="normal"
        helperText="What this plan actually costs you to deliver (used for the estimated profit report)"
        InputProps={{
          startAdornment: <InputAdornment position="start">$</InputAdornment>,
        }}
      />
      <FormControl fullWidth margin="normal" required>
        <InputLabel>Billing Cycle</InputLabel>
        <Select
          name="billing_cycle"
          value={formData.billing_cycle}
          onChange={handleChange}
          label="Billing Cycle"
        >
          <MenuItem value="monthly">Monthly</MenuItem>
          <MenuItem value="yearly">Yearly</MenuItem>
        </Select>
      </FormControl>
      {/* Removed Duration, Speed, Data Limit TextFields */}
      <FormControl fullWidth margin="normal" required>
        <InputLabel>Status</InputLabel>
        <Select
          name="status"
          value={formData.status}
          onChange={handleChange}
          label="Status"
        >
          <MenuItem value="active">Active</MenuItem>
          <MenuItem value="inactive">Inactive</MenuItem>
        </Select>
      </FormControl>
      <Box sx={{ mt: 3, display: 'flex', justifyContent: 'flex-end', gap: 2 }}>
        <Button variant="outlined" onClick={onCancel} disabled={loading}>
          Cancel
        </Button>
        <Button type="submit" variant="contained" color="primary" disabled={loading}>
          {loading ? <CircularProgress size={24} /> : 'Save Plan'}
        </Button>
      </Box>
    </Box>
  );
}

export default SubscriptionPlanForm;

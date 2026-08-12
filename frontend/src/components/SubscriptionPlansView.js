// src/components/SubscriptionPlansView.js

import React, { useState, useEffect } from 'react';
import { Dialog, Button, Box, Typography } from '@mui/material';
import { useAppContext } from '../context/AppContext';
import SubscriptionPlanForm from './SubscriptionPlanForm';

const SubscriptionPlansView = ({ refetchSubscriptionPlans }) => {
  const { apiService, setSnackbar } = useAppContext();
  const [plans, setPlans] = useState([]);
  const [selectedPlan, setSelectedPlan] = useState(null);
  const [openForm, setOpenForm] = useState(false);

  const fetchPlans = async () => {
    try {
      const res = await apiService.fetchSubscriptionPlans();
      console.log('Fetched plans:', res);
      setPlans(res);
    } catch (err) {
      console.error('Error fetching plans:', err);
      setSnackbar({ open: true, message: 'Failed to fetch plans', severity: 'error' });
    }
  };

  useEffect(() => {
    fetchPlans();
  }, []);

  const handleAddPlan = () => {
    setSelectedPlan(null);
    setOpenForm(true);
  };

  const handleEditPlan = (plan) => {
    setSelectedPlan(plan);
    setOpenForm(true);
  };

  const handleCloseForm = () => {
    setSelectedPlan(null);
    setOpenForm(false);
  };

  const handleSave = async () => {
    await fetchPlans(); // Refresh this view's own list after save
    // Also refresh the shared list held by App.js (used by SubscriptionsView's
    // plan dropdowns) -- this view keeps its own separate copy, so without this
    // a newly added/edited plan wouldn't show up there until a full page reload.
    await refetchSubscriptionPlans?.();
    setOpenForm(false);
    setSnackbar({ open: true, message: 'Plan saved successfully', severity: 'success' });
  };

  return (
    <Box p={3}>
      <Typography variant="h4" gutterBottom>Subscription Plans</Typography>
      <Button variant="contained" onClick={handleAddPlan}>Add New Plan</Button>

      <table style={{ width: '100%', marginTop: '20px', borderCollapse: 'collapse' }}>
        <thead>
          <tr style={{ backgroundColor: '#f2f2f2' }}>
            <th style={{ padding: '10px', borderBottom: '1px solid #ddd' }}>Name</th>
            <th style={{ padding: '10px', borderBottom: '1px solid #ddd' }}>Price</th>
            <th style={{ padding: '10px', borderBottom: '1px solid #ddd' }}>Status</th>
            <th style={{ padding: '10px', borderBottom: '1px solid #ddd' }}>Actions</th>
          </tr>
        </thead>
        <tbody>
          {plans.map(plan => (
            <tr key={plan.id}>
              <td style={{ padding: '10px', borderBottom: '1px solid #ddd' }}>{plan.name}</td>
              <td style={{ padding: '10px', borderBottom: '1px solid #ddd' }}>${plan.price}</td>
              <td style={{ padding: '10px', borderBottom: '1px solid #ddd' }}>{plan.status}</td>
              <td style={{ padding: '10px', borderBottom: '1px solid #ddd' }}>
                <Button variant="outlined" onClick={() => handleEditPlan(plan)}>Edit</Button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      <Dialog open={openForm} onClose={handleCloseForm} maxWidth="md" fullWidth>
        <SubscriptionPlanForm
          plan={selectedPlan}
          onSave={handleSave}
          onCancel={handleCloseForm}
        />
      </Dialog>
    </Box>
  );
};

export default SubscriptionPlansView;

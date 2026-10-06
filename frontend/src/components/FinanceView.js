import React, { useState, useMemo } from 'react';
import { Box, Tabs, Tab } from '@mui/material';
import { useAppContext } from '../context/AppContext.js';
import CashFlowRegister from './CashFlowRegister.js';
import EmployeesView from './EmployeesView.js';
import SuppliersView from './SuppliersView.js';
import ResellerManagementView from './ResellerManagementView.js';
import ExpensesView from './ExpensesView.js';

export const FINANCE_TABS = [
  { key: 'cash-flow', label: 'Cash Flow', roles: ['admin', 'finance'], render: () => <CashFlowRegister /> },
  { key: 'employees', label: 'Payroll', roles: ['admin'], render: () => <EmployeesView /> },
  { key: 'suppliers', label: 'Suppliers', roles: ['admin', 'finance'], render: () => <SuppliersView /> },
  { key: 'resellers', label: 'Resellers', roles: ['admin', 'finance'], render: () => <ResellerManagementView /> },
  { key: 'expenses', label: 'Expenses', roles: ['admin'], render: () => <ExpensesView /> },
];

function FinanceView({ initialTab }) {
  const { user } = useAppContext();
  const roleStr = user?.role || '';
  const tabs = useMemo(() => {
    const userRoles = roleStr.split(',').map((r) => r.trim());
    return FINANCE_TABS.filter((t) => t.roles.some((r) => userRoles.includes(r)));
  }, [roleStr]);
  const [selected, setSelected] = useState(initialTab);
  const active = tabs.find((t) => t.key === selected) || tabs[0];

  if (!active) return null;

  return (
    <Box>
      <Tabs value={active.key} onChange={(_, v) => setSelected(v)} variant="scrollable"
        scrollButtons="auto" sx={{ mb: 2, borderBottom: 1, borderColor: 'divider' }}>
        {tabs.map((t) => <Tab key={t.key} value={t.key} label={t.label} />)}
      </Tabs>
      {active.render()}
    </Box>
  );
}

export default FinanceView;

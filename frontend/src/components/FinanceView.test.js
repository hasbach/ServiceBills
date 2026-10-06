import React from 'react';
import { render, screen } from '@testing-library/react';
import FinanceView from './FinanceView';

let mockRole = 'admin';
jest.mock('../context/AppContext.js', () => ({
    useAppContext: () => ({ user: { role: mockRole } }),
    apiService: { api: {} },
}));
jest.mock('./CashFlowRegister.js', () => () => <div>CashFlowRegister-mock</div>);
jest.mock('./EmployeesView.js', () => () => <div>EmployeesView-mock</div>);
jest.mock('./SuppliersView.js', () => () => <div>SuppliersView-mock</div>);
jest.mock('./ResellerManagementView.js', () => () => <div>ResellerManagementView-mock</div>);
jest.mock('./ExpensesView.js', () => () => <div>ExpensesView-mock</div>);

const tabNames = () => screen.getAllByRole('tab').map((t) => t.textContent);

describe('FinanceView', () => {
    it('shows all five tabs to admin', () => {
        mockRole = 'admin';
        render(<FinanceView />);
        expect(tabNames()).toEqual(['Cash Flow', 'Payroll', 'Suppliers', 'Resellers', 'Expenses']);
        expect(screen.getByText('CashFlowRegister-mock')).toBeInTheDocument();
    });

    it('shows only Cash Flow, Suppliers, Resellers to finance', () => {
        mockRole = 'finance';
        render(<FinanceView />);
        expect(tabNames()).toEqual(['Cash Flow', 'Suppliers', 'Resellers']);
    });

    it('opens the initialTab when allowed', () => {
        mockRole = 'admin';
        render(<FinanceView initialTab="expenses" />);
        expect(screen.getByText('ExpensesView-mock')).toBeInTheDocument();
    });

    it('falls back to the first allowed tab when initialTab is not allowed', () => {
        mockRole = 'finance';
        render(<FinanceView initialTab="employees" />);
        expect(screen.getByText('CashFlowRegister-mock')).toBeInTheDocument();
    });
});

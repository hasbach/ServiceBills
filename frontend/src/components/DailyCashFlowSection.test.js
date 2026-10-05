import React from 'react';
import { render, screen, fireEvent } from '@testing-library/react';
import DailyCashFlowSection from './DailyCashFlowSection';

// Mock apiService to avoid network calls
jest.mock('../context/AppContext.js', () => ({
    apiService: {
        api: {
            put: jest.fn().mockResolvedValue({ data: {} })
        }
    }
}));

describe('DailyCashFlowSection', () => {
    const mockFlow = {
        day: '2026-10-05',
        cash_in: {
            total: 100,
            items: [{ category: 'Customer payments', count: 2, total: 100, entries: [{ time: '10:00', description: 'Bill #1', amount: 100 }] }]
        },
        cash_out: {
            total: 30,
            items: [{ category: 'Expenses', count: 1, total: 30, entries: [{ time: '11:00', description: 'Office supplies', amount: 30 }] }]
        },
        net: 70,
        whish_in: {
            total: 50,
            items: [{ category: 'Customer payments', count: 1, total: 50, entries: [{ time: '12:00', description: 'Whish collection', amount: 50 }] }]
        },
        whish_out: {
            total: 10,
            items: [{ category: 'Upstream top-ups', count: 1, total: 10, entries: [{ time: '13:00', description: 'Topup', amount: 10 }] }]
        },
        whish_net: 40,
        total_in: 150,
        total_out: 40,
        total_net: 110,
        cash_start: 500,
        cash_end: 570,
        whish_start: 200,
        whish_end: 240,
        total_start: 700,
        total_end: 810,
        opening: {
            date: '2026-10-01',
            amount: 500,
            whish_amount: 200,
        }
    };

    test('renders Combined Total tab with total cards and subtexts by default', () => {
        render(<DailyCashFlowSection flow={mockFlow} currency="USD" onOpeningSaved={jest.fn()} />);

        // Tabs
        expect(screen.getByRole('tab', { name: 'Combined Total' })).toBeInTheDocument();
        expect(screen.getByRole('tab', { name: 'Cash' })).toBeInTheDocument();
        expect(screen.getByRole('tab', { name: 'Whish' })).toBeInTheDocument();

        // Cards in Combined mode
        expect(screen.getByText('Total at start of day')).toBeInTheDocument();
        expect(screen.getByText('700.00 USD')).toBeInTheDocument();
        expect(screen.getByText('Total in')).toBeInTheDocument();
        expect(screen.getByText('+ 150.00 USD')).toBeInTheDocument();
        expect(screen.getByText('Total out')).toBeInTheDocument();
        expect(screen.getByText('− 40.00 USD')).toBeInTheDocument();
        expect(screen.getByText('Total at end of day')).toBeInTheDocument();
        expect(screen.getByText('810.00 USD')).toBeInTheDocument();

        // Opening balance in header displays both Cash and Whish
        expect(screen.getByText(/Opening: Cash 500\.00 USD · Whish 200\.00 USD on 2026-10-01/)).toBeInTheDocument();

        // All 4 breakdown sections visible in Combined view
        expect(screen.getByText('Cash in')).toBeInTheDocument();
        expect(screen.getByText('Whish in')).toBeInTheDocument();
        expect(screen.getByText('Cash out')).toBeInTheDocument();
        expect(screen.getByText('Whish out')).toBeInTheDocument();
    });

    test('switches to Cash tab and displays only cash cards and sections', () => {
        render(<DailyCashFlowSection flow={mockFlow} currency="USD" onOpeningSaved={jest.fn()} />);

        fireEvent.click(screen.getByRole('tab', { name: 'Cash' }));

        expect(screen.getByText('Cash at start of day')).toBeInTheDocument();
        expect(screen.getByText('500.00 USD')).toBeInTheDocument();
        expect(screen.getByText('Cash at end of day')).toBeInTheDocument();
        expect(screen.getByText('570.00 USD')).toBeInTheDocument();

        // Whish in/out sections should not be displayed
        expect(screen.queryByText('Whish in')).not.toBeInTheDocument();
        expect(screen.queryByText('Whish out')).not.toBeInTheDocument();
    });

    test('switches to Whish tab and displays only Whish cards and sections', () => {
        render(<DailyCashFlowSection flow={mockFlow} currency="USD" onOpeningSaved={jest.fn()} />);

        fireEvent.click(screen.getByRole('tab', { name: 'Whish' }));

        expect(screen.getByText('Whish at start of day')).toBeInTheDocument();
        expect(screen.getByText('200.00 USD')).toBeInTheDocument();
        expect(screen.getByText('Whish at end of day')).toBeInTheDocument();
        expect(screen.getByText('240.00 USD')).toBeInTheDocument();

        // Cash in/out sections should not be displayed
        expect(screen.queryByText('Cash in')).not.toBeInTheDocument();
        expect(screen.queryByText('Cash out')).not.toBeInTheDocument();
    });

    test('opens dialog with Cash and Whish opening balance inputs', () => {
        render(<DailyCashFlowSection flow={mockFlow} currency="USD" onOpeningSaved={jest.fn()} />);

        fireEvent.click(screen.getByRole('button', { name: 'Edit' }));

        expect(screen.getByRole('dialog')).toBeInTheDocument();
        expect(screen.getByText('Opening balances')).toBeInTheDocument();
        expect(screen.getByLabelText(/Cash on hand at start of that day/i)).toBeInTheDocument();
        expect(screen.getByLabelText(/Whish balance at start of that day/i)).toBeInTheDocument();
    });
});

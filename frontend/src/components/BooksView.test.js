import React from 'react';
import { render, screen, within, waitFor } from '@testing-library/react';
import BooksView from './BooksView';

const mockApi = {
    fetchBooksAccounts: jest.fn(),
    fetchBooksJournal: jest.fn(),
    fetchBooksCheck: jest.fn(),
};
jest.mock('../context/AppContext.js', () => ({
    useAppContext: () => ({ apiService: mockApi }),
}));

const ACCOUNTS = {
    as_of: '2026-11-17',
    accounts: [
        { key: 'cash', number: 1000, name: 'Cash', type: 'asset', debit: 150, credit: 50, balance: 100 },
        { key: 'customer_balances', number: 2000, name: 'Customer balances', type: 'liability', debit: 0, credit: 30, balance: 30 },
        { key: 'capital', number: 3000, name: 'Opening equity', type: 'equity', debit: 0, credit: 0, balance: 0 },
        { key: 'revenue', number: 4000, name: 'Subscription revenue', type: 'income', debit: 0, credit: 120, balance: 120 },
        { key: 'expenses', number: 5000, name: 'Operating expenses', type: 'expense', debit: 50, credit: 0, balance: 50 },
    ],
    totals: { debit: 200, credit: 200 },
};
const JOURNAL = {
    total: 1,
    items: [{
        id: 7, date: '2026-11-16', kind: 'event', memo: 'Payment from Rami', created_by: 'alice',
        lines: [
            { account: 'cash', party_type: null, party_id: null, party_name: null, debit: 25, credit: 0, source_type: 'payment', source_id: 3 },
            { account: 'customer_balances', party_type: 'customer', party_id: 9, party_name: 'Rami Khoury', debit: 0, credit: 25, source_type: 'payment', source_id: 3 },
        ],
    }],
};
const STARTED = { started_at: '2026-11-10T00:00:00', start_day: '2026-11-10' };
const CHECK_OK = {
    ...STARTED,
    last_check: {
        checked_at: '2026-11-17T02:00:00', unbalanced_entries: [], party_mismatches: [],
        cash: { books: 100, cash_flow: 100 }, whish: { books: 0, cash_flow: null },
        unexplained_count: 0, problem_count: 0,
        profit_by_month: [{ month: '2026-11', from: '2026-11-10', partial: true, books_cash_basis: 70, reports: 70 }],
    },
};

beforeEach(() => {
    mockApi.fetchBooksAccounts.mockResolvedValue({ data: ACCOUNTS });
    mockApi.fetchBooksJournal.mockResolvedValue({ data: JOURNAL });
    mockApi.fetchBooksCheck.mockResolvedValue({ data: CHECK_OK });
});

describe('BooksView', () => {
    it('renders accounts grouped with balanced totals', async () => {
        render(<BooksView />);
        expect(await screen.findByText('Subscription revenue')).toBeInTheDocument();
        ['Assets', 'Liabilities', 'Equity', 'Income', 'Expenses'].forEach((g) =>
            expect(screen.getByText(g)).toBeInTheDocument());
        expect(screen.getByText('Books started on 2026-11-10')).toBeInTheDocument();
        const totals = screen.getByText('Total').closest('tr');
        expect(within(totals).getAllByText('$200.00')).toHaveLength(2);
    });

    it('journal shows entry lines', async () => {
        render(<BooksView />);
        expect(await screen.findByText('Payment from Rami')).toBeInTheDocument();
        expect(screen.getByText('Rami Khoury')).toBeInTheDocument();
        expect(screen.getByText('event')).toBeInTheDocument();
        expect(screen.getByText(/alice/)).toBeInTheDocument();
    });

    it('check shows all agree when empty', async () => {
        render(<BooksView />);
        expect(await screen.findByText('All agree')).toBeInTheDocument();
    });

    it('check lists a mismatch', async () => {
        mockApi.fetchBooksCheck.mockResolvedValue({
            data: {
                ...STARTED,
                last_check: {
                    ...CHECK_OK.last_check, problem_count: 1,
                    party_mismatches: [{ party_type: 'customer', party_id: 9, name: 'Rami Khoury', stored: 10, books: 12.5 }],
                },
            },
        });
        render(<BooksView />);
        expect(await screen.findByText('1 to look at')).toBeInTheDocument();
        await waitFor(() => expect(screen.getByText('$12.50')).toBeInTheDocument());
        expect(screen.getAllByText('Rami Khoury').length).toBeGreaterThan(0);
    });

    it('shows not-started message', async () => {
        mockApi.fetchBooksCheck.mockResolvedValue({ data: { started_at: null, start_day: null, last_check: null } });
        render(<BooksView />);
        expect(await screen.findByText('Books start automatically tonight')).toBeInTheDocument();
        expect(screen.queryByText('All agree')).not.toBeInTheDocument();
    });
});

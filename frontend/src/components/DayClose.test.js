import React from 'react';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import CashFlowRegister from './CashFlowRegister';

let mockRole = 'admin';
const mockGet = jest.fn();
const mockPost = jest.fn();
const mockDelete = jest.fn();
jest.mock('../context/AppContext.js', () => ({
    useAppContext: () => ({ user: { role: mockRole } }),
    apiService: { api: {
        get: (...a) => mockGet(...a),
        post: (...a) => mockPost(...a),
        delete: (...a) => mockDelete(...a),
    } },
}));
jest.mock('./DailyCashFlowSection', () => () => <div>flow-mock</div>);
jest.mock('@mui/x-date-pickers/DatePicker', () => ({ DatePicker: () => <div>picker-mock</div> }));
jest.mock('@mui/x-date-pickers/LocalizationProvider', () => ({ LocalizationProvider: ({ children }) => <>{children}</> }));
jest.mock('@mui/x-date-pickers/AdapterDateFns', () => ({ AdapterDateFns: function () {} }));

const pad = (n) => String(n).padStart(2, '0');
const iso = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
const today = iso(new Date());
const yesterday = iso(new Date(Date.now() - 86400000));

const mkClose = (day, late) => ({
    id: 7, day, expected_cash: 100, counted_cash: 95, cash_diff: -5,
    expected_whish: 50, counted_whish: 50, whish_diff: 0, note: 'short on coins',
    closed_by: 'maya', closed_at: '2026-10-06T18:30:00Z',
    late: late || { cash: 0, whish: 0, count: 0 },
});

const setup = (cashFlow, entries = []) => {
    mockGet.mockImplementation((url) => {
        if (url === '/reports/daily-cash') {
            return Promise.resolve({ data: { cash_flow: cashFlow, groups: [], grand_total: 0, reporting_currency: 'USD' } });
        }
        if (url === '/cash-entries') return Promise.resolve({ data: entries });
        return Promise.resolve({ data: [] });
    });
};

beforeEach(() => { jest.clearAllMocks(); mockRole = 'admin'; });

describe('Close day', () => {
    const openFlow = {
        opening: { date: '2020-01-01', amount: 0, whish_amount: 0 },
        cash_end: 100, whish_end: 50, close: null, locked_through: null,
    };

    it('computes the difference live and posts the close', async () => {
        setup(openFlow);
        mockPost.mockResolvedValue({ data: {} });
        render(<CashFlowRegister />);
        fireEvent.click(await screen.findByRole('button', { name: 'Close day' }));
        fireEvent.change(await screen.findByLabelText('Counted cash'), { target: { value: '95' } });
        expect(screen.getByTestId('cash-diff')).toHaveTextContent('-5.00');
        expect(screen.getByTestId('cash-diff')).toHaveTextContent('short');
        expect(screen.getByTestId('whish-diff')).toHaveTextContent('Balanced');
        fireEvent.click(screen.getAllByRole('button', { name: 'Close day' }).pop());
        await waitFor(() => expect(mockPost).toHaveBeenCalledWith('/day-closes', {
            day: today, counted_cash: 95, counted_whish: 50, note: '',
        }));
    });

    it('shows the API error in the dialog', async () => {
        setup(openFlow);
        mockPost.mockRejectedValue({ response: { data: { error: 'Day is in the future' } } });
        render(<CashFlowRegister />);
        fireEvent.click(await screen.findByRole('button', { name: 'Close day' }));
        fireEvent.click(screen.getAllByRole('button', { name: 'Close day' }).pop());
        expect(await screen.findByText('Day is in the future')).toBeInTheDocument();
    });

    it('is hidden for roles without access', async () => {
        mockRole = 'collector';
        setup(openFlow);
        render(<CashFlowRegister />);
        await waitFor(() => expect(mockGet).toHaveBeenCalledWith('/day-closes', expect.anything()));
        expect(screen.queryByRole('button', { name: 'Close day' })).toBeNull();
    });
});

describe('Closed / locked day', () => {
    it('renders banner and late chip, admin can reopen the latest close', async () => {
        setup({
            opening: { date: '2020-01-01' }, close: mkClose(today, { cash: 3, whish: 0, count: 1 }),
            locked_through: today,
        });
        mockDelete.mockResolvedValue({ data: {} });
        window.confirm = jest.fn(() => true);
        render(<CashFlowRegister />);
        expect(await screen.findByText(/Day closed by maya/)).toBeInTheDocument();
        expect(screen.getByText(/Changed after close: Cash \+3.00/)).toBeInTheDocument();
        expect(screen.getByText(/short on coins/)).toBeInTheDocument();
        expect(screen.queryByRole('button', { name: 'Close day' })).toBeNull();
        fireEvent.click(screen.getByRole('button', { name: 'Reopen' }));
        await waitFor(() => expect(mockDelete).toHaveBeenCalledWith('/day-closes/7'));
    });

    it('hides Reopen for non-admins and for a close that is not the latest', async () => {
        mockRole = 'finance';
        setup({ opening: { date: '2020-01-01' }, close: mkClose(today), locked_through: today });
        const { unmount } = render(<CashFlowRegister />);
        await screen.findByText(/Day closed by maya/);
        expect(screen.queryByRole('button', { name: 'Reopen' })).toBeNull();
        unmount();

        mockRole = 'admin';
        setup({ opening: { date: '2020-01-01' }, close: mkClose(yesterday), locked_through: today });
        render(<CashFlowRegister />);
        await screen.findByText(/Day closed by maya/);
        expect(screen.queryByRole('button', { name: 'Reopen' })).toBeNull();
    });

    it('disables Cash in / Transfer and hides deletes on a locked day', async () => {
        setup({ opening: { date: '2020-01-01' }, close: null, locked_through: today },
            [{ id: 1, account: 'cash', amount: 10, reason: 'Float', created_by: 'a' }]);
        render(<CashFlowRegister />);
        expect(await screen.findByText(/Locked/)).toBeInTheDocument();
        await screen.findByText(/Float/);
        expect(screen.getByRole('button', { name: 'Cash in' })).toBeDisabled();
        expect(screen.getByRole('button', { name: 'Transfer' })).toBeDisabled();
        expect(screen.queryByLabelText('Delete entry')).toBeNull();
        expect(screen.queryByRole('button', { name: 'Close day' })).toBeNull();
    });
});

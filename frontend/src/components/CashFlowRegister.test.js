import React from 'react';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import CashFlowRegister, { TransferDialog } from './CashFlowRegister';

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

beforeEach(() => {
    jest.clearAllMocks();
    mockGet.mockImplementation((url) => {
        if (url === '/reports/daily-cash') {
            return Promise.resolve({ data: { cash_flow: {}, groups: [], grand_total: 0, reporting_currency: 'USD' } });
        }
        if (url === '/cash-entries') {
            return Promise.resolve({ data: [{ id: 1, account: 'cash', amount: 10, reason: 'Float', date: '2026-10-06', created_by: 'a' }] });
        }
        return Promise.resolve({ data: [{ id: 2, from_account: 'cash', to_account: 'whish', amount: 5, note: '', date: '2026-10-06', created_by: 'a' }] });
    });
});

describe('CashFlowRegister delete buttons', () => {
    it('shows delete buttons for admin', async () => {
        mockRole = 'admin';
        render(<CashFlowRegister />);
        await waitFor(() => expect(screen.getByText(/Float/)).toBeInTheDocument());
        expect(screen.getByLabelText('Delete entry')).toBeInTheDocument();
        expect(screen.getByLabelText('Delete transfer')).toBeInTheDocument();
    });

    it('hides delete buttons for finance', async () => {
        mockRole = 'finance';
        render(<CashFlowRegister />);
        await waitFor(() => expect(screen.getByText(/Float/)).toBeInTheDocument());
        expect(screen.queryByLabelText('Delete entry')).toBeNull();
        expect(screen.queryByLabelText('Delete transfer')).toBeNull();
    });
});

describe('TransferDialog', () => {
    it('flips the other account so from never equals to', () => {
        render(<TransferDialog open defaultDate="2026-10-06" onClose={() => {}} onSaved={() => {}} />);
        // Defaults: from Cash, to Whish. Set "to" to Cash -> from must become Whish.
        fireEvent.mouseDown(screen.getByLabelText('To account'));
        fireEvent.click(screen.getByRole('option', { name: 'Cash' }));
        const hidden = document.querySelectorAll('input.MuiSelect-nativeInput');
        expect(hidden[0].value).toBe('whish'); // from
        expect(hidden[1].value).toBe('cash');  // to
        fireEvent.change(screen.getByLabelText('Amount'), { target: { value: '5' } });
        expect(screen.getByRole('button', { name: 'Save' })).toBeEnabled();
        expect(hidden[0].value).not.toBe(hidden[1].value);
    });
});

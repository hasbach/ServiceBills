import React from 'react';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import axios from 'axios';
import AIConnectorCard from './AIConnectorCard';

// Factory mock: axios ships ESM that CRA's Jest won't transform.
jest.mock('axios', () => ({ get: jest.fn(), delete: jest.fn() }));

const LIST = {
    mcp_url: 'https://servicebills.example/mcp', enabled: true, is_admin: true,
    grants: [{ id: 7, client_name: 'Claude', username: 'ali', role: 'cashier',
               created_at: '2026-10-21T10:00:00Z', last_used_at: null }],
};

beforeEach(() => {
    axios.get.mockReset();
    axios.delete.mockReset();
    window.confirm = jest.fn(() => true);
});

test('shows the connector URL and the connected apps', async () => {
    axios.get.mockResolvedValue({ data: LIST });
    render(<AIConnectorCard />);
    expect(await screen.findByTestId('mcp-url')).toHaveTextContent('https://servicebills.example/mcp');
    // /Claude/ alone also matches the setup hints, so anchor on the grant row.
    expect(screen.getByText(/^Claude: ali \(cashier\)/)).toBeInTheDocument();
});

test('disconnect revokes and reloads the list', async () => {
    axios.get.mockResolvedValueOnce({ data: LIST }).mockResolvedValueOnce({ data: { ...LIST, grants: [] } });
    axios.delete.mockResolvedValue({ data: { ok: true } });
    render(<AIConnectorCard />);
    fireEvent.click(await screen.findByText('Disconnect'));
    await waitFor(() => expect(axios.delete).toHaveBeenCalledWith('/api/mcp/grants/7', expect.any(Object)));
    expect(await screen.findByText(/No AI apps connected yet/)).toBeInTheDocument();
});

test('explains when the module is off', async () => {
    axios.get.mockResolvedValue({ data: { ...LIST, enabled: false, grants: [] } });
    render(<AIConnectorCard />);
    expect(await screen.findByText(/not part of your plan/)).toBeInTheDocument();
});

import React from 'react';
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';
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

afterEach(() => {
    jest.useRealTimers();
});

test('shows the connector URL and the connected apps', async () => {
    axios.get.mockResolvedValue({ data: LIST });
    render(<AIConnectorCard />);
    expect(await screen.findByTestId('mcp-url')).toHaveTextContent('https://servicebills.example/mcp');
    // /Claude/ alone also matches the setup hints, so anchor on the grant row.
    expect(screen.getByText(/^Claude: ali \(cashier\)/)).toBeInTheDocument();
});

test('shows a spinner while loading', async () => {
    axios.get.mockResolvedValue({ data: LIST });
    render(<AIConnectorCard />);
    expect(screen.getByRole('progressbar')).toBeInTheDocument();
    expect(await screen.findByTestId('mcp-url')).toBeInTheDocument();
    expect(screen.queryByRole('progressbar')).not.toBeInTheDocument();
});

test('disconnect revokes and reloads the list', async () => {
    axios.get.mockResolvedValueOnce({ data: LIST }).mockResolvedValueOnce({ data: { ...LIST, grants: [] } });
    axios.delete.mockResolvedValue({ data: { ok: true } });
    render(<AIConnectorCard />);
    fireEvent.click(await screen.findByText('Disconnect'));
    await waitFor(() => expect(axios.delete).toHaveBeenCalledWith('/api/mcp/grants/7', expect.any(Object)));
    expect(await screen.findByText(/No AI apps connected yet/)).toBeInTheDocument();
    expect(axios.get).toHaveBeenCalledTimes(2);
});

test('a failed disconnect still reloads so a grant revoked elsewhere disappears', async () => {
    axios.get.mockResolvedValueOnce({ data: LIST }).mockResolvedValueOnce({ data: { ...LIST, grants: [] } });
    axios.delete.mockRejectedValue({ response: { data: { msg: 'Grant not found.' } } });
    render(<AIConnectorCard />);
    fireEvent.click(await screen.findByText('Disconnect'));
    expect(await screen.findByText(/No AI apps connected yet/)).toBeInTheDocument();
    expect(axios.get).toHaveBeenCalledTimes(2);
    expect(screen.getByText('Grant not found.')).toBeInTheDocument();
});

test('a later successful action clears the earlier error', async () => {
    axios.get.mockResolvedValue({ data: LIST });
    axios.delete
        .mockRejectedValueOnce({ response: { data: { msg: 'Could not disconnect right now.' } } })
        .mockResolvedValueOnce({ data: { ok: true } });
    render(<AIConnectorCard />);
    fireEvent.click(await screen.findByText('Disconnect'));
    expect(await screen.findByText('Could not disconnect right now.')).toBeInTheDocument();
    await waitFor(() => expect(axios.get).toHaveBeenCalledTimes(2));
    fireEvent.click(screen.getByText('Disconnect'));
    await waitFor(() => expect(axios.get).toHaveBeenCalledTimes(3));
    await waitFor(() => expect(screen.queryByText('Could not disconnect right now.')).not.toBeInTheDocument());
});

test('explains when the module is off', async () => {
    axios.get.mockResolvedValue({ data: { ...LIST, enabled: false, grants: [] } });
    render(<AIConnectorCard />);
    expect(await screen.findByText(/not part of your plan/)).toBeInTheDocument();
});

test('copy button is labelled and the Copied state resets', async () => {
    axios.get.mockResolvedValue({ data: LIST });
    const writeText = jest.fn().mockResolvedValue();
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true });
    render(<AIConnectorCard />);
    const button = await screen.findByRole('button', { name: 'Copy connector URL' });

    jest.useFakeTimers();
    await act(async () => { fireEvent.click(button); });
    expect(writeText).toHaveBeenCalledWith('https://servicebills.example/mcp');
    act(() => { fireEvent.mouseOver(button); });
    act(() => { jest.advanceTimersByTime(200); });
    expect(screen.getByRole('tooltip')).toHaveTextContent('Copied');
    act(() => { jest.advanceTimersByTime(2100); });
    expect(screen.getByRole('tooltip')).toHaveTextContent('Copy');
    expect(screen.getByRole('tooltip')).not.toHaveTextContent('Copied');
});

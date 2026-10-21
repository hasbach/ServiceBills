import React from 'react';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import axios from 'axios';
import OAuthConsentView from './OAuthConsentView';
import { useAppContext } from '../context/AppContext';

// Factory mocks: axios ships ESM that CRA's Jest won't transform.
jest.mock('axios', () => ({ get: jest.fn(), post: jest.fn() }));
jest.mock('../context/AppContext', () => ({ useAppContext: jest.fn() }));

const QUERY = '?client_id=c1&redirect_uri=https%3A%2F%2Fclaude.ai%2Fcb&response_type=code'
    + '&code_challenge=x&code_challenge_method=S256&state=s';
const PARAMS = {
    client_id: 'c1', redirect_uri: 'https://claude.ai/cb', response_type: 'code',
    code_challenge: 'x', code_challenge_method: 'S256', state: 's',
};
const INFO = {
    client_name: 'Claude', tenant_name: 'Acme ISP', username: 'ali', role: 'cashier',
    redirect_host: 'claude.ai',
};

const realLocation = window.location;
let login;
let logout;
let assign;

function setAuth(isAuthenticated) {
    useAppContext.mockReturnValue({ isAuthenticated, login, logout });
}

beforeEach(() => {
    axios.get.mockReset();
    axios.post.mockReset();
    login = jest.fn();
    logout = jest.fn();
    assign = jest.fn();
    window.history.pushState({}, '', `/oauth/consent${QUERY}`);
    delete window.location;
    window.location = { ...realLocation, search: QUERY, assign };
});

afterEach(() => {
    window.location = realLocation;
});

test('authenticated user sees who is asking for what', async () => {
    setAuth(true);
    axios.get.mockResolvedValue({ data: INFO });
    render(<OAuthConsentView />);
    expect(await screen.findByText('Acme ISP')).toBeInTheDocument();
    expect(screen.getByText('Claude')).toBeInTheDocument();
    expect(screen.getByText('ali')).toBeInTheDocument();
    expect(axios.get).toHaveBeenCalledWith('/api/mcp/oauth/consent-info',
        expect.objectContaining({ params: PARAMS }));
});

test('Allow posts the decision and follows the redirect', async () => {
    setAuth(true);
    axios.get.mockResolvedValue({ data: INFO });
    axios.post.mockResolvedValue({ data: { redirect_url: 'https://claude.ai/cb?code=abc&state=s' } });
    render(<OAuthConsentView />);
    fireEvent.click(await screen.findByText('Allow'));
    await waitFor(() => expect(assign).toHaveBeenCalledWith('https://claude.ai/cb?code=abc&state=s'));
    expect(axios.post).toHaveBeenCalledWith('/api/mcp/oauth/approve',
        { ...PARAMS, decision: 'allow' }, expect.any(Object));
});

test('a failed Allow shows the error, and a retry clears it', async () => {
    setAuth(true);
    axios.get.mockResolvedValue({ data: INFO });
    axios.post
        .mockRejectedValueOnce({ response: { data: { error: 'Temporary failure.' } } })
        .mockResolvedValueOnce({ data: { redirect_url: 'https://claude.ai/cb?code=abc' } });
    render(<OAuthConsentView />);
    fireEvent.click(await screen.findByText('Allow'));
    expect(await screen.findByText('Temporary failure.')).toBeInTheDocument();
    fireEvent.click(screen.getByText('Allow'));
    await waitFor(() => expect(assign).toHaveBeenCalledWith('https://claude.ai/cb?code=abc'));
    expect(screen.queryByText('Temporary failure.')).not.toBeInTheDocument();
});

test('consent-info failure shows the error and a way to switch account', async () => {
    setAuth(true);
    axios.get.mockRejectedValue({
        response: { status: 403, data: { error: 'Sign in with your business account to connect an AI app.' } },
    });
    render(<OAuthConsentView />);
    expect(await screen.findByText('Sign in with your business account to connect an AI app.')).toBeInTheDocument();
    expect(screen.queryByText('Allow')).not.toBeInTheDocument();
    fireEvent.click(screen.getByText('Use a different account'));
    expect(logout).toHaveBeenCalledTimes(1);
});

test('not authenticated shows the sign-in form', () => {
    setAuth(false);
    render(<OAuthConsentView />);
    expect(screen.getByLabelText('Username')).toBeInTheDocument();
    expect(screen.getByLabelText('Password')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Sign in' })).toBeInTheDocument();
    expect(axios.get).not.toHaveBeenCalled();
});

test('signing in as someone else never leaves the previous identity clickable', async () => {
    setAuth(true);
    axios.get.mockResolvedValueOnce({ data: INFO });
    const { rerender } = render(<OAuthConsentView />);
    expect(await screen.findByText('Allow')).toBeInTheDocument();

    // "Not ali? Sign out" -> sign-in form.
    setAuth(false);
    rerender(<OAuthConsentView />);
    expect(screen.queryByText('Allow')).not.toBeInTheDocument();

    // A different user signs in; their consent-info is still in flight.
    let resolveSecond;
    axios.get.mockReturnValueOnce(new Promise(r => { resolveSecond = r; }));
    setAuth(true);
    rerender(<OAuthConsentView />);
    expect(screen.queryByText('Allow')).not.toBeInTheDocument();
    expect(screen.queryByText('ali')).not.toBeInTheDocument();

    resolveSecond({ data: { ...INFO, username: 'sara' } });
    expect(await screen.findByText('sara')).toBeInTheDocument();
    expect(screen.queryByText('ali')).not.toBeInTheDocument();
});

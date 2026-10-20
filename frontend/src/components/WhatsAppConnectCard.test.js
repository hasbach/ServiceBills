import React from 'react';
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';
import WhatsAppConnectCard from './WhatsAppConnectCard';
import StandardTemplatesDialog from './StandardTemplatesDialog';

const mockApi = {
    getWaConnectConfig: jest.fn(),
    getWaConnectStatus: jest.fn(),
    completeWaConnect: jest.fn(),
    refreshWaConnect: jest.fn(),
    disconnectWa: jest.fn(),
    getStandardTemplates: jest.fn(),
    submitStandardTemplates: jest.fn(),
};
jest.mock('../context/AppContext.js', () => ({
    useAppContext: () => ({ apiService: mockApi }),
}));

const CONFIG = {
    automatic_available: true, coexistence_available: true, app_id: '1', graph_version: 'v24.0',
    es_config_id: '111', es_coex_config_id: '222',
    coex_extras: { featureType: 'whatsapp_business_app_onboarding', sessionInfoVersion: '3' },
    plan_allows_api: true,
};
const DISCONNECTED = { method: 'manual', status: 'disconnected' };
const CONNECTED = {
    method: 'coexistence', status: 'connected', display_phone_number: '+961 3 123 456',
    verified_name: 'DeltaNet', meta_business_name: 'DeltaNet WABA', quality_rating: 'GREEN',
    messaging_limit_tier: 'TIER_250', payment_method_status: 'missing',
    connected_at: '2026-10-20T09:00:00Z', connected_by: 'admin',
};

beforeEach(() => {
    Object.values(mockApi).forEach(f => f.mockReset());
    mockApi.getWaConnectConfig.mockResolvedValue({ data: CONFIG });
    mockApi.getWaConnectStatus.mockResolvedValue({ data: { connection: DISCONNECTED, events: [] } });
    mockApi.getStandardTemplates.mockResolvedValue({ data: { templates: [] } });
    window.FB = { init: jest.fn(), login: jest.fn() };
});
afterEach(() => { delete window.FB; });

test('locks the automatic methods when the plan lacks API messaging', async () => {
    mockApi.getWaConnectConfig.mockResolvedValue({ data: { ...CONFIG, plan_allows_api: false } });
    render(<WhatsAppConnectCard connection={DISCONNECTED} />);
    expect(await screen.findByText('Use my WhatsApp Business app number')).toBeInTheDocument();
    expect(screen.getAllByText(/Not included in your plan/)).toHaveLength(2);
});

test('only manual when automatic is unavailable', async () => {
    const onAuto = jest.fn();
    mockApi.getWaConnectConfig.mockResolvedValue({ data: { automatic_available: false } });
    render(<WhatsAppConnectCard connection={DISCONNECTED} onAutomaticAvailable={onAuto} />);
    expect(await screen.findByText('Manual configuration')).toBeInTheDocument();
    expect(screen.queryByText('New number (WhatsApp Business API)')).not.toBeInTheDocument();
    expect(onAuto).toHaveBeenCalledWith(false);
});

test('coexistence flow posts code + ids from the postMessage', async () => {
    const onChange = jest.fn();
    mockApi.completeWaConnect.mockResolvedValue({ data: { connection: CONNECTED } });
    mockApi.getWaConnectStatus
        .mockResolvedValueOnce({ data: { connection: DISCONNECTED, events: [] } })
        .mockResolvedValue({ data: { connection: CONNECTED, events: [] } });
    render(<WhatsAppConnectCard connection={DISCONNECTED} onConnectionChange={onChange} />);
    fireEvent.click(await screen.findByText('Use my WhatsApp Business app number'));
    expect(await screen.findByText('Broadcast lists')).toBeInTheDocument();
    const go = await screen.findByRole('button', { name: 'Continue with Facebook' });
    await waitFor(() => expect(go).not.toBeDisabled());
    fireEvent.click(go);

    expect(window.FB.login).toHaveBeenCalledTimes(1);
    const [cb, opts] = window.FB.login.mock.calls[0];
    expect(opts).toEqual({
        config_id: '222', response_type: 'code', override_default_response_type: true,
        extras: { setup: {}, featureType: 'whatsapp_business_app_onboarding', sessionInfoVersion: '3' },
    });
    // A message from a foreign origin is ignored.
    act(() => {
        window.dispatchEvent(new MessageEvent('message', { origin: 'https://evilfacebook.com',
            data: JSON.stringify({ type: 'WA_EMBEDDED_SIGNUP', event: 'FINISH_WHATSAPP_BUSINESS_APP_ONBOARDING', data: { waba_id: 'x' } }) }));
    });
    act(() => {
        window.dispatchEvent(new MessageEvent('message', { origin: 'https://www.facebook.com',
            data: JSON.stringify({ type: 'WA_EMBEDDED_SIGNUP', event: 'FINISH_WHATSAPP_BUSINESS_APP_ONBOARDING',
                data: { phone_number_id: 'P1', waba_id: 'W1', business_id: 'B1' } }) }));
    });
    act(() => { cb({ authResponse: { code: 'CODE' } }); });

    await waitFor(() => expect(mockApi.completeWaConnect).toHaveBeenCalledWith({
        method: 'coexistence', code: 'CODE', waba_id: 'W1', phone_number_id: 'P1', business_id: 'B1',
        es_event: 'FINISH_WHATSAPP_BUSINESS_APP_ONBOARDING',
    }));
    expect(await screen.findByText(/WhatsApp connected: \+961 3 123 456/)).toBeInTheDocument();
    expect(onChange).toHaveBeenCalledWith(CONNECTED);
    expect(screen.getAllByText('Connected').length).toBeGreaterThan(0);
    expect(screen.getByText(/No payment method is set up in Meta/)).toBeInTheDocument();
});

test('cancel shows "Connection not completed" with the step', async () => {
    render(<WhatsAppConnectCard connection={DISCONNECTED} />);
    fireEvent.click(await screen.findByText('New number (WhatsApp Business API)'));
    const go = await screen.findByRole('button', { name: 'Continue with Facebook' });
    await waitFor(() => expect(go).not.toBeDisabled());
    fireEvent.click(go);
    const [cb, opts] = window.FB.login.mock.calls[0];
    expect(opts.config_id).toBe('111');
    expect(opts.extras).toEqual({ setup: {} });
    act(() => {
        window.dispatchEvent(new MessageEvent('message', { origin: 'https://web.facebook.com',
            data: JSON.stringify({ type: 'WA_EMBEDDED_SIGNUP', event: 'CANCEL', data: { current_step: 'PHONE_NUMBER_VERIFICATION' } }) }));
    });
    act(() => { cb({ authResponse: null }); });
    expect(await screen.findByText(/Connection not completed\. You stopped at: phone number verification\./, {}, { timeout: 3000 })).toBeInTheDocument();
    expect(mockApi.completeWaConnect).not.toHaveBeenCalled();
});

test('server error text is shown on failure', async () => {
    mockApi.completeWaConnect.mockRejectedValue({ response: { data: { error: 'This WhatsApp number is already connected to another account.' } } });
    render(<WhatsAppConnectCard connection={DISCONNECTED} />);
    fireEvent.click(await screen.findByText('New number (WhatsApp Business API)'));
    const go = await screen.findByRole('button', { name: 'Continue with Facebook' });
    await waitFor(() => expect(go).not.toBeDisabled());
    fireEvent.click(go);
    const [cb] = window.FB.login.mock.calls[0];
    act(() => { cb({ authResponse: { code: 'C' } }); });
    act(() => {
        window.dispatchEvent(new MessageEvent('message', { origin: 'https://www.facebook.com',
            data: { type: 'WA_EMBEDDED_SIGNUP', event: 'FINISH', data: { phone_number_id: 'P', waba_id: 'W', business_id: 'B' } } }));
    });
    expect(await screen.findByText(/already connected to another account/)).toBeInTheDocument();
});

test('switching to manual while connected asks, disconnects, then reveals manual', async () => {
    const onManual = jest.fn();
    mockApi.getWaConnectStatus.mockResolvedValue({ data: { connection: CONNECTED, events: [
        { id: 1, event: 'connect', to_status: 'connected', source: 'user', user: 'admin', detail: null, created_at: '2026-10-20T09:00:00Z' },
    ] } });
    mockApi.disconnectWa.mockResolvedValue({ data: { connection: { ...CONNECTED, status: 'disconnected' } } });
    render(<WhatsAppConnectCard connection={CONNECTED} onSelectManual={onManual} />);
    expect(await screen.findByText('Connection history (1)')).toBeInTheDocument();
    fireEvent.click(screen.getByText('Manual configuration'));
    expect(await screen.findByText(/Switching to manual will disconnect the current automatic connection/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Disconnect and switch' }));
    await waitFor(() => expect(mockApi.disconnectWa).toHaveBeenCalled());
    await waitFor(() => expect(onManual).toHaveBeenCalledWith(true));
});

test('templates dialog validates and submits', async () => {
    mockApi.getStandardTemplates.mockResolvedValue({ data: { templates: [{
        key: 'payment_paid', name: 'payment_confirmation', category: 'UTILITY', header: null,
        body: 'Dear {customer_name}, we received {amount}. Balance: {balance}.', footer: null, buttons: [],
        variables: [{ key: 'customer_name', label: 'Customer name' }, { key: 'amount', label: 'Amount' }, { key: 'balance', label: 'Balance' }],
        existing: null,
    }] } });
    mockApi.submitStandardTemplates.mockResolvedValue({ data: { results: [{ key: 'payment_paid', ok: true, status: 'PENDING' }] } });
    render(<StandardTemplatesDialog open onClose={() => {}} />);
    expect(await screen.findByText('payment_confirmation')).toBeInTheDocument();
    fireEvent.click(screen.getByLabelText('Edit text'));
    const body = screen.getByLabelText('Body');
    const text = 'Dear {customer_name}, we received {amount}. Balance: .';
    fireEvent.change(body, { target: { value: text } });
    body.setSelectionRange(text.length - 1, text.length - 1);
    fireEvent.select(body);
    expect(await screen.findByText('Missing variable {balance}')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Submit 1 template/ })).toBeDisabled();
    fireEvent.click(screen.getByText('Balance {balance}'));
    await waitFor(() => expect(screen.getByRole('button', { name: /Submit 1 template/ })).not.toBeDisabled());
    fireEvent.click(screen.getByRole('button', { name: /Submit 1 template/ }));
    await waitFor(() => expect(mockApi.submitStandardTemplates).toHaveBeenCalled());
    const payload = mockApi.submitStandardTemplates.mock.calls[0][0];
    expect(payload.items[0]).toMatchObject({ key: 'payment_paid', name: 'payment_confirmation', header: null, footer: null, use_existing: false });
    expect(payload.items[0].body).toMatch(/\{balance\}/);
    expect(await screen.findByText(/1 submitted/)).toBeInTheDocument();
});

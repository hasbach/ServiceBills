import React from 'react';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import axios from 'axios';
import CSAgentToolSecretCard from './CSAgentToolSecretCard';

// Factory mock: axios ships ESM that CRA's Jest won't transform.
jest.mock('axios', () => ({ post: jest.fn() }));

beforeEach(() => {
    axios.post.mockReset();
    window.confirm = jest.fn(() => true);
});

test('generates a secret and shows it once with the exact header', async () => {
    axios.post.mockResolvedValue({ data: { secret: 'csat_abc123', created_at: '2026-10-21T18:00:00' } });
    const onChange = jest.fn();
    render(<CSAgentToolSecretCard hasSecret={false} headerName="X-CS-Agent-Secret" onChange={onChange} />);

    fireEvent.click(screen.getByText('إنشاء المفتاح'));

    await waitFor(() => expect(screen.getByTestId('tool-secret-header'))
        .toHaveTextContent('X-CS-Agent-Secret: csat_abc123'));
    expect(axios.post).toHaveBeenCalledWith('/api/cs-agent/tool-secret', {}, expect.any(Object));
    expect(window.confirm).not.toHaveBeenCalled();
    expect(onChange).toHaveBeenCalledWith({ hasSecret: true, createdAt: '2026-10-21T18:00:00' });
});

test('rotating asks for confirmation and does nothing when declined', () => {
    window.confirm = jest.fn(() => false);
    render(<CSAgentToolSecretCard hasSecret createdAt="2026-10-01T00:00:00" />);
    expect(screen.getByText(/2026-10-01/)).toBeInTheDocument();
    fireEvent.click(screen.getByText('تدوير المفتاح (Rotate)'));
    expect(window.confirm).toHaveBeenCalled();
    expect(axios.post).not.toHaveBeenCalled();
    expect(screen.queryByTestId('tool-secret-header')).toBeNull();
});

test('shows the server error for a non-admin', async () => {
    axios.post.mockRejectedValue({ response: { data: { msg: 'Admins only!' } } });
    render(<CSAgentToolSecretCard hasSecret={false} />);
    fireEvent.click(screen.getByText('إنشاء المفتاح'));
    await waitFor(() => expect(screen.getByText('Admins only!')).toBeInTheDocument());
});

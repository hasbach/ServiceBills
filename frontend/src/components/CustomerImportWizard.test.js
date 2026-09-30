import React from 'react';
import { render, screen } from '@testing-library/react';
import CustomerImportWizard from './CustomerImportWizard';

jest.mock('../context/AppContext', () => ({
    useAppContext: () => ({ apiService: {} }),
}));

test('opens on the download-template step', () => {
    render(<CustomerImportWizard open onClose={() => {}} onImported={() => {}} />);
    expect(screen.getByText('Import customers')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /download template/i })).toBeInTheDocument();
});

test('renders nothing when closed', () => {
    render(<CustomerImportWizard open={false} onClose={() => {}} onImported={() => {}} />);
    expect(screen.queryByText('Import customers')).not.toBeInTheDocument();
});

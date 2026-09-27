import React from 'react';
import { render, screen, fireEvent } from '@testing-library/react';
import DirectAccessHelp, { APP_OUTBOUND_IPS } from './DirectAccessHelp';

test('guide lists the app outbound IPs and uses placeholders, not real addresses', () => {
    const { container } = render(<DirectAccessHelp />);
    fireEvent.click(screen.getByText(/Setup guide: reach your devices/));
    const text = container.textContent;

    APP_OUTBOUND_IPS.forEach(ip => expect(text).toContain(ip));
    expect(text).toContain('<your-public-ip>');
    expect(text).toContain('<your-device-ip>');
    expect(text).toContain('<your-desired-port>');
    expect(text).toContain('src-address-list=app-outbound');

    // No concrete public or LAN address other than the app's own ranges.
    const ips = text.match(/\b\d{1,3}(\.\d{1,3}){3}\b/g) || [];
    const allowed = APP_OUTBOUND_IPS.map(r => r.split('/')[0]);
    expect(ips.filter(ip => !allowed.includes(ip))).toEqual([]);
});

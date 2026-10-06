import React from 'react';
import { render, act } from '@testing-library/react';
import useWindowRows from './useWindowRows';

let top = 100;

function Harness({ count, bump }) {
    const rows = useWindowRows(count, 64, (i) => i);
    return (
        <div>
            <span data-testid="tick">{bump}</span>
            <div ref={rows.anchorRef} data-testid="anchor" />
            <span data-testid="margin">{rows.virtualizer.options.scrollMargin}</span>
        </div>
    );
}

test('scrollMargin follows the anchor when it moves without the list changing', () => {
    top = 100;
    window.scrollTo = jest.fn();
    const spy = jest
        .spyOn(Element.prototype, 'getBoundingClientRect')
        .mockImplementation(() => ({ top, bottom: top, left: 0, right: 0, width: 0, height: 0 }));
    const { getByTestId, rerender } = render(<Harness count={10} bump={0} />);
    expect(getByTestId('margin').textContent).toBe('100');

    // Content above the list grew (e.g. a banner appeared), then something unrelated re-rendered.
    top = 450;
    rerender(<Harness count={10} bump={1} />);
    expect(getByTestId('margin').textContent).toBe('450');

    top = 300;
    act(() => { window.dispatchEvent(new Event('resize')); });
    expect(getByTestId('margin').textContent).toBe('300');
    spy.mockRestore();
});

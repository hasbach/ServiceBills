import { spanStyle, nodeMarkerStyle, TILE_LAYERS } from './fiberMapStyles';

describe('spanStyle', () => {
  test('a green span is thin and solid', () => {
    const s = spanStyle({ status: 'green', is_fault_boundary: false });
    expect(s.color).toBe('#2e7d32');
    expect(s.weight).toBe(3);
    expect(s.dashArray).toBeNull();
    expect(s.className).toBe('');
  });

  test('an ordinary red span is red but not animated', () => {
    const s = spanStyle({ status: 'red', is_fault_boundary: false });
    expect(s.color).toBe('#c62828');
    expect(s.weight).toBe(3);
    expect(s.className).toBe('');
  });

  test('the fault boundary is thicker and animated', () => {
    const boundary = spanStyle({ status: 'red', is_fault_boundary: true });
    const ordinary = spanStyle({ status: 'red', is_fault_boundary: false });
    expect(boundary.weight).toBe(7);
    expect(boundary.weight).toBeGreaterThan(ordinary.weight);
    expect(boundary.className).toBe('fiber-span-fault-boundary');
  });

  test('a grey span is dashed, so unknown never reads as an outage', () => {
    const s = spanStyle({ status: 'grey', is_fault_boundary: false });
    expect(s.color).toBe('#9e9e9e');
    expect(s.weight).toBe(2);
    expect(s.dashArray).toBe('6 6');
  });

  test('an unrecognised status falls back to grey rather than throwing', () => {
    expect(spanStyle({ status: 'banana' }).color).toBe('#9e9e9e');
    expect(spanStyle({}).color).toBe('#9e9e9e');
  });
});

describe('nodeMarkerStyle', () => {
  test('the root is drawn larger than an ONU', () => {
    expect(nodeMarkerStyle('root', 'online').radius)
      .toBeGreaterThan(nodeMarkerStyle('onu', 'online').radius);
  });

  test('every kind and status renders at 0.9 fill opacity', () => {
    expect(nodeMarkerStyle('root', 'online').fillOpacity).toBe(0.9);
    expect(nodeMarkerStyle('junction', 'offline').fillOpacity).toBe(0.9);
    expect(nodeMarkerStyle('onu', 'unknown').fillOpacity).toBe(0.9);
  });

  test('a junction is the smallest marker on the map', () => {
    const junction = nodeMarkerStyle('junction', 'online').radius;
    expect(junction).toBe(6);
    expect(junction).toBeLessThan(nodeMarkerStyle('root', 'online').radius);
    expect(junction).toBeLessThan(nodeMarkerStyle('onu', 'online').radius);
  });

  test('offline is red and online is green at every kind', () => {
    expect(nodeMarkerStyle('onu', 'offline').color).toBe('#c62828');
    expect(nodeMarkerStyle('junction', 'online').color).toBe('#2e7d32');
  });

  test('unknown is grey, never red', () => {
    expect(nodeMarkerStyle('onu', 'unknown').color).toBe('#9e9e9e');
  });
});

describe('TILE_LAYERS', () => {
  test('offers a keyless satellite layer first and a street layer', () => {
    expect(TILE_LAYERS).toHaveLength(2);
    expect(TILE_LAYERS[0].key).toBe('satellite');
    expect(TILE_LAYERS[1].key).toBe('street');
  });

  test('no layer URL carries an API key or token placeholder', () => {
    TILE_LAYERS.forEach((layer) => {
      expect(layer.url).not.toMatch(/key=|token=|access_token|apikey/i);
    });
  });

  test('every layer carries attribution, which both sources require', () => {
    TILE_LAYERS.forEach((layer) => {
      expect(layer.attribution.length).toBeGreaterThan(10);
    });
  });
});

// Datos de muestra para el prototipo — precios sintéticos, no reales.
// Estructura pensada para calzar con card_view.py / series_view.py del repo:
// por celda producto×provincia hay min/mediana/max, cantidad de anuncios y
// una serie de observaciones puntuales (día, precio, evidencia).

export const PROVINCE_ORDER = [
  'Pinar del Río', 'Artemisa', 'La Habana', 'Mayabeque', 'Matanzas',
  'Cienfuegos', 'Villa Clara', 'Sancti Spíritus', 'Ciego de Ávila',
  'Camagüey', 'Las Tunas', 'Holguín', 'Granma', 'Santiago de Cuba',
  'Guantánamo', 'Isla de la Juventud',
];

// Hues oklch distintos entre sí y separados de la identidad de provincia
// (violeta/índigo/verde-azulado del token --m3-prov-*), evitando rojo puro
// (~25) y verde puro (~145): son ejes ya reservados a advertencia/éxito.
export const PRODUCTS = [
  { id: 'aceite', name: 'Aceite', unit: 'L', hue: 233 },
  { id: 'arroz', name: 'Arroz', unit: 'lb', hue: 40 },
  { id: 'pollo', name: 'Pollo', unit: 'lb', hue: 176 },
  { id: 'azucar', name: 'Azúcar blanca', unit: 'lb', hue: 300 },
  { id: 'leche', name: 'Leche en polvo', unit: 'lb', hue: 96 },
  { id: 'cerdo', name: 'Carne de cerdo', unit: 'lb', hue: 260 },
];

// Cobertura curada a mano: sparse a propósito, distinta por producto, para
// que el mapa nunca finja cobertura pareja. `count` = anuncios en 30 días.
const COVERAGE = {
  aceite: [
    { province: 'La Habana', base: 1080, count: 14, state: 'publicada' },
    { province: 'Villa Clara', base: 1150, count: 6, state: 'publicada' },
    { province: 'Santiago de Cuba', base: 1240, count: 5, state: 'publicada' },
    { province: 'Holguín', base: 1190, count: 4, state: 'publicada' },
    { province: 'Camagüey', base: 1210, count: 2, state: 'en-ingesta' },
    { province: 'Matanzas', base: 1050, count: 1, state: 'en-ingesta' },
  ],
  arroz: [
    { province: 'La Habana', base: 210, count: 17, state: 'publicada' },
    { province: 'Camagüey', base: 195, count: 8, state: 'publicada' },
    { province: 'Granma', base: 200, count: 6, state: 'publicada' },
    { province: 'Holguín', base: 205, count: 5, state: 'publicada' },
    { province: 'Guantánamo', base: 220, count: 4, state: 'publicada' },
    { province: 'Sancti Spíritus', base: 215, count: 3, state: 'en-ingesta' },
    { province: 'Las Tunas', base: 208, count: 2, state: 'en-ingesta' },
  ],
  pollo: [
    { province: 'La Habana', base: 420, count: 9, state: 'publicada' },
    { province: 'Santiago de Cuba', base: 460, count: 7, state: 'publicada' },
    { province: 'Cienfuegos', base: 400, count: 4, state: 'publicada' },
    { province: 'Ciego de Ávila', base: 410, count: 3, state: 'en-ingesta' },
    { province: 'Villa Clara', base: 430, count: 2, state: 'en-ingesta' },
  ],
  azucar: [
    { province: 'Matanzas', base: 135, count: 5, state: 'publicada' },
    { province: 'Sancti Spíritus', base: 128, count: 4, state: 'publicada' },
    { province: 'Granma', base: 150, count: 4, state: 'publicada' },
    { province: 'Camagüey', base: 140, count: 3, state: 'en-ingesta' },
    { province: 'Artemisa', base: 132, count: 2, state: 'en-ingesta' },
    { province: 'La Habana', base: 145, count: 1, state: 'en-ingesta' },
  ],
  leche: [
    { province: 'La Habana', base: 820, count: 6, state: 'publicada' },
    { province: 'Santiago de Cuba', base: 900, count: 4, state: 'publicada' },
    { province: 'Holguín', base: 870, count: 3, state: 'en-ingesta' },
    { province: 'Villa Clara', base: 850, count: 2, state: 'en-ingesta' },
  ],
  cerdo: [
    { province: 'La Habana', base: 450, count: 11, state: 'publicada' },
    { province: 'Camagüey', base: 470, count: 6, state: 'publicada' },
    { province: 'Mayabeque', base: 430, count: 5, state: 'publicada' },
    { province: 'Artemisa', base: 420, count: 4, state: 'publicada' },
    { province: 'Pinar del Río', base: 410, count: 3, state: 'en-ingesta' },
    { province: 'Cienfuegos', base: 445, count: 3, state: 'en-ingesta' },
    { province: 'Guantánamo', base: 490, count: 2, state: 'en-ingesta' },
  ],
};

function seededRand(seed) {
  const x = Math.sin(seed) * 10000;
  return x - Math.floor(x);
}

function buildCell(productId, row, index) {
  const seedBase = productId.length * 97 + row.province.length * 13 + index * 7;
  const n = Math.min(row.count, 6);
  const days = [];
  let cursor = 30;
  for (let i = 0; i < n; i += 1) {
    cursor -= Math.max(2, Math.round(seededRand(seedBase + i) * 6));
    days.push(Math.max(cursor, 0));
  }
  days.sort((a, b) => b - a); // más viejo primero
  const prices = days.map((_, i) => {
    const drift = (seededRand(seedBase + i * 3 + 1) - 0.5) * 0.16;
    return Math.round(row.base * (1 + drift));
  });
  const history = days.map((daysAgo, i) => ({
    daysAgo,
    price: prices[i],
    evidenceId: `ev-${productId}-${row.province}-${i}`.replace(/\s|í|é|á|ó|ú|ñ/g, ''),
    public: seededRand(seedBase + i * 5) > 0.12,
  }));
  const min = Math.min(...prices);
  const max = Math.max(...prices);
  const sorted = [...prices].sort((a, b) => a - b);
  const median = sorted[Math.floor(sorted.length / 2)];
  return {
    province: row.province,
    state: row.state,
    count: row.count,
    min,
    median,
    max,
    history,
  };
}

const CELLS_BY_PRODUCT = {};
for (const [productId, rows] of Object.entries(COVERAGE)) {
  CELLS_BY_PRODUCT[productId] = rows.map((row, i) => buildCell(productId, row, i));
}

// `sparse=true` simula un sistema recién arrancado: solo la primera
// provincia curada por producto tiene dato, el resto queda sin observar.
// Sirve para el estado "dato escaso" del prototipo, sin inventar un segundo
// set de datos a mano.
export function getCoverage(productId, sparse) {
  const rows = CELLS_BY_PRODUCT[productId] || [];
  return sparse ? rows.slice(0, 1) : rows;
}

export function getCell(productId, province, sparse) {
  const rows = getCoverage(productId, sparse);
  return rows.find((r) => r.province === province) || null;
}

// Rango global del producto (para la escala de color del mapa).
export function getProductRange(productId, sparse) {
  const rows = getCoverage(productId, sparse);
  if (!rows.length) return null;
  const medians = rows.map((r) => r.median);
  return { min: Math.min(...medians), max: Math.max(...medians) };
}

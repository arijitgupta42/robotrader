const GBP = new Intl.NumberFormat("en-GB", { style: "currency", currency: "GBP", minimumFractionDigits: 2, maximumFractionDigits: 2 });
const INT = new Intl.NumberFormat("en-GB", { maximumFractionDigits: 0 });

/** £1,234.56; with `signed`, +£1.00 / −£1.00. */
export function gbp(value: number | null | undefined, signed = false): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "–";
  const text = GBP.format(Math.abs(value));
  if (!signed) return value < 0 ? `−${text}` : text;
  return `${value < 0 ? "−" : "+"}${text}`;
}

export function int(value: number | null | undefined): string {
  return value === null || value === undefined ? "–" : INT.format(value);
}

/** 0.712 -> "71%"; with `signed` and `digits`, +1.2%. */
export function pct(value: number | null | undefined, signed = false, digits = 0): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "–";
  const text = `${Math.abs(value * 100).toFixed(digits)}%`;
  if (!signed) return value < 0 ? `−${text}` : text;
  return `${value < 0 ? "−" : "+"}${text}`;
}

/** A price in its own units: 2,627.00p for LSE, $130.21 for US. */
export function price(value: number, market: string): string {
  const n = value.toLocaleString("en-GB", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  return market === "US" ? `$${n}` : `${n}p`;
}

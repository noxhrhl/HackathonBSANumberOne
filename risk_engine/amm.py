"""Mathématiques de l'AMM Pendle V2, transcrites de MarketMathCore.sol (en flottants).

Source : github.com/pendle-finance/pendle-core-v2-public, contracts/core/Market/MarketMathCore.sol
  rateScalar  = scalarRoot × 365 j / temps restant
  totalAsset  = totalSy × taux SY → actif
  proportion  = totalPt / (totalPt + totalAsset)
  rateAnchor  = exp(lastLnImpliedRate × t / an) − logit(proportion) / rateScalar
  taux (actif par PT) = logit(proportion') / rateScalar + rateAnchor     (≥ 1)
  frais : feeRate = exp(lnFeeRateRoot × t / an) ; vente de PT : frais = A × (feeRate − 1) / feeRate
  refus « MarketProportionTooHigh » si proportion' > 96 %.

Un retrait de liquidité est proportionnel (on rend des LP contre PT et SY dans la proportion
du pool) : proportion et taux implicite inchangés, donc PRIX SPOT INCHANGÉ ; seule la
profondeur baisse, et l'impact de prix d'une vente donnée augmente.
"""

import math

YEAR = 365 * 86400
MAX_MARKET_PROPORTION = 0.96


def logit(p):
    return math.log(p / (1 - p))


class PendleMarket:
    def __init__(self, total_pt, total_sy, sy_rate, scalar_root, ln_fee_rate_root, last_ln_implied_rate,
                 expiry_ts, now_ts):
        self.total_pt, self.total_sy, self.sy_rate = total_pt, total_sy, sy_rate
        self.scalar_root, self.ln_fee_rate_root = scalar_root, ln_fee_rate_root
        self.last_ln_implied_rate, self.expiry_ts, self.now_ts = last_ln_implied_rate, expiry_ts, now_ts
        self.t = max(expiry_ts - now_ts, 1)
        self.rate_scalar = scalar_root * YEAR / self.t
        self.total_asset = total_sy * sy_rate
        p0 = total_pt / (total_pt + self.total_asset)
        self.rate_anchor = math.exp(last_ln_implied_rate * self.t / YEAR) - logit(p0) / self.rate_scalar
        self.fee_rate = math.exp(ln_fee_rate_root * self.t / YEAR)

    @classmethod
    def from_state(cls, st):
        return cls(st["total_pt"], st["total_sy"], st["sy_rate"], st["scalar_root"], st["ln_fee_rate_root"],
                   st["last_ln_implied_rate"], st["expiry_ts"], st["snapshot_ts"])

    def scaled(self, keep):
        """Le pool après retrait proportionnel de (1 − keep) de la liquidité."""
        return PendleMarket(self.total_pt * keep, self.total_sy * keep, self.sy_rate, self.scalar_root,
                            self.ln_fee_rate_root, self.last_ln_implied_rate, self.expiry_ts, self.now_ts)

    def spot_pt_to_asset(self):
        """Prix marginal d'un PT en actif comptable (hors frais) = exp(−lnImpliedRate × t / an)."""
        p = self.total_pt / (self.total_pt + self.total_asset)
        return 1.0 / (logit(p) / self.rate_scalar + self.rate_anchor)

    def liquidity_asset(self):
        """Valeur du pool en actif comptable (PT au prix spot + actif)."""
        return self.total_pt * self.spot_pt_to_asset() + self.total_asset

    def max_pt_sellable(self):
        return max(0.0, MAX_MARKET_PROPORTION * (self.total_pt + self.total_asset) - self.total_pt)

    def sell_pt(self, pt_in):
        """Vente exacte de `pt_in` PT contre du SY (swapExactPtForSy)."""
        if pt_in <= 0:
            return {"ok": True, "pt_in": 0.0, "sy_out": 0.0, "asset_out": 0.0, "fee_asset": 0.0, "price_impact": 0.0}
        prop = (self.total_pt + pt_in) / (self.total_pt + self.total_asset)
        if prop > MAX_MARKET_PROPORTION:
            return {"ok": False, "pt_in": pt_in, "reason": "MarketProportionTooHigh",
                    "max_pt_sellable": self.max_pt_sellable()}
        rate = logit(prop) / self.rate_scalar + self.rate_anchor
        if rate < 1:
            return {"ok": False, "pt_in": pt_in, "reason": "MarketExchangeRateBelowOne"}
        pre_fee = pt_in / rate
        fee = pre_fee * (self.fee_rate - 1) / self.fee_rate
        asset_out = pre_fee - fee
        spot = self.spot_pt_to_asset()
        return {"ok": True, "pt_in": pt_in, "asset_out": asset_out, "sy_out": asset_out / self.sy_rate, "fee_asset": fee,
                "exec_pt_to_asset": asset_out / pt_in, "spot_pt_to_asset": spot,
                "price_impact": 1 - (asset_out / pt_in) / spot}

    def after_sale(self, pt_in, asset_out):
        """Pool après une vente (pour estimer l'effet d'une vente forcée sur le prix spot)."""
        prop = (self.total_pt + pt_in) / (self.total_pt + self.total_asset)
        new_rate = logit(prop) / self.rate_scalar + self.rate_anchor  # lastLnImpliedRate mis à jour par le swap
        return PendleMarket(self.total_pt + pt_in, (self.total_asset - asset_out) / self.sy_rate, self.sy_rate,
                            self.scalar_root, self.ln_fee_rate_root, math.log(new_rate) * YEAR / self.t,
                            self.expiry_ts, self.now_ts)

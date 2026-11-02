# Burning Sky V1.6.14 Web AOD zero-reach fix

- Fixed zero local theoretical illumination reach being treated as unlimited distance.
- Web path analysis now preserves a valid 0 km local reach rather than falling back to the maximum sampled reach.
- The shared boundary estimator excludes remote cloud samples when local reach is 0 km.
- No AOD formula, score weights, or probability thresholds were changed.
- Regression test covers 0 km reach with remote mid-cloud samples.

## Probability cap follow-up
- Added a final-result rule: when potential fire-cloud range is absent/0 km, conventional probability is capped at 20%.
- This cap is applied after score adjustments so later processing cannot raise it again.
- Existing stricter zero-probability rules and established verdicts (low cloud obstruction, too-low profile base, continuous-cloud attenuation, clear-sky verdict, and below-20% verdict) retain priority.
- Added regression tests for a 43% input score with zero potential range and for a valid nonzero range.
- Validation: `PYTHONPATH=. pytest -q` — 32 passed.

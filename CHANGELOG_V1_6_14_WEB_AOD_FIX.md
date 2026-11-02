# Burning Sky V1.6.14 Web AOD zero-reach fix

- Fixed zero local theoretical illumination reach being treated as unlimited distance.
- Web path analysis now preserves a valid 0 km local reach rather than falling back to the maximum sampled reach.
- The shared boundary estimator excludes remote cloud samples when local reach is 0 km.
- No AOD formula, score weights, or probability thresholds were changed.
- Regression test covers 0 km reach with remote mid-cloud samples.

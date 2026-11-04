# Debug40 · Rainbow visual corrections

- Rainbow arc opacity now follows primary/secondary opportunity and quality independently. At zero, arcs are nearly invisible.
- Evening peak cloud colour is blended into the schematic sky only if a >30 primary-rainbow candidate occurs within 45 minutes of sunset. No fabricated peak colour for standalone queries.
- Rainbow timeline displays only actual candidate samples above 30/100, excluding impossible periods from the x axis. It does not interpolate across gaps.
- Bitmap labels use ASCII to avoid missing Chinese glyphs on Streamlit Cloud.
- Removed archived debug READMEs, bundled case caches, logs, test outputs and experimental scripts from distribution. Core engine, desktop/web entry points, supporting modules, Excel city list and GitHub workflow retained.
- No changes to the rainbow probability or fire-cloud scoring algorithms.

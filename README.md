# Burning Sky V1.4.67 — Engine Architecture Migration

This release is a compatibility-first architecture refactor. The validated v1.4.67
forecast calculations are retained; no new weather models are enabled in this build.

## Architecture

- `burning_sky/algorithm.py`: independent deterministic calculation core extracted
  verbatim from the v1.4.67 baseline. It has no Tkinter, Streamlit, HTTP, or file-I/O
  dependencies.
- `burning_sky/services/`: provider-neutral application service that calls the core.
- `burning_sky/models.py`: common request, model metadata, and normalized forecast
  data contracts. Native resolution, initialization time, and cloud provenance are
  retained so native and humidity-derived cloud fields are never silently conflated.
- `burning_sky/providers/`: provider interface and Open-Meteo migration adapter.
  The existing Open-Meteo fetch/parser remains in the compatibility runtime in this
  release; the adapter boundary is prepared for the next migration step.
- `engine.py`: shared v1.4.67 compatibility runtime. It hosts the existing data
  acquisition and UI-facing workflow and imports the independent algorithm module.
- `app.py`: Streamlit web client; uses the same shared runtime and algorithm.
- `desktop/` and `clients/desktop/`: launchers for the same shared desktop runtime.

## Run

Desktop (Windows):

```bash
python desktop/fire_cloud_forecast_v1_4_67_local_200km_burn_scale.py
```

Web:

```bash
pip install -r requirements.txt
streamlit run app.py
```

## Algorithm preservation

The pure calculation block (`clamp` through `score_two_day_event_shared_core`,
including AOD, cloud-base geometry, path scoring, low-cloud screen constraint,
color scoring, and two-day scoring) was moved without changing function bodies.
The desktop runtime source is the v1.4.67 desktop implementation, not the older
headless `engine.py` copy from the prior package. This also keeps the local 200 km
burn-scale label behavior in both clients.

The class-level live-simulation/report rendering and legacy Open-Meteo request flow
remain in the compatibility runtime for this migration release. They are deliberately
not rewritten in the same change, to minimize regression risk. The next step can
move provider I/O and the remaining UI-independent live-simulation calculations into
separate modules behind the same interfaces.

## Tests

Run:

```bash
python -m unittest discover -s tests -v
```

The regression tests compare representative algorithm outputs against values
captured from the original v1.4.67 source, and compile/import checks cover both
client entry points.

## Next providers (not yet activated)

After this architecture build is validated, add separate adapters for ICON Global,
JMA MSM, ECMWF AIFS, and CMA GRAPES. These adapters must normalize input data only;
they must not contain or alter forecast scoring logic.

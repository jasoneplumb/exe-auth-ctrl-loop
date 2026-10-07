"""
Intent: Reproducible, offline, seeded simulations of the v2 authority lifecycle
Context: Every partition starts at n=0. A synthetic proposal stream is driven through the
        real controller, lifecycle, shadow log, risk policy, and gateway; only the models,
        the human reviewer, and the adjudication oracle are simulated. See README.md here.
Pattern: Config in, artifacts out. A scenario is a JSON file; a run is a seed; artifacts
        are CSV/JSON/JSONL/SVG whose canonical hash must not change between reruns.
Future: Synthetic. Establishes that the implemented system behaves as implemented under
        the modelled assumptions; it is not field validation.
"""

"""CLI entry: ``python -m harness.cli`` (run via ``scripts/run_agent_sim.sh``).

Examples
--------
Mechanism-only batch (provider=None → deterministic fallback, no model needed)::

    python -m harness.cli --manifest tp_demo_v5 --personas normal,asker,prober,idler

Live-model run inside the worker netns (Ollama on loopback)::

    docker exec investigators-worker python -m harness.cli \
        --manifest tp_demo_v5 --personas normal --provider ollama --dsn $DSN
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.providers.base import ProviderRequest, ProviderResponse


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="run_agent_sim", description=__doc__)
    ap.add_argument("--manifest", default="tp_demo_v5", help="manifest/profile id")
    ap.add_argument(
        "--personas",
        default="normal",
        help="comma list or name:count mix, e.g. 'normal:2,idler:1'",
    )
    ap.add_argument("--seed", type=int, default=1, help="base seed (replays a batch)")
    ap.add_argument(
        "--provider",
        choices=["none", "stub", "ollama"],
        default="none",
        help="generation mode: none=deterministic fallback, stub=canned lines, "
        "ollama=live model (worker netns only)",
    )
    ap.add_argument(
        "--paraphrase",
        action="store_true",
        help="rewrite bot utterances via AGENT_SIM_OLLAMA_URL when reachable",
    )
    ap.add_argument(
        "--dsn", default=None, help="existing disposable DSN (else ephemeral container)"
    )
    ap.add_argument("--out", type=Path, default=None, help="evidence output directory")
    ap.add_argument("--wall-step-ms", type=int, default=4000)
    ap.add_argument("--max-steps", type=int, default=400)
    ap.add_argument("--label", default=None)
    ap.add_argument(
        "--simulate",
        action="store_true",
        help="also render replays/<sid>.json into simulations/<sid>.html "
        "(self-contained walkthrough playback — needs node on PATH)",
    )
    args = ap.parse_args(argv)

    from app.domain.manifests.store import ContentStore

    from . import report as report_mod
    from .personas import mix
    from .runner import Runner
    from .simdb import provision

    persona_names = mix(args.personas)
    db, _disposable = provision(args.dsn)
    try:
        store = ContentStore.load_default()
        provider = _make_provider(args.provider)
        runner = Runner(
            db,
            store,
            provider=provider,
            wall_step_ms=args.wall_step_ms,
            max_steps=args.max_steps,
        )
        reports = report_mod.run_batch(
            runner, args.manifest, persona_names, seed=args.seed, paraphrase=args.paraphrase
        )
    finally:
        db.dispose()
    out = report_mod.write_report(
        reports,
        out_dir=args.out,
        label=args.label,
        provider_requested=args.provider,
        provider_effective=getattr(provider, "provider_id", None) or "deterministic-fallback",
    )
    if args.simulate:
        n = report_mod.render_simulations(out)
        if n:
            print(f"  simulations: {n} html file(s) under {out / 'simulations'}")
    fails = sum(r.failed for r in reports)
    print(f"wrote {out}")
    for r in reports:
        print(
            f"  {r.persona:16s} {r.stage:15s} {r.terminal_reason or '-':20s} "
            f"steps={len(r.steps):3d} fails={r.failed}"
        )
    return 1 if fails else 0


def _make_provider(mode: str) -> Any:
    """Resolve the requested generation mode to a provider.

    BA-85: ``--provider ollama`` must produce a real local adapter — if settings
    resolve to ``None`` (MODEL_PROVIDER=none/off) or to a double/remote adapter,
    the run must fail loudly rather than silently execute the fallback path.
    """
    if mode == "none":
        return None
    if mode == "ollama":
        from app.providers.select import provider_from_settings
        from app.settings import load_settings

        settings = load_settings()
        provider = provider_from_settings(settings)
        if (
            provider is None
            or getattr(provider, "is_double", False)
            or getattr(provider, "is_remote", False)
        ):
            raise SystemExit(
                "--provider ollama requires a live local adapter "
                "(MODEL_PROVIDER=ollama/local with LOCAL_MODEL_BASE_URL + LOCAL_MODEL_ID); "
                f"resolved to {provider!r} under MODEL_PROVIDER={settings.MODEL_PROVIDER!r}"
            )
        return provider
    return _StubProvider()


class _StubProvider:
    """Deterministic canned generation — exercises the generate→validate→deliver
    path without a model. Lines are drawn per-seat from a small bank so the
    near-repeat oracle has something real to check."""

    is_remote = False
    is_double = True
    provider_id = "sim-stub"

    def __init__(self) -> None:
        import itertools

        self._cycle = itertools.cycle(
            [
                "on it, checking my notes",
                "looking through what i have",
                "give me a sec on that",
                "i'll take a look now",
            ]
        )

    def generate(self, request: ProviderRequest, *, timeout_s: float) -> ProviderResponse:  # noqa: ARG002 - Provider protocol signature
        from app.providers.base import ProviderResponse

        return ProviderResponse(
            text=next(self._cycle),
            provider_id=self.provider_id,
            model_id="sim-stub-0",
            latency_ms=1,
            finish_reason="stop",
            params={},
        )


if __name__ == "__main__":
    sys.exit(main())

"""Several models standing in for each other on one design.

The hosted endpoint went quiet mid-design and the design failed:

    failed — meta/muse-glimmer-30b@https://integrate.api.nvidia.com/v1
             could not read a response: The read operation timed out

Two models were live at the time. Nothing used the second one, because lanes
spread *different* designs across models and there was only one design in
flight — which is the wrong shape of help. What a second model is for is this:
when the first stops answering, the work moves across rather than stopping.

The rule is about silence, not about how long the model takes. A vision model
reading a dense card can legitimately think for minutes, and cutting that off
would throw away work that was about to succeed. So the clock only runs while
nothing is coming back; once the model starts answering it may take as long as
it needs.
"""

from __future__ import annotations

import logging

from .base import ProviderError, VisionProvider
from .openai_compat import Silent

log = logging.getLogger("stockforge.providers")


class Failover(VisionProvider):
    """Tries each model in turn and returns the first real answer."""

    def __init__(self, providers: list[VisionProvider], on_switch=None):
        if not providers:
            raise ProviderError("a failover chain needs at least one model")
        self.providers = list(providers)
        self.on_switch = on_switch
        self.name = " then ".join(p.name for p in self.providers)

    @property
    def first(self) -> VisionProvider:
        return self.providers[0]

    def chat(self, system: str, user_text: str, images, **kw) -> str:
        # A wrong request is wrong at every model, so a 400 is raised rather
        # than walked down the chain — four models refusing the same malformed
        # call is four times the wait and the same answer. Silence and the
        # server's own faults are what the others exist for.
        trouble: list[str] = []
        for number, provider in enumerate(self.providers):
            try:
                if number:
                    log.warning("handing this design to %s", provider.name)
                    if self.on_switch:
                        self.on_switch(provider, trouble[-1])
                return provider.chat(system, user_text, images, **kw)
            except Silent as quiet:
                trouble.append(str(quiet))
                log.warning("%s", quiet)
            except ProviderError as exc:
                if not _worth_moving_on(exc):
                    raise
                trouble.append(f"{provider.name}: {exc}")
                log.warning("%s failed: %s", provider.name, exc)

        raise ProviderError(
            "every live model failed on this design:\n  "
            + "\n  ".join(trouble)
            + "\n\nRetry it from Review once one of them is answering again, or "
              "add another model on Setup."
        )


def _worth_moving_on(exc: ProviderError) -> bool:
    """Is this the endpoint's problem rather than the request's?

    A 400 means the request itself was refused and the next model will refuse
    it in the same way. Everything else — a 500, a gateway error, a model that
    came back empty — is worth another model's opinion.
    """
    text = str(exc)
    return "HTTP 400" not in text and "HTTP 401" not in text and "HTTP 403" not in text


def chain(providers: list[VisionProvider], on_switch=None) -> VisionProvider:
    """The chain, or the single provider unwrapped when there is nothing to
    fall back to — no point in the extra layer, or the extra name."""
    providers = [p for p in providers if p is not None]
    if not providers:
        raise ProviderError("no models to work with")
    if len(providers) == 1:
        return providers[0]
    return Failover(providers, on_switch=on_switch)

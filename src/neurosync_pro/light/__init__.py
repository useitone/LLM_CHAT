"""Optional EEG → LED intent bridge (see LedMatrix.md). Disabled unless NSP_LIGHT_ENABLED."""

from neurosync_pro.light.intent_sink import LightIntentSink, try_attach_light_intent_sink
from neurosync_pro.light.metrics_hook import MetricsLightBridge, try_attach_metrics_light_bridge

__all__ = [
    "LightIntentSink",
    "MetricsLightBridge",
    "try_attach_light_intent_sink",
    "try_attach_metrics_light_bridge",
]

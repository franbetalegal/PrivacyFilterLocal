"""Release smoke test: prove a built package can actually find a person's name.

Run on every platform we publish, against the environment that will ship. It
does what a first run does — download the spaCy models through
``server.ner_models`` — and then asserts that a name written in caps comes back
as a person.

Why this exists as a separate script instead of a unit test: the unit tests are
offline by design and skip when the models are absent, which is exactly the
condition that shipped in every release up to 2.6.3. Those builds redacted DNIs
and addresses while leaving every name in caps untouched, and nothing in the
pipeline failed. This is the check that fails.

    python smoke_ner.py
"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

# An invented name, in caps, of the shape Spanish tax and court documents use:
# surnames first, no accents, no honorific in front of it. The all-caps form is
# the one the models miss unless server/ner_es.py normalises the case first.
SAMPLE = "AURORA VIDALBA SERENA"


def main() -> int:
    if not os.environ.get("PF_NER_DIR"):
        # Keep a CI run from writing into the checkout, and make a local run
        # disposable. A real install points this at the app folder.
        os.environ["PF_NER_DIR"] = tempfile.mkdtemp(prefix="pf_ner_smoke_")
    print(f"Models directory: {os.environ['PF_NER_DIR']}")

    from server import inference, ner_es, ner_models

    print(f"Configured models: {', '.join(ner_models.MODEL_NAMES)}")
    inference.ensure_ner_models_ready()

    state = inference.ner_status()
    for model in state["models"]:
        print(f"  {model['name']}: present={model['present']} version={model['version']}")
    if state["error"]:
        print(f"FAIL: the models could not be installed: {state['error']}")
        return 1
    if not state["available"]:
        print("FAIL: the NER layer reports itself unavailable after the install.")
        return 1

    spans = ner_es.analyze(SAMPLE)
    labels = {span.entity_type for span in spans}
    print(f"Analyzing {SAMPLE!r} -> {sorted(labels) or 'nothing'}")
    if "ES_NER_PER" not in labels:
        print(
            "FAIL: an all-caps person name was not detected. This build would "
            "return documents with every name in caps left in place."
        )
        return 1

    print("OK: the packaged environment detects a person name written in caps.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

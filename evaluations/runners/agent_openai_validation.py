"""Active OpenAI-only quality scope; no Gemini credentials, jobs or candidates."""
import hashlib
from pathlib import Path
import agent_selection as evaluator
import agent_selection_v2 as v2

ROOT = evaluator.ROOT
DATA = v2.DATA
CONFIG = ROOT/'evaluations/rubrics/agent_openai_validation.json'
OUT = ROOT/'local/ai-04-openai'
_BASE_HASHES = evaluator.source_hashes


def source_hashes():
    hashes = _BASE_HASHES()
    paths = [Path(__file__), Path(v2.__file__),
             *Path(__file__).parent.glob('test_agent_openai*.py')]
    for path in paths:
        hashes[path.relative_to(ROOT).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return hashes


def configure():
    evaluator.DATA, evaluator.CONFIG, evaluator.OUT = DATA, CONFIG, OUT
    evaluator.source_hashes, evaluator.baseline = source_hashes, v2.baseline
    return evaluator


if __name__=='__main__':
    configure().main()

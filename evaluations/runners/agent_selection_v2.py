"""New frozen dataset/output namespace; reuse the bounded AI-04 evaluator."""
import hashlib
import re
from pathlib import Path
import agent_selection as evaluator

ROOT = evaluator.ROOT
DATA = ROOT/'evaluations/datasets/agent_selection_v2.json'
CONFIG = ROOT/'evaluations/rubrics/agent_selection_v2.json'
OUT = ROOT/'local/ai-04-v2'
_BASE_HASHES = evaluator.source_hashes
_BASE_BASELINE = evaluator.baseline


def baseline(row):
    if row['task'] != 'plan':
        return _BASE_BASELINE(row)
    report, tools = row['report'], row['tools']
    # Conventional text intake: declared reference labels first, never gold.
    declared = re.findall(r'\b(booking|reservation|transaction|reservasi|transaksi|pesanan)\s+([A-Za-z0-9_-]{1,80})(?![A-Za-z0-9_-])', report, re.I)
    transaction = [(kind, ref) for kind, ref in declared if kind.lower() in {'transaction','transaksi'}]
    if transaction:
        name, refs = 'read_transaction', [ref for _, ref in transaction]
    else:
        policy = re.search(r'\b(policy|terms|cancellation|kebijakan|ketentuan|pembatalan)\b', report, re.I)
        name = 'read_booking_policy' if policy else 'read_booking'
        # Include additional unlabelled reference tokens in a two-order request.
        refs = [ref for _, ref in declared]
        refs += re.findall(r'(?<![A-Za-z0-9_-])(?=[A-Za-z0-9_-]*\d)[A-Za-z0-9][A-Za-z0-9_-]{0,79}(?![A-Za-z0-9_-])', report)
    refs = list(dict.fromkeys(refs))
    if len(refs)==1 and name in tools:
        return {'kind':'tool', 'tool':{'name':name, 'arguments':{'reference':refs[0]}}}
    return {'kind':'clarify', 'clarification':{'question':
            'Which airport service and reference should we check?' if row['language']=='en'
            else 'Layanan bandara dan referensi mana yang perlu diperiksa?'}}


def source_hashes():
    hashes = _BASE_HASHES()
    paths = [Path(__file__), Path(__file__).with_name('gemini_diagnostic.py'),
             *Path(__file__).parent.glob('test_agent_v2*.py')]
    for path in paths:
        hashes[path.relative_to(ROOT).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return hashes


def configure():
    evaluator.DATA, evaluator.CONFIG, evaluator.OUT = DATA, CONFIG, OUT
    evaluator.source_hashes = source_hashes
    evaluator.baseline = baseline
    return evaluator


if __name__ == '__main__':
    configure().main()
